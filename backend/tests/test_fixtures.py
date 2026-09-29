from __future__ import annotations

import random
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.services.parsing import parse_document

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_TEXT = {
    "sample.pdf": "Project: Document Checker Sample.",
    "sample.docx": "Тестовый проект",
    "sample.txt": "Проект: Проверка документов",
    "sample.md": "Тестовый проект",
    "sample.csv": "Проект: Альфа",
    "sample.xml": "Тестовый проект",
    "sample.xlsx": "Альфа",
    "sample.xls": "Альфа",
    "sample.pptx": "Тестовый проект",
    "sample.html": "Тестовый проект",
    "sample.json": "Тестовый проект",
    "sample.epub": "Тестовый проект",
}
EXPECTED_LOCATOR_KIND = {
    "sample.pdf": "pdf",
    "sample.docx": "docx",
    "sample.txt": "txt",
    "sample.md": "md",
    "sample.csv": "csv",
    "sample.xml": "xml",
    "sample.xlsx": "xlsx",
    "sample.xls": "xls",
    "sample.pptx": "pptx",
    "sample.html": "html",
    "sample.json": "json",
    "sample.epub": "epub",
}


@pytest.mark.parametrize("filename", sorted(EXPECTED_TEXT))
def test_synthetic_fixture_parses_with_content_and_source_location(filename: str) -> None:
    parsed = parse_document(filename, (FIXTURES / filename).read_bytes())
    all_text = "\n".join(block.text for block in parsed.blocks)

    assert parsed.file_type == Path(filename).suffix[1:]
    assert parsed.blocks
    assert EXPECTED_TEXT[filename] in all_text
    assert any(block.locator.get("kind") == EXPECTED_LOCATOR_KIND[filename] for block in parsed.blocks)


def test_pdf_fixture_has_page_source_and_real_text() -> None:
    parsed = parse_document("sample.pdf", (FIXTURES / "sample.pdf").read_bytes())

    assert parsed.metadata["page_count"] == 1
    assert parsed.blocks[0].locator["page"] == 1
    assert "Reviewer: Morgan Example" in parsed.blocks[0].text


def test_docx_fixture_includes_paragraph_and_table_sources() -> None:
    parsed = parse_document("sample.docx", (FIXTURES / "sample.docx").read_bytes())
    kinds = {block.locator["kind"] for block in parsed.blocks}

    assert "docx" in kinds
    assert "docx_table" in kinds
    assert any("Ответственный" in block.text for block in parsed.blocks)


def test_csv_fixture_aggregates_are_exact_and_keep_row_locations() -> None:
    parsed = parse_document("sample.csv", (FIXTURES / "sample.csv").read_bytes())
    metrics = {item["name"]: item for item in parsed.metadata["numeric_columns"]}

    assert parsed.metadata["delimiter"] == ";"
    assert parsed.metadata["row_count"] == 3
    assert parsed.metadata["column_count"] == 3
    assert metrics["Часы"] == {
        "name": "Часы", "count": 3, "sum": "60", "average": "20", "minimum": "10", "maximum": "30",
    }
    assert metrics["Стоимость"] == {
        "name": "Стоимость", "count": 3, "sum": "300.00", "average": "100.00", "minimum": "80.25", "maximum": "125.25",
    }
    assert all("row_start" in block.locator and "row_end" in block.locator for block in parsed.blocks)


def test_xml_fixture_preserves_nested_paths_and_attributes() -> None:
    parsed = parse_document("sample.xml", (FIXTURES / "sample.xml").read_bytes())

    owner = next(block for block in parsed.blocks if "Алексей Пример" in block.text)
    milestone = next(block for block in parsed.blocks if "Проверка" in block.text)
    assert owner.locator["path"] == "/project[1]/owner[1]"
    assert "role=executor" in owner.text
    assert "due=2026-11-30" in milestone.text


def test_docx_near_screenshot_size_is_still_valid_and_extractable() -> None:
    source = (FIXTURES / "sample.docx").read_bytes()
    expanded = BytesIO()
    with zipfile.ZipFile(BytesIO(source)) as source_archive, zipfile.ZipFile(expanded, "w") as target_archive:
        for entry in source_archive.infolist():
            target_archive.writestr(entry, source_archive.read(entry.filename))
        payload = random.Random(42).randbytes(2_800_000)
        target_archive.writestr("word/media/synthetic-payload.bin", payload, compress_type=zipfile.ZIP_STORED)

    data = expanded.getvalue()
    assert 2_700_000 < len(data) < 25 * 1024 * 1024

    parsed = parse_document("large-synthetic.docx", data)

    assert any("Тестовый проект" in block.text for block in parsed.blocks)
    assert parsed.metadata["table_count"] == 1
