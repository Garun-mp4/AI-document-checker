from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.config import settings
from app.services.document_security import storage_path
from app.services.job_queue import LeaseLost
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock

_slots = asyncio.Semaphore(2)
_PROGRESS_PREFIX = b'\x1eDOC_PROGRESS '


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
             'document_worker_memory_mb', 'document_worker_cpu_seconds', 'document_worker_max_output_bytes')
    overrides = configuration or {}
    if overrides.keys() - {'ocr_enabled', 'ocr_languages', 'ocr_dpi', 'ocr_max_pages'}:
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


async def parse_uploaded(path: Path, filename: str, **parameters) -> ParsedDocument:
    return parsed_result(await run_document_operation('parse', path, filename=filename, **parameters))


async def ocr_uploaded(path: Path, *, progress_callback=None, **parameters):
    from app.services.ocr import OCRProcessingError, OCRResult
    try:
        result = await run_document_operation('ocr', path, progress_callback=progress_callback, **parameters)
    except DocumentParsingError as exc:
        raise OCRProcessingError(str(exc)) from exc
    result['parsed'] = parsed_result(result['parsed'])
    return OCRResult(**result)


async def map_uploaded(path: Path, filename: str, markdown_path: Path):
    from app.services.markdown_mapping import MappedMarkdownBlock
    from app.services.markitdown_service import MarkdownConversionError
    try:
        result = await run_document_operation('map', path, filename=filename, markdown_path=str(markdown_path))
    except DocumentParsingError as exc:
        raise MarkdownConversionError(str(exc)) from exc
    return [MappedMarkdownBlock(**block) for block in result['blocks']], result['mapping']
