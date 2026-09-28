from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeElementTree
from docx import Document as DocxDocument
from docx.document import Document as DocxDocumentType
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xml"}
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_ENTRIES = 5_000
CHUNK_TARGET_CHARS = 1_100
CSV_GROUP_CHARS = 900


class DocumentParsingError(ValueError):
    """An expected document validation or extraction failure."""


@dataclass(frozen=True)
class SourceBlock:
    text: str
    locator: dict[str, Any]
    derived: bool = False


@dataclass
class ParsedDocument:
    file_type: str
    blocks: list[SourceBlock] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def safe_filename(filename: str) -> str:
    leaf = filename.replace("\\", "/").split("/")[-1].strip().replace("\x00", "")
    leaf = re.sub(r"[\r\n\t]", " ", leaf).strip(" .")
    if not leaf:
        raise DocumentParsingError("Не удалось определить имя файла.")
    return leaf[:255]


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise DocumentParsingError("Файл содержит неподдерживаемую двоичную кодировку.")
    for encoding in ("utf-8-sig", "utf-16", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParsingError("Не удалось определить кодировку текста.")


def _split_long_text(text: str, locator: dict[str, Any], target: int = CHUNK_TARGET_CHARS) -> list[SourceBlock]:
    normalized = text.strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    if not paragraphs:
        paragraphs = [normalized]
    result: list[SourceBlock] = []
    buffer = ""
    part_index = 1
    for paragraph in paragraphs:
        if len(paragraph) > target:
            if buffer:
                result.append(SourceBlock(buffer, {**locator, "part": part_index}))
                buffer = ""
                part_index += 1
            start = 0
            while start < len(paragraph):
                end = min(start + target, len(paragraph))
                if end < len(paragraph):
                    boundary = paragraph.rfind(" ", start + target // 2, end)
                    if boundary > start:
                        end = boundary
                part = paragraph[start:end].strip()
                if part:
                    result.append(SourceBlock(part, {**locator, "part": part_index}))
                    part_index += 1
                start = max(end - 120, end)
            continue
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) > target and buffer:
            result.append(SourceBlock(buffer, {**locator, "part": part_index}))
            part_index += 1
            buffer = paragraph
        else:
            buffer = candidate
    if buffer:
        result.append(SourceBlock(buffer, {**locator, "part": part_index}))
    return result


def _parse_pdf(data: bytes) -> ParsedDocument:
    if not data.startswith(b"%PDF-"):
        raise DocumentParsingError("Расширение PDF не соответствует содержимому файла.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentParsingError("PDF защищён паролем. Зашифрованные документы пока не поддерживаются.")
        pages = list(reader.pages)
        blocks: list[SourceBlock] = []
        extracted_chars = 0
        for page_number, page in enumerate(pages, start=1):
            page_text = "" if "/Contents" not in page else page.extract_text(extraction_mode="layout") or page.extract_text() or ""
            extracted_chars += len(page_text.strip())
            blocks.extend(_split_long_text(page_text, {"kind": "pdf", "page": page_number, "label": f"Страница {page_number}"}))
    except DocumentParsingError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError) as exc:
        raise DocumentParsingError("PDF повреждён или имеет неподдерживаемую структуру.") from exc
    if extracted_chars < 30 or not blocks:
        raise DocumentParsingError("В PDF не найден извлекаемый текст. Возможно, это скан; OCR пока не поддерживается.")
    page_width = page_height = None
    if pages:
        try:
            page_width = float(pages[0].mediabox.width)
            page_height = float(pages[0].mediabox.height)
        except (TypeError, ValueError):
            page_width = page_height = None
    metadata = {"page_count": len(pages)}
    if page_width and page_height:
        metadata.update({"page_width": page_width, "page_height": page_height})
    return ParsedDocument("pdf", blocks, metadata)


def _iter_docx_blocks(document: DocxDocumentType) -> Iterable[DocxParagraph | DocxTable]:
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield DocxParagraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield DocxTable(child, document)


def _validate_docx(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                raise DocumentParsingError("В DOCX слишком много внутренних частей.")
            if sum(item.file_size for item in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise DocumentParsingError("Внутренний размер DOCX превышает безопасный предел.")
            if any(item.flag_bits & 0x1 for item in entries):
                raise DocumentParsingError("Зашифрованные DOCX пока не поддерживаются.")
            names = {item.filename for item in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise DocumentParsingError("Файл не является документом DOCX.")
    except DocumentParsingError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocumentParsingError("DOCX повреждён или имеет неверную структуру.") from exc


def _parse_docx(data: bytes) -> ParsedDocument:
    _validate_docx(data)
    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise DocumentParsingError("Не удалось прочитать структуру DOCX.") from exc

    blocks: list[SourceBlock] = []
    paragraph_number = 0
    table_number = 0
    for item in _iter_docx_blocks(document):
        if isinstance(item, DocxParagraph):
            paragraph_number += 1
            text = item.text.strip()
            if text:
                blocks.extend(_split_long_text(text, {
                    "kind": "docx",
                    "label": f"Абзац {paragraph_number}",
                    "paragraph": paragraph_number,
                    "heading": item.style.name if item.style and item.style.name.startswith("Heading") else None,
                }))
            continue
        table_number += 1
        for row_number, row in enumerate(item.rows, start=1):
            values = [cell.text.strip().replace("\n", " / ") for cell in row.cells]
            row_text = " | ".join(value for value in values if value)
            if row_text:
                blocks.extend(_split_long_text(row_text, {
                    "kind": "docx_table",
                    "label": f"Таблица {table_number}, строка {row_number}",
                    "table": table_number,
                    "row": row_number,
                }))
    if not blocks:
        raise DocumentParsingError("В DOCX не найден текст или заполненные строки таблиц.")
    metadata: dict[str, Any] = {"paragraph_count": paragraph_number, "table_count": table_number}
    if document.sections:
        section = document.sections[0]
        if section.page_width and section.page_height:
            metadata.update({
                "page_width": round(section.page_width.inches * 25.4, 2),
                "page_height": round(section.page_height.inches * 25.4, 2),
            })
    return ParsedDocument("docx", blocks, metadata)


def _parse_plain_text(data: bytes, file_type: str) -> ParsedDocument:
    text = _decode_text(data)
    lines = text.splitlines()
    blocks: list[SourceBlock] = []
    start_line = 1
    while start_line <= len(lines):
        end_line = min(start_line + 34, len(lines))
        section = "\n".join(lines[start_line - 1:end_line]).strip()
        if section:
            blocks.extend(_split_long_text(section, {
                "kind": file_type,
                "label": f"Строки {start_line}–{end_line}",
                "line_start": start_line,
                "line_end": end_line,
            }))
        start_line = end_line + 1
    if not blocks:
        raise DocumentParsingError("Файл пустой — извлекать нечего.")
    return ParsedDocument(file_type, blocks, {"line_count": len(lines)})


def _number(value: str) -> Decimal | None:
    candidate = value.strip().replace("\u00a0", "").replace(" ", "")
    if not candidate:
        return None
    if "," in candidate and "." not in candidate:
        candidate = candidate.replace(",", ".")
    elif "," in candidate and "." in candidate:
        if candidate.rfind(",") > candidate.rfind("."):
            candidate = candidate.replace(".", "").replace(",", ".")
        else:
            candidate = candidate.replace(",", "")
    try:
        result = Decimal(candidate)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _parse_csv(data: bytes) -> ParsedDocument:
    text = _decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        dialect = csv.excel
        delimiter = ";" if text.count(";") > text.count(",") else ","
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), dialect=dialect, delimiter=delimiter))
    except csv.Error as exc:
        raise DocumentParsingError("Не удалось разобрать строки CSV.") from exc
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise DocumentParsingError("CSV пустой — строк не найдено.")

    headers = [cell.strip() or f"Столбец {index + 1}" for index, cell in enumerate(rows[0])]
    if len(headers) > 500:
        raise DocumentParsingError("В CSV слишком много столбцов (максимум 500).")
    data_rows = rows[1:]
    blocks: list[SourceBlock] = []
    header_text = "Заголовки столбцов: " + " | ".join(headers)
    blocks.append(SourceBlock(header_text, {
        "kind": "csv",
        "label": "Заголовки столбцов",
        "row_start": 1,
        "row_end": 1,
    }))

    group: list[str] = []
    group_start = 2
    group_chars = 0
    for row_number, row in enumerate(data_rows, start=2):
        normalized = row[:len(headers)] + [""] * max(0, len(headers) - len(row))
        line = " | ".join(f"{headers[i]}: {normalized[i].strip()}" for i in range(len(headers)) if normalized[i].strip())
        if not line:
            continue
        if group and group_chars + len(line) > CSV_GROUP_CHARS:
            blocks.append(SourceBlock("\n".join(group), {
                "kind": "csv",
                "label": f"Строки {group_start}–{row_number - 1}",
                "row_start": group_start,
                "row_end": row_number - 1,
            }))
            group = []
            group_chars = 0
            group_start = row_number
        if len(line) > CSV_GROUP_CHARS:
            if group:
                blocks.append(SourceBlock("\n".join(group), {
                    "kind": "csv",
                    "label": f"Строки {group_start}–{row_number - 1}",
                    "row_start": group_start,
                    "row_end": row_number - 1,
                }))
                group = []
                group_chars = 0
            blocks.extend(_split_long_text(line, {
                "kind": "csv",
                "label": f"Строка {row_number}",
                "row_start": row_number,
                "row_end": row_number,
            }, target=CSV_GROUP_CHARS))
            group_start = row_number + 1
            continue
        group.append(line)
        group_chars += len(line) + 1
    if group:
        blocks.append(SourceBlock("\n".join(group), {
            "kind": "csv",
            "label": f"Строки {group_start}–{len(data_rows) + 1}",
            "row_start": group_start,
            "row_end": len(data_rows) + 1,
        }))

    numeric_columns: list[dict[str, Any]] = []
    for column_index, header in enumerate(headers):
        values = [_number(row[column_index]) for row in data_rows if column_index < len(row)]
        numbers = [value for value in values if value is not None]
        nonempty_count = sum(1 for row in data_rows if column_index < len(row) and row[column_index].strip())
        if numbers and len(numbers) >= max(2, int(nonempty_count * 0.75)):
            total = sum(numbers, Decimal(0))
            numeric_columns.append({
                "name": header,
                "count": len(numbers),
                "sum": str(total),
                "average": str(total / Decimal(len(numbers))),
                "minimum": str(min(numbers)),
                "maximum": str(max(numbers)),
            })

    metadata = {
        "row_count": len(data_rows),
        "column_count": len(headers),
        "columns": headers,
        "delimiter": delimiter,
        "numeric_columns": numeric_columns,
    }
    return ParsedDocument("csv", blocks, metadata)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(data: bytes) -> ParsedDocument:
    try:
        root = SafeElementTree.fromstring(data)
    except Exception as exc:
        raise DocumentParsingError("XML повреждён или содержит запрещённую DTD/entity-конструкцию.") from exc

    blocks: list[SourceBlock] = []
    stack: list[tuple[Element, str]] = [(root, f"/{_local_name(root.tag)}[1]")]
    node_count = 0
    while stack:
        element, path = stack.pop()
        node_count += 1
        if node_count > 200_000:
            raise DocumentParsingError("В XML слишком много узлов (максимум 200 000).")
        children = list(element)
        attributes = "; ".join(f"{_local_name(name)}={value}" for name, value in element.attrib.items())
        text = (element.text or "").strip()
        if text or (attributes and not children):
            value = text or attributes
            if attributes and text:
                value = f"{text} ({attributes})"
            blocks.extend(_split_long_text(value, {
                "kind": "xml",
                "label": path,
                "path": path,
            }))
        if element.tail and element.tail.strip():
            blocks.extend(_split_long_text(element.tail.strip(), {
                "kind": "xml",
                "label": path,
                "path": path,
            }))
        counts: dict[str, int] = {}
        indexed_children: list[tuple[Element, str]] = []
        for child in children:
            name = _local_name(child.tag)
            counts[name] = counts.get(name, 0) + 1
            indexed_children.append((child, f"{path}/{name}[{counts[name]}]"))
        stack.extend(reversed(indexed_children))
    if not blocks:
        raise DocumentParsingError("В XML не найдено текстовых значений или атрибутов.")
    return ParsedDocument("xml", blocks, {"root": _local_name(root.tag), "element_count": node_count})


def parse_document(filename: str, data: bytes) -> ParsedDocument:
    filename = safe_filename(filename)
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentParsingError(f"Формат {extension or 'без расширения'} не поддерживается. Допустимы: {supported}.")
    if not data:
        raise DocumentParsingError("Файл пустой.")
    if extension == ".pdf":
        return _parse_pdf(data)
    if extension == ".docx":
        return _parse_docx(data)
    if extension == ".txt":
        return _parse_plain_text(data, "txt")
    if extension == ".md":
        return _parse_plain_text(data, "md")
    if extension == ".csv":
        return _parse_csv(data)
    return _parse_xml(data)
