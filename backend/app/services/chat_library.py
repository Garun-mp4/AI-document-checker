from __future__ import annotations

from typing import Any

from app.schemas import ChatSummaryOut


def build_chat_summary(chat: Any, document: Any, messages: list[Any]) -> ChatSummaryOut:
    """Build a ChatGPT-like library row from durable document and messages."""

    first_user_message = next((item for item in messages if item.role == "user" and item.content.strip()), None)
    title = first_user_message.content.strip() if first_user_message else document.filename
    if len(title) > 72:
        title = f"{title[:69].rstrip()}…"
    latest_message = messages[-1] if messages else None
    last_activity_at = latest_message.created_at if latest_message else max(chat.created_at, document.updated_at)
    preview = None
    if latest_message:
        normalized = " ".join(latest_message.content.split())
        preview = normalized[:180]
        if len(normalized) > 180:
            preview = f"{preview.rstrip()}…"
    return ChatSummaryOut(
        id=str(chat.id),
        document_id=str(document.id),
        title=title,
        filename=document.filename,
        file_type=document.file_type,
        file_size=document.file_size,
        status=document.status,
        error_message=document.error_message,
        chunk_count=document.chunk_count,
        metadata=document.metadata_json or {},
        created_at=chat.created_at,
        last_activity_at=last_activity_at,
        message_count=len(messages),
        last_message_at=latest_message.created_at if latest_message else None,
        last_message_preview=preview,
    )
