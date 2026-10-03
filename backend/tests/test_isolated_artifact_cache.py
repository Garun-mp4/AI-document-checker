from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

import pytest

from app.config import settings
from app.services import isolated_documents
from app.services.markdown_mapping import MappedMarkdownBlock
from app.services.parsing import ParsedDocument, SourceBlock


def test_parsed_document_cache_avoids_repeating_isolated_parser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    source = tmp_path / "source.txt"
    source.write_text("synthetic source", encoding="utf-8")
    result = ParsedDocument("txt", [SourceBlock("synthetic source", {"line_start": 1, "line_end": 1})], {"encoding": "utf-8"})
    calls = 0

    async def parse(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return asdict(result)

    monkeypatch.setattr(isolated_documents, "run_document_operation", parse)

    first, second = asyncio.run(isolated_documents.parse_uploaded(source, "source.txt", cache_checksum="a" * 64)), asyncio.run(
        isolated_documents.parse_uploaded(source, "source.txt", cache_checksum="a" * 64)
    )
    changed_ocr_configuration = asyncio.run(isolated_documents.parse_uploaded(
        source,
        "source.txt",
        cache_checksum="a" * 64,
        configuration={"ocr_languages": "eng", "ocr_dpi": 300},
    ))
    monkeypatch.setattr(settings, "document_max_chars", settings.document_max_chars + 1)
    changed_parser_limit = asyncio.run(isolated_documents.parse_uploaded(
        source,
        "source.txt",
        cache_checksum="a" * 64,
        configuration={"ocr_languages": "eng", "ocr_dpi": 300},
    ))

    assert first == second == changed_ocr_configuration == changed_parser_limit == result
    assert calls == 2


def test_ocr_cache_reuses_only_matching_parameters_and_reports_cached_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    engine_version = ["tesseract-test-1"]
    from app.services.ocr import OCRService
    monkeypatch.setattr(OCRService, "_engine_version", staticmethod(lambda: engine_version[0]))
    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic pdf checksum anchor")
    parsed = ParsedDocument("pdf", [SourceBlock("recognized", {"page": 1, "ocr": True})], {
        "ocr_page_map": [{"page": 1}],
    })
    calls = 0

    async def recognize(*_args, progress_callback=None, **_kwargs):
        nonlocal calls
        calls += 1
        if progress_callback:
            await progress_callback(1, 1)
        return {
            "parsed": asdict(parsed),
            "markdown": "recognized\n",
            "engine_version": engine_version[0],
            "language": "rus+eng",
            "confidence": 95.0,
        }

    monkeypatch.setattr(isolated_documents, "run_document_operation", recognize)
    progress: list[tuple[int, int]] = []

    async def callback(processed: int, total: int) -> None:
        progress.append((processed, total))

    first = asyncio.run(isolated_documents.ocr_uploaded(
        source,
        cache_checksum="b" * 64,
        configuration={"ocr_dpi": 200},
        pages=[1],
        progress_callback=callback,
    ))
    second = asyncio.run(isolated_documents.ocr_uploaded(
        source,
        cache_checksum="b" * 64,
        configuration={"ocr_dpi": 200},
        pages=[1],
        progress_callback=callback,
    ))
    changed = asyncio.run(isolated_documents.ocr_uploaded(
        source,
        cache_checksum="b" * 64,
        configuration={"ocr_dpi": 300},
        pages=[1],
        progress_callback=callback,
    ))
    engine_version[0] = "tesseract-test-2"
    changed_engine = asyncio.run(isolated_documents.ocr_uploaded(
        source,
        cache_checksum="b" * 64,
        configuration={"ocr_dpi": 200},
        pages=[1],
        progress_callback=callback,
    ))

    assert first == second
    assert changed == first
    assert changed_engine.engine_version == "tesseract-test-2"
    assert calls == 3
    assert progress == [(1, 1), (1, 1), (1, 1), (1, 1)]


def test_source_map_cache_keys_markdown_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    source = tmp_path / "source.docx"
    source.write_bytes(b"synthetic source")
    markdown = tmp_path / "markdown.md"
    markdown.write_text("# first", encoding="utf-8")
    block = MappedMarkdownBlock("first", {"paragraph": 1}, 1, 1, 2, 7, "exact")
    calls = 0

    async def map_source(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"blocks": [asdict(block)], "mapping": {"exact": 1}}

    monkeypatch.setattr(isolated_documents, "run_document_operation", map_source)

    first = asyncio.run(isolated_documents.map_uploaded(source, "source.docx", markdown, cache_checksum="c" * 64))
    second = asyncio.run(isolated_documents.map_uploaded(source, "source.docx", markdown, cache_checksum="c" * 64))
    markdown.write_text("# second", encoding="utf-8")
    third = asyncio.run(isolated_documents.map_uploaded(source, "source.docx", markdown, cache_checksum="c" * 64))
    monkeypatch.setattr(isolated_documents, "PARSER_CACHE_VERSION", "document-parser-next")
    changed_parser = asyncio.run(isolated_documents.map_uploaded(source, "source.docx", markdown, cache_checksum="c" * 64))

    assert first == second == third
    assert changed_parser == first
    assert calls == 3


def test_m17_parser_cache_revision_is_limited_to_xlsx() -> None:
    assert isolated_documents._parser_cache_version("formulas.xlsx").endswith("xlsx-formula-context-m17-v1")
    assert isolated_documents._parser_cache_version("report.txt") == isolated_documents.PARSER_CACHE_VERSION
    assert isolated_documents._parser_cache_version("report.xls") == isolated_documents.PARSER_CACHE_VERSION
