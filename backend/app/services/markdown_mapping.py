from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.services.parsing import CHUNK_TARGET_CHARS, SourceBlock, _split_long_text


@dataclass(frozen=True)
class MappedMarkdownBlock:
    text: str
    locator: dict[str, Any]
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    confidence: str


def _plain(value: str) -> str:
    value = re.sub(r"```[^\n]*|`", "", value)
    value = re.sub(r"!\[[^]]*\]\([^)]*\)", "", value)
    value = re.sub(r"[#*_>~-]+", " ", value)
    value = value.replace("|", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _markdown_blocks(markdown: str) -> Iterable[tuple[str, int, int, int, int]]:
    lines = markdown.splitlines(keepends=True)
    start: int | None = None
    char_start = 0
    cursor = 0
    for index, line in enumerate(lines, start=1):
        is_blank = not line.strip()
        if not is_blank and start is None:
            start = index
            char_start = cursor
        cursor += len(line)
        if is_blank and start is not None:
            end = index - 1
            char_end = cursor - len(line)
            text = markdown[char_start:char_end].strip()
            if text:
                yield text, start, end, char_start, char_end
            start = None
    if start is not None:
        text = markdown[char_start:].strip()
        if text:
            yield text, start, len(lines), char_start, len(markdown)


def _split_markdown_table_row(line: str) -> list[str] | None:
    value = line.strip()
    if not value.startswith("|"):
        return None
    value = value[1:-1] if value.endswith("|") else value[1:]
    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            next_char = value[index + 1]
            if next_char == "|":
                current.append(next_char)
            else:
                current.extend((char, next_char))
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _render_markdown_table_row(cells: list[str]) -> str:
    return "| " + " | ".join(value.replace("|", r"\|") for value in cells) + " |"


def _is_markdown_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _formula_context_index(
    native_blocks: list[SourceBlock],
) -> dict[tuple[str, str], tuple[int, dict[str, Any]]]:
    contexts: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for index, block in enumerate(native_blocks):
        sheet = block.locator.get("sheet")
        if not isinstance(sheet, str):
            continue
        for formula in block.locator.get("formula_cells", []):
            if not isinstance(formula, dict):
                continue
            cell = formula.get("cell")
            if isinstance(cell, str):
                key = (sheet.casefold(), cell.replace("$", "").upper())
                contexts.setdefault(key, (index, formula))
    return contexts


def _formula_context_for_cell(
    text: str,
    formula_index: dict[tuple[str, str], tuple[int, dict[str, Any]]],
) -> tuple[int, dict[str, Any]] | None:
    match = re.search(
        r"Формула\s+XLSX\s*[—-]\s*лист\s*«(?P<sheet>[^»]+)»\s*,\s*ячейка\s*(?P<cell>\$?[A-Z]{1,3}\$?\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    key = (
        match.group("sheet").strip().casefold(),
        match.group("cell").replace("$", "").upper(),
    )
    context = formula_index.get(key)
    if context is None:
        return None
    formula = context[1].get("formula")
    if not isinstance(formula, str):
        return None
    normalized_formula = _plain(formula)
    if not normalized_formula or normalized_formula not in _plain(text):
        return None
    return context


def _inline_code(value: str) -> str:
    delimiter = "`" * (max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0) + 1)
    padding = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{delimiter}{padding}{value}{padding}{delimiter}"


def add_xlsx_formula_context(markdown: str, native_blocks: list[SourceBlock]) -> str:
    """Fix empty-cell NaN placeholders and expose formula state without evaluating it."""
    formula_rows: dict[tuple[str, int], dict[int, dict[str, Any]]] = {}
    populated_columns: dict[tuple[str, int], set[int]] = {}
    sheet_row_counts: dict[str, int] = {}
    formulas: list[tuple[str, dict[str, Any]]] = []
    for block in native_blocks:
        locator = block.locator
        sheet = locator.get("sheet")
        row = locator.get("row")
        if not isinstance(sheet, str) or not isinstance(row, int):
            continue
        if row == 1 and isinstance(locator.get("sheet_row_count"), int):
            sheet_row_counts[sheet] = locator["sheet_row_count"]
        columns = locator.get("populated_columns")
        if isinstance(columns, list):
            populated_columns[(sheet, row)] = {
                index for index in columns if isinstance(index, int) and index >= 0
            }
        for formula in locator.get("formula_cells", []):
            if not isinstance(formula, dict) or not isinstance(formula.get("column_index"), int):
                continue
            formula_rows.setdefault((sheet, row), {})[formula["column_index"]] = formula
            formulas.append((sheet, formula))

    lines = markdown.splitlines()
    section_headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading and heading.group(1) in sheet_row_counts:
            section_headings.append((index, heading.group(1)))

    for heading_index, (start, sheet) in enumerate(section_headings):
        end = section_headings[heading_index + 1][0] if heading_index + 1 < len(section_headings) else len(lines)
        separator_index = next(
            (
                index for index in range(start + 1, end)
                if (cells := _split_markdown_table_row(lines[index])) is not None
                and _is_markdown_table_separator(cells)
            ),
            None,
        )
        if separator_index is None:
            continue
        header_index = next(
            (
                index for index in range(separator_index - 1, start, -1)
                if _split_markdown_table_row(lines[index]) is not None
            ),
            None,
        )
        if header_index is None:
            continue
        table_rows = [
            index for index in range(separator_index + 1, end)
            if _split_markdown_table_row(lines[index]) is not None
        ]
        if len(table_rows) + 1 != sheet_row_counts[sheet]:
            # Do not shift source coordinates if the converter omitted or regrouped rows.
            continue
        rows = [(header_index, 1), *[(index, row_number) for row_number, index in enumerate(table_rows, start=2)]]
        for line_index, row_number in rows:
            cells = _split_markdown_table_row(lines[line_index])
            if cells is None:
                continue
            populated = populated_columns.get((sheet, row_number), set())
            formulas_for_row = formula_rows.get((sheet, row_number), {})
            for column_index, cell in enumerate(cells):
                formula = formulas_for_row.get(column_index)
                if formula is not None:
                    if formula.get("has_cached_value"):
                        cached_value = formula.get("cached_value")
                        cells[column_index] = (
                            str(cached_value) if cached_value is not None else "пустое сохранённое значение"
                        )
                    else:
                        cells[column_index] = "Нет сохранённого результата"
                elif cell == "NaN" and column_index not in populated:
                    cells[column_index] = ""
            lines[line_index] = _render_markdown_table_row(cells)

    result = "\n".join(lines).strip()
    if formulas:
        notes: list[str] = []
        for sheet, formula in formulas:
            expression = str(formula["formula"])
            column = formula.get("column", f"столбец {formula['column_index'] + 1}")
            if formula.get("has_cached_value"):
                cached_value = formula.get("cached_value")
                cached = str(cached_value) if cached_value is not None else "пустое значение"
                status = f"сохранённое значение: {cached}."
            else:
                status = "сохранённое значение отсутствует; приложение формулу не вычисляло."
            notes.append(
                f"Формула XLSX — лист «{sheet}», ячейка {formula['cell']} ({column}); "
                f"выражение {_inline_code(expression)}; {status}"
            )
        result = f"{result}\n\n" + "\n\n".join(notes) if result else "\n\n".join(notes)
    return result


def _best_sources(
    text: str,
    native_blocks: list[SourceBlock],
    used: set[int],
    formula_index: dict[tuple[str, str], tuple[int, dict[str, Any]]],
) -> tuple[list[int], str]:
    candidate = _plain(text)
    if not candidate:
        return [], "none"
    formula_context = _formula_context_for_cell(text, formula_index)
    if formula_context is not None:
        return [formula_context[0]], "exact"
    exact: list[int] = []
    for index, block in enumerate(native_blocks):
        source = _plain(block.text)
        if source and (candidate in source or source in candidate):
            exact.append(index)
            continue
        if block.locator.get("kind") == "xml":
            # The native parser appends XML attributes as (key=value), while
            # MarkItDown preserves them in the opening tag, before node text.
            node_text = _plain(re.sub(r"\s+\([^()]*=[^()]*\)$", "", block.text))
            if node_text and node_text in candidate:
                exact.append(index)
                continue
        if block.locator.get("kind") in {"xlsx", "xls"} and block.locator.get("columns"):
            # Native spreadsheet rows include column labels; Markdown tables
            # put those labels in the header. Compare the row's ordered cell
            # values, accounting for xlrd's 10.0 vs Markdown's 10 formatting.
            cells = block.text.split(" | ")
            values = []
            for cell in cells:
                for column in block.locator["columns"]:
                    prefix = f"{column}: "
                    if cell.startswith(prefix):
                        values.append(cell[len(prefix):])
                        break
            row = _plain(" | ".join(values))
            normalize_numbers = lambda value: re.sub(r"(?<![\w.])(\d+)\.0+(?![\w.])", r"\1", value)
            if row and normalize_numbers(row) in normalize_numbers(candidate):
                exact.append(index)
    if exact:
        return exact[:4], "exact"
    scored: list[tuple[float, int]] = []
    for index, block in enumerate(native_blocks):
        source = _plain(block.text)
        if not source:
            continue
        score = SequenceMatcher(None, candidate[:1_500], source[:1_500]).ratio()
        token_overlap = len(set(candidate.split()) & set(source.split())) / max(1, len(set(candidate.split())))
        score = max(score, token_overlap * 0.9)
        scored.append((score, index))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.68:
        threshold = max(0.58, scored[0][0] - 0.12)
        return [index for score, index in scored[:4] if score >= threshold], "fuzzy"
    if scored and scored[0][0] >= 0.35:
        return [scored[0][1]], "nearest"
    return [], "none"


def map_markdown(markdown: str, native_blocks: list[SourceBlock]) -> tuple[list[MappedMarkdownBlock], dict[str, Any]]:
    mapped: list[MappedMarkdownBlock] = []
    sidecar_blocks: list[dict[str, Any]] = []
    quality = {"exact": 0, "fuzzy": 0, "nearest": 0, "none": 0}
    formula_index = _formula_context_index(native_blocks)
    for text, line_start, line_end, char_start, char_end in _markdown_blocks(markdown):
        source_indices, confidence = _best_sources(text, native_blocks, set(), formula_index)
        source_locators = [native_blocks[index].locator for index in source_indices]
        source_text = "\n\n".join(native_blocks[index].text for index in source_indices)
        primary = dict(native_blocks[source_indices[0]].locator) if source_indices else {}
        formula_context = _formula_context_for_cell(text, formula_index)
        if formula_context is not None:
            _source_index, formula = formula_context
            primary.update({
                "cell": formula["cell"],
                "column": formula.get("column"),
                "column_index": formula["column_index"],
                "formula": formula["formula"],
                "formula_has_cached_value": bool(formula.get("has_cached_value")),
            })
        if source_locators and primary.get("kind") in {"xlsx", "xls"}:
            same_sheet = all(item.get("sheet") == primary.get("sheet") for item in source_locators)
            if same_sheet:
                primary["row_start"] = min(item["row_start"] for item in source_locators)
                primary["row_end"] = max(item["row_end"] for item in source_locators)
        locator = primary
        locator.update({
            "kind": primary.get("kind", "markdown"),
            "markdown_line_start": line_start,
            "markdown_line_end": line_end,
            "markdown_char_start": char_start,
            "markdown_char_end": char_end,
            "mapping_confidence": confidence,
        })
        if source_text:
            locator["source_text"] = source_text[:8_000]
            locator["source_locators"] = source_locators
        if len(text) <= CHUNK_TARGET_CHARS:
            pieces = [(text, locator, char_start, char_end)]
        else:
            pieces = []
            for piece in _split_long_text(text, locator, target=CHUNK_TARGET_CHARS):
                offset = text.find(piece.text)
                pieces.append((piece.text, dict(piece.locator), char_start + max(0, offset), char_start + max(0, offset) + len(piece.text)))
        for piece_text, piece_locator, piece_char_start, piece_char_end in pieces:
            block = MappedMarkdownBlock(
                text=piece_text,
                locator=piece_locator,
                line_start=line_start,
                line_end=line_end,
                char_start=piece_char_start,
                char_end=piece_char_end,
                confidence=confidence,
            )
            mapped.append(block)
        quality[confidence] += 1
        sidecar_blocks.append({
            "line_start": line_start,
            "line_end": line_end,
            "char_start": char_start,
            "char_end": char_end,
            "confidence": confidence,
            "source_locators": source_locators,
        })
    return mapped, {"version": 1, "quality": quality, "blocks": sidecar_blocks}


def serialize_map(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
