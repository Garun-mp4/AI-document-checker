from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import api
from app.services.app_help import (
    AppHelpAvailableSource,
    AppHelpCitation,
    AppHelpResponse,
    search_app_capabilities,
    validate_app_help_response,
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


def test_application_response_rejects_unknown_ui_targets() -> None:
    capability = search_app_capabilities("Где изменить модель?")[0].capability
    sources = {capability.source_id: AppHelpAvailableSource(
        source_type="application", ui_target_ids=capability.ui_target_ids,
    )}
    payload = {
        "answer": "Откройте настройки.",
        "status": "answered",
        "scope": "application",
        "citations": [{"source_type": "application", "source_id": capability.source_id}],
        "ui_target_id": "document.upload.open;document.cookie",
    }

    with pytest.raises(ValueError, match="неизвестный UI target"):
        validate_app_help_response(payload, available_sources=sources)


def test_application_response_target_must_belong_to_a_cited_capability() -> None:
    capability = search_app_capabilities("Где изменить модель?")[0].capability
    sources = {capability.source_id: AppHelpAvailableSource(
        source_type="application", ui_target_ids=capability.ui_target_ids,
    )}
    payload = {
        "answer": "Откройте настройки.",
        "status": "answered",
        "scope": "application",
        "citations": [{"source_type": "application", "source_id": capability.source_id}],
        "ui_target_id": "document.upload.open",
    }

    with pytest.raises(ValueError, match="не подтверждён"):
        validate_app_help_response(payload, available_sources=sources)


def test_message_api_roundtrips_saved_ui_target_version_metadata() -> None:
    now = datetime.now(timezone.utc)
    chat = SimpleNamespace(scope="application", document_id=None)
    message = SimpleNamespace(
        id=uuid4(), role="assistant", content="Откройте настройки.", citations=[], model="gpt-6-luna",
        reasoning_effort="low", created_at=now, context_epoch=0, reply_to_message_id=None,
        generation_status="complete", generation_error=None, source_version=None,
        ui_target_id="codex.settings.open", ui_target_catalog_version="1", ui_target_build_id="build-123",
    )

    result = asyncio.run(api._message_out(_NoDocumentAccessSession(), chat, message))

    assert result.model_dump(include={"ui_target_id", "ui_target_catalog_version", "ui_target_build_id"}) == {
        "ui_target_id": "codex.settings.open",
        "ui_target_catalog_version": "1",
        "ui_target_build_id": "build-123",
    }


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
