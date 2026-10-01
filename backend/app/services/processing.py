from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import Chat, Chunk, Document
from app.services.analysis import analyze_document
from app.services.codex import (
    CodexModelUnavailable,
    CodexNeedsLogin,
    CodexService,
    CodexUnavailable,
)
from app.services.embeddings import EmbeddingConfigurationError, embed_passages
from app.services.markdown_mapping import map_markdown, serialize_map
from app.services.markitdown_service import MarkdownConversionError, MarkItDownService
from app.services.ocr import OCRProcessingError, OCRService
from app.services.parsing import (
    DocumentParsingError,
    ParsedDocument,
    SourceBlock,
    parse_document,
)

logger = logging.getLogger(__name__)

PROCESSING_STATES = {"extracting", "ocr", "indexing", "analyzing"}


def _computed_blocks(parsed: ParsedDocument) -> list[SourceBlock]:
    if parsed.file_type not in {"csv", "xlsx", "xls"}:
        return []
    metadata = parsed.metadata
    row_end = int(metadata.get("row_count", 0)) + 1
    blocks = [SourceBlock(
        f"Локальная структура таблицы: {metadata.get('row_count', 0)} строк данных, {metadata.get('column_count', 0)} столбцов.",
        {"kind": f"{parsed.file_type}_derived", "label": "Сводка таблицы", "row_start": 1, "row_end": row_end, "derived": True},
        derived=True,
    )]
    for item in metadata.get("numeric_columns", []):
        blocks.append(SourceBlock(
            f"Локальный расчёт по столбцу «{item['name']}» и значениям строк 2–{row_end}: "
            f"числовых значений {item['count']}; сумма {item['sum']}; среднее {item['average']}; "
            f"минимум {item['minimum']}; максимум {item['maximum']}.",
            {
                "kind": f"{parsed.file_type}_derived",
                "label": f"Показатели столбца «{item['name']}» · строки 2–{row_end}",
                "column": item["name"],
                "row_start": 2,
                "row_end": row_end,
                "derived": True,
            },
            derived=True,
        ))
    return blocks


def _ocr_analysis_blocks(
    parsed: ParsedDocument,
    markdown: str,
) -> tuple[list[tuple[str, dict[str, object], str, int | None, int | None, int | None, int | None, str | None]], dict[str, object]]:
    """Build direct Markdown-to-page anchors for OCR output."""
    result: list[tuple[str, dict[str, object], str, int | None, int | None, int | None, int | None, str | None]] = []
    cursor = 0
    for block in parsed.blocks:
        position = markdown.find(block.text, cursor)
        if position < 0:
            position = markdown.find(block.text)
        if position < 0:
            continue
        end = position + len(block.text)
        line_start = markdown.count("\n", 0, position) + 1
        line_end = markdown.count("\n", 0, end) + 1
        locator = {
            **block.locator,
            "source_text": block.text,
            "source_locators": [block.locator],
            "markdown_line_start": line_start,
            "markdown_line_end": line_end,
            "markdown_char_start": position,
            "markdown_char_end": end,
        }
        result.append((block.text, locator, "ocr", line_start, line_end, position, end, "exact"))
        cursor = end
    return result, {"quality": {"exact": len(result), "fuzzy": 0, "nearest": 0, "none": max(0, len(parsed.blocks) - len(result))}}


class DocumentProcessor:
    def __init__(self, codex: CodexService) -> None:
        self.codex = codex
        self.markitdown = MarkItDownService(settings.upload_dir)
        self.ocr = OCRService(settings.upload_dir)
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    async def start(self) -> None:
        async with SessionLocal() as session:
            # Older databases may contain documents created before the chat
            # library existed. Repair that relationship on startup so a
            # restart never hides an existing document session.
            document_ids = set((await session.execute(select(Document.id))).scalars().all())
            chat_document_ids = set((await session.execute(select(Chat.document_id))).scalars().all())
            session.add_all([
                Chat(document_id=document_id)
                for document_id in document_ids - chat_document_ids
            ])
            interrupted = (await session.execute(
                select(Document).where(Document.status.in_(PROCESSING_STATES))
            )).scalars().all()
            for document in interrupted:
                document.status = "queued"
                document.error_message = None
            queued = (await session.execute(select(Document.id).where(Document.status == "queued"))).scalars().all()
            await session.commit()
        for document_id in queued:
            self.schedule(document_id)
        try:
            state = await self.codex.status(refresh=True)
            if state["authenticated"] and state["model_available"] and state["reasoning_available"]:
                await self.schedule_pending_analysis()
        except Exception:
            logger.exception("Could not resume pending document analysis")

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def schedule(self, document_id: uuid.UUID) -> None:
        task = self._tasks.get(document_id)
        if task and not task.done():
            return
        self._tasks[document_id] = asyncio.create_task(self._process(document_id))

    async def schedule_pending_analysis(self) -> None:
        async with SessionLocal() as session:
            ids = (await session.execute(
                select(Document.id).where(Document.status.in_(["needs_auth", "model_unavailable"]))
            )).scalars().all()
        for document_id in ids:
            self.schedule_analysis(document_id)

    def schedule_analysis(self, document_id: uuid.UUID) -> None:
        task = self._tasks.get(document_id)
        if task and not task.done():
            return
        self._tasks[document_id] = asyncio.create_task(self._analyze(document_id))

    async def retry(self, document_id: uuid.UUID) -> None:
        async with SessionLocal() as session:
            document = await session.get(Document, document_id)
            if document is None:
                raise KeyError(document_id)
            if document.status in {"needs_auth", "model_unavailable"} and document.chunk_count:
                document.status = "analyzing"
                document.error_message = None
                await session.commit()
                self.schedule_analysis(document_id)
            else:
                document.status = "queued"
                document.error_message = None
                await session.commit()
                self.schedule(document_id)

    async def _process(self, document_id: uuid.UUID) -> None:
        try:
            async with SessionLocal() as session:
                document = await session.get(Document, document_id)
                if document is None:
                    return
                path = Path(document.storage_path)
                filename = document.filename
                document.status = "extracting"
                document.error_message = None
                await session.commit()

            content = await asyncio.to_thread(path.read_bytes)
            parsed = await asyncio.to_thread(parse_document, filename, content)
            # The original Markdown upload is already named <id>.md. Use a
            # separate artifact name so conversion/fallback cannot overwrite
            # or unlink the immutable original (including its encoding).
            markdown_path = Path(settings.upload_dir).resolve() / f"{document_id}.markdown.md"
            markdown_map_path = Path(settings.upload_dir).resolve() / f"{document_id}.map.json"
            analysis_blocks: list[tuple[str, dict[str, object], str, int | None, int | None, int | None, int | None, str | None]] = []
            markdown_status = "fallback"
            analysis_source = "native_fallback"
            markdown_error: str | None = None
            markdown_mapping: dict[str, object] = {}
            markdown_checksum: str | None = None
            markdown_text = ""
            markdown_engine_version: str | None = None
            ocr_metadata: dict[str, object] = {}
            if parsed.metadata.get("ocr_required"):
                await self._set_ocr_state(document_id, "processing", None)
                ocr_result = await asyncio.to_thread(self.ocr.process, path)
                parsed = ocr_result.parsed
                markdown_text = ocr_result.markdown
                analysis_blocks, markdown_mapping = _ocr_analysis_blocks(parsed, markdown_text)
                if not analysis_blocks:
                    raise OCRProcessingError("OCR не смог связать распознанный текст со страницей документа.")
                markdown_status = "ready"
                analysis_source = "ocr"
                markdown_engine_version = ocr_result.engine_version
                ocr_metadata = parsed.metadata
                markdown_checksum = hashlib.sha256(markdown_text.encode("utf-8")).hexdigest()
                await asyncio.to_thread(markdown_path.write_text, markdown_text, "utf-8")
                await asyncio.to_thread(markdown_map_path.write_text, serialize_map(markdown_mapping), "utf-8")
            else:
                if not parsed.blocks:
                    raise DocumentParsingError("Не удалось извлечь текст из файла.")
                try:
                    markdown_result = await self.markitdown.convert(path)
                    markdown_text = markdown_result.markdown
                    mapped_blocks, markdown_mapping = map_markdown(markdown_result.markdown, parsed.blocks)
                    if not mapped_blocks or not any(block.locator.get("source_locators") for block in mapped_blocks):
                        raise MarkdownConversionError("MarkItDown не смог связать Markdown с исходными местами документа.")
                    await asyncio.to_thread(markdown_path.write_text, markdown_result.markdown, "utf-8")
                    await asyncio.to_thread(markdown_map_path.write_text, serialize_map(markdown_mapping), "utf-8")
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
                            artifact.unlink(missing_ok=True)
                        except OSError:
                            logger.warning("Could not remove failed Markdown artifact %s", artifact, exc_info=True)
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

            async with SessionLocal() as session:
                document = await session.get(Document, document_id)
                if document is None:
                    return
                await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
                document.file_type = parsed.file_type
                document.metadata_json = {
                    **parsed.metadata,
                    "markdown_status": markdown_status,
                    "analysis_source": analysis_source,
                }
                document.markdown_status = markdown_status
                document.analysis_source = analysis_source
                document.markdown_path = str(markdown_path) if markdown_status == "ready" else None
                document.markdown_map_path = str(markdown_map_path) if markdown_status == "ready" else None
                document.markdown_error = markdown_error
                document.markdown_converter_version = markdown_engine_version
                document.markdown_char_count = len(markdown_text) if markdown_status == "ready" else sum(len(item[0]) for item in analysis_blocks)
                document.markdown_line_count = len(markdown_text.splitlines()) if markdown_status == "ready" else 0
                document.markdown_checksum = markdown_checksum
                document.markdown_mapping_json = markdown_mapping.get("quality", {}) if markdown_status == "ready" else {}
                if ocr_metadata:
                    document.ocr_status = "ready"
                    document.ocr_language = str(ocr_metadata.get("ocr_language") or self.ocr.languages)
                    document.ocr_page_count = int(ocr_metadata.get("ocr_page_count") or 0)
                    document.ocr_confidence = ocr_metadata.get("ocr_confidence")
                    document.ocr_error = None
                    document.ocr_engine_version = markdown_engine_version
                    document.ocr_char_count = int(ocr_metadata.get("ocr_char_count") or 0)
                document.status = "indexing"
                document.chunk_count = 0
                await session.commit()

            derived_blocks = _computed_blocks(parsed)
            for start in range(0, len(analysis_blocks), 48):
                batch = analysis_blocks[start:start + 48]
                vectors = await asyncio.to_thread(embed_passages, [block[0] for block in batch], settings.embedding_cache_dir)
                async with SessionLocal() as session:
                    session.add_all([
                        Chunk(
                            document_id=document_id,
                            ordinal=start + index,
                            text=block[0],
                            locator=block[1],
                            embedding=vector,
                            is_derived=False,
                            content_source=block[2],
                            markdown_line_start=block[3],
                            markdown_line_end=block[4],
                            markdown_char_start=block[5],
                            markdown_char_end=block[6],
                            mapping_confidence=block[7],
                        )
                        for index, (block, vector) in enumerate(zip(batch, vectors, strict=True))
                    ])
                    document = await session.get(Document, document_id)
                    if document:
                        document.chunk_count = min(start + len(batch), len(analysis_blocks))
                    await session.commit()

            async with SessionLocal() as session:
                for index, block in enumerate(derived_blocks, start=len(analysis_blocks)):
                    session.add(Chunk(
                        document_id=document_id,
                        ordinal=index,
                        text=block.text,
                        locator=block.locator,
                        embedding=None,
                        is_derived=True,
                    ))
                document = await session.get(Document, document_id)
                if document:
                    document.chunk_count = len(analysis_blocks) + len(derived_blocks)
                    existing_chat = (await session.execute(select(Chat.id).where(Chat.document_id == document_id))).scalar_one_or_none()
                    if existing_chat is None:
                        session.add(Chat(document_id=document_id))
                await session.commit()

            await self._analyze(document_id)
        except asyncio.CancelledError:
            raise
        except OCRProcessingError as exc:
            await self._set_ocr_state(document_id, "failed", str(exc))
            await self._set_error(document_id, "error", str(exc))
        except DocumentParsingError as exc:
            await self._set_error(document_id, "error", str(exc))
        except CodexNeedsLogin as exc:
            await self._set_error(document_id, "needs_auth", str(exc))
        except CodexModelUnavailable as exc:
            await self._set_error(document_id, "model_unavailable", str(exc))
        except Exception as exc:
            logger.exception("Document processing failed for %s", document_id)
            await self._set_error(document_id, "error", self._processing_error(exc))
        finally:
            self._tasks.pop(document_id, None)

    async def _analyze(self, document_id: uuid.UUID) -> None:
        try:
            await analyze_document(document_id, self.codex)
        except asyncio.CancelledError:
            raise
        except CodexNeedsLogin as exc:
            await self._set_error(document_id, "needs_auth", str(exc))
        except CodexModelUnavailable as exc:
            await self._set_error(document_id, "model_unavailable", str(exc))
        except CodexUnavailable as exc:
            await self._set_error(document_id, "error", str(exc))
        except Exception as exc:
            logger.exception("Insight generation failed for %s", document_id)
            await self._set_error(document_id, "error", self._processing_error(exc))
        finally:
            self._tasks.pop(document_id, None)

    async def _set_error(self, document_id: uuid.UUID, status: str, message: str) -> None:
        async with SessionLocal() as session:
            document = await session.get(Document, document_id)
            if document:
                document.status = status
                document.error_message = message[:1_000]
                await session.commit()

    async def _set_ocr_state(self, document_id: uuid.UUID, state: str, message: str | None) -> None:
        async with SessionLocal() as session:
            document = await session.get(Document, document_id)
            if document:
                document.ocr_status = state
                document.ocr_error = message[:1_000] if message else None
                if state == "processing":
                    document.status = "ocr"
                await session.commit()

    @staticmethod
    def _processing_error(exc: Exception) -> str:
        if isinstance(exc, EmbeddingConfigurationError):
            return str(exc)
        lowered = str(exc).lower()
        if "connect" in lowered or "network" in lowered or "download" in lowered or "https" in lowered:
            return "Не удалось загрузить локальную модель или связаться с Codex. Проверьте интернет и повторите обработку."
        if "out of memory" in lowered or "memory" in lowered:
            return "Не хватило памяти для обработки документа. Закройте другие приложения и повторите попытку."
        return "Не удалось обработать документ. Проверьте файл и повторите попытку."
