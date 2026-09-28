from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.preview import build_preview


def chunk(text: str, locator: dict, ordinal: int = 0, *, derived: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        text=text,
        locator=locator,
        ordinal=ordinal,
        is_derived=derived,
    )


@pytest.mark.parametrize(
    ("file_type", "layout", "kind", "metadata"),
    [
        ("pdf", "pdf", "page", {"page_count": 2}),
        ("docx", "paper", "paragraph", {"paragraph_count": 1}),
        ("txt", "paper", "text", {"line_count": 2}),
        ("md", "paper", "text", {"line_count": 2}),
        ("csv", "table", "row", {"columns": ["Name", "Value"]}),
        ("xml", "tree", "node", {"root": "report"}),
    ],
)
def test_preview_keeps_source_anchor_for_every_supported_format(
    file_type: str,
    layout: str,
    kind: str,
    metadata: dict,
) -> None:
    source = chunk("Evidence", {"kind": file_type, "label": "Источник 1"})

    preview = build_preview(
        document_id="document-1",
        file_type=file_type,
        metadata=metadata,
        chunks=[source],
        original_url="/api/v1/documents/document-1/file",
    )

    assert preview["layout"] == layout
    assert preview["blocks"][0]["kind"] == kind
    assert preview["blocks"][0]["source_id"] == str(source.id)
    assert preview["blocks"][0]["locator"] == source.locator
    assert preview["aspect_ratio"] > 0
    assert preview["total_blocks"] == 1
    assert preview["truncated"] is False
    assert preview["original_url"] == "/api/v1/documents/document-1/file"


def test_csv_preview_recovers_cells_without_delegating_table_shape_to_model() -> None:
    source = chunk(
        "Name: Alpha | Value: 10\nName: Beta | Value: 20",
        {"kind": "csv", "label": "Строки 2–3", "row_start": 2, "row_end": 3},
    )

    preview = build_preview(
        document_id="document-1",
        file_type="csv",
        metadata={"columns": ["Name", "Value"]},
        chunks=[source],
        original_url="/api/v1/documents/document-1/file",
    )

    assert preview["blocks"][0]["rows"] == [["Alpha", "10"], ["Beta", "20"]]


def test_preview_uses_source_page_dimensions_for_pdf_and_docx() -> None:
    source = chunk("Evidence", {"kind": "pdf", "page": 1})

    pdf = build_preview(
        document_id="document-1",
        file_type="pdf",
        metadata={"page_count": 1, "page_width": 100, "page_height": 200},
        chunks=[source],
        original_url="/api/v1/documents/document-1/file",
    )
    docx = build_preview(
        document_id="document-1",
        file_type="docx",
        metadata={"page_width": 297, "page_height": 210},
        chunks=[source],
        original_url=None,
    )

    assert pdf["aspect_ratio"] == 0.5
    assert docx["aspect_ratio"] == 297 / 210


def test_derived_csv_calculation_is_marked_and_has_no_fake_cells() -> None:
    source = chunk(
        "Локальный расчёт: сумма 30",
        {"kind": "csv_derived", "label": "Показатели столбца «Value»", "derived": True},
        derived=True,
    )

    preview = build_preview(
        document_id="document-1",
        file_type="csv",
        metadata={"columns": ["Name", "Value"]},
        chunks=[source],
        original_url=None,
    )

    assert preview["blocks"][0]["kind"] == "calculation"
    assert preview["blocks"][0]["rows"] is None


def test_preview_marks_large_documents_as_truncated() -> None:
    sources = [chunk(f"Block {index}", {"kind": "txt"}, index) for index in range(2_005)]

    preview = build_preview(
        document_id="document-1",
        file_type="txt",
        metadata={},
        chunks=sources,
        original_url=None,
    )

    assert len(preview["blocks"]) == 2_000
    assert preview["total_blocks"] == 2_005
    assert preview["truncated"] is True


def test_preview_uses_index_count_when_api_fetches_only_a_safe_prefix() -> None:
    source = chunk("Block", {"kind": "txt"})

    preview = build_preview(
        document_id="document-1",
        file_type="txt",
        metadata={},
        chunks=[source],
        original_url=None,
        total_blocks=4_500,
    )

    assert preview["total_blocks"] == 4_500
    assert preview["truncated"] is True
