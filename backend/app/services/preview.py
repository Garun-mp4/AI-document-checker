from __future__ import annotations

"""Build source maps and safe previews for uploaded documents.

The preview endpoint deliberately does not turn indexed chunks into the
document itself. The browser receives the original file URL and uses the
chunks only as a source map for citation navigation. Keeping this boundary
explicit prevents model-friendly normalized text (for example ``Name: value``
CSV lines) from being presented as the uploaded document.
"""

import csv
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.services.parsing import DocumentParsingError, _decode_text

MAX_PREVIEW_BLOCKS = 2_000
MAX_TABLE_ROWS = 500


def _renderer_for(file_type: str) -> str:
    return {
        "pdf": "pdf",
        "docx": "docx",
        "txt": "text",
        "md": "text",
        "csv": "csv",
        "xml": "xml",
        "xlsx": "xlsx",
        "xls": "xls",
        "pptx": "pptx",
        "html": "html",
        "htm": "html",
        "json": "json",
        "epub": "epub",
    }.get(file_type, "text")


def _layout_for(file_type: str, metadata: dict[str, Any]) -> tuple[str, float]:
    """Keep the old layout field for clients while exposing renderer separately."""

    if file_type == "pdf":
        width = metadata.get("page_width")
        height = metadata.get("page_height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
            return "pdf", float(width) / float(height)
        return "pdf", 210 / 297
    if file_type == "csv":
        return "table", 2.1
    if file_type in {"xlsx", "xls"}:
        return "table", 2.1
    if file_type == "pptx":
        return "slides", 16 / 9
    if file_type == "xml":
        return "tree", 1.25
    if file_type in {"json", "epub"}:
        return "tree" if file_type == "json" else "paper", 1.25 if file_type == "json" else 210 / 297
    width = metadata.get("page_width")
    height = metadata.get("page_height")
    if file_type == "docx" and isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
        return "paper", float(width) / float(height)
    return "paper", 210 / 297


def _kind_for(file_type: str, locator: dict[str, Any], is_derived: bool) -> str:
    if is_derived or locator.get("kind") in {"csv_derived", "xlsx_derived", "xls_derived"}:
        return "calculation"
    if file_type == "pdf":
        return "page"
    if file_type == "csv":
        return "row"
    if file_type in {"xlsx", "xls"}:
        return "row"
    if file_type == "pptx":
        return "paragraph"
    if file_type == "xml":
        return "node"
    if locator.get("kind") == "docx_table":
        return "table"
    if file_type in {"txt", "md", "html", "htm", "json", "epub"}:
        return "text"
    return "paragraph"


def _csv_rows(text: str, columns: list[str]) -> list[list[str]] | None:
    """Recover legacy normalized CSV blocks for backwards-compatible clients."""

    if not columns:
        return None
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("Заголовки столбцов:"):
            rows.append(columns)
            continue
        cells = {name: "" for name in columns}
        matched = False
        for part in line.split(" | "):
            name, separator, value = part.partition(": ")
            if separator and name in cells:
                cells[name] = value
                matched = True
        if matched:
            rows.append([cells[name] for name in columns])
    return rows or None


def _csv_dialect(text: str) -> tuple[csv.Dialect, str]:
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        return dialect, dialect.delimiter
    except csv.Error:
        delimiter = ";" if text.count(";") > text.count(",") else ","
        dialect = csv.excel
        return dialect, delimiter


def read_csv_table(data: bytes, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read original CSV rows without exposing normalized chunk text.

    ``offset`` is measured in data rows (the header is excluded). Row numbers
    retain their original one-based CSV positions, which makes source locators
    stable even when the table is paginated.
    """

    if offset < 0:
        raise DocumentParsingError("offset не может быть отрицательным.")
    limit = max(1, min(limit, MAX_TABLE_ROWS))
    text = _decode_text(data)
    dialect, delimiter = _csv_dialect(text)
    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), dialect=dialect, delimiter=delimiter))
    except csv.Error as exc:
        raise DocumentParsingError("Не удалось разобрать строки CSV.") from exc
    parsed = [row for row in parsed if any(cell.strip() for cell in row)]
    if not parsed:
        raise DocumentParsingError("CSV пустой — строк не найдено.")
    columns = [cell.strip() or f"Столбец {index + 1}" for index, cell in enumerate(parsed[0])]
    if len(columns) > 500:
        raise DocumentParsingError("В CSV слишком много столбцов (максимум 500).")
    data_rows = parsed[1:]
    total_rows = len(data_rows)
    page = data_rows[offset: offset + limit]
    rows = [
        {"number": offset + index + 2, "cells": row[:len(columns)] + [""] * max(0, len(columns) - len(row))}
        for index, row in enumerate(page)
    ]
    return {
        "columns": columns,
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "total_rows": total_rows,
        "delimiter": delimiter,
    }


def read_csv_table_file(path: Path, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    try:
        return read_csv_table(path.read_bytes(), offset=offset, limit=limit)
    except FileNotFoundError as exc:
        raise DocumentParsingError("Исходный файл документа недоступен.") from exc
    except OSError as exc:
        raise DocumentParsingError("Не удалось прочитать исходный CSV-файл.") from exc


def read_spreadsheet_table(data: bytes, file_type: str, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    if offset < 0:
        raise DocumentParsingError("offset не может быть отрицательным.")
    limit = max(1, min(limit, MAX_TABLE_ROWS))
    try:
        if file_type == "xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = workbook.worksheets[0]
            rows = [["" if value is None else str(value) for value in row] for row in sheet.iter_rows(values_only=True)]
            sheet_name = sheet.title
            workbook.close()
        else:
            import xlrd
            workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
            sheet = workbook.sheet_by_index(0)
            rows = [["" if value is None else str(value) for value in sheet.row_values(index)] for index in range(sheet.nrows)]
            sheet_name = sheet.name
    except ImportError as exc:
        raise DocumentParsingError(f"Для {file_type.upper()} не установлен модуль чтения таблиц.") from exc
    except Exception as exc:
        raise DocumentParsingError(f"{file_type.upper()} повреждён или имеет неверную структуру.") from exc
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise DocumentParsingError("Таблица пустая — строк не найдено.")
    columns = [cell.strip() or f"Столбец {index + 1}" for index, cell in enumerate(rows[0])]
    if len(columns) > 500:
        raise DocumentParsingError("В таблице слишком много столбцов (максимум 500).")
    data_rows = rows[1:]
    page = data_rows[offset:offset + limit]
    return {
        "columns": columns,
        "rows": [
            {"number": offset + index + 2, "cells": row[:len(columns)] + [""] * max(0, len(columns) - len(row))}
            for index, row in enumerate(page)
        ],
        "offset": offset,
        "limit": limit,
        "total_rows": len(data_rows),
        "sheet": sheet_name,
    }


def read_table_file(path: Path, file_type: str, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise DocumentParsingError("Исходный файл документа недоступен.") from exc
    except OSError as exc:
        raise DocumentParsingError("Не удалось прочитать исходный файл таблицы.") from exc
    if file_type == "csv":
        return read_csv_table(data, offset=offset, limit=limit)
    return read_spreadsheet_table(data, file_type, offset=offset, limit=limit)


def build_preview(
    *,
    document_id: str,
    file_type: str,
    metadata: dict[str, Any] | None,
    chunks: Iterable[Any],
    original_url: str | None,
    total_blocks: int | None = None,
) -> dict[str, Any]:
    """Return the original-file preview contract and its citation source map."""

    metadata = metadata or {}
    layout, aspect_ratio = _layout_for(file_type, metadata)
    all_chunks = list(chunks)
    blocks: list[dict[str, Any]] = []
    columns = [str(value) for value in metadata.get("columns", [])]
    for chunk in all_chunks[:MAX_PREVIEW_BLOCKS]:
        locator = dict(getattr(chunk, "locator", None) or {})
        is_derived = bool(getattr(chunk, "is_derived", False))
        block: dict[str, Any] = {
            "id": str(chunk.id),
            "source_id": str(chunk.id),
            "ordinal": int(chunk.ordinal),
            "kind": _kind_for(file_type, locator, is_derived),
            "text": str(locator.get("source_text") or chunk.text),
            "locator": locator,
            "rows": None,
        }
        if file_type in {"csv", "xlsx", "xls"} and not is_derived:
            block["rows"] = _csv_rows(str(chunk.text), columns)
        blocks.append(block)

    page_count = metadata.get("page_count")
    if not isinstance(page_count, int):
        page_count = None
    encoding = metadata.get("encoding")
    if not isinstance(encoding, str):
        encoding = None
    total_count = len(all_chunks) if total_blocks is None else max(0, total_blocks)
    return {
        "document_id": str(document_id),
        "file_type": file_type,
        "renderer": _renderer_for(file_type),
        "layout": layout,
        "aspect_ratio": aspect_ratio,
        "page_count": page_count,
        "original_url": original_url,
        "encoding": encoding,
        "source_count": total_count,
        "blocks": blocks,
        "total_blocks": total_count,
        "truncated": total_count > MAX_PREVIEW_BLOCKS,
    }
