"""Stable, versioned source locators shared by every document renderer."""

from __future__ import annotations

from typing import Any

LOCATOR_VERSION = 1

RANGE_FIELDS = (
    "page",
    "line_start",
    "line_end",
    "char_start",
    "char_end",
    "paragraph",
    "table",
    "row",
    "row_start",
    "row_end",
    "sheet",
    "column",
    "slide",
    "shape",
    "chapter",
    "path",
    "part",
)


def _source_type(file_type: str | None, locator: dict[str, Any], is_derived: bool) -> str:
    kind = str(locator.get("kind") or (file_type or "unknown")).casefold()
    if is_derived or locator.get("derived") is True or kind.endswith("_derived"):
        return "calculation"
    if kind == "pdf" and locator.get("ocr") is True:
        return "pdf_ocr"
    if kind == "pdf" or file_type == "pdf":
        return "pdf_text"
    if kind == "docx_table":
        return "docx_table"
    if kind == "docx" or file_type == "docx":
        return "docx_paragraph"
    if kind in {"csv", "xlsx", "xls"} or file_type in {"csv", "xlsx", "xls"}:
        return "table_row"
    if kind == "pptx" or file_type == "pptx":
        return "slide_block"
    if kind == "epub" or file_type == "epub":
        return "chapter_block"
    if kind == "xml" or file_type == "xml":
        return "xml_node"
    if kind == "json" or file_type == "json":
        return "json_value"
    if kind in {"html", "htm"} or file_type in {"html", "htm"}:
        return "html_block"
    if kind in {"txt", "md", "text"} or file_type in {"txt", "md"}:
        return "text_range"
    return "unknown"


def _has_range(locator: dict[str, Any], start: str, end: str) -> bool:
    first, last = locator.get(start), locator.get(end)
    return (
        isinstance(first, int)
        and not isinstance(first, bool)
        and isinstance(last, int)
        and not isinstance(last, bool)
        and first >= 0
        and last >= first
    )


def _default_quality(source_type: str, locator: dict[str, Any]) -> str:
    if source_type == "calculation":
        return "calculation"
    if source_type == "pdf_ocr":
        ocr_map = locator.get("ocr_map")
        if isinstance(ocr_map, dict) and (ocr_map.get("word_boxes") or ocr_map.get("line_boxes")):
            return "exact"
        return "page_only" if isinstance(locator.get("page"), int) else "not_found"
    if source_type == "table_row":
        return "exact" if _has_range(locator, "row_start", "row_end") else "page_only"
    if source_type in {"text_range", "json_value", "xml_node", "html_block"}:
        if _has_range(locator, "char_start", "char_end"):
            return "exact"
        if _has_range(locator, "line_start", "line_end"):
            return "approximate"
        if locator.get("path"):
            return "page_only"
    if source_type == "pdf_text":
        if isinstance(locator.get("page"), int) and _has_range(locator, "char_start", "char_end"):
            return "exact"
        return "approximate" if isinstance(locator.get("page"), int) else "not_found"
    if source_type in {"docx_paragraph", "docx_table", "slide_block", "chapter_block"}:
        if source_type.startswith("docx") and _has_range(locator, "char_start", "char_end"):
            if source_type == "docx_paragraph" and isinstance(locator.get("paragraph"), int):
                return "exact"
            if source_type == "docx_table" and isinstance(locator.get("table"), int) and isinstance(locator.get("row"), int):
                return "exact"
        if any(locator.get(key) is not None for key in ("paragraph", "row", "slide", "chapter", "path")):
            return "approximate"
    if any(locator.get(key) is not None for key in ("page", "paragraph", "row", "slide", "chapter", "path")):
        return "page_only"
    return "not_found"


def _coordinate_space(source_type: str, locator: dict[str, Any]) -> str:
    if source_type == "calculation":
        return "derived-input-range"
    if source_type == "pdf_ocr":
        return "page-normalized-top-left"
    if source_type == "pdf_text":
        return "pdf-page-text"
    if source_type == "docx_paragraph":
        return "docx-paragraph-text"
    if source_type == "docx_table":
        return "docx-table-row-text"
    if source_type == "table_row":
        return "spreadsheet-row-number" if locator.get("sheet") else "csv-row-number"
    if source_type in {"slide_block", "chapter_block"}:
        return "structured-block"
    if source_type in {"text_range", "json_value", "xml_node", "html_block"}:
        return "original-file-text"
    return "unknown"


def versioned_source_locator(
    locator: dict[str, Any] | None,
    *,
    document_id: str,
    processing_version: int,
    file_type: str | None = None,
    is_derived: bool = False,
) -> dict[str, Any]:
    """Add a canonical locator envelope while preserving legacy top-level keys.

    Existing locators remain readable by older clients. The canonical
    ``source_range`` makes new clients independent of format-specific field
    names, and the processing version binds every citation to its source map.
    """

    result = dict(locator or {})
    source_type = _source_type(file_type, result, is_derived)
    source_range = {"coordinate_space": _coordinate_space(source_type, result)}
    source_range.update({
        field: result[field]
        for field in RANGE_FIELDS
        if field in result and isinstance(result[field], (str, int, float)) and not isinstance(result[field], bool)
    })
    result.update(
        {
            "locator_version": LOCATOR_VERSION,
            "document_id": str(document_id),
            "processing_version": processing_version,
            "source_type": source_type,
            "match_quality": (
                result.get("match_quality")
                if result.get("match_quality") in {"exact", "approximate", "page_only", "not_found", "calculation"}
                else _default_quality(source_type, result)
            ),
            "source_range": source_range,
        }
    )
    return result
