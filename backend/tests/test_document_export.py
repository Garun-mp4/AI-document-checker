from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app.services.document_export import (
    ExportAnswer,
    ExportError,
    ExportMessage,
    ExportSnapshot,
    ExportSource,
    build_markdown,
    build_pdf,
    safe_filename,
    sanitize_markdown,
)
from pypdf import PdfReader


@pytest.fixture
def snapshot() -> ExportSnapshot:
    source = ExportSource(
        id="private-source-id",
        text="Подтверждение находится в первом разделе документа.",
        locator={"page": 3, "paragraph": 9, "match_quality": "exact"},
        ordinal=8,
        version=4,
    )
    return ExportSnapshot(
        filename="Соглашение.docx",
        file_type="docx",
        exported_at=datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc),
        processing_version=5,
        chunk_version=4,
        model="gpt-6.1-sol",
        reasoning_effort="high",
        analysis_source="markitdown",
        markdown_status="ready",
        markdown_converter_version="0.1.8",
        ocr_status="not_needed",
        insights=(
            ExportAnswer("overview", "О чём документ?", "Обзор подтверждён источником 〔1〕.", (source,)),
            ExportAnswer("goals", "Цель и задачи", "Цель описана отдельно.", ()),
        ),
        messages=(
            ExportMessage("user", "Перескажи документ.", created_at=datetime(2026, 10, 2, 11, tzinfo=timezone.utc)),
            ExportMessage("assistant", "Краткий **ответ** 〔1〕", (source,), datetime(2026, 10, 2, 11, 1, tzinfo=timezone.utc), "gpt-6.1-sol", "high"),
        ),
    )


def test_markdown_export_contains_processing_provenance_and_version_bound_source(snapshot: ExportSnapshot) -> None:
    result = build_markdown(snapshot, "analysis")

    assert "Соглашение.docx" in result
    assert "gpt-6.1-sol" in result
    assert "reasoning:** high" in result
    assert "Версия обработки:** 5 (индекс источников 4)" in result
    assert "[1]" in result
    assert "Абзац 9" in result
    assert "Страница 3" in result
    assert "Точное совпадение" in result
    assert "Подтверждение находится" in result
    assert "private-source-id" not in result
    assert "localhost" not in result


def test_selected_export_contains_only_requested_answer(snapshot: ExportSnapshot) -> None:
    result = build_markdown(snapshot, "selected_answers", {"goals"})

    assert "Цель и задачи" in result
    assert "Обзор подтверждён" not in result
    with pytest.raises(ExportError, match="хотя бы один ответ"):
        build_markdown(snapshot, "selected_answers", set())


def test_conversation_export_keeps_chronology_and_historical_model_metadata(snapshot: ExportSnapshot) -> None:
    result = build_markdown(snapshot, "conversation")

    assert result.index("### Вы ·") < result.index("### Ассистент ·")
    assert "Перескажи документ" in result
    assert "Краткий **ответ** [1]" in result
    assert "Модель: gpt-6.1-sol · reasoning: high" in result
    assert "Абзац 9 · версия 4" in result


def test_export_warnings_are_clear_and_do_not_leak_raw_ocr_errors(snapshot: ExportSnapshot) -> None:
    changed = ExportSnapshot(
        **{
            **snapshot.__dict__,
            "markdown_status": "fallback",
            "ocr_status": "partial",
            "ocr_error": "C:/secret/private.pdf: stack trace and document text",
        }
    )

    result = build_markdown(changed, "analysis")
    assert "резервным локальным парсером" in result
    assert "OCR распознал документ не полностью" in result
    assert "C:/secret" not in result
    assert "stack trace" not in result


def test_markdown_export_neutralizes_active_html_and_unsafe_links() -> None:
    result = sanitize_markdown(
        '<script>alert("x")</script> [опасно](javascript:alert(1)) '
        '[локальный адрес](http://127.0.0.1:5173/private) [сайт](https://example.com/a) '
        '[ссылка с частью](https://example.com/a_(b))'
    )

    assert "&lt;script&gt;" in result
    assert "alert(\"x\")" in result
    assert "[опасно]" in result and "javascript:" not in result
    assert "[локальный адрес]" in result and "127.0.0.1" not in result
    assert "[сайт](https://example.com/a)" in result
    assert "[ссылка с частью](https://example.com/a_%28b%29)" in result


def test_safe_export_filenames_preserve_unicode_and_remove_header_controls() -> None:
    assert safe_filename("..\\Итог;\n.pdf", "pdf") == "Итог__.pdf"
    assert safe_filename("   ", "markdown") == "document.md"


def test_pdf_export_embeds_cyrillic_and_splits_long_tables(snapshot: ExportSnapshot) -> None:
    rows = "\n".join(f"| Строка {index} | Значение {index} |" for index in range(1, 100))
    table_answer = "| Поле | Данные |\n| --- | --- |\n" + rows
    long_snapshot = ExportSnapshot(
        **{
            **snapshot.__dict__,
            "insights": (ExportAnswer("table", "Длинная таблица", table_answer, snapshot.insights[0].citations),),
        }
    )

    pdf = build_pdf(long_snapshot, "analysis")
    reader = PdfReader(__import__("io").BytesIO(pdf))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert pdf.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    assert "Длинная таблица" in extracted
    assert "Строка 1" in extracted and "Строка 99" in extracted
    assert extracted.count("Поле") >= 2
    assert "Соглашение.docx" in extracted
    assert "Подтверждение" in extracted


def test_pdf_export_treats_raw_html_as_text(snapshot: ExportSnapshot) -> None:
    unsafe = ExportSnapshot(
        **{
            **snapshot.__dict__,
            "insights": (ExportAnswer("html", "HTML", '<script>window.bad=true</script>', ()),),
        }
    )
    pdf = build_pdf(unsafe, "analysis")
    text = PdfReader(__import__("io").BytesIO(pdf)).pages[0].extract_text() or ""
    assert "window.bad" in text
    assert "script" in text
