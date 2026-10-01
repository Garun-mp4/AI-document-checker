"""Validation shared by ingestion, workers and artifact access."""
from __future__ import annotations

import io
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from defusedxml import ElementTree

from app.config import settings
from app.services.parsing import DocumentParsingError, _decode_text

PACKAGE_PARTS = {
    '.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml',
    '.pptx': 'ppt/presentation.xml', '.epub': 'META-INF/container.xml',
}
MIME_TYPES = {
    '.pdf': {'application/pdf'},
    '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    '.xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
    '.xls': {'application/vnd.ms-excel'},
    '.pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
    '.epub': {'application/epub+zip', 'application/epub', 'application/zip', 'application/x-zip-compressed'},
    '.txt': {'text/plain'}, '.md': {'text/plain', 'text/markdown', 'text/x-markdown'},
    '.csv': {'text/plain', 'text/csv', 'application/csv', 'application/vnd.ms-excel'},
    '.xml': {'text/plain', 'application/xml', 'text/xml'},
    '.html': {'text/plain', 'text/html'}, '.htm': {'text/plain', 'text/html'},
    '.json': {'text/plain', 'application/json'},
}


def validate_mime(filename: str, mime: str | None) -> None:
    value = (mime or '').split(';', 1)[0].strip().lower()
    if value in {'', 'application/octet-stream', 'binary/octet-stream'}:
        return
    if value not in MIME_TYPES.get(Path(filename).suffix.lower(), set()):
        raise DocumentParsingError('Тип содержимого не соответствует расширению файла.')


def validate_package(data: bytes, extension: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > settings.archive_max_entries:
                raise DocumentParsingError('В документе слишком много внутренних частей.')
            if sum(item.file_size for item in entries) > settings.archive_max_bytes:
                raise DocumentParsingError('Распакованный документ превышает безопасный размер.')
            names: set[str] = set()
            for item in entries:
                # ZipInfo normalizes backslashes/truncates NUL on some hosts.
                # Validate the raw central-directory name, before normalization.
                name = item.orig_filename
                path = PurePosixPath(name)
                mode = item.external_attr >> 16
                if (not name or '\\' in name or '\x00' in name or ':' in name or path.is_absolute()
                        or '..' in path.parts or name.rstrip('/') != str(path)
                        or name in names or stat.S_ISLNK(mode)):
                    raise DocumentParsingError('Документ содержит небезопасный внутренний путь.')
                names.add(name)
                if item.flag_bits & 1:
                    raise DocumentParsingError('Зашифрованные архивы документов не поддерживаются.')
                if item.file_size > settings.archive_member_max_bytes:
                    raise DocumentParsingError('Внутренний ресурс документа превышает безопасный размер.')
                if item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise DocumentParsingError('Неподдерживаемое сжатие внутренних ресурсов документа.')
                # Read bounded members now: CRC and actual size are checked,
                # not merely trusted from the central directory.
                if item.is_dir():
                    continue
                with archive.open(item) as stream:
                    content = stream.read(settings.archive_member_max_bytes + 1)
                if len(content) != item.file_size or len(content) > settings.archive_member_max_bytes:
                    raise DocumentParsingError('Некорректный размер внутреннего ресурса документа.')
                if name.endswith(('.xml', '.rels', '.opf', '.xhtml')):
                    try:
                        tree = ElementTree.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
                    except Exception as exc:
                        raise DocumentParsingError('Внутренний XML повреждён или содержит запрещённые сущности.') from exc
                    if name.endswith('.rels'):
                        for relation in tree:
                            target = relation.get('Target', '')
                            external = relation.get('TargetMode', '').lower() == 'external'
                            if external:
                                # Ordinary hyperlinks are inert until clicked;
                                # external images, fonts, templates and OLE are forbidden.
                                if not relation.get('Type', '').endswith('/hyperlink') or urlsplit(target).scheme not in {'http', 'https', 'mailto'}:
                                    raise DocumentParsingError('Документ содержит внешний ресурс или небезопасную ссылку.')
                            elif '\\' in target or ':' in target or target.startswith('//'):
                                raise DocumentParsingError('Документ содержит небезопасную ссылку на ресурс.')
                            else:
                                base = str(PurePosixPath(name).parent.parent)
                                destination = posixpath.normpath(target.lstrip('/')) if target.startswith('/') else posixpath.normpath(posixpath.join(base, target))
                                if destination == '..' or destination.startswith('../'):
                                    raise DocumentParsingError('Внутренняя ссылка выходит за пределы документа.')
            if PACKAGE_PARTS[extension] not in names:
                raise DocumentParsingError('Расширение не соответствует структуре документа.')
            if extension == '.epub':
                if 'mimetype' not in names or archive.read('mimetype').strip() != b'application/epub+zip':
                    raise DocumentParsingError('Файл не является EPUB.')
            elif '[Content_Types].xml' not in names:
                raise DocumentParsingError('В документе отсутствует описание внутренних типов.')
    except DocumentParsingError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError) as exc:
        raise DocumentParsingError('Архив документа повреждён или имеет неверную структуру.') from exc


def validate_content(filename: str, data: bytes) -> None:
    extension = Path(filename).suffix.lower()
    if not data:
        raise DocumentParsingError('Файл пустой.')
    if len(data) > settings.max_upload_bytes:
        raise DocumentParsingError('Файл превышает безопасный размер загрузки.')
    if extension in PACKAGE_PARTS:
        validate_package(data, extension)
    elif extension == '.pdf':
        if not data.startswith(b'%PDF-'):
            raise DocumentParsingError('Расширение PDF не соответствует содержимому файла.')
    elif extension == '.xls':
        if not data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
            raise DocumentParsingError('Расширение XLS не соответствует содержимому файла.')
    else:
        if data.startswith((b'MZ', b'PK\x03\x04', b'%PDF-', bytes.fromhex('D0CF11E0A1B11AE1'))):
            raise DocumentParsingError('Текстовое расширение не соответствует двоичному содержимому.')
        text = _decode_text(data)
        if any(ord(char) < 32 and char not in '\r\n\t\ufeff' for char in text):
            raise DocumentParsingError('Текстовый файл содержит двоичные управляющие символы.')
        if len(text) > settings.document_max_chars:
            raise DocumentParsingError('Текст документа превышает безопасный предел.')
        if extension == '.xml':
            try:
                ElementTree.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)
            except Exception as exc:
                raise DocumentParsingError('XML повреждён или содержит запрещённые сущности.') from exc


def storage_path(path: str | Path, root: str | Path | None = None) -> Path:
    """Require a direct regular leaf; never resolve away a symlink."""
    base = Path(root or settings.upload_dir).resolve()
    candidate = Path(os.path.abspath(path))
    if candidate.parent != base or candidate.is_symlink():
        raise DocumentParsingError('Файл находится за пределами безопасного хранилища.')
    return candidate


def owned_storage(path: str | Path, document_id: object) -> Path:
    candidate = storage_path(path)
    suffixes = {*MIME_TYPES, '.markdown.md', '.map.json'}
    if candidate.name not in {f'{document_id}{suffix}' for suffix in suffixes}:
        raise DocumentParsingError('Артефакт не принадлежит этому документу.')
    return candidate


def open_storage(path: str | Path, root: str | Path | None = None):
    candidate = storage_path(path, root)
    fd = os.open(candidate, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DocumentParsingError('Артефакт не является отдельным обычным файлом.')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def read_storage(path: str | Path, max_bytes: int, root: str | Path | None = None) -> bytes:
    with open_storage(path, root) as stream:
        result = stream.read(max_bytes + 1)
    if len(result) > max_bytes:
        raise DocumentParsingError('Артефакт превышает безопасный размер.')
    return result


def write_artifact(path: Path, text: str) -> None:
    target = storage_path(path)
    data = text.encode('utf-8')
    if len(data) > settings.document_worker_max_output_bytes:
        raise DocumentParsingError('Производный артефакт превышает безопасный размер.')
    fd, temporary = tempfile.mkstemp(prefix='.artifact-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        storage_path(target)  # Refuse a symlink introduced while writing.
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def remove_storage(path: str | Path) -> None:
    candidate = storage_path(path)
    # Unlink the leaf itself, never its resolved destination.
    candidate.unlink(missing_ok=True)


def download_name(filename: str, suffix: str | None = None) -> str:
    leaf = filename.replace('\\', '/').split('/')[-1]
    leaf = re.sub(r'[\x00-\x1f\x7f";]', '_', leaf).strip(' .')[:180] or 'document'
    return f'{Path(leaf).stem}{suffix}' if suffix else leaf
