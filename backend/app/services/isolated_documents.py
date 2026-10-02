from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from app.config import settings
from app.services.artifact_cache import (
    file_checksum,
    load_json_cache,
    processing_cache_key,
    store_json_cache,
)
from app.services.document_security import storage_path
from app.services.job_queue import LeaseLost
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock

_slots = asyncio.Semaphore(2)
_PROGRESS_PREFIX = b'\x1eDOC_PROGRESS '
PARSER_CACHE_VERSION = "document-parser-m14-v1"
OCR_CACHE_VERSION = "tesseract-ocr-m14-v1"
MAPPING_CACHE_VERSION = "markdown-source-map-m14-v1"
logger = logging.getLogger(__name__)


async def _consume_progress(
    stderr: asyncio.StreamReader,
    progress_callback: Callable[[int, int], Awaitable[None]] | None,
    max_pages: int,
) -> None:
    """Drain child diagnostics and forward bounded page progress while the lease is valid."""
    line = bytearray()
    oversized = False
    report_progress = progress_callback is not None

    async def handle(candidate: bytes) -> None:
        nonlocal report_progress
        if not report_progress or progress_callback is None or not candidate.startswith(_PROGRESS_PREFIX):
            return
        try:
            event = json.loads(candidate[len(_PROGRESS_PREFIX):])
            processed, total = event.get('processed_pages'), event.get('total_pages')
        except (ValueError, TypeError, AttributeError):
            return
        if (isinstance(processed, int) and not isinstance(processed, bool)
                and isinstance(total, int) and not isinstance(total, bool)
                and 0 <= processed <= total <= max_pages):
            try:
                await progress_callback(processed, total)
            except LeaseLost:
                # Cancellation or lease recovery makes further progress writes stale.
                report_progress = False

    while chunk := await stderr.read(4096):
        offset = 0
        while offset < len(chunk):
            end = chunk.find(b'\n', offset)
            segment = chunk[offset:] if end < 0 else chunk[offset:end]
            if not oversized:
                if len(line) + len(segment) <= 512:
                    line.extend(segment)
                else:
                    oversized = True
            if end < 0:
                break
            if not oversized:
                await handle(bytes(line))
            line.clear()
            oversized = False
            offset = end + 1


def _linux_descendants(root_pid: int) -> list[int]:
    """Return descendants without relying on psutil inside the minimal image."""

    if sys.platform == "win32":
        return []
    parent_by_pid: dict[int, int] = {}
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # The process name in /proc/<pid>/stat may contain spaces, so split
            # only after the closing ')' of the comm field. The remainder is
            # indexed from state: ppid is the second field there.
            stat = (entry / "stat").read_text(encoding="ascii")
            _, remainder = stat.rsplit(")", 1)
            fields = remainder.split()
            parent_by_pid[int(entry.name)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    descendants: list[int] = []
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        children = [pid for pid, ppid in parent_by_pid.items() if ppid == parent]
        descendants.extend(children)
        pending.extend(children)
    return descendants


async def _stop_process_tree(process: asyncio.subprocess.Process) -> None:
    """Stop children before their parent so subprocess.run can reap them."""

    descendants = _linux_descendants(process.pid)
    if sys.platform != "win32":
        for pid in reversed(descendants):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if descendants:
            try:
                # Poppler/Tesseract can need a few seconds to unwind their
                # pipes after SIGTERM. Give the worker enough time to reap
                # those children before the leader is force-killed; otherwise
                # they can survive as orphaned zombies under PID 1.
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        if process.returncode is None:
            try:
                os.kill(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # If the leader did not reap a stubborn descendant, kill its process
        # group as a final fence. The normal path above makes the leader reap
        # children before it exits, preventing orphaned zombies.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif process.returncode is None:
        process.kill()
    await process.wait()


async def run_document_operation(operation: str, path: Path, *, timeout: float | None = None,
                                 configuration: dict | None = None,
                                 progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
                                 **parameters):
    path = storage_path(path)
    names = ('embedding_cache_dir', 'upload_dir', 'max_upload_bytes', 'archive_max_bytes', 'archive_member_max_bytes',
             'archive_max_entries', 'document_max_pages', 'document_max_chars', 'markdown_max_chars',
             'ocr_enabled', 'ocr_languages', 'ocr_dpi', 'ocr_max_pages', 'ocr_timeout_seconds', 'ocr_max_chars',
             'ocr_confidence_warning_threshold',
             'document_worker_memory_mb', 'document_worker_cpu_seconds', 'document_worker_max_output_bytes')
    overrides = configuration or {}
    if overrides.keys() - {'ocr_enabled', 'ocr_languages', 'ocr_dpi', 'ocr_max_pages', 'ocr_confidence_warning_threshold'}:
        raise ValueError('Unsupported processing configuration')
    configuration = {name: getattr(settings, name) for name in names}
    configuration.update(overrides)
    configuration['upload_dir'] = str(Path(settings.upload_dir).resolve())
    payload = {'operation': operation, 'path': str(path), 'settings': configuration, **parameters}
    # Do not inherit database credentials, Codex auth or cloud tokens.
    environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR') if key in os.environ}
    environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]),
                       OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
                       PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    async with _slots:
        with tempfile.TemporaryDirectory(prefix='document-worker-') as temporary:
            environment.update(TMPDIR=temporary, TEMP=temporary, TMP=temporary)
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'app.services.document_worker', cwd=temporary, env=environment,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=sys.platform != 'win32',
            )

            async def exchange():
                process.stdin.write(json.dumps(payload).encode('utf-8'))
                await process.stdin.drain()
                process.stdin.close()
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    output.extend(chunk)
                    if len(output) > settings.document_worker_max_output_bytes:
                        raise DocumentParsingError('Результат обработки превышает безопасный размер.')
                await process.wait()
                if process.returncode:
                    raise DocumentParsingError('Обработка остановлена: превышен лимит ресурсов или файл повреждён.')
                try:
                    response = json.loads(output)
                except (ValueError, UnicodeError) as exc:
                    raise DocumentParsingError('Не удалось получить безопасный результат обработки.') from exc
                if 'error' in response:
                    raise DocumentParsingError(response['error'])
                return response['result']
            progress_task = asyncio.create_task(_consume_progress(process.stderr, progress_callback, settings.ocr_max_pages))
            try:
                return await asyncio.wait_for(exchange(), timeout or settings.document_worker_timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise DocumentParsingError('Обработка остановлена: превышено допустимое время.') from exc
            finally:
                cleanup = asyncio.create_task(_stop_process_tree(process))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    if not progress_task.done():
                        progress_task.cancel()
                    await asyncio.gather(progress_task, return_exceptions=True)
                    raise
                if progress_task.done():
                    progress_task.result()
                else:
                    progress_task.cancel()
                    await asyncio.gather(progress_task, return_exceptions=True)


def parsed_result(result: dict) -> ParsedDocument:
    return ParsedDocument(result['file_type'], [SourceBlock(**block) for block in result['blocks']], result['metadata'])


def _cache_key(
    *, input_checksum: str, filename: str, parameters: dict, parser_version: str,
) -> str:
    return processing_cache_key(
        input_checksum=input_checksum,
        file_type=Path(filename).suffix.lower().lstrip("."),
        configuration={
            "parameters": parameters,
            "limits": {
                "max_pages": settings.document_max_pages,
                "max_chars": settings.document_max_chars,
                "archive_max_bytes": settings.archive_max_bytes,
                "archive_member_max_bytes": settings.archive_member_max_bytes,
                "archive_max_entries": settings.archive_max_entries,
                "markdown_max_chars": settings.markdown_max_chars,
            },
        },
        parser_version=parser_version,
        converter_version="not-applicable",
    )


async def parse_uploaded(
    path: Path,
    filename: str,
    *,
    cache_checksum: str | None = None,
    **parameters,
) -> ParsedDocument:
    checksum = cache_checksum or await asyncio.to_thread(file_checksum, path)
    # OCR language/DPI do not change the native parser output; keep that cache
    # reusable when only OCR settings change. Parser limits remain in _cache_key.
    parser_parameters = {key: value for key, value in parameters.items() if key != "configuration"}
    key = _cache_key(
        input_checksum=checksum,
        filename=filename,
        parameters=parser_parameters,
        parser_version=PARSER_CACHE_VERSION,
    )
    cached = load_json_cache(settings.upload_dir, "parsed", key)
    if cached is not None:
        try:
            parsed = parsed_result(cached)
            logger.info("Document artifact cache hit: stage=parse")
            return parsed
        except (KeyError, TypeError, ValueError):
            pass
    parsed = parsed_result(await run_document_operation('parse', path, filename=filename, **parameters))
    store_json_cache(settings.upload_dir, "parsed", key, asdict(parsed))
    return parsed


async def ocr_uploaded(path: Path, *, progress_callback=None, cache_checksum: str | None = None, **parameters):
    from app.services.ocr import OCRProcessingError, OCRResult, OCRService
    checksum = cache_checksum or await asyncio.to_thread(file_checksum, path)
    engine_version = await asyncio.to_thread(OCRService._engine_version)
    key = _cache_key(
        input_checksum=checksum,
        filename="document.pdf",
        parameters={
            **parameters,
            "ocr_cache_version": OCR_CACHE_VERSION,
            "ocr_engine_version": engine_version,
        },
        parser_version=PARSER_CACHE_VERSION,
    )
    cached = load_json_cache(settings.upload_dir, "ocr", key)
    if cached is not None:
        try:
            cached["parsed"] = parsed_result(cached["parsed"])
            result = OCRResult(**cached)
            if progress_callback:
                total = len(result.parsed.metadata.get("ocr_page_map", []))
                if total:
                    await progress_callback(total, total)
            logger.info("Document artifact cache hit: stage=ocr")
            return result
        except (KeyError, TypeError, ValueError):
            pass
    try:
        result = await run_document_operation('ocr', path, progress_callback=progress_callback, **parameters)
    except DocumentParsingError as exc:
        raise OCRProcessingError(str(exc)) from exc
    result['parsed'] = parsed_result(result['parsed'])
    converted = OCRResult(**result)
    store_json_cache(settings.upload_dir, "ocr", key, asdict(converted))
    return converted


async def map_uploaded(path: Path, filename: str, markdown_path: Path, *, cache_checksum: str | None = None):
    from app.services.markdown_mapping import MappedMarkdownBlock
    from app.services.markitdown_service import MarkdownConversionError
    original_checksum = cache_checksum or await asyncio.to_thread(file_checksum, path)
    markdown_checksum = await asyncio.to_thread(file_checksum, markdown_path)
    key = _cache_key(
        input_checksum=original_checksum,
        filename=filename,
        parameters={"markdown_checksum": markdown_checksum},
        parser_version=f"{MAPPING_CACHE_VERSION}:{PARSER_CACHE_VERSION}",
    )
    cached = load_json_cache(settings.upload_dir, "mapping", key)
    if cached is not None:
        try:
            blocks = [MappedMarkdownBlock(**block) for block in cached["blocks"]]
            mapping = cached["mapping"]
            if isinstance(mapping, dict):
                logger.info("Document artifact cache hit: stage=source-map")
                return blocks, mapping
        except (KeyError, TypeError, ValueError):
            pass
    try:
        result = await run_document_operation('map', path, filename=filename, markdown_path=str(markdown_path))
    except DocumentParsingError as exc:
        raise MarkdownConversionError(str(exc)) from exc
    blocks = [MappedMarkdownBlock(**block) for block in result['blocks']]
    store_json_cache(settings.upload_dir, "mapping", key, result)
    return blocks, result['mapping']
