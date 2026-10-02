from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import Document
from app.services import additional_analysis, analysis
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
        self.preferences = []

    async def complete(self, payload, _schema, **preferences):
        self.payloads.append(payload)
        self.preferences.append(preferences)
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

    async def fake_search(_document_id, _question, limit=4, *, version):
        assert limit == 4
        assert version == 3
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
    document_id, document, source, codex, _final_session = _analysis_fixture(monkeypatch, response)

    insights = asyncio.run(analyze_document(document_id, codex, version=3,
        snapshot={"file_type":"txt", "metadata_json":{"line_count":3}}))

    # Preparing answers never mutates the active version; the worker activates atomically.
    assert document.status == "queued"
    assert document.error_message == "old error"
    assert codex.payloads[0]["sources"] == [{
        "label": "S01",
        "location": "Абзац 1",
        "excerpt": source.text,
    }]
    assert len(codex.payloads[0]["questions"]) == 7
    assert len(insights) == 7
    overview = next(item for item in insights if item.key == "overview")
    assert overview.answer == "Документ создан Алексееем 〔1〕."
    assert overview.citations == [str(source.id)]
    assert all(item.answer == NO_EVIDENCE for item in insights if item.key != "overview")


def test_analyze_document_rejects_invalid_model_json(monkeypatch: pytest.MonkeyPatch) -> None:
    document_id, document, _source, codex, _final_session = _analysis_fixture(monkeypatch, "not-json")

    with pytest.raises(RuntimeError, match="некорректный формат"):
        asyncio.run(analyze_document(document_id, codex, version=3,
        snapshot={"file_type":"txt", "metadata_json":{"line_count":3}}))

    assert document.status == "queued"


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

    insights = asyncio.run(analyze_document(document_id, codex, version=3,
        snapshot={"file_type":"txt", "metadata_json":{"line_count":3}}))

    assert len(insights) == 7
    assert all(item.answer == NO_EVIDENCE for item in insights)
    assert all(item.citations == [] for item in insights)


def test_additional_analysis_persists_only_sources_from_the_selected_document_version(monkeypatch: pytest.MonkeyPatch) -> None:
    source_id = uuid4()
    source = SimpleNamespace(id=source_id, text="Выполнить проверку до 15 ноября.", locator={"label": "Абзац 4"})
    seen = {}

    async def fake_search(document_id, query, *, limit, version):
        seen.update(document_id=document_id, query=query, limit=limit, version=version)
        return [source]

    monkeypatch.setattr(additional_analysis, "search_chunks", fake_search)
    codex = _Codex(json.dumps({"insights": [{
        "key": "tasks", "answer": "Проверить документ до 15 ноября [S01].",
        "citations": ["S01", "S99"], "not_found": False,
    }]}))
    document_id = uuid4()

    result = asyncio.run(additional_analysis.analyze_additional(
        document_id, codex, source_version=9, mode="tasks", model="gpt-6-luna", reasoning_effort="medium",
    ))

    assert seen == {"document_id": document_id, "query": additional_analysis.MODE_QUESTIONS["tasks"][1], "limit": 8, "version": 9}
    assert codex.payloads[0]["questions"][0]["available_sources"] == ["S01"]
    assert "исполнителя и срок" in codex.payloads[0]["purpose"]
    assert "не указан" in codex.payloads[0]["purpose"]
    assert result.answer == "Проверить документ до 15 ноября 〔1〕."
    assert result.citations == [str(source_id)]
    assert codex.preferences == [{"model": "gpt-6-luna", "reasoning_effort": "medium"}]


def test_additional_risk_mode_keeps_interpretations_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=uuid4(), text="Срок не указан.", locator={"label": "Абзац 2"})

    async def fake_search(*_args, **_kwargs):
        return [source]

    monkeypatch.setattr(additional_analysis, "search_chunks", fake_search)
    codex = _Codex(json.dumps({"insights": [{
        "key": "risks", "answer": "Не указан срок выполнения [S01].",
        "citations": ["S01"], "not_found": False,
    }]}))

    result = asyncio.run(additional_analysis.analyze_additional(
        uuid4(), codex, source_version=2, mode="risks", model="gpt-6.1-sol", reasoning_effort="high",
    ))

    assert "интерпретацию" in codex.payloads[0]["purpose"]
    assert result.answer.endswith("〔1〕.")


def test_additional_analysis_without_sources_does_not_call_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(additional_analysis, "search_chunks", fake_search)
    codex = _Codex("should not be parsed")

    result = asyncio.run(additional_analysis.analyze_additional(
        uuid4(), codex, source_version=1, mode="brief", model="gpt-6-luna", reasoning_effort="low",
    ))

    assert result.answer == NO_EVIDENCE
    assert result.citations == []
    assert codex.payloads == []
