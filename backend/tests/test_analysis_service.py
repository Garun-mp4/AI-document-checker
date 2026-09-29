from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import Document
from app.services import analysis
from app.services.analysis import NO_EVIDENCE, _format_number, analyze_document


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, document):
        self.document = document
        self.insights = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, _document_id):
        return self.document if model is Document else None

    async def execute(self, _statement):
        return _Result([])

    def add_all(self, values):
        self.insights.extend(values)

    async def commit(self):
        return None


class _Codex:
    def __init__(self, response):
        self.response = response
        self.payloads = []

    async def complete(self, payload, _schema):
        self.payloads.append(payload)
        return self.response


def test_format_number_handles_grouping_signs_and_invalid_values() -> None:
    assert _format_number("1234567.50") == "1 234 567,50"
    assert _format_number("-42") == "−42"
    assert _format_number("not-a-number") == "not-a-number"


def _analysis_fixture(monkeypatch: pytest.MonkeyPatch, response: str):
    document_id = uuid4()
    document = SimpleNamespace(
        id=document_id,
        file_type="txt",
        metadata_json={"line_count": 3},
        status="queued",
        error_message="old error",
    )
    source = SimpleNamespace(
        id=uuid4(),
        text="Автор документа — Алексей",
        locator={"label": "Абзац 1", "line_start": 1, "line_end": 1},
    )
    first_session = _Session(document)
    final_session = _Session(document)
    sessions = [first_session, final_session]
    monkeypatch.setattr(analysis, "SessionLocal", lambda: sessions.pop(0))

    async def fake_search(_document_id, _question, limit=4):
        assert limit == 4
        return [source]

    monkeypatch.setattr(analysis, "search_chunks", fake_search)
    return document_id, document, source, _Codex(response), final_session


def test_analyze_document_builds_source_scoped_insights_and_marks_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    response = json.dumps({
        "insights": [{
            "key": "overview",
            "answer": "Документ создан Алексееем [S01].",
            "citations": ["S01", "S99"],
            "not_found": False,
        }],
    })
    document_id, document, source, codex, final_session = _analysis_fixture(monkeypatch, response)

    asyncio.run(analyze_document(document_id, codex))

    assert document.status == "ready"
    assert document.error_message is None
    assert codex.payloads[0]["sources"] == [{
        "label": "S01",
        "location": "Абзац 1",
        "excerpt": source.text,
    }]
    assert len(codex.payloads[0]["questions"]) == 7
    assert len(final_session.insights) == 7
    overview = next(item for item in final_session.insights if item.key == "overview")
    assert overview.answer == "Документ создан Алексееем 〔1〕."
    assert overview.citations == [str(source.id)]
    assert all(item.answer == NO_EVIDENCE for item in final_session.insights if item.key != "overview")


def test_analyze_document_rejects_invalid_model_json(monkeypatch: pytest.MonkeyPatch) -> None:
    document_id, document, _source, codex, _final_session = _analysis_fixture(monkeypatch, "not-json")

    with pytest.raises(RuntimeError, match="некорректный формат"):
        asyncio.run(analyze_document(document_id, codex))

    assert document.status == "analyzing"


def test_analyze_document_fills_missing_answers_with_no_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    document_id = uuid4()
    document = SimpleNamespace(id=document_id, file_type="txt", metadata_json={}, status="queued", error_message=None)
    first = _Session(document)
    final = _Session(document)
    sessions = [first, final]
    monkeypatch.setattr(analysis, "SessionLocal", lambda: sessions.pop(0))
    source = SimpleNamespace(id=uuid4(), text="Подтверждённый фрагмент", locator={"label": "Страница 1"})

    async def fake_search(*_args, **_kwargs):
        return [source]

    monkeypatch.setattr(analysis, "search_chunks", fake_search)
    codex = _Codex(json.dumps({"insights": []}))

    asyncio.run(analyze_document(document_id, codex))

    assert len(final.insights) == 7
    assert all(item.answer == NO_EVIDENCE for item in final.insights)
    assert all(item.citations == [] for item in final.insights)
