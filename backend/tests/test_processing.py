from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import processing
from app.services.codex import CodexModelUnavailable, CodexNeedsLogin, CodexUnavailable
from app.services.embeddings import EmbeddingConfigurationError
from app.services.markdown_mapping import MappedMarkdownBlock
from app.services.markitdown_service import MarkdownConversionError, MarkdownResult
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock
from app.services.processing import (
    DocumentProcessor,
    _computed_blocks,
    _ocr_analysis_blocks,
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
    parsed = ParsedDocument("pdf", [SourceBlock("Распознанная строка", {"kind": "pdf", "page": 3, "label": "Страница 3"})])

    blocks, mapping = _ocr_analysis_blocks(parsed, "## Страница 3\nРаспознанная строка\n")

    assert blocks[0][2] == "ocr"
    assert blocks[0][1]["page"] == 3
    assert blocks[0][3] == 2
    assert blocks[0][5] > 0
    assert mapping["quality"] == {"exact": 1, "fuzzy": 0, "nearest": 0, "none": 0}


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
    assert expected in DocumentProcessor._processing_error(error)


@pytest.mark.parametrize(
    ("raised", "status", "message"),
    [
        (CodexNeedsLogin("login"), "needs_auth", "login"),
        (CodexModelUnavailable("model"), "model_unavailable", "model"),
        (CodexUnavailable("service"), "error", "service"),
        (RuntimeError("network"), "error", "Не удалось загрузить локальную модель"),
    ],
)
def test_analyze_translates_codex_failures_to_document_state(
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    status: str,
    message: str,
) -> None:
    processor = DocumentProcessor(SimpleNamespace())
    calls: list[tuple[object, ...]] = []

    async def fail(*_args, **_kwargs):
        raise raised

    async def set_error(*args):
        calls.append(args)

    monkeypatch.setattr(processing, "analyze_document", fail)
    monkeypatch.setattr(processor, "_set_error", set_error)

    asyncio.run(processor._analyze(uuid4()))

    assert calls
    assert calls[0][1] == status
    assert message in calls[0][2]
    assert not processor._tasks


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, document, *, existing_chat=None):
        self.document = document
        self.existing_chat = existing_chat
        self.added: list[object] = []
        self.added_batches: list[list[object]] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _document_id):
        return self.document

    async def execute(self, _statement):
        return _Result(self.existing_chat)

    def add(self, item):
        self.added.append(item)

    def add_all(self, items):
        batch = list(items)
        self.added_batches.append(batch)
        self.added.extend(batch)

    async def commit(self):
        self.commits += 1


class _SessionFactory:
    def __init__(self, sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


def _processing_document(tmp_path: Path):
    document_id = uuid4()
    path = tmp_path / "report.txt"
    path.write_text("original", encoding="utf-8")
    document = SimpleNamespace(
        id=document_id,
        filename=path.name,
        storage_path=str(path),
        status="queued",
        error_message=None,
        file_type="txt",
        metadata_json={},
        markdown_status="pending",
        analysis_source="native_fallback",
        markdown_path=None,
        markdown_map_path=None,
        markdown_error=None,
        markdown_converter_version=None,
        markdown_char_count=0,
        markdown_line_count=0,
        markdown_checksum=None,
        markdown_mapping_json={},
        chunk_count=0,
    )
    return document_id, path, document


@pytest.mark.parametrize("file_type", ["txt", "md"])
def test_process_persists_markdown_mapping_and_indexes_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, file_type: str,
) -> None:
    document_id, _path, document = _processing_document(tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    original = upload_dir / f"{document_id}.{file_type}"
    original.write_bytes("Автор: Алексей Пример".encode("cp1251"))
    document.storage_path = str(original)
    document.file_type = file_type
    monkeypatch.setattr(processing.settings, "upload_dir", str(upload_dir))
    native = SourceBlock("Original text", {"kind": "txt", "label": "Строка 1", "line_start": 1})
    parsed = ParsedDocument("txt", [native], {"line_count": 1})
    mapped = MappedMarkdownBlock(
        "Original text", {"kind": "txt", "label": "Строка 1", "line_start": 1, "source_locators": [native.locator]},
        line_start=3, line_end=3, char_start=10, char_end=23, confidence="exact",
    )
    sessions = [_Session(document), _Session(document), _Session(document), _Session(document)]
    monkeypatch.setattr(processing, "SessionLocal", _SessionFactory(sessions))
    async def parse(*_args):
        return parsed
    monkeypatch.setattr(processing, "parse_uploaded", parse)
    processor = DocumentProcessor(SimpleNamespace())
    async def convert(_path):
        return MarkdownResult("# Report\n\nOriginal text\n", "Report")
    processor.markitdown.convert = convert  # type: ignore[method-assign]
    async def mapping(*_args):
        return [mapped], {"quality": {"exact": 1}}
    monkeypatch.setattr(processing, "map_uploaded", mapping)
    monkeypatch.setattr(processing, "embed_passages", lambda texts, _cache: [[float(index)] for index, _ in enumerate(texts)])
    analyzed: list[object] = []
    async def fake_analyze(document_id_arg):
        analyzed.append(document_id_arg)
    monkeypatch.setattr(processor, "_analyze", fake_analyze)

    asyncio.run(processor._process(document_id))

    assert analyzed == [document_id]
    assert document.status == "indexing"
    assert document.markdown_status == "ready"
    assert document.analysis_source == "markitdown"
    assert original.read_bytes() == "Автор: Алексей Пример".encode("cp1251")
    assert Path(document.markdown_path) != original
    assert document.markdown_path and Path(document.markdown_path).read_text(encoding="utf-8") == "# Report\n\nOriginal text\n"
    assert document.markdown_line_count == 3
    assert document.markdown_char_count == len("# Report\n\nOriginal text\n")
    assert document.markdown_mapping_json == {"exact": 1}
    assert sessions[2].added_batches[0][0].content_source == "markitdown"
    assert sessions[2].added_batches[0][0].markdown_line_start == 3
    assert sessions[3].added[0].document_id == document_id


@pytest.mark.parametrize("file_type", ["txt", "md"])
def test_process_falls_back_to_native_sources_when_markdown_conversion_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, file_type: str,
) -> None:
    document_id, _path, document = _processing_document(tmp_path)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    original = upload_dir / f"{document_id}.{file_type}"
    original.write_bytes(b"unchanged original")
    document.storage_path = str(original)
    document.file_type = file_type
    monkeypatch.setattr(processing.settings, "upload_dir", str(upload_dir))
    native = SourceBlock("Native source", {"kind": "txt", "label": "Строка 1", "line_start": 1})
    parsed = ParsedDocument("txt", [native], {"line_count": 1})
    sessions = [_Session(document), _Session(document), _Session(document), _Session(document)]
    monkeypatch.setattr(processing, "SessionLocal", _SessionFactory(sessions))
    async def parse(*_args):
        return parsed
    monkeypatch.setattr(processing, "parse_uploaded", parse)
    processor = DocumentProcessor(SimpleNamespace())
    async def fail(_path):
        raise MarkdownConversionError("conversion failed")
    processor.markitdown.convert = fail  # type: ignore[method-assign]
    monkeypatch.setattr(processing, "embed_passages", lambda texts, _cache: [[1.0] for _ in texts])
    monkeypatch.setattr(processor, "_analyze", lambda *_args: asyncio.sleep(0))

    asyncio.run(processor._process(document_id))

    assert document.markdown_status == "fallback"
    assert original.read_bytes() == b"unchanged original"
    assert document.analysis_source == "native_fallback"
    assert document.markdown_error == "conversion failed"
    assert document.markdown_path is None
    assert sessions[2].added_batches[0][0].content_source == "native_fallback"
    assert sessions[2].added_batches[0][0].locator["source_text"] == "Native source"


def test_process_translates_parser_failure_and_clears_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    document_id, _path, document = _processing_document(tmp_path)
    document.storage_path = str(tmp_path / f'{document_id}.txt')
    monkeypatch.setattr(processing.settings, 'upload_dir', str(tmp_path))
    sessions = [_Session(document)]
    monkeypatch.setattr(processing, "SessionLocal", _SessionFactory(sessions))
    async def parse(*_args):
        raise DocumentParsingError('Пустой файл')
    monkeypatch.setattr(processing, "parse_uploaded", parse)
    processor = DocumentProcessor(SimpleNamespace())
    calls: list[tuple[object, ...]] = []
    async def set_error(*args):
        calls.append(args)
    monkeypatch.setattr(processor, "_set_error", set_error)

    asyncio.run(processor._process(document_id))

    assert calls == [(document_id, "error", "Пустой файл")]
    assert not processor._tasks
