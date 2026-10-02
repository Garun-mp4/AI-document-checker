from __future__ import annotations

from app.services.parsing import ParsedDocument, SourceBlock


def _computed_blocks(parsed: ParsedDocument) -> list[SourceBlock]:
    if parsed.file_type not in {"csv", "xlsx", "xls"}:
        return []
    metadata = parsed.metadata
    row_start = int(metadata.get("data_start_row", 2))
    row_end = int(metadata.get("row_end", int(metadata.get("row_count", 0)) + 1))
    blocks = [SourceBlock(
        f"Локальная структура таблицы: {metadata.get('row_count', 0)} строк данных, {metadata.get('column_count', 0)} столбцов.",
        {"kind": f"{parsed.file_type}_derived", "label": "Сводка таблицы", "row_start": row_start, "row_end": row_end, "derived": True},
        derived=True,
    )]
    for item in metadata.get("numeric_columns", []):
        blocks.append(SourceBlock(
            f"Локальный расчёт по столбцу «{item['name']}» и значениям строк {row_start}–{row_end}: "
            f"числовых значений {item['count']}; сумма {item['sum']}; среднее {item['average']}; "
            f"минимум {item['minimum']}; максимум {item['maximum']}.",
            {
                "kind": f"{parsed.file_type}_derived",
                "label": f"Показатели столбца «{item['name']}» · строки {row_start}–{row_end}",
                "column": item["name"],
                "row_start": row_start,
                "row_end": row_end,
                "derived": True,
            },
            derived=True,
        ))
    return blocks


def _carry_forward_unselected_ocr_pages(
    previous_blocks: list[SourceBlock],
    previous_page_map: list[dict[str, object]],
    current_blocks: list[SourceBlock],
    current_page_map: list[dict[str, object]],
    selected_pages: set[int],
) -> tuple[list[SourceBlock], list[dict[str, object]]]:
    """Reuse successful OCR locators on untouched pages in a new processing version."""
    blocks = [block for block in previous_blocks if block.locator.get('page') not in selected_pages]
    blocks.extend(current_blocks)
    pages = {
        item['page']: dict(item)
        for item in previous_page_map
        if isinstance(item.get('page'), int) and item['page'] not in selected_pages
    }
    for item in current_page_map:
        page = item.get('page')
        if isinstance(page, int):
            pages[page] = dict(item)
    return blocks, [pages[page] for page in sorted(pages)]


def _ocr_analysis_blocks(
    parsed: ParsedDocument,
    markdown: str,
) -> tuple[list[tuple[str, dict[str, object], str, int | None, int | None, int | None, int | None, str | None]], dict[str, object]]:
    """Build direct Markdown-to-page anchors for OCR output."""
    result: list[tuple[str, dict[str, object], str, int | None, int | None, int | None, int | None, str | None]] = []
    cursor = 0
    for block in parsed.blocks:
        position = markdown.find(block.text, cursor)
        if position < 0:
            position = markdown.find(block.text)
        if position < 0:
            continue
        end = position + len(block.text)
        line_start = markdown.count("\n", 0, position) + 1
        line_end = markdown.count("\n", 0, end) + 1
        locator = {
            **block.locator,
            "source_text": block.text,
            "source_locators": [block.locator],
            "markdown_line_start": line_start,
            "markdown_line_end": line_end,
            "markdown_char_start": position,
            "markdown_char_end": end,
        }
        source = "ocr" if block.locator.get("ocr") is True else "pdf_native"
        result.append((block.text, locator, source, line_start, line_end, position, end, "exact"))
        cursor = end
    return result, {"quality": {"exact": len(result), "fuzzy": 0, "nearest": 0, "none": max(0, len(parsed.blocks) - len(result))}}


def _merge_pdf_ocr_pages(native: ParsedDocument, ocr: ParsedDocument) -> tuple[ParsedDocument, dict[str, list[int]]]:
    """Choose the better source per PDF page and keep an auditable page map."""
    page_count = int(native.metadata.get("page_count", 0))
    page_map = [dict(item) for item in native.metadata.get("pdf_page_map", [])]
    native_by_page: dict[int, list[SourceBlock]] = {}
    ocr_by_page: dict[int, list[SourceBlock]] = {}
    for block in native.blocks:
        page = block.locator.get("page")
        if isinstance(page, int):
            native_by_page.setdefault(page, []).append(block)
    for block in ocr.blocks:
        page = block.locator.get("page")
        if isinstance(page, int):
            ocr_by_page.setdefault(page, []).append(block)
    ocr_page_results = {item.get("page"): item for item in ocr.metadata.get("ocr_page_map", [])}

    selected: list[SourceBlock] = []
    summary = {"native_pages": [], "ocr_pages": [], "blank_pages": [], "unreadable_pages": [], "native_preserved_pages": []}
    for page_number in range(1, page_count + 1):
        info = page_map[page_number - 1] if page_number <= len(page_map) else {"page": page_number, "classification": "ocr_candidate"}
        native_blocks = native_by_page.get(page_number, [])
        ocr_blocks = ocr_by_page.get(page_number, [])
        raster_result = ocr_page_results.get(page_number, {})
        original_classification = info.get("classification")

        if original_classification == "blank" or raster_result.get("classification") == "blank":
            chosen = native_blocks
            final_classification = "native" if chosen else "blank"
            if not chosen:
                summary["blank_pages"].append(page_number)
        elif ocr_blocks:
            # These pages were classified as sparse or suspicious by native
            # extraction. Prefer OCR when available so partial text layers
            # cannot be duplicated beside the full rendered-page transcript.
            chosen = ocr_blocks
            final_classification = "ocr"
            summary["ocr_pages"].append(page_number)
        elif native_blocks:
            chosen = native_blocks
            final_classification = "native"
            if original_classification == "ocr_candidate":
                summary["native_preserved_pages"].append(page_number)
            if raster_result.get("classification") == "unreadable":
                summary["unreadable_pages"].append(page_number)
        else:
            chosen = []
            final_classification = "unreadable" if raster_result.get("classification") == "unreadable" else "blank"
            if final_classification == "unreadable":
                summary["unreadable_pages"].append(page_number)
            else:
                summary["blank_pages"].append(page_number)

        if final_classification == "native":
            summary["native_pages"].append(page_number)
        info["native_classification"] = original_classification
        info["classification"] = final_classification
        info["ocr_result"] = raster_result.get("classification", "not_run")
        info["ocr_word_count"] = raster_result.get("word_count", 0)
        info["ocr_line_count"] = raster_result.get("line_count", 0)
        info["ocr_confidence"] = raster_result.get("confidence")
        info["ocr_language"] = raster_result.get("language")
        info["ocr_dpi"] = raster_result.get("dpi")
        selected.extend(chosen)

    selected.sort(key=lambda block: (int(block.locator.get("page", 0)), int(block.locator.get("char_start", 0))))
    metadata = {
        **native.metadata,
        "pdf_page_map": page_map,
        "ocr_pages": list(native.metadata.get("ocr_pages", [])),
        "ocr_used_pages": summary["ocr_pages"],
        "ocr_summary": {key: values for key, values in summary.items()},
        "ocr_used": bool(summary["ocr_pages"]),
        "ocr_language": ocr.metadata.get("ocr_language"),
        "ocr_dpi": ocr.metadata.get("ocr_dpi"),
        "ocr_settings": ocr.metadata.get("ocr_settings"),
        "ocr_engine_version": ocr.metadata.get("ocr_engine_version"),
        "ocr_page_map": ocr.metadata.get("ocr_page_map", []),
        "ocr_page_count": len(summary["ocr_pages"]),
        "ocr_char_count": sum(len(block.text) for block in selected if block.locator.get("ocr") is True),
    }
    used_confidences = [
        ocr_page_results[page].get("confidence")
        for page in summary["ocr_pages"]
        if page in ocr_page_results and isinstance(ocr_page_results[page].get("confidence"), (int, float))
    ]
    metadata["ocr_confidence"] = round(sum(used_confidences) / len(used_confidences), 2) if used_confidences else None
    return ParsedDocument("pdf", selected, metadata), summary


def _pdf_markdown(parsed: ParsedDocument) -> str:
    pages: dict[int, list[SourceBlock]] = {}
    for block in parsed.blocks:
        page = block.locator.get("page")
        if isinstance(page, int):
            pages.setdefault(page, []).append(block)
    parts: list[str] = []
    for page_number in range(1, int(parsed.metadata.get("page_count", 0)) + 1):
        page_blocks = pages.get(page_number, [])
        if not page_blocks:
            continue
        text = "\n".join(block.text.strip() for block in page_blocks if block.text.strip())
        if text:
            parts.extend([f"## Страница {page_number}\n", text, "\n\n"])
    return "".join(parts).rstrip() + "\n"
