from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from starlette.responses import PlainTextResponse

from app import api
from app.main import protect_local_writes
from app.services.codex import CodexUnavailable


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows, document=None):
        self.rows = rows
        self.document = document

    async def execute(self, _statement):
        return _Result(self.rows)

    async def get(self, _model, _document_id):
        return self.document


def test_sse_serializes_unicode_and_keeps_event_framing() -> None:
    payload = api._sse("delta", {"text": "Ответ по-русски"})

    assert payload.startswith("event: delta\ndata: ")
    assert payload.endswith("\n\n")
    assert json.loads(payload.split("data: ", 1)[1]) == {"text": "Ответ по-русски"}


def test_sources_for_ids_ignores_invalid_ids_and_preserves_requested_order() -> None:
    first_id, second_id = uuid4(), uuid4()
    rows = [
        SimpleNamespace(id=first_id, version=3, text="first fallback", locator={"label": "Абзац 1", "source_text": "first source"}, ordinal=1, is_derived=False),
        SimpleNamespace(id=second_id, version=2, text="second", locator={"label": "Абзац 2"}, ordinal=2, is_derived=True),
    ]
    requested = [str(second_id), "not-a-uuid", str(first_id), str(uuid4())]
    document = SimpleNamespace(id=uuid4(), file_type="txt")

    result = asyncio.run(api._sources_for_ids(_Session(rows, document), document.id, requested))

    assert [item.id for item in result] == [str(second_id), str(first_id)]
    assert result[0].text == "second"
    assert result[1].text == "first source"
    assert result[0].is_derived is True
    assert result[0].locator["document_id"] == str(document.id)
    assert result[0].locator["processing_version"] == 2
    assert result[0].locator["source_type"] == "calculation"
    assert result[1].locator["processing_version"] == 3


def test_document_out_exposes_markdown_processing_metadata() -> None:
    now = datetime.now(timezone.utc)
    document = SimpleNamespace(
        id=uuid4(), filename="report.pdf", file_type="pdf", file_size=123,
        status="ready", error_message=None, chunk_count=4, metadata_json={"page_count": 2},
        markdown_status="ready", analysis_source="markitdown", markdown_error=None,
        markdown_converter_version="0.1.8", markdown_char_count=1200, markdown_line_count=80,
        markdown_checksum="a" * 64, markdown_mapping_json={"exact": 4},
        ocr_status="ready", ocr_language="rus+eng", ocr_page_count=2,
        ocr_confidence=91.5, ocr_error=None, ocr_engine_version="tesseract-5.5.0", ocr_char_count=480,
        created_at=now, updated_at=now,
    )

    result = api._document_out(document)

    assert result.id == str(document.id)
    assert result.markdown_status == "ready"
    assert result.analysis_source == "markitdown"
    assert result.markdown_mapping == {"exact": 4}
    assert result.ocr_status == "ready"
    assert result.ocr_confidence == 91.5

    document.ocr_status = "partial"
    document.ocr_error = "Не удалось распознать текст на страницах: 3."
    partial_result = api._document_out(document)
    assert partial_result.ocr_status == "partial"
    assert partial_result.ocr_error.endswith("3.")


def test_document_media_type_is_stable_for_all_supported_formats() -> None:
    expected = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
        "md": "text/markdown",
        "csv": "text/csv",
        "xml": "application/xml",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xls": "application/vnd.ms-excel",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "html": "text/html",
        "htm": "text/html",
        "json": "application/json",
        "epub": "application/epub+zip",
    }

    for extension, media_type in expected.items():
        assert api.document_media_type(extension, f"sample.{extension}") == media_type


def test_codex_error_message_hides_unexpected_internal_details() -> None:
    assert api.codex_error_message(CodexUnavailable("Сервис недоступен")) == "Сервис недоступен"
    assert "Не удалось получить ответ" in api.codex_error_message(RuntimeError("secret stack detail"))
    assert "secret stack detail" not in api.codex_error_message(RuntimeError("secret stack detail"))


def test_local_write_middleware_rejects_foreign_origin() -> None:
    request = SimpleNamespace(method="POST", headers={"origin": "https://evil.example"})

    async def call_next(_request):
        return PlainTextResponse("ok")

    response = asyncio.run(protect_local_writes(request, call_next))

    assert response.status_code == 403
    assert json.loads(response.body) == {"detail": "Запрос разрешён только из локального интерфейса."}


def test_local_write_middleware_allows_local_origin_and_read_requests() -> None:
    calls: list[object] = []

    async def call_next(request):
        calls.append(request)
        return PlainTextResponse("ok")

    local = SimpleNamespace(method="POST", headers={"origin": "http://localhost:5173"})
    read = SimpleNamespace(method="GET", headers={"origin": "https://evil.example"})

    assert asyncio.run(protect_local_writes(local, call_next)).status_code == 200
    assert asyncio.run(protect_local_writes(read, call_next)).status_code == 200
    assert calls == [local, read]


def test_local_write_origins_can_isolate_an_explicit_test_port(monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "local_ui_origins", ["http://localhost:5174"])

    async def call_next(request):
        return PlainTextResponse("ok")

    for origin, status in [("http://localhost:5174", 200), ("http://localhost:5173", 403),
                           ("https://evil.example", 403)]:
        request = SimpleNamespace(method="POST", headers={"origin": origin})
        assert asyncio.run(protect_local_writes(request, call_next)).status_code == status
