from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.chat_library import build_chat_summary


def test_chat_summary_uses_question_title_and_latest_message() -> None:
    created_at = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
    first_message_at = datetime(2026, 9, 28, 11, tzinfo=timezone.utc)
    latest_message_at = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
    chat = SimpleNamespace(id="chat-1", created_at=created_at)
    document = SimpleNamespace(
        id="document-1",
        filename="research.pdf",
        file_type="pdf",
        file_size=123,
        status="ready",
        error_message=None,
        chunk_count=4,
        metadata_json={"page_count": 2},
        updated_at=created_at,
    )
    messages = [
        SimpleNamespace(role="user", content="Какие сроки указаны?", created_at=first_message_at),
        SimpleNamespace(role="assistant", content="Срок указан до пятницы.", created_at=latest_message_at),
    ]

    summary = build_chat_summary(chat, document, messages)

    assert summary.id == "chat-1"
    assert summary.document_id == "document-1"
    assert summary.title == "Какие сроки указаны?"
    assert summary.message_count == 2
    assert summary.last_message_at == latest_message_at
    assert summary.last_activity_at == latest_message_at
    assert summary.last_message_preview == "Срок указан до пятницы."


def test_chat_summary_falls_back_to_filename_without_messages() -> None:
    moment = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
    chat = SimpleNamespace(id="chat-1", created_at=moment)
    document = SimpleNamespace(
        id="document-1",
        filename="notes.txt",
        file_type="txt",
        file_size=123,
        status="queued",
        error_message=None,
        chunk_count=0,
        metadata_json={},
        updated_at=moment,
    )

    summary = build_chat_summary(chat, document, [])

    assert summary.title == "notes.txt"
    assert summary.message_count == 0
    assert summary.last_message_at is None
    assert summary.last_message_preview is None
    assert summary.last_activity_at == moment
