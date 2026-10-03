from __future__ import annotations

"""Build source maps and safe previews for uploaded documents.

The preview endpoint deliberately does not turn indexed chunks into the
document itself. The browser receives the original file URL and uses the
chunks only as a source map for citation navigation. Keeping this boundary
explicit prevents model-friendly normalized text (for example ``Name: value``
CSV lines) from being presented as the uploaded document.
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.services.document_formats import PREVIEW_RENDERERS
from app.services.parsing import DocumentParsingError
from app.services.source_locators import versioned_source_locator

MAX_PREVIEW_BLOCKS = 2_000
MAX_TABLE_ROWS = 500
logger = logging.getLogger(__name__)


def _renderer_for(file_type: str) -> str:
    return PREVIEW_RENDERERS.get(file_type.casefold().lstrip("."), "text")


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


def read_csv_table(data: bytes, *, offset: int = 0, limit: int = 100, **query: Any) -> dict[str, Any]:
    from app.services.table_analysis import query_table

    return query_table(data, 'csv', offset=offset, limit=limit, **query)


def read_spreadsheet_table(
    data: bytes,
    file_type: str,
    *,
    offset: int = 0,
    limit: int = 100,
    sheet: str | None = None,
    **query: Any,
) -> dict[str, Any]:
    from app.services.table_analysis import query_table

    return query_table(data, file_type, offset=offset, limit=limit, sheet=sheet, **query)


def read_table_file(
    path: Path,
    file_type: str,
    *,
    offset: int = 0,
    limit: int = 100,
    sheet: str | None = None,
    **query: Any,
) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise DocumentParsingError("Исходный файл документа недоступен.") from exc
    except OSError as exc:
        raise DocumentParsingError("Не удалось прочитать исходный файл таблицы.") from exc
    if file_type == 'csv':
        return read_csv_table(data, offset=offset, limit=limit, **query)
    return read_spreadsheet_table(data, file_type, offset=offset, limit=limit, sheet=sheet, **query)


def build_preview(
    *,
    document_id: str,
    file_type: str,
    metadata: dict[str, Any] | None,
    chunks: Iterable[Any],
    original_url: str | None,
    total_blocks: int | None = None,
    processing_version: int = 1,
) -> dict[str, Any]:
    """Return the original-file preview contract and its citation source map."""

    metadata = metadata or {}
    layout, aspect_ratio = _layout_for(file_type, metadata)
    all_chunks = list(chunks)
    blocks: list[dict[str, Any]] = []
    columns = [str(value) for value in metadata.get("columns", [])]
    for chunk in all_chunks[:MAX_PREVIEW_BLOCKS]:
        is_derived = bool(getattr(chunk, "is_derived", False))
        raw_locator = dict(getattr(chunk, "locator", None) or {})
        chunk_version = getattr(chunk, "version", raw_locator.get("processing_version", processing_version))
        locator = versioned_source_locator(
            raw_locator,
            document_id=document_id,
            processing_version=chunk_version if isinstance(chunk_version, int) else processing_version,
            file_type=file_type,
            is_derived=is_derived,
        )
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
