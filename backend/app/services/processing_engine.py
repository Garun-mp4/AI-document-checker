from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models import Chunk, Document, DocumentVersion, ProcessingJob
from app.services.analysis import analyze_document
from app.services.codex import CodexModelUnavailable, CodexNeedsLogin, CodexUnavailable
from app.services.document_security import (
    owned_storage,
    read_storage,
    remove_storage,
    write_artifact,
)
from app.services.embeddings import EmbeddingConfigurationError
from app.services.isolated_documents import (
    map_uploaded,
    ocr_uploaded,
    parse_uploaded,
    run_document_operation,
)
from app.services.job_queue import LeaseLost, discard, enter_analysis, fenced
from app.services.markdown_mapping import serialize_map
from app.services.markitdown_service import MarkdownConversionError, MarkItDownService
from app.services.ocr import OCRProcessingError
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock
from app.services.processing_helpers import (
    _carry_forward_unselected_ocr_pages,
    _computed_blocks,
    _merge_pdf_ocr_pages,
    _ocr_analysis_blocks,
    _pdf_markdown,
)

logger = logging.getLogger(__name__)

RESULT_FIELDS = ('file_type', 'metadata_json', 'chunk_count', 'markdown_status', 'analysis_source',
                 'markdown_path', 'markdown_map_path', 'markdown_error', 'markdown_converter_version',
                 'markdown_char_count', 'markdown_line_count', 'markdown_checksum', 'markdown_mapping_json',
                 'ocr_status', 'ocr_language', 'ocr_page_count', 'ocr_confidence', 'ocr_error',
                 'ocr_engine_version', 'ocr_char_count')

def publish(document, snapshot):
    for name in RESULT_FIELDS:
        if name in snapshot:
            setattr(document, name, snapshot[name])
    if 'metadata' in snapshot and 'metadata_json' not in snapshot:
        document.metadata_json = snapshot['metadata']

class ProcessingAttempt:
    def __init__(self, job, codex, stage_hook=None):
        self.job = job
        self.codex = codex
        self.stage_hook = stage_hook
        self.prefix = f'{job.document_id}.v{job.version}.{job.owner.hex}'
        self.markitdown = MarkItDownService(settings.upload_dir)
        ocr = getattr(job, 'parameters', {}).get('ocr', {})
        self.configuration = {
            'ocr_enabled': ocr.get('enabled', settings.ocr_enabled),
            'ocr_languages': ocr.get('languages', settings.ocr_languages),
            'ocr_dpi': ocr.get('dpi', settings.ocr_dpi),
            'ocr_max_pages': ocr.get('max_pages', settings.ocr_max_pages),
            'ocr_confidence_warning_threshold': settings.ocr_confidence_warning_threshold,
        }

    def artifact(self, suffix):
        return Path(settings.upload_dir).resolve() / f'{self.prefix}.{suffix}'

    async def stage(self, name, **progress):
        async with fenced(self.job.id, self.job.owner) as (session, document, job):
            if job.stage != name:
                job.stage_started_at = await session.scalar(select(func.clock_timestamp()))
            job.stage = name
            job.progress = progress
            if document.status != 'ready':
                document.status = ('analyzing' if name in ('analysis_request', 'waiting_analysis')
                                   else 'indexing' if name == 'indexing_checkpoint' else name)
                if name == 'ocr':
                    document.ocr_status = 'processing'
        if self.stage_hook:
            await self.stage_hook(self.job, name)

    async def write(self, path, contents):
        async with fenced(self.job.id, self.job.owner):
            write_artifact(owned_storage(path, self.job.document_id), contents)

    async def index(self):
        document_id = self.job.document_id
        previous_ocr_metadata: dict[str, object] = {}
        previous_ocr_blocks: list[SourceBlock] = []
        async with fenced(self.job.id, self.job.owner) as (session, document, job):
            path = owned_storage(document.storage_path, document_id)
            filename = document.filename
            checksum = hashlib.sha256(read_storage(path, settings.max_upload_bytes)).hexdigest()
            if (job.input_version and checksum != job.input_version) or (document.input_checksum and checksum != document.input_checksum):
                raise DocumentParsingError('Исходный файл изменился. Загрузите документ заново.')
            document.input_checksum = checksum
            job.input_version = checksum
            version = await session.get(DocumentVersion, (document_id, job.version))
            version.snapshot = {'attempt_prefix': self.prefix}
            if document.active_version:
                previous_version = await session.get(DocumentVersion, (document_id, document.active_version))
                if previous_version:
                    previous_ocr_metadata = dict(
                        previous_version.snapshot.get('metadata_json') or document.metadata_json or {}
                    )
                    previous_chunks = (await session.execute(select(Chunk.text, Chunk.locator).where(
                        Chunk.document_id == document_id,
                        Chunk.version == previous_version.chunk_version,
                    ))).all()
                    previous_ocr_blocks = [
                        SourceBlock(text, locator)
                        for text, locator in previous_chunks
                        if isinstance(locator, dict) and locator.get('ocr') is True
                    ]
        await self.stage('extracting')
        parsed = await parse_uploaded(path, filename, configuration=self.configuration)
        # The original Markdown upload is already named <id>.md. Use a
        # separate artifact name so conversion/fallback cannot overwrite
        # or unlink the immutable original (including its encoding).
        markdown_path = self.artifact("markdown.md")
        markdown_map_path = self.artifact("map.json")
        analysis_blocks: list[tuple[str, dict[str, object], str, int | None, int | None, int | None, int | None, str | None]] = []
        markdown_status = "fallback"
        analysis_source = "native_fallback"
        markdown_error: str | None = None
        markdown_mapping: dict[str, object] = {}
        markdown_checksum: str | None = None
        markdown_text = ""
        markdown_engine_version: str | None = None
        ocr_metadata: dict[str, object] = {}
        candidate_pages = parsed.metadata.get("ocr_pages", []) if parsed.file_type == "pdf" else []
        requested_pages = getattr(self.job, 'parameters', {}).get('ocr_pages')
        previous_page_results = previous_ocr_metadata.get('ocr_page_map', [])
        if parsed.file_type == 'pdf' and requested_pages is None:
            retryable_previous_pages = [
                item.get('page') for item in previous_page_results
                if isinstance(item, dict) and item.get('classification') in {'ocr', 'ocr_candidate', 'unreadable'}
            ]
            candidate_pages = sorted(set(candidate_pages) | {
                page for page in retryable_previous_pages if isinstance(page, int)
            })
        elif requested_pages is not None:
            candidate_pages = requested_pages
        deferred_pages: list[int] = []
        if requested_pages is None and len(candidate_pages) > settings.ocr_max_pages:
            deferred_pages = candidate_pages[settings.ocr_max_pages:]
            candidate_pages = candidate_pages[:settings.ocr_max_pages]
        if candidate_pages:
            await self.stage('ocr', processed_pages=0, total_pages=len(candidate_pages))

            async def report_ocr_progress(processed_pages: int, total: int) -> None:
                await self.stage('ocr', processed_pages=processed_pages, total_pages=total)

            ocr_result = await ocr_uploaded(path, configuration=self.configuration, pages=candidate_pages,
                                            allow_empty=bool(parsed.blocks) or bool(previous_ocr_blocks),
                                            progress_callback=report_ocr_progress)
            selected_pages = set(candidate_pages)
            deferred_results = [
                {
                    'page': page,
                    'classification': 'unreadable',
                    'error': 'page_limit',
                    'raster_width': None,
                    'raster_height': None,
                    'dpi': self.configuration['ocr_dpi'],
                    'rotation': 0,
                    'crop_box': None,
                    'language': self.configuration['ocr_languages'],
                    'engine_version': ocr_result.engine_version,
                    'word_count': 0,
                    'line_count': 0,
                    'confidence': None,
                }
                for page in deferred_pages
            ]
            carried_blocks, carried_page_results = _carry_forward_unselected_ocr_pages(
                previous_ocr_blocks,
                [item for item in previous_page_results if isinstance(item, dict)],
                ocr_result.parsed.blocks,
                [item for item in ocr_result.parsed.metadata.get('ocr_page_map', []) if isinstance(item, dict)],
                selected_pages,
            )
            combined_ocr = ParsedDocument(
                'pdf',
                carried_blocks,
                {
                    **ocr_result.parsed.metadata,
                    'ocr_page_map': [
                        *carried_page_results,
                        *deferred_results,
                    ],
                    'ocr_language': self.configuration['ocr_languages'],
                    'ocr_dpi': self.configuration['ocr_dpi'],
                    'ocr_settings': {
                        'language': self.configuration['ocr_languages'],
                        'dpi': self.configuration['ocr_dpi'],
                        'quality': getattr(self.job, 'parameters', {}).get('ocr', {}).get('quality', 'balanced'),
                    },
                },
            )
            parsed, ocr_summary = _merge_pdf_ocr_pages(parsed, combined_ocr)
            ocr_metadata = parsed.metadata
            if not parsed.blocks:
                raise OCRProcessingError("В PDF не найден читаемый текст: страницы пустые или не удалось распознать скан.")
            if ocr_summary["ocr_pages"]:
                markdown_text = _pdf_markdown(parsed)
                analysis_blocks, markdown_mapping = _ocr_analysis_blocks(parsed, markdown_text)
                if not analysis_blocks:
                    raise OCRProcessingError("OCR не смог связать распознанный текст со страницей документа.")
                markdown_status = "ready"
                analysis_source = "ocr"
                markdown_engine_version = ocr_result.engine_version
                markdown_checksum = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
                await self.write(markdown_path, markdown_text)
                await self.write(markdown_map_path, serialize_map(markdown_mapping))

        if markdown_status != "ready":
            if not parsed.blocks:
                if parsed.file_type == "pdf":
                    raise DocumentParsingError("В PDF не найден читаемый текст: документ может состоять из пустых страниц.")
                raise DocumentParsingError("Не удалось извлечь текст из файла.")
            try:
                markdown_result = await self.markitdown.convert(path)
                markdown_text = markdown_result.markdown
                await self.write(markdown_path, markdown_result.markdown)
                mapped_blocks, markdown_mapping = await map_uploaded(path, filename, markdown_path)
                if not mapped_blocks or not any(block.locator.get("source_locators") for block in mapped_blocks):
                    raise MarkdownConversionError("MarkItDown не смог связать Markdown с исходными местами документа.")
                await self.write(markdown_map_path, serialize_map(markdown_mapping))
                markdown_checksum = hashlib.sha256(markdown_result.markdown.encode("utf-8")).hexdigest()
                analysis_blocks = [(
                    block.text,
                    block.locator,
                    "markitdown",
                    block.line_start,
                    block.line_end,
                    block.char_start,
                    block.char_end,
                    block.confidence,
                ) for block in mapped_blocks]
                markdown_status = "ready"
                analysis_source = "markitdown"
                markdown_engine_version = "0.1.8"
            except (MarkdownConversionError, OSError) as exc:
                markdown_error = str(exc) or "MarkItDown не смог сохранить результат преобразования."
                for artifact in (markdown_path, markdown_map_path):
                    try:
                        remove_storage(artifact)
                    except (OSError, DocumentParsingError):
                        logger.warning("Could not remove failed Markdown artifact")
                analysis_blocks = [(
                    block.text,
                    {**block.locator, "source_text": block.text},
                    "native_fallback",
                    None,
                    None,
                    None,
                    None,
                    "exact",
                ) for block in parsed.blocks]

        snapshot = {
            'attempt_prefix': self.prefix,
            'file_type': parsed.file_type,
            'metadata_json': {
                **parsed.metadata,
                'markdown_status': markdown_status,
                'analysis_source': analysis_source,
                'ocr_confidence_warning_threshold': settings.ocr_confidence_warning_threshold,
                'ocr_max_pages': settings.ocr_max_pages,
            },
            'markdown_status': markdown_status, 'analysis_source': analysis_source,
            'markdown_path': str(markdown_path) if markdown_status == 'ready' else None,
            'markdown_map_path': str(markdown_map_path) if markdown_status == 'ready' else None,
            'markdown_error': markdown_error, 'markdown_converter_version': markdown_engine_version,
            'markdown_char_count': len(markdown_text) if markdown_status == 'ready' else sum(len(b[0]) for b in analysis_blocks),
            'markdown_line_count': len(markdown_text.splitlines()) if markdown_status == 'ready' else 0,
            'markdown_checksum': markdown_checksum,
            'markdown_mapping_json': markdown_mapping.get('quality', {}) if markdown_status == 'ready' else {},
            'ocr_status': (
                'partial' if ocr_metadata.get('ocr_summary', {}).get('unreadable_pages')
                else 'ready' if ocr_metadata.get('ocr_used') else 'not_needed'
            ),
            'ocr_language': ocr_metadata.get('ocr_language'),
            'ocr_page_count': ocr_metadata.get('ocr_page_count') or None,
            'ocr_confidence': ocr_metadata.get('ocr_confidence'), 'ocr_error': None,
            'ocr_engine_version': ocr_metadata.get('ocr_engine_version') if ocr_metadata else None,
            'ocr_char_count': ocr_metadata.get('ocr_char_count', 0),
        }
        unreadable_pages = ocr_metadata.get('ocr_summary', {}).get('unreadable_pages', [])
        if unreadable_pages:
            snapshot['ocr_error'] = 'Не удалось распознать текст на страницах: ' + ', '.join(map(str, unreadable_pages)) + '.'
        derived = _computed_blocks(parsed)
        await self.stage('indexing', completed=0, total=len(analysis_blocks))
        for start in range(0, len(analysis_blocks), 48):
            batch = analysis_blocks[start:start+48]
            batch_path = self.artifact('embedding.json')
            await self.write(batch_path, json.dumps([b[0] for b in batch], ensure_ascii=False))
            vectors = await run_document_operation('embed', batch_path)
            async with fenced(self.job.id, self.job.owner) as (session, _, job):
                remove_storage(batch_path)
                session.add_all([Chunk(
                    document_id=document_id, version=job.version, ordinal=start+i, text=b[0],
                    locator={**b[1], 'processing_version': job.version}, embedding=v, is_derived=False,
                    content_source=b[2], markdown_line_start=b[3], markdown_line_end=b[4],
                    markdown_char_start=b[5], markdown_char_end=b[6], mapping_confidence=b[7],
                ) for i, (b,v) in enumerate(zip(batch,vectors,strict=True))])
                job.progress = {'completed': start+len(batch), 'total': len(analysis_blocks)}
            await self.stage('indexing_checkpoint', completed=start+len(batch), total=len(analysis_blocks))
        async with fenced(self.job.id, self.job.owner) as (session, document, job):
            session.add_all([Chunk(document_id=document_id, version=job.version,
                ordinal=len(analysis_blocks)+i, text=b.text,
                locator={**b.locator, 'processing_version': job.version}, is_derived=True)
                for i,b in enumerate(derived)])
            snapshot['chunk_count'] = len(analysis_blocks)+len(derived)
            version = await session.get(DocumentVersion, (document_id, job.version))
            version.snapshot = snapshot
            version.state = 'indexed'
            # The first index is useful even while authentication is unavailable.
            if not document.chunk_count:
                publish(document, snapshot)
                document.active_version = job.version
        return snapshot

    async def run(self):
        try:
            async with fenced(self.job.id, self.job.owner) as (session, document, job):
                version = await session.get(DocumentVersion, (job.document_id, job.version))
                snapshot = dict(version.snapshot)
                chunk_version = version.chunk_version
                indexed = version.state == 'indexed'
                if indexed:
                    checksum = hashlib.sha256(read_storage(owned_storage(document.storage_path, document.id), settings.max_upload_bytes)).hexdigest()
                    if (document.input_checksum and checksum != document.input_checksum) or (job.input_version and checksum != job.input_version):
                        raise DocumentParsingError('Исходный файл изменился. Загрузите документ заново.')
                    document.input_checksum = checksum
                    job.input_version = checksum
            if not indexed:
                snapshot = await self.index()
            await self.stage('waiting_analysis')
            while not await enter_analysis(self.job.id, self.job.owner):
                await asyncio.sleep(settings.queue_poll_seconds)
            if self.stage_hook:
                await self.stage_hook(self.job, 'analysis_request')
            insights = await analyze_document(self.job.document_id, self.codex,
                version=chunk_version, snapshot=snapshot, before_request=self.before_request)
            async with fenced(self.job.id, self.job.owner) as (session, document, job):
                for insight in insights:
                    insight.version = job.version
                session.add_all(insights)
                version = await session.get(DocumentVersion, (job.document_id, job.version))
                version.state = 'ready'
                publish(document, version.snapshot)
                document.active_version = job.version
                document.status = 'ready'
                document.error_message = None
                job.state = 'succeeded'
                job.stage = 'complete'
                job.finished_at = await session.scalar(select(func.now()))
                job.lease_until = None
        except LeaseLost:
            await self.finish_cancel()
            return
        except asyncio.CancelledError:
            await self.finish_cancel()
            raise
        except Exception as exc:
            status, code, message = failure(exc)
            try:
                async with fenced(self.job.id, self.job.owner) as (session, document, job):
                    await discard(session, job)
                    job.state, job.error_code, job.error = 'failed', code, message[:1000]
                    job.finished_at = await session.scalar(select(func.now()))
                    job.lease_until = None
                    if document.status != 'ready':
                        document.status = status
                        if isinstance(exc, OCRProcessingError):
                            document.ocr_status = 'failed'
                            document.ocr_error = message[:1000]
                    document.error_message = message[:1000]
            except LeaseLost:
                pass
            logger.exception('Processing attempt failed (%s)', type(exc).__name__, exc_info=False)

    async def before_request(self):
        # Check the fence immediately before the external side effect.
        async with fenced(self.job.id, self.job.owner):
            pass

    async def finish_cancel(self):
        # A cancelled/expired owner cannot modify a successor's attempt.
        async with SessionLocal() as session, session.begin():
            document = (await session.execute(select(Document).where(Document.id == self.job.document_id).with_for_update())).scalar_one_or_none()
            job = await session.get(ProcessingJob, self.job.id, with_for_update=True)
            if document is None or job is None or job.owner != self.job.owner:
                return
            if job.state == 'cancelling':
                await discard(session, job)
                job.state = 'cancelled'
                job.finished_at = await session.scalar(select(func.now()))
                job.lease_until = None
                if document.status != 'ready':
                    document.status = 'cancelled'
            # Worker shutdown leaves running leases for recovery, not cancellation.


def failure(exc):
    if isinstance(exc, CodexNeedsLogin):
        return 'needs_auth', 'needs_auth', str(exc)
    if isinstance(exc, CodexModelUnavailable):
        return 'model_unavailable', 'model_unavailable', str(exc)
    if isinstance(exc, (CodexUnavailable, DocumentParsingError)):
        return 'error', 'processing_failed', str(exc)
    return 'error', 'processing_failed', processing_error(exc)

def processing_error(exc: Exception) -> str:
    if isinstance(exc, EmbeddingConfigurationError):
        return str(exc)
    lowered = str(exc).lower()
    if "connect" in lowered or "network" in lowered or "download" in lowered or "https" in lowered:
        return "Не удалось загрузить локальную модель или связаться с Codex. Проверьте интернет и повторите обработку."
    if "out of memory" in lowered or "memory" in lowered:
        return "Не хватило памяти для обработки документа. Закройте другие приложения и повторите попытку."
    return "Не удалось обработать документ. Проверьте файл и повторите попытку."
