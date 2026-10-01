from __future__ import annotations

import asyncio
import io
import os
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import api
from app.config import settings
from app.private_errors import PrivateErrorsMiddleware
from app.services.artifact_response import artifact_response
from app.services.document_security import (
    download_name,
    open_storage,
    owned_storage,
    read_storage,
    remove_storage,
    validate_content,
    validate_mime,
    validate_package,
    write_artifact,
)
from app.services.isolated_documents import run_document_operation
from app.services.parsing import DocumentParsingError, parse_document
from app.upload_limits import UploadBodyLimitMiddleware

FIXTURES = Path(__file__).parent / 'fixtures'


def test_artifact_ownership_prevents_other_document_reads_and_deletion(tmp_path, monkeypatch):
    from uuid import uuid4
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    document_id, other_id = uuid4(), uuid4()
    own = tmp_path / f'{document_id}.txt'
    other = tmp_path / f'{other_id}.txt'
    own.write_bytes(b'own')
    other.write_bytes(b'private')
    assert owned_storage(own, document_id) == own
    assert owned_storage(tmp_path / f'{document_id}.markdown.md', document_id).parent == tmp_path
    with pytest.raises(DocumentParsingError, match='принадлежит'):
        remove_storage(owned_storage(other, document_id))
    assert other.read_bytes() == b'private'


@pytest.mark.parametrize('mime', ['application/epub', 'application/epub+zip', 'application/x-zip-compressed'])
def test_browser_epub_mime_aliases_still_require_real_package(mime):
    validate_mime('book.epub', mime)
    with pytest.raises(DocumentParsingError):
        validate_content('book.epub', b'PK-not-an-epub')


def test_page_and_text_limits(monkeypatch):
    monkeypatch.setattr(settings, 'document_max_pages', 0)
    with pytest.raises(DocumentParsingError):
        parse_document('sample.pdf', (FIXTURES / 'sample.pdf').read_bytes())
    with pytest.raises(DocumentParsingError, match='слайдов'):
        parse_document('sample.pptx', (FIXTURES / 'sample.pptx').read_bytes())
    monkeypatch.setattr(settings, 'document_max_chars', 3)
    with pytest.raises(DocumentParsingError, match='предел'):
        validate_content('sample.txt', b'four')


def test_background_exception_is_observed_without_document_text(caplog):
    from types import SimpleNamespace

    from app.worker import QueueWorker
    async def scenario():
        async def broken(*args): raise RuntimeError('PRIVATE_DOCUMENT secret-token')
        from app.services import processing_engine
        original = processing_engine.ProcessingAttempt.run
        processing_engine.ProcessingAttempt.run = broken
        try:
            worker = QueueWorker(SimpleNamespace())
            await worker.execute(SimpleNamespace(id=uuid4(), document_id=uuid4(), version=1, owner=uuid4(), parameters={}))
        finally:
            processing_engine.ProcessingAttempt.run = original
    asyncio.run(scenario())
    assert 'RuntimeError' in caplog.text
    assert 'PRIVATE_DOCUMENT' not in caplog.text
    assert 'secret-token' not in caplog.text


def package(extension='.docx', extra=(), replacements=None):
    contents = {}
    with zipfile.ZipFile(FIXTURES / f'sample{extension}') as archive:
        contents = {item.filename: archive.read(item) for item in archive.infolist()}
    contents.update(replacements or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        for name, data in extra:
            if isinstance(name, str) and '\\' in name:
                entry = zipfile.ZipInfo(name)
                entry.filename = name  # Preserve an adversarial raw ZIP path on Windows.
                name = entry
            archive.writestr(name, data)
    return stream.getvalue()


@pytest.mark.parametrize('extension', ['.docx', '.xlsx', '.pptx', '.epub'])
@pytest.mark.parametrize('name', ['../outside', '/outside', 'C:/outside', 'dir\\outside'])
def test_archive_traversal_is_rejected_in_every_container(extension, name):
    with pytest.raises(DocumentParsingError, match='путь'):
        validate_package(package(extension, [(name, b'unsafe')]), extension)


@pytest.mark.parametrize('extension', ['.docx', '.xlsx', '.pptx', '.epub'])
def test_archive_member_total_and_entry_limits(extension, monkeypatch):
    valid = package(extension)
    validate_package(valid, extension)
    with zipfile.ZipFile(io.BytesIO(valid)) as archive:
        total = sum(item.file_size for item in archive.infolist())
        largest = max(item.file_size for item in archive.infolist())
        entries = len(archive.infolist())
    for setting, ceiling in [('archive_max_bytes', total - 1), ('archive_member_max_bytes', largest - 1),
                             ('archive_max_entries', entries - 1)]:
        with monkeypatch.context() as patch:
            patch.setattr(settings, setting, ceiling)
            with pytest.raises(DocumentParsingError):
                validate_package(valid, extension)


def test_archive_symlink_duplicate_crc_and_encryption_are_rejected():
    link = zipfile.ZipInfo('word/link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(DocumentParsingError, match='путь'):
        validate_package(package(extra=[(link, b'/etc/passwd')]), '.docx')
    with pytest.warns(UserWarning), pytest.raises(DocumentParsingError, match='путь'):
        validate_package(package(extra=[('word/document.xml', b'duplicate')]), '.docx')
    data = bytearray(package())
    central = data.index(b'PK\x01\x02')
    data[central + 8] |= 1
    with pytest.raises(DocumentParsingError, match='Зашифрованные'):
        validate_package(bytes(data), '.docx')
    data = bytearray(package())
    data[central + 16:central + 20] = b'\x00\x00\x00\x00'
    with pytest.raises(DocumentParsingError, match='повреждён'):
        validate_package(bytes(data), '.docx')


@pytest.mark.parametrize('target,kind', [('https://example.invalid/image', 'image'), ('file:///etc/passwd', 'hyperlink'),
                                      ('javascript:alert(1)', 'hyperlink'), ('../../../outside', 'image')])
def test_external_resources_and_unsafe_relationships_are_rejected(target, kind):
    external = ' TargetMode="External"' if not target.startswith('..') else ''
    rels = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="evil" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{target}"{external}/></Relationships>'
    with pytest.raises(DocumentParsingError):
        validate_package(package(replacements={'word/_rels/document.xml.rels': rels}), '.docx')


def test_safe_hyperlink_is_allowed_but_internal_dtd_is_not():
    rels = '<Relationships><Relationship Type="x/hyperlink" Target="https://example.org" TargetMode="External"/></Relationships>'
    validate_package(package(replacements={'word/_rels/document.xml.rels': rels}), '.docx')
    with pytest.raises(DocumentParsingError, match='XML'):
        validate_package(package(replacements={'word/document.xml': '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'}), '.docx')


@pytest.mark.parametrize('filename,mime', [('file.pdf','text/html'), ('file.docx','application/pdf'), ('file.txt','image/png')])
def test_mime_mismatch_is_rejected(filename, mime):
    with pytest.raises(DocumentParsingError):
        validate_mime(filename, mime)


@pytest.mark.parametrize('mime', [None, '', 'application/octet-stream', 'binary/octet-stream'])
def test_unknown_browser_mime_is_allowed(mime):
    validate_mime('document.pdf', mime)


@pytest.mark.parametrize('filename,data', [('fake.txt', b'MZbinary'), ('fake.xls', b'not-xls'),
                                         ('fake.pdf', b'not-pdf'), ('fake.txt', b'abc\x01def')])
def test_disguised_binary_and_signature_mismatch_are_rejected(filename, data):
    with pytest.raises(DocumentParsingError):
        validate_content(filename, data)


def test_html_does_not_index_script_style_or_iframe_contents():
    parsed = parse_document('safe.html', b'<h1>Visible</h1><script>secret_script</script><style>secret_style</style><iframe>secret_frame</iframe><p>Body</p>')
    assert 'secret_' not in ' '.join(block.text for block in parsed.blocks)
    assert 'Body' in parsed.blocks[-1].text


def test_paths_symlinks_and_hardlinks_are_never_followed_or_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path / 'uploads'))
    root = Path(settings.upload_dir)
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'private')
    link = root / 'link.txt'
    link.symlink_to(outside)
    for path in [outside, link]:
        for operation in [lambda path=path: read_storage(path, 100), lambda path=path: remove_storage(path), lambda path=path: write_artifact(path, 'overwrite')]:
            with pytest.raises(DocumentParsingError):
                operation()
    hardlink = root / 'hard.txt'
    os.link(outside, hardlink)
    with pytest.raises(DocumentParsingError):
        open_storage(hardlink)
    assert outside.read_bytes() == b'private'


def test_atomic_artifact_write_and_bounded_read(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    path = tmp_path / 'artifact.md'
    write_artifact(path, 'Содержимое')
    assert read_storage(path, 100).decode() == 'Содержимое'
    assert not list(tmp_path.glob('.artifact-*'))
    with pytest.raises(DocumentParsingError):
        read_storage(path, 1)


def test_download_headers_ranges_and_pinned_descriptor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    path = tmp_path / 'source.txt'
    path.write_bytes(b'0123456789')
    request = Request({'type':'http', 'headers':[(b'range',b'bytes=2-5')]})
    response = artifact_response(path, request, 'text/plain', 'bad\r\n"name.html')
    assert response.status_code == 206
    assert response.headers['content-length'] == '4'
    assert [value for key, value in response.raw_headers if key == b'content-length'] == [b'4']
    assert response.headers['content-range'] == 'bytes 2-5/10'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert "default-src 'none'" in response.headers['content-security-policy']
    assert '\r' not in response.headers['content-disposition']
    try:
        path.rename(tmp_path / 'old.txt')
    except PermissionError:
        assert sys.platform == 'win32'  # Windows locks an open descriptor's pathname.
    else:
        path.write_bytes(b'FOREIGN DATA')
    async def collect():
        return b''.join([chunk async for chunk in response.body_iterator])
    assert asyncio.run(collect()) == b'2345'
    with pytest.raises(HTTPException) as error:
        artifact_response(path, Request({'type':'http','headers':[(b'range', b'bytes=999-')]}), 'text/plain', 'safe.txt')
    assert error.value.status_code == 416
    assert download_name('../bad\r\nname.docx', '.md') == 'bad__name.md'


def test_worker_preserves_safe_converter_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'markdown_max_chars', 1)
    path = tmp_path / 'source.txt'
    path.write_bytes(b'valid text')
    with pytest.raises(DocumentParsingError, match='Markdown превышает'):
        asyncio.run(run_document_operation('markdown', path))


def test_worker_real_parse_and_output_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    path = tmp_path / 'source.txt'
    path.write_bytes('Автор: Алексей'.encode())
    result = asyncio.run(run_document_operation('parse', path, filename='source.txt'))
    assert 'Алексей' in result['blocks'][0]['text']
    monkeypatch.setattr(settings, 'document_worker_max_output_bytes', 1024)
    path.write_bytes(b'word ' * 500)
    with pytest.raises(DocumentParsingError, match='размер'):
        asyncio.run(run_document_operation('parse', path, filename='source.txt'))


@pytest.mark.parametrize('cancel', [False, True])
def test_worker_timeout_or_cancellation_kills_and_cleans_temp(tmp_path, monkeypatch, cancel):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    path = tmp_path / 'source.txt'
    path.write_bytes(b'valid')
    real_spawn = asyncio.create_subprocess_exec
    spawned = []
    directories = []
    async def scenario():
        started = asyncio.Event()
        async def capture(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            spawned.append(process)
            directories.append(kwargs['cwd'])
            started.set()
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
        task = asyncio.create_task(run_document_operation('validate', path, timeout=0.001))
        if cancel:
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(DocumentParsingError, match='время'):
                await task
    asyncio.run(scenario())
    assert spawned and all(process.returncode is not None for process in spawned)
    assert all(not Path(directory).exists() for directory in directories)
    assert path.read_bytes() == b'valid'


def test_api_size_guard_and_cancel_remove_only_partial_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'max_upload_bytes', 3)
    class Upload:
        filename = 'new.txt'
        content_type = 'text/plain'
        closed = False
        def __init__(self, cancel=False): self.cancel = cancel
        async def read(self, _size):
            if self.cancel: raise asyncio.CancelledError
            return b'oversized'
        async def close(self): self.closed = True
    keep = tmp_path / 'keep.txt'
    keep.write_bytes(b'keep')
    for cancel in [False, True]:
        file = Upload(cancel)
        with pytest.raises(asyncio.CancelledError if cancel else HTTPException):
            asyncio.run(api.upload_document(SimpleNamespace(), file))
        assert file.closed
        assert list(tmp_path.iterdir()) == [keep]
    assert keep.read_bytes() == b'keep'


def test_streamed_http_limit_without_content_length_and_private_errors(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, 'max_upload_bytes', 1)
    async def scenario():
        scope = {'type':'http','method':'POST','path':'/api/v1/documents','headers':[]}
        async def receive(): return {'type':'http.request','body':b'x' * (1024 * 1024 + 2)}
        sent = []
        async def send(message): sent.append(message)
        async def multipart_app(scope, receive, send):
            from starlette.formparsers import MultiPartException
            try: await receive()
            except MultiPartException: pass
            await send({'type':'http.response.start','status':400})
        await UploadBodyLimitMiddleware(multipart_app)(scope, receive, send)
        assert sent[0]['status'] == 413
        async def broken(*_args): raise RuntimeError('PRIVATE_DOCUMENT and secret-token')
        sent.clear()
        await PrivateErrorsMiddleware(broken)(scope, receive, send)
        assert sent[0]['status'] == 500
        assert b'PRIVATE_DOCUMENT' not in sent[1]['body']
    asyncio.run(scenario())
    assert 'PRIVATE_DOCUMENT' not in caplog.text
    assert 'secret-token' not in caplog.text


@pytest.mark.parametrize('cancel_commit', [False, True])
def test_upload_commit_failure_or_cancellation_preserves_consistency(tmp_path, monkeypatch, caplog, cancel_commit):
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path))
    enqueued = []
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(processor=SimpleNamespace())))
    class Upload:
        filename = 'new.txt'
        content_type = 'text/plain'
        closed = False
        read_count = 0
        async def read(self, _size):
            self.read_count += 1
            return b'original' if self.read_count == 1 else b''
        async def close(self): self.closed = True
    file = Upload()
    keep = tmp_path / 'keep.txt'
    keep.write_bytes(b'foreign')
    async def validate(*_args, **_kwargs): return {'valid': True}
    monkeypatch.setattr(api, 'run_document_operation', validate)
    async def enqueue(session, document):
        session.pending_job = document.id
    monkeypatch.setattr(api, 'enqueue', enqueue)
    async def scenario():
        committing, finish = asyncio.Event(), asyncio.Event()
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): pass
            def add(self, _document): pass
            async def commit(self):
                committing.set()
                if cancel_commit:
                    await finish.wait()
                    enqueued.append(self.pending_job)
                else:
                    raise RuntimeError('PRIVATE_DOCUMENT secret-token')
        monkeypatch.setattr(api, 'SessionLocal', Session)
        task = asyncio.create_task(api.upload_document(request, file))
        if cancel_commit:
            await committing.wait()
            task.cancel()
            finish.set()
            with pytest.raises(asyncio.CancelledError): await task
        else:
            with pytest.raises(HTTPException) as error: await task
            assert error.value.status_code == 500
            assert 'PRIVATE_DOCUMENT' not in str(error.value.detail)
    asyncio.run(scenario())
    assert file.closed
    assert keep.read_bytes() == b'foreign'
    uploads = [path for path in tmp_path.iterdir() if path != keep]
    if cancel_commit:
        assert len(enqueued) == len(uploads) == 1
        assert uploads[0].name == f'{enqueued[0]}.txt'
        assert uploads[0].read_bytes() == b'original'
    else:
        assert not uploads and not enqueued
    assert 'PRIVATE_DOCUMENT' not in caplog.text
    assert 'secret-token' not in caplog.text
