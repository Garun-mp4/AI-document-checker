from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.codex import CodexModelUnavailable, CodexNeedsLogin, CodexUnavailable
from app.services.embeddings import EmbeddingConfigurationError
from app.services.markdown_mapping import MappedMarkdownBlock, map_markdown
from app.services.markitdown_service import MarkdownConversionError, MarkdownResult
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock
from app.services.processing_helpers import (
    _carry_forward_unselected_ocr_pages,
    _computed_blocks,
    _merge_pdf_ocr_pages,
    _ocr_analysis_blocks,
    _pdf_markdown,
)


def test_computed_blocks_adds_table_summary_and_numeric_columns() -> None:
    parsed = ParsedDocument(
        file_type="csv",
        metadata={
            "row_count": 3,
            "column_count": 2,
            "numeric_columns": [{
                "name": "Стоимость",
                "count": 3,
                "sum": "300.00",
                "average": "100.00",
                "minimum": "80.25",
                "maximum": "125.25",
            }],
        },
        blocks=[SourceBlock("row", {"kind": "csv", "row_start": 2, "row_end": 2})],
    )

    derived = _computed_blocks(parsed)

    assert len(derived) == 2
    assert derived[0].derived is True
    assert derived[0].locator["label"] == "Сводка таблицы"
    assert derived[0].locator["row_end"] == 4
    assert "сумма 300.00" in derived[1].text
    assert derived[1].locator["column"] == "Стоимость"


def test_ocr_analysis_blocks_keep_page_locator_and_markdown_range() -> None:
    parsed = ParsedDocument("pdf", [SourceBlock("Распознанная строка", {"kind": "pdf", "page": 3, "label": "Страница 3", "ocr": True})])

    blocks, mapping = _ocr_analysis_blocks(parsed, "## Страница 3\nРаспознанная строка\n")

    assert blocks[0][2] == "ocr"
    assert blocks[0][1]["page"] == 3
    assert blocks[0][3] == 2
    assert blocks[0][5] > 0
    assert mapping["quality"] == {"exact": 1, "fuzzy": 0, "nearest": 0, "none": 0}


def test_mixed_pdf_merge_preserves_native_text_and_uses_one_ocr_source_per_page() -> None:
    native = ParsedDocument("pdf", [
        SourceBlock("Native page one.", {"kind": "pdf", "page": 1, "char_start": 0}),
        SourceBlock("Partial hidden layer.", {"kind": "pdf", "page": 2, "char_start": 0}),
    ], {
        "page_count": 4,
        "ocr_pages": [2, 4],
        "pdf_page_map": [
            {"page": 1, "classification": "native"},
            {"page": 2, "classification": "ocr_candidate"},
            {"page": 3, "classification": "blank"},
            {"page": 4, "classification": "ocr_candidate", "rotation": 90},
        ],
    })
    ocr = ParsedDocument("pdf", [
        SourceBlock("Complete OCR page two.", {"kind": "pdf", "page": 2, "char_start": 0, "ocr": True, "ocr_map": {"word_boxes": [[0, 0, 10, 10, 0, 4, 1, 90]]}}),
        SourceBlock("Rotated OCR page four.", {"kind": "pdf", "page": 4, "char_start": 0, "ocr": True, "ocr_map": {"word_boxes": [[0, 0, 10, 10, 0, 7, 1, 90]]}}),
    ], {
        "ocr_used": True,
        "ocr_language": "rus+eng",
        "ocr_engine_version": "tesseract-5.3",
        "ocr_page_count": 2,
        "ocr_page_map": [
            {"page": 2, "classification": "ocr", "word_count": 4, "line_count": 1},
            {"page": 4, "classification": "ocr", "word_count": 4, "line_count": 1},
        ],
    })

    merged, summary = _merge_pdf_ocr_pages(native, ocr)
    markdown = _pdf_markdown(merged)

    assert [(block.locator["page"], block.text) for block in merged.blocks] == [
        (1, "Native page one."), (2, "Complete OCR page two."), (4, "Rotated OCR page four."),
    ]
    assert "Partial hidden layer." not in markdown
    assert markdown.index("## Страница 1") < markdown.index("## Страница 2") < markdown.index("## Страница 4")
    assert "## Страница 3" not in markdown
    assert summary == {
        "native_pages": [1], "ocr_pages": [2, 4], "blank_pages": [3],
        "unreadable_pages": [], "native_preserved_pages": [],
    }
    assert merged.metadata["pdf_page_map"][3]["rotation"] == 90


def test_mixed_pdf_marks_unreadable_scan_partial_but_keeps_native_sources() -> None:
    native = ParsedDocument("pdf", [
        SourceBlock("Text that is still available.", {"kind": "pdf", "page": 1}),
    ], {
        "page_count": 2,
        "ocr_pages": [2],
        "pdf_page_map": [
            {"page": 1, "classification": "native"},
            {"page": 2, "classification": "ocr_candidate"},
        ],
    })
    ocr = ParsedDocument("pdf", [], {
        "ocr_page_map": [{"page": 2, "classification": "unreadable"}],
    })

    merged, summary = _merge_pdf_ocr_pages(native, ocr)

    assert [block.text for block in merged.blocks] == ["Text that is still available."]
    assert summary["unreadable_pages"] == [2]
    assert merged.metadata["pdf_page_map"][1]["classification"] == "unreadable"
    assert merged.metadata["pdf_page_map"][1]["ocr_result"] == "unreadable"


@pytest.mark.parametrize("file_type", ["txt", "pdf", "docx", "md", "xml"])
def test_computed_blocks_does_not_add_table_artifacts_for_non_tables(file_type: str) -> None:
    parsed = ParsedDocument(file_type=file_type, metadata={"row_count": 10}, blocks=[])

    assert _computed_blocks(parsed) == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (EmbeddingConfigurationError("модель не найдена"), "модель не найдена"),
        (RuntimeError("network connection failed"), "Не удалось загрузить локальную модель"),
        (RuntimeError("out of memory while embedding"), "Не хватило памяти"),
        (RuntimeError("unexpected parser failure"), "Не удалось обработать документ"),
    ],
)
def test_processing_error_converts_internal_failures_to_user_messages(error: Exception, expected: str) -> None:
    assert expected in engine.processing_error(error)



from contextlib import asynccontextmanager
from datetime import datetime, timezone

from app.models import DocumentVersion
from app.services import processing_engine as engine
from app.services.processing_engine import ProcessingAttempt


def harness(monkeypatch, tmp_path, file_type='txt', status='queued'):
    document_id = uuid4()
    original = tmp_path / f'{document_id}.{file_type}'
    original.write_bytes('Автор: Алексей Пример'.encode('cp1251'))
    monkeypatch.setattr(engine.settings, 'upload_dir', str(tmp_path))
    document = SimpleNamespace(id=document_id, filename=original.name, storage_path=str(original),
        file_type=file_type, status=status, input_checksum=None, chunk_count=0, active_version=0,
        error_message=None)
    job = SimpleNamespace(id=uuid4(), document_id=document_id, version=1, owner=uuid4(),
                          input_version=None, state='running', stage='queued', progress={})
    version = SimpleNamespace(state='staging', snapshot={}, chunk_version=1)
    class Session:
        def __init__(self):
            self.added = []
        async def get(self, model, key):
            return version if model is DocumentVersion else document
        async def scalar(self, statement):
            return datetime.now(timezone.utc)
        def add_all(self, rows):
            self.added.extend(rows)
    session = Session()
    @asynccontextmanager
    async def fenced(*args):
        yield session, document, job
    monkeypatch.setattr(engine, 'fenced', fenced)
    async def enter(*args): return True
    monkeypatch.setattr(engine, 'enter_analysis', enter)
    async def analyze(*args, **kwargs): return []
    monkeypatch.setattr(engine, 'analyze_document', analyze)
    async def discard(*args): version.snapshot = {}
    monkeypatch.setattr(engine, 'discard', discard)
    async def vectors(operation, path):
        import json
        assert operation == 'embed'
        return [[1.0]*384 for _ in json.loads(path.read_text(encoding='utf-8'))]
    monkeypatch.setattr(engine, 'run_document_operation', vectors)
    return ProcessingAttempt(job, SimpleNamespace()), document, original, version, session


def _xlsx_formula_parsed() -> tuple[ParsedDocument, str]:
    formula = {
        "cell": "B2",
        "column": "Итог",
        "column_index": 1,
        "formula": "=1-1",
        "has_cached_value": True,
        "cached_value": "0",
    }
    header = SourceBlock("Лист «Данные»: Код | Итог", {
        "kind": "xlsx", "sheet": "Данные", "row": 1, "row_start": 1, "row_end": 1,
        "sheet_row_count": 2, "populated_columns": [0, 1], "columns": ["Код", "Итог"],
    })
    row = SourceBlock("Код: A | Итог: 0 (формула: =1-1; сохранённое значение: 0)", {
        "kind": "xlsx", "sheet": "Данные", "row": 2, "row_start": 2, "row_end": 2,
        "populated_columns": [0, 1], "columns": ["Код", "Итог"], "formula_cells": [formula],
    })
    parsed = ParsedDocument("xlsx", [header, row], {
        "sheet_count": 1, "row_count": 1, "column_count": 2,
        "columns": ["Код", "Итог"], "numeric_columns": [],
    })
    markdown = "# Данные\n\n| Код | Итог |\n| --- | --- |\n| A | NaN |\n"
    return parsed, markdown


@pytest.mark.parametrize(('raised','status','message'), [
    (CodexNeedsLogin('login'), 'needs_auth', 'login'),
    (CodexModelUnavailable('model'), 'model_unavailable', 'model'),
    (CodexUnavailable('service'), 'error', 'service'),
    (RuntimeError('network'), 'error', 'Не удалось загрузить локальную модель'),
])
def test_analyze_translates_codex_failures_to_document_state(monkeypatch, tmp_path, raised, status, message):
    attempt, document, _, version, _ = harness(monkeypatch, tmp_path)
    version.state = 'indexed'
    version.snapshot = {'file_type':'txt'}
    async def fail(*args, **kwargs): raise raised
    monkeypatch.setattr(engine, 'analyze_document', fail)
    asyncio.run(attempt.run())
    assert attempt.job.state == 'failed'
    assert document.status == status
    assert message in attempt.job.error


@pytest.mark.parametrize('file_type', ['txt', 'md'])
def test_process_persists_markdown_mapping_and_indexes_markdown(monkeypatch, tmp_path, file_type):
    attempt, document, original, version, session = harness(monkeypatch, tmp_path, file_type)
    native = SourceBlock('Original text', {'kind':'txt', 'label':'Строка 1', 'line_start':1})
    parsed = ParsedDocument('txt', [native], {'line_count':1})
    mapped = MappedMarkdownBlock('Original text', {**native.locator, 'source_locators':[native.locator]},
                                line_start=3,line_end=3,char_start=10,char_end=23,confidence='exact')
    async def parse(*args, **kwargs): return parsed
    async def convert(*args, **kwargs): return MarkdownResult('# Report\n\nOriginal text\n', 'Report')
    async def mapping(*args, **kwargs): return [mapped], {'quality':{'exact':1}}
    monkeypatch.setattr(engine, 'parse_uploaded', parse)
    monkeypatch.setattr(engine, 'map_uploaded', mapping)
    attempt.markitdown.convert = convert
    asyncio.run(attempt.run())
    assert attempt.job.state == 'succeeded'
    assert document.status == 'ready'
    assert document.active_version == 1
    assert document.markdown_status == 'ready'
    assert document.analysis_source == 'markitdown'
    assert original.read_bytes() == 'Автор: Алексей Пример'.encode('cp1251')
    assert Path(document.markdown_path) != original
    assert Path(document.markdown_path).read_text(encoding='utf-8') == '# Report\n\nOriginal text\n'
    assert document.markdown_line_count == 3
    assert document.markdown_char_count == len('# Report\n\nOriginal text\n')
    assert document.markdown_mapping_json == {'exact':1}
    assert session.added[0].content_source == 'markitdown'
    assert session.added[0].markdown_line_start == 3
    assert session.added[0].version == 1
    assert version.state == 'ready'
    assert attempt.job.progress['performance_ms']['parser_ms'] >= 0
    assert attempt.job.progress['performance_ms']['markdown_conversion_ms'] >= 0
    assert attempt.job.progress['performance_ms']['source_mapping_ms'] >= 0
    assert attempt.job.progress['performance_ms']['embedding_ms'] >= 0


def test_xlsx_processing_indexes_formula_markdown_and_preserves_original(monkeypatch, tmp_path):
    attempt, document, original, _version, session = harness(monkeypatch, tmp_path, 'xlsx')
    parsed, converted_markdown = _xlsx_formula_parsed()
    async def parse(*args, **kwargs): return parsed
    async def convert(*args, **kwargs): return MarkdownResult(converted_markdown, 'Данные')
    async def mapping(_path, _filename, markdown_path, **_kwargs):
        markdown = Path(markdown_path).read_text(encoding='utf-8')
        return map_markdown(markdown, parsed.blocks)
    monkeypatch.setattr(engine, 'parse_uploaded', parse)
    monkeypatch.setattr(engine, 'map_uploaded', mapping)
    attempt.markitdown.convert = convert

    asyncio.run(attempt.run())

    markdown = Path(document.markdown_path).read_text(encoding='utf-8')
    assert attempt.job.state == 'succeeded'
    assert document.markdown_status == 'ready'
    assert document.analysis_source == 'markitdown'
    assert '| A | 0 |' in markdown
    assert 'выражение `=1-1`' in markdown
    assert 'сохранённое значение: 0' in markdown
    assert original.read_bytes() == 'Автор: Алексей Пример'.encode('cp1251')
    citation = next(row for row in session.added if row.content_source == 'markitdown' and row.locator.get('cell') == 'B2')
    assert citation.locator['sheet'] == 'Данные'
    assert citation.locator['row_start'] == 2
    assert citation.locator['formula_has_cached_value'] is True
    assert citation.mapping_confidence == 'exact'


def test_xlsx_formula_markdown_overflow_uses_native_fallback(monkeypatch, tmp_path):
    attempt, document, original, _version, session = harness(monkeypatch, tmp_path, 'xlsx')
    parsed, converted_markdown = _xlsx_formula_parsed()
    monkeypatch.setattr(engine.settings, 'markdown_max_chars', len(converted_markdown))
    async def parse(*args, **kwargs): return parsed
    async def convert(*args, **kwargs): return MarkdownResult(converted_markdown, 'Данные')
    monkeypatch.setattr(engine, 'parse_uploaded', parse)
    attempt.markitdown.convert = convert

    asyncio.run(attempt.run())

    assert attempt.job.state == 'succeeded'
    assert document.markdown_status == 'fallback'
    assert document.analysis_source == 'native_fallback'
    assert 'превышает безопасный размер' in document.markdown_error
    assert document.markdown_path is None
    assert all(row.content_source == 'native_fallback' for row in session.added if not row.is_derived)
    assert original.read_bytes() == 'Автор: Алексей Пример'.encode('cp1251')


@pytest.mark.parametrize('file_type', ['txt','md'])
def test_process_falls_back_to_native_sources_when_markdown_conversion_fails(monkeypatch,tmp_path,file_type):
    attempt, document, original, _version, session = harness(monkeypatch,tmp_path,file_type)
    native = SourceBlock('Native source', {'kind':'txt','label':'Строка 1','line_start':1})
    async def parse(*args, **kwargs): return ParsedDocument('txt',[native],{'line_count':1})
    async def fail(*args, **kwargs): raise MarkdownConversionError('conversion failed')
    monkeypatch.setattr(engine,'parse_uploaded',parse)
    attempt.markitdown.convert = fail
    asyncio.run(attempt.run())
    assert attempt.job.state == 'succeeded'
    assert original.read_bytes() == 'Автор: Алексей Пример'.encode('cp1251')
    assert document.markdown_status == 'fallback'
    assert document.analysis_source == 'native_fallback'
    assert document.markdown_error == 'conversion failed'
    assert document.markdown_path is None
    assert session.added[0].content_source == 'native_fallback'
    assert session.added[0].locator['source_text'] == 'Native source'


def test_process_translates_parser_failure_and_records_failed_job(monkeypatch,tmp_path):
    attempt,document,_,_version,_ = harness(monkeypatch,tmp_path)
    async def fail(*args, **kwargs): raise DocumentParsingError('Пустой файл')
    monkeypatch.setattr(engine,'parse_uploaded',fail)
    asyncio.run(attempt.run())
    assert document.status == 'error'
    assert document.error_message == 'Пустой файл'
    assert attempt.job.state == 'failed'


def test_failed_replacement_preserves_active_result(monkeypatch,tmp_path):
    attempt,document,_,_version,_ = harness(monkeypatch,tmp_path,status='ready')
    document.active_version = 0
    document.chunk_count = 10
    document.markdown_path = 'previous-artifact'
    async def fail(*args, **kwargs): raise DocumentParsingError('Пустой файл')
    monkeypatch.setattr(engine,'parse_uploaded',fail)
    asyncio.run(attempt.run())
    assert document.status == 'ready'
    assert document.active_version == 0
    assert document.chunk_count == 10
    assert document.markdown_path == 'previous-artifact'
    assert attempt.job.state == 'failed'


def test_changed_original_is_refused_before_parser(monkeypatch,tmp_path):
    attempt,document,original,_,_ = harness(monkeypatch,tmp_path)
    document.input_checksum = '0'*64
    asyncio.run(attempt.run())
    assert attempt.job.state == 'failed'
    assert 'изменился' in attempt.job.error
    assert original.exists()


def test_lost_lease_does_not_write_failure_or_activate(monkeypatch,tmp_path):
    from app.services.job_queue import LeaseLost
    attempt,document,_,_,session = harness(monkeypatch,tmp_path)
    @asynccontextmanager
    async def expired(*args):
        raise LeaseLost()
        yield
    monkeypatch.setattr(engine,'fenced',expired)
    async def finish(): pass
    monkeypatch.setattr(attempt, 'finish_cancel', finish)
    asyncio.run(attempt.run())
    assert not session.added
    assert document.active_version == 0
    assert attempt.job.state == 'running'


def test_selected_ocr_reprocess_carries_untouched_page_text_and_coordinates_forward() -> None:
    previous_blocks = [
        SourceBlock("Old page two", {"kind": "pdf", "page": 2, "ocr": True, "ocr_map": {"word_boxes": [[1, 2, 3, 4]]}}),
        SourceBlock("Old page four", {"kind": "pdf", "page": 4, "ocr": True, "ocr_map": {"word_boxes": [[5, 6, 7, 8]]}}),
    ]
    previous_page_map = [
        {"page": 2, "classification": "ocr", "confidence": 71},
        {"page": 4, "classification": "ocr", "confidence": 84},
    ]
    current_blocks = [SourceBlock("New page two", {"kind": "pdf", "page": 2, "ocr": True, "ocr_map": {"word_boxes": [[9, 10, 11, 12]]}})]
    current_page_map = [{"page": 2, "classification": "ocr", "confidence": 96}]

    blocks, page_map = _carry_forward_unselected_ocr_pages(
        previous_blocks,
        previous_page_map,
        current_blocks,
        current_page_map,
        {2},
    )

    assert [(block.locator["page"], block.text) for block in blocks] == [(4, "Old page four"), (2, "New page two")]
    assert page_map == [
        {"page": 2, "classification": "ocr", "confidence": 96},
        {"page": 4, "classification": "ocr", "confidence": 84},
    ]


def test_pdf_ocr_merge_preserves_selected_run_settings() -> None:
    from app.services.processing_helpers import _merge_pdf_ocr_pages

    native = ParsedDocument(
        "pdf",
        [SourceBlock("Native text", {"kind": "pdf", "page": 1})],
        {
            "page_count": 2,
            "pdf_page_map": [
                {"page": 1, "classification": "native"},
                {"page": 2, "classification": "ocr_candidate"},
            ],
        },
    )
    ocr = ParsedDocument(
        "pdf",
        [SourceBlock("Recognized text", {"kind": "pdf", "page": 2, "ocr": True})],
        {
            "ocr_language": "eng",
            "ocr_dpi": 300,
            "ocr_settings": {"language": "eng", "quality": "high", "dpi": 300},
            "ocr_engine_version": "5.3.0",
            "ocr_page_map": [{"page": 2, "classification": "ocr", "language": "eng", "dpi": 300}],
        },
    )

    merged, _ = _merge_pdf_ocr_pages(native, ocr)

    assert merged.metadata["ocr_settings"] == {"language": "eng", "quality": "high", "dpi": 300}
    assert merged.metadata["ocr_language"] == "eng"
    assert merged.metadata["ocr_dpi"] == 300
