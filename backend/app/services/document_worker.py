"""One disposable process per untrusted document operation; no API/DB imports."""
from __future__ import annotations

import ctypes
import ctypes.util
import dataclasses
import json
import logging
import sys


def restrict_process(memory_mb: int, cpu_seconds: int, output_bytes: int) -> None:
    if sys.platform != 'linux':
        return  # Windows host tests use the parent wall/output limits.
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (output_bytes,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    library = ctypes.util.find_library('seccomp')
    if not library:
        raise RuntimeError('Worker network isolation unavailable')
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError('Worker isolation failed')
    try:
        for name in (b'socket', b'connect'):
            syscall = seccomp.seccomp_syscall_resolve_name(name)
            if syscall < 0 or seccomp.seccomp_rule_add(context, 0x50001, syscall, 0) != 0:
                raise RuntimeError('Worker isolation failed')
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError('Worker isolation failed')
    finally:
        seccomp.seccomp_release(context)


def execute(payload: dict):
    from pathlib import Path

    from app.config import settings
    for name, value in payload['settings'].items():
        setattr(settings, name, value)
    restrict_process(settings.document_worker_memory_mb, settings.document_worker_cpu_seconds,
                     settings.document_worker_max_output_bytes)
    from app.services.document_security import read_storage, validate_content
    from app.services.parsing import parse_document
    path = Path(payload['path'])
    data = read_storage(path, settings.max_upload_bytes)
    filename = payload.get('filename', path.name)
    validate_content(filename, data)
    operation = payload['operation']
    if operation == 'validate':
        return {'valid': True}
    if operation == 'parse':
        parsed = parse_document(filename, data)
        if sum(len(block.text) for block in parsed.blocks) > settings.document_max_chars:
            from app.services.parsing import DocumentParsingError
            raise DocumentParsingError('Текст документа превышает безопасный предел.')
        return dataclasses.asdict(parsed)
    if operation == 'map':
        from app.services.markdown_mapping import map_markdown
        markdown = read_storage(payload['markdown_path'], settings.document_worker_max_output_bytes).decode('utf-8')
        blocks, mapping = map_markdown(markdown, parse_document(filename, data).blocks)
        return {'blocks': [dataclasses.asdict(block) for block in blocks], 'mapping': mapping}
    if operation == 'markdown':
        from app.services.markitdown_service import (
            MarkdownConversionError,
            MarkItDownService,
        )
        from app.services.parsing import DocumentParsingError
        snapshot = Path.cwd() / f'original{path.suffix}'
        snapshot.write_bytes(data)
        try:
            return dataclasses.asdict(MarkItDownService(Path.cwd()).convert_local(snapshot))
        except MarkdownConversionError as exc:
            raise DocumentParsingError(str(exc)) from None
    if operation == 'ocr':
        from app.services.ocr import OCRService
        snapshot = Path.cwd() / 'original.pdf'
        snapshot.write_bytes(data)
        return dataclasses.asdict(OCRService(Path.cwd()).process(snapshot))
    if operation == 'table':
        from app.services.preview import read_csv_table, read_spreadsheet_table
        if payload['file_type'] == 'csv':
            return read_csv_table(data, offset=payload['offset'], limit=payload['limit'])
        return read_spreadsheet_table(data, payload['file_type'], offset=payload['offset'], limit=payload['limit'])
    raise ValueError('Unknown worker operation')


def main() -> None:
    # Never serialize arbitrary exception text or traceback: document contents
    # and third-party messages may contain private values.
    from app.services.parsing import DocumentParsingError
    try:
        payload = json.loads(sys.stdin.buffer.read(65536))
        result = execute(payload)
        response = {'result': result}
    except DocumentParsingError as exc:
        response = {'error': str(exc), 'expected': True}
    except Exception as exc:
        logging.getLogger(__name__).exception('Worker failed (%s)', type(exc).__name__, exc_info=False)
        response = {'error': 'Изолированная обработка документа не удалась.', 'expected': False}
    sys.stdout.write(json.dumps(response, ensure_ascii=False))


if __name__ == '__main__':
    main()
