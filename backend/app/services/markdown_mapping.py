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


def _best_sources(text: str, native_blocks: list[SourceBlock], used: set[int]) -> tuple[list[int], str]:
    candidate = _plain(text)
    if not candidate:
        return [], "none"
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
    for text, line_start, line_end, char_start, char_end in _markdown_blocks(markdown):
        source_indices, confidence = _best_sources(text, native_blocks, set())
        source_locators = [native_blocks[index].locator for index in source_indices]
        source_text = "\n\n".join(native_blocks[index].text for index in source_indices)
        primary = dict(native_blocks[source_indices[0]].locator) if source_indices else {}
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
