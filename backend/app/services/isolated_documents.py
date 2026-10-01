from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.services.document_security import storage_path
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock

_slots = asyncio.Semaphore(2)


async def run_document_operation(operation: str, path: Path, *, timeout: float | None = None, **parameters):
    path = storage_path(path)
    names = ('upload_dir', 'max_upload_bytes', 'archive_max_bytes', 'archive_member_max_bytes',
             'archive_max_entries', 'document_max_pages', 'document_max_chars', 'markdown_max_chars',
             'ocr_enabled', 'ocr_languages', 'ocr_dpi', 'ocr_max_pages', 'ocr_timeout_seconds', 'ocr_max_chars',
             'document_worker_memory_mb', 'document_worker_cpu_seconds', 'document_worker_max_output_bytes')
    configuration = {name: getattr(settings, name) for name in names}
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
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
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
            try:
                return await asyncio.wait_for(exchange(), timeout or settings.document_worker_timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise DocumentParsingError('Обработка остановлена: превышено допустимое время.') from exc
            finally:
                # Kill the group even if its leader exited: Poppler/Tesseract
                # must not outlive a cancelled request or timed-out conversion.
                if sys.platform != 'win32':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.returncode is None:
                    process.kill()
                await process.wait()


def parsed_result(result: dict) -> ParsedDocument:
    return ParsedDocument(result['file_type'], [SourceBlock(**block) for block in result['blocks']], result['metadata'])


async def parse_uploaded(path: Path, filename: str) -> ParsedDocument:
    return parsed_result(await run_document_operation('parse', path, filename=filename))


async def ocr_uploaded(path: Path):
    from app.services.ocr import OCRProcessingError, OCRResult
    try:
        result = await run_document_operation('ocr', path)
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
