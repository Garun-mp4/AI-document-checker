from __future__ import annotations

"""Build a citation-aware visual representation of an indexed document.

The browser can render a PDF natively, but it cannot render DOCX/XML/CSV with
stable anchors without an external office viewer.  This service therefore
keeps the original extracted text and its parser locators and presents it as a
small, predictable document model.  Every visible block retains the source
chunk id, so a citation can always navigate to the exact same evidence used by
the model.
"""

from collections.abc import Iterable
from typing import Any

MAX_PREVIEW_BLOCKS = 2_000


def _layout_for(file_type: str, metadata: dict[str, Any]) -> tuple[str, float]:
    if file_type == "pdf":
        width = metadata.get("page_width")
        height = metadata.get("page_height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
            return "pdf", float(width) / float(height)
        return "pdf", 210 / 297
    if file_type == "csv":
        return "table", 4 / 3
    if file_type == "xml":
        return "tree", 0.82
    # DOCX uses its first section's real page size when the parser could read
    # it. TXT and Markdown use an A4-like paper as a comfortable default.
    width = metadata.get("page_width")
    height = metadata.get("page_height")
    if file_type == "docx" and isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
        return "paper", float(width) / float(height)
    return "paper", 210 / 297


def _kind_for(file_type: str, locator: dict[str, Any], is_derived: bool) -> str:
    if is_derived or locator.get("kind") == "csv_derived":
        return "calculation"
    if file_type == "pdf":
        return "page"
    if file_type == "csv":
        return "row"
    if file_type == "xml":
        return "node"
    if locator.get("kind") == "docx_table":
        return "table"
    if file_type in {"txt", "md"}:
        return "text"
    return "paragraph"


def _csv_rows(text: str, columns: list[str]) -> list[list[str]] | None:
    """Recover table cells from the normalized CSV chunk text.

    The parser intentionally stores CSV evidence as ``Header: value`` lines
    so the retriever can search it.  Turning the same representation back into
    rows keeps the preview useful without exposing the original CSV path or
    trusting a model to invent table structure.
    """

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


def build_preview(
    *,
    document_id: str,
    file_type: str,
    metadata: dict[str, Any] | None,
    chunks: Iterable[Any],
    original_url: str | None,
    total_blocks: int | None = None,
) -> dict[str, Any]:
    """Return the API payload for the document preview.

    ``chunks`` can be SQLAlchemy models or small test doubles with the same
    ``id``, ``ordinal``, ``text``, ``locator`` and ``is_derived`` attributes.
    Keeping this function free of database calls makes its format-specific
    behaviour straightforward to test.
    """

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
            "text": str(chunk.text),
            "locator": locator,
            "rows": None,
        }
        if file_type == "csv" and not is_derived:
            block["rows"] = _csv_rows(str(chunk.text), columns)
        blocks.append(block)

    page_count = metadata.get("page_count")
    if not isinstance(page_count, int):
        page_count = None
    total_count = len(all_chunks) if total_blocks is None else max(0, total_blocks)
    return {
        "document_id": str(document_id),
        "file_type": file_type,
        "layout": layout,
        "aspect_ratio": aspect_ratio,
        "page_count": page_count,
        "original_url": original_url,
        "blocks": blocks,
        "total_blocks": total_count,
        "truncated": total_count > MAX_PREVIEW_BLOCKS,
    }
