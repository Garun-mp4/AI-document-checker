from __future__ import annotations

import csv
from io import BytesIO

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.services.parsing import DocumentParsingError, parse_document, safe_filename


def test_safe_filename_keeps_only_leaf_name() -> None:
    assert safe_filename(r"C:\private\folder\report.pdf") == "report.pdf"


def test_plain_text_supports_cp1251_and_keeps_line_locations() -> None:
    parsed = parse_document("notes.txt", "Первая строка\nВторая строка".encode("cp1251"))

    assert parsed.file_type == "txt"
    assert "Первая строка" in parsed.blocks[0].text
    assert parsed.blocks[0].locator["line_start"] == 1
    assert parsed.blocks[0].locator["line_end"] == 2


def test_csv_fallback_does_not_mutate_global_excel_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_dialect(*args: object, **kwargs: object) -> csv.Dialect:
        raise csv.Error("ambiguous sample")

    monkeypatch.setattr(csv.Sniffer, "sniff", no_dialect)
    parsed = parse_document("table.csv", b"Name;Value\none;first\ntwo;second\n")

    assert parsed.metadata["delimiter"] == ";"
    assert csv.excel.delimiter == ","
    assert any("Name: one" in block.text for block in parsed.blocks)


def test_csv_calculates_decimal_comma_columns_exactly() -> None:
    parsed = parse_document("table.csv", "Товар;Сумма\nА;1,5\nБ;2,5\n".encode())

    assert parsed.metadata["row_count"] == 2
    assert parsed.metadata["column_count"] == 2
    assert parsed.metadata["numeric_columns"] == [{
        "name": "Сумма",
        "count": 2,
        "sum": "4.0",
        "average": "2.0",
        "minimum": "1.5",
        "maximum": "2.5",
    }]


def test_large_csv_row_is_split_without_losing_its_locator() -> None:
    data = ("Описание\n" + "слово " * 400).encode()
    parsed = parse_document("large.csv", data)
    row_parts = [block for block in parsed.blocks if block.locator.get("row_start") == 2]

    assert len(row_parts) > 1
    assert all(block.locator["row_end"] == 2 for block in row_parts)
    assert all(len(block.text) <= 900 for block in row_parts)


def test_docx_preserves_paragraph_and_table_row_locations() -> None:
    document = DocxDocument()
    document.add_heading("Цель", level=1)
    document.add_paragraph("Проверить документ.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Этап"
    table.cell(0, 1).text = "Срок"
    stream = BytesIO()
    document.save(stream)
    parsed = parse_document("brief.docx", stream.getvalue())

    assert any(block.locator.get("heading") == "Heading 1" for block in parsed.blocks)
    assert any(block.locator.get("kind") == "docx_table" and "Срок" in block.text for block in parsed.blocks)


def test_xml_keeps_element_path_and_rejects_entities() -> None:
    parsed = parse_document("report.xml", b"<report><owner>Team</owner></report>")

    assert parsed.blocks[0].locator["path"] == "/report[1]/owner[1]"
    with pytest.raises(DocumentParsingError, match="XML"):
        parse_document("unsafe.xml", b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///secret">]><x>&e;</x>')


def test_pdf_text_is_extracted_with_page_locator() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Project owner is Team Alpha. The project uses Python.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    stream = BytesIO()
    writer.write(stream)

    parsed = parse_document("brief.pdf", stream.getvalue())

    assert parsed.metadata["page_count"] == 1
    assert parsed.metadata["page_width"] == 300
    assert parsed.metadata["page_height"] == 300
    assert parsed.blocks[0].locator["page"] == 1
    assert "Team Alpha" in parsed.blocks[0].text


def test_scanned_pdf_is_marked_for_ocr_and_encrypted_pdf_still_fails() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    stream = BytesIO()
    writer.write(stream)
    parsed = parse_document("scan.pdf", stream.getvalue())
    assert parsed.blocks == []
    assert parsed.metadata["ocr_required"] is True
    assert parsed.metadata["page_count"] == 1

    writer.encrypt("password")
    encrypted = BytesIO()
    writer.write(encrypted)
    with pytest.raises(DocumentParsingError, match="паролем"):
        parse_document("locked.pdf", encrypted.getvalue())


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("document.exe", b"text"),
        ("report.pdf", b"not a pdf"),
        ("broken.docx", b"not a zip archive"),
        ("broken.xml", b"<root>"),
        ("empty.txt", b""),
    ],
)
def test_invalid_inputs_fail_with_domain_error(filename: str, content: bytes) -> None:
    with pytest.raises(DocumentParsingError):
        parse_document(filename, content)
