from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import api
from app.services.app_help import (
    AppHelpCitation,
    AppHelpResponse,
    search_app_capabilities,
)
from app.services.application_assistant import (
    build_application_assistant_payload,
    classify_assistant_scope,
    format_validated_app_answer,
)
from app.services.chat_library import build_chat_summary


def test_application_intent_routes_to_catalog_without_document_retrieval() -> None:
    question = "Где изменить модель?"
    matches = search_app_capabilities(question)

    assert matches
    assert classify_assistant_scope(question, matches, chat_scope="document") == "application"
    assert classify_assistant_scope("Кратко перескажи документ и где изменить модель?", matches, chat_scope="document") == "mixed"
    assert classify_assistant_scope("Кратко перескажи документ", (), chat_scope="document") == "document"
    assert classify_assistant_scope("Кратко перескажи документ", (), chat_scope="application") == "application"


def test_application_payload_contains_only_catalog_and_active_chat_history() -> None:
    matches = search_app_capabilities("Где изменить модель?")
    history = [{"role": "user", "text": "Как открыть настройки Codex?"}]

    payload = build_application_assistant_payload(
        "Где изменить модель?", matches, previous_messages=history, scope="application",
    )

    assert payload["assistant_mode"] == "application_help"
    assert payload["request_scope"] == "application"
    assert payload["previous_messages"] == history
    assert payload["document_evidence"] == []
    assert payload["application_evidence"]
    assert all(item["source_id"].startswith("app:") for item in payload["application_evidence"])


def test_application_only_payload_rejects_document_evidence() -> None:
    with pytest.raises(ValueError, match="application-only"):
        build_application_assistant_payload(
            "Где изменить модель?", search_app_capabilities("Где изменить модель?"),
            previous_messages=[], scope="application",
            document_evidence=[{"source_id": str(uuid4()), "excerpt": "Секретный текст"}],
        )


def test_server_adds_citation_markers_and_strips_model_supplied_markers() -> None:
    response = AppHelpResponse(
        answer="Откройте настройки модели 〔99〕 [S01].",
        status="answered",
        scope="application",
        citations=[AppHelpCitation(source_type="application", source_id="app:codex-model-preferences")],
        ui_target_id="codex.settings.open",
    )

    assert format_validated_app_answer(response) == "Откройте настройки модели.\n\n〔1〕"


class _NoDocumentAccessSession:
    async def get(self, *_args, **_kwargs):
        raise AssertionError("Application citations must not query document rows.")

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("Application citations must not query chunks.")


def test_application_source_resolution_uses_only_allowlisted_catalog_records() -> None:
    chat = SimpleNamespace(scope="application", document_id=None)
    known = search_app_capabilities("Где изменить модель?")[0].capability
    result = asyncio.run(api._sources_for_chat_ids(
        _NoDocumentAccessSession(), chat,
        [known.source_id, "app:not-a-real-feature", str(uuid4())],
    ))

    assert len(result) == 1
    assert result[0].id == known.source_id
    assert result[0].source_type == "application"
    assert result[0].title == known.title


def test_application_chat_library_summary_does_not_invent_document_metadata() -> None:
    moment = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
    chat = SimpleNamespace(
        id=uuid4(), scope="application", created_at=moment, title=None, pinned_at=None, revision=1,
    )

    summary = build_chat_summary(chat, None, [])

    assert summary.scope == "application"
    assert summary.document_id is None
    assert summary.title == "Помощь по приложению"
    assert summary.filename is None
    assert summary.file_type is None
    assert summary.status is None
    assert summary.chunk_count == 0
