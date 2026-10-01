"""Exercise Linux kernel limits in disposable children of the test container."""
import asyncio
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

assert os.environ.get('E2E_AUDIT_PROJECT') == 'document-checker-e2e', 'Use the isolated E2E project only'
assert sys.platform == 'linux'
prefix = 'from app.services.document_worker import restrict_process; restrict_process(256, 1, 4096); '


def run(code):
    return subprocess.run([sys.executable, '-c', prefix + code], timeout=10, capture_output=True, text=True, check=False)


network = run("import socket\ntry: socket.socket()\nexcept PermissionError: print('blocked')\nelse: raise AssertionError('network allowed')")
assert network.returncode == 0 and 'blocked' in network.stdout, 'Network syscall restriction failed'
memory = run("\ntry: bytearray(512 * 1024 * 1024)\nexcept MemoryError: print('blocked')\nelse: raise AssertionError('memory unbounded')")
assert memory.returncode == 0 and 'blocked' in memory.stdout, 'Address-space restriction failed'
cpu = run('\nwhile True: pass')
assert cpu.returncode in {-signal.SIGKILL, -signal.SIGXCPU}, 'CPU limit failed'
files = run("import tempfile\ntry:\n with tempfile.TemporaryFile() as file: file.write(b'x' * 8192); file.flush()\nexcept OSError: print('blocked')\nelse: raise AssertionError('file output unbounded')")
assert files.returncode == 0 and 'blocked' in files.stdout, 'File-size restriction failed'
print('Linux network/CPU/memory/file-output limits: 4 passed')

# Exercise the actual ASGI multipart parser with Transfer-Encoding: chunked.
# No Content-Length is supplied, so metadata-only checks cannot pass this test.
def oversized_multipart():
    yield b'--audit\r\nContent-Disposition: form-data; name="file"; filename="audit.txt"\r\nContent-Type: text/plain\r\n\r\n'
    for _ in range(27):
        yield b'x' * (1024 * 1024)
    yield b'\r\n--audit--\r\n'


with httpx.Client(timeout=30) as client:
    before = client.get('http://localhost:8000/api/v1/documents').json()
    response = client.post('http://localhost:8000/api/v1/documents',
                           headers={'Content-Type': 'multipart/form-data; boundary=audit'},
                           content=oversized_multipart())
    assert response.status_code == 413, 'Chunked upload bypassed API body limit'
    assert response.json()['detail'], 'Missing safe upload error'
    after = client.get('http://localhost:8000/api/v1/documents').json()
    assert after == before, 'Rejected upload was persisted'
    response = client.post('http://web/api/v1/documents', headers={
        'Content-Type': 'multipart/form-data; boundary=audit', 'Content-Length': str(27 * 1024 * 1024),
    }, content=b'x' * (27 * 1024 * 1024))
    assert response.status_code == 413, 'Proxy body limit failed'
print('Actual chunked API upload and nginx body limits: 2 passed')

# Linux permits replacing an open pathname, unlike Windows. Verify the actual
# descriptor protection here rather than relying on the host's locking behavior.
from app.config import settings
from app.services.artifact_response import artifact_response
from app.services.document_security import read_storage, remove_storage
from app.services.isolated_documents import run_document_operation
from app.services.parsing import DocumentParsingError

with tempfile.TemporaryDirectory(prefix='artifact-audit-') as temporary:
    root = Path(temporary) / 'uploads'
    root.mkdir()
    settings.upload_dir = str(root)
    original = root / 'original.txt'
    original.write_bytes(b'verified')
    response = artifact_response(original, None, 'text/plain', 'original.txt')
    original.rename(root / 'renamed.txt')
    original.write_bytes(b'replaced')
    async def collect():
        return b''.join([piece async for piece in response.body_iterator])
    assert asyncio.run(collect()) == b'verified', 'Descriptor did not stay pinned'
    foreign = Path(temporary) / 'foreign.txt'
    foreign.write_bytes(b'private')
    link = root / 'link.txt'
    link.symlink_to(foreign)
    for operation in (lambda: read_storage(link, 100), lambda: remove_storage(link)):
        try:
            operation()
        except DocumentParsingError:
            pass
        else:
            raise AssertionError('Symlink followed')
    assert foreign.read_bytes() == b'private', 'Foreign file changed'
    hard = root / 'hard.txt'
    os.link(foreign, hard)
    try:
        read_storage(hard, 100)
    except DocumentParsingError:
        pass
    else:
        raise AssertionError('Hardlink served')
    settings.markdown_max_chars = 1
    try:
        asyncio.run(run_document_operation('markdown', original))
    except DocumentParsingError as error:
        assert 'Markdown превышает' in str(error), 'Safe converter error was replaced by an internal error'
    else:
        raise AssertionError('Converter text limit failed')
print('Linux descriptor pinning, symlink and hardlink protections: 3 passed')
print('Safe converter error across Linux worker boundary: 1 passed')
