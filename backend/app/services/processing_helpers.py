from __future__ import annotations

from app.services.parsing import ParsedDocument, SourceBlock


def _computed_blocks(parsed: ParsedDocument) -> list[SourceBlock]:
    if parsed.file_type not in {"csv", "xlsx", "xls"}:
        return []
    metadata = parsed.metadata
    row_end = int(metadata.get("row_count", 0)) + 1
    blocks = [SourceBlock(
        f"Локальная структура таблицы: {metadata.get('row_count', 0)} строк данных, {metadata.get('column_count', 0)} столбцов.",
        {"kind": f"{parsed.file_type}_derived", "label": "Сводка таблицы", "row_start": 1, "row_end": row_end, "derived": True},
        derived=True,
    )]
    for item in metadata.get("numeric_columns", []):
        blocks.append(SourceBlock(
            f"Локальный расчёт по столбцу «{item['name']}» и значениям строк 2–{row_end}: "
            f"числовых значений {item['count']}; сумма {item['sum']}; среднее {item['average']}; "
            f"минимум {item['minimum']}; максимум {item['maximum']}.",
            {
                "kind": f"{parsed.file_type}_derived",
                "label": f"Показатели столбца «{item['name']}» · строки 2–{row_end}",
                "column": item["name"],
                "row_start": 2,
                "row_end": row_end,
                "derived": True,
            },
            derived=True,
        ))
    return blocks


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
        result.append((block.text, locator, "ocr", line_start, line_end, position, end, "exact"))
        cursor = end
    return result, {"quality": {"exact": len(result), "fuzzy": 0, "nearest": 0, "none": max(0, len(parsed.blocks) - len(result))}}
