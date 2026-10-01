from app.services.source_locators import versioned_source_locator


def test_locator_envelope_preserves_legacy_fields_and_binds_processing_version() -> None:
    legacy = {"kind": "txt", "label": "Строки 4–5", "line_start": 4, "line_end": 5, "char_start": 30, "char_end": 52}

    locator = versioned_source_locator(
        legacy,
        document_id="doc-1",
        processing_version=7,
        file_type="txt",
    )

    assert locator["kind"] == "txt"
    assert locator["label"] == "Строки 4–5"
    assert locator["locator_version"] == 1
    assert locator["document_id"] == "doc-1"
    assert locator["processing_version"] == 7
    assert locator["source_type"] == "text_range"
    assert locator["match_quality"] == "exact"
    assert locator["source_range"] == {
        "coordinate_space": "original-file-text",
        "line_start": 4,
        "line_end": 5,
        "char_start": 30,
        "char_end": 52,
    }


def test_locator_classifies_all_supported_source_families() -> None:
    cases = [
        ("pdf", {"kind": "pdf", "page": 2, "char_start": 0, "char_end": 15}, False, "pdf_text", "exact"),
        ("pdf", {"kind": "pdf", "page": 2, "ocr": True, "ocr_map": {"word_boxes": [[1, 2, 3, 4, 0, 3, 1, 90]]}}, False, "pdf_ocr", "exact"),
        ("docx", {"kind": "docx", "paragraph": 3, "char_start": 5, "char_end": 14}, False, "docx_paragraph", "exact"),
        ("docx", {"kind": "docx_table", "table": 1, "row": 4, "char_start": 0, "char_end": 12}, False, "docx_table", "exact"),
        ("csv", {"kind": "csv", "sheet": "Data", "row_start": 8, "row_end": 9, "columns": ["Name"]}, False, "table_row", "exact"),
        ("pptx", {"kind": "pptx", "slide": 2, "shape": 4}, False, "slide_block", "approximate"),
        ("epub", {"kind": "epub", "chapter": 3, "path": "chapter3.xhtml"}, False, "chapter_block", "approximate"),
        ("xml", {"kind": "xml", "path": "/root/name[1]", "line_start": 2, "line_end": 2, "char_start": 9, "char_end": 14}, False, "xml_node", "exact"),
        ("json", {"kind": "json", "path": "$.owner", "line_start": 2, "line_end": 2, "char_start": 12, "char_end": 22}, False, "json_value", "exact"),
        ("html", {"kind": "html", "element": "p", "line_start": 1, "line_end": 1, "char_start": 6, "char_end": 19}, False, "html_block", "exact"),
        ("csv", {"kind": "csv_derived", "derived": True, "row_start": 2, "row_end": 8}, True, "calculation", "calculation"),
    ]
    for file_type, legacy, derived, source_type, quality in cases:
        locator = versioned_source_locator(
            legacy,
            document_id="doc-2",
            processing_version=4,
            file_type=file_type,
            is_derived=derived,
        )
        assert locator["source_type"] == source_type
        assert locator["match_quality"] == quality
        assert locator["processing_version"] == 4


def test_explicit_lower_confidence_is_not_upgraded_by_range_shape() -> None:
    locator = versioned_source_locator(
        {"kind": "xml", "path": "/root/name[1]", "char_start": 2, "char_end": 9, "match_quality": "approximate"},
        document_id="doc-3",
        processing_version=2,
        file_type="xml",
    )

    assert locator["match_quality"] == "approximate"


def test_versioned_locator_declares_sheet_and_page_coordinate_spaces() -> None:
    spreadsheet = versioned_source_locator(
        {"kind": "xlsx", "sheet": "Data", "row_start": 20, "row_end": 22},
        document_id="doc-4",
        processing_version=1,
        file_type="xlsx",
    )
    pdf = versioned_source_locator(
        {"kind": "pdf", "page": 3, "char_start": 10, "char_end": 20},
        document_id="doc-5",
        processing_version=1,
        file_type="pdf",
    )

    assert spreadsheet["source_range"]["coordinate_space"] == "spreadsheet-row-number"
    assert pdf["source_range"]["coordinate_space"] == "pdf-page-text"
