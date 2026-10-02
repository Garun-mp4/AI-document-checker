from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chat, Document, Message
from app.schemas import ChatLibraryPageOut, ChatSettingsOut, ChatSummaryOut

DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


def normalize_search_query(query: str | None) -> str | None:
    normalized = " ".join((query or "").split())
    return normalized or None


def escape_like_query(query: str) -> str:
    """Escape SQL LIKE metacharacters so search terms are treated literally."""

    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _trim_title(value: str) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= 72 else f"{normalized[:69].rstrip()}…"


def _preview(value: str | None) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.split())
    return normalized[:180] if len(normalized) <= 180 else f"{normalized[:180].rstrip()}…"


def _search_snippet(value: str | None, query: str) -> str | None:
    if not value:
        return None
    normalized = " ".join(value.split())
    match_at = normalized.casefold().find(query.casefold())
    if match_at < 0:
        return _preview(normalized)
    if len(normalized) <= 180:
        return normalized
    start = max(0, match_at - 64)
    end = min(len(normalized), start + 180)
    if end - start < 180:
        start = max(0, end - 180)
    snippet = normalized[start:end]
    return f"{'…' if start else ''}{snippet}{'…' if end < len(normalized) else ''}"


def _summary_from_values(
    chat: Any,
    document: Any,
    *,
    message_count: int,
    first_user_message: str | None,
    latest_message_at: datetime | None,
    latest_message_content: str | None,
    last_activity_at: datetime,
    search_query: str | None = None,
    search_message_id: Any = None,
    search_message_content: str | None = None,
) -> ChatSummaryOut:
    custom_title = getattr(chat, "title", None)
    title = _trim_title(custom_title or first_user_message or document.filename)
    return ChatSummaryOut(
        id=str(chat.id),
        document_id=str(document.id),
        title=title,
        custom_title=custom_title,
        pinned=getattr(chat, "pinned_at", None) is not None,
        revision=getattr(chat, "revision", 1),
        filename=document.filename,
        file_type=document.file_type,
        file_size=document.file_size,
        status=document.status,
        error_message=document.error_message,
        chunk_count=document.chunk_count,
        metadata=document.metadata_json or {},
        created_at=chat.created_at,
        last_activity_at=last_activity_at,
        message_count=message_count,
        last_message_at=latest_message_at,
        last_message_preview=_preview(latest_message_content),
        search_snippet=_search_snippet(search_message_content, search_query) if search_query else None,
        search_message_id=str(search_message_id) if search_message_id is not None else None,
    )


def build_chat_summary(chat: Any, document: Any, messages: list[Any]) -> ChatSummaryOut:
    """Build a library row from an already-loaded chat and its messages."""

    first_user_message = next(
        (item.content.strip() for item in messages if item.role == "user" and item.content.strip()),
        None,
    )
    latest_message = max(messages, key=lambda item: (item.created_at, str(getattr(item, "id", ""))), default=None)
    fallback_activity = max(chat.created_at, document.updated_at)
    return _summary_from_values(
        chat,
        document,
        message_count=len(messages),
        first_user_message=first_user_message,
        latest_message_at=latest_message.created_at if latest_message else None,
        latest_message_content=latest_message.content if latest_message else None,
        last_activity_at=latest_message.created_at if latest_message else fallback_activity,
    )


def _summary_query(search_query: str | None):
    latest_ranked = (
        select(
            Message.chat_id.label("chat_id"),
            Message.id.label("message_id"),
            Message.content.label("content"),
            Message.created_at.label("created_at"),
            func.row_number()
            .over(partition_by=Message.chat_id, order_by=(Message.created_at.desc(), Message.id.desc()))
            .label("rank"),
        )
        .cte("chat_latest_ranked")
    )
    latest = select(
        latest_ranked.c.chat_id,
        latest_ranked.c.message_id,
        latest_ranked.c.content,
        latest_ranked.c.created_at,
    ).where(latest_ranked.c.rank == 1).cte("chat_latest")

    counts = (
        select(Message.chat_id.label("chat_id"), func.count(Message.id).label("message_count"))
        .group_by(Message.chat_id)
        .cte("chat_message_counts")
    )
    first_user_ranked = (
        select(
            Message.chat_id.label("chat_id"),
            Message.content.label("content"),
            func.row_number()
            .over(partition_by=Message.chat_id, order_by=(Message.created_at.asc(), Message.id.asc()))
            .label("rank"),
        )
        .where(Message.role == "user", func.length(func.trim(Message.content)) > 0)
        .cte("chat_first_user_ranked")
    )
    first_user = select(first_user_ranked.c.chat_id, first_user_ranked.c.content).where(
        first_user_ranked.c.rank == 1
    ).cte("chat_first_user")

    match = None
    if search_query:
        match_ranked = (
            select(
                Message.chat_id.label("chat_id"),
                Message.id.label("message_id"),
                Message.content.label("content"),
                func.row_number()
                .over(partition_by=Message.chat_id, order_by=(Message.created_at.asc(), Message.id.asc()))
                .label("rank"),
            )
            .where(Message.content.ilike(escape_like_query(search_query), escape="\\"))
            .cte("chat_search_ranked")
        )
        match = select(
            match_ranked.c.chat_id,
            match_ranked.c.message_id,
            match_ranked.c.content,
        ).where(match_ranked.c.rank == 1).cte("chat_search_match")

    activity = func.coalesce(
        latest.c.created_at,
        case((Chat.created_at >= Document.updated_at, Chat.created_at), else_=Document.updated_at),
    )
    statement = (
        select(
            Chat,
            Document,
            activity.label("last_activity_at"),
            counts.c.message_count,
            first_user.c.content.label("first_user_message"),
            latest.c.created_at.label("last_message_at"),
            latest.c.content.label("last_message_content"),
            latest.c.message_id.label("last_message_id"),
            match.c.message_id.label("search_message_id") if match is not None else None,
            match.c.content.label("search_message_content") if match is not None else None,
        )
        .join(Document, Document.id == Chat.document_id)
        .outerjoin(latest, latest.c.chat_id == Chat.id)
        .outerjoin(counts, counts.c.chat_id == Chat.id)
        .outerjoin(first_user, first_user.c.chat_id == Chat.id)
    )
    if match is not None:
        statement = statement.outerjoin(match, match.c.chat_id == Chat.id).where(
            or_(
                Chat.title.ilike(escape_like_query(search_query), escape="\\"),
                Document.filename.ilike(escape_like_query(search_query), escape="\\"),
                match.c.message_id.is_not(None),
            )
        )
    return statement, activity


async def list_chat_library(
    session: AsyncSession,
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int | None = DEFAULT_PAGE_SIZE,
) -> ChatLibraryPageOut:
    """Return paginated chat summaries without materializing message histories."""

    search_query = normalize_search_query(query)
    statement, activity = _summary_query(search_query)
    count_statement = select(func.count()).select_from(Chat).join(Document, Document.id == Chat.document_id)
    if search_query:
        pattern = escape_like_query(search_query)
        matched_message = select(Message.id).where(
            Message.chat_id == Chat.id,
            Message.content.ilike(pattern, escape="\\"),
        ).exists()
        count_statement = count_statement.where(or_(
            Chat.title.ilike(pattern, escape="\\"),
            Document.filename.ilike(pattern, escape="\\"),
            matched_message,
        ))
    total = int(await session.scalar(count_statement) or 0)

    statement = statement.order_by(
        Chat.pinned_at.desc().nullslast(),
        activity.desc(),
        Chat.created_at.desc(),
        Chat.id.asc(),
    ).offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    rows = (await session.execute(statement)).all()
    items = [
        _summary_from_values(
            chat,
            document,
            message_count=int(message_count or 0),
            first_user_message=first_user_message,
            latest_message_at=last_message_at,
            latest_message_content=last_message_content,
            last_activity_at=last_activity_at,
            search_query=search_query,
            search_message_id=search_message_id,
            search_message_content=search_message_content,
        )
        for (
            chat,
            document,
            last_activity_at,
            message_count,
            first_user_message,
            last_message_at,
            last_message_content,
            _last_message_id,
            search_message_id,
            search_message_content,
        ) in rows
    ]
    return ChatLibraryPageOut(
        items=items,
        total=total,
        offset=offset,
        limit=limit or max(total, 1),
        has_more=offset + len(items) < total,
    )


async def get_chat_settings(session: AsyncSession, chat_id: Any) -> ChatSettingsOut | None:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return None
    document = await session.get(Document, chat.document_id)
    if document is None:
        return None
    first_user_message = await session.scalar(
        select(Message.content)
        .where(Message.chat_id == chat.id, Message.role == "user", func.length(func.trim(Message.content)) > 0)
        .order_by(Message.created_at, Message.id)
        .limit(1)
    )
    title = _trim_title(chat.title or first_user_message or document.filename)
    return ChatSettingsOut(
        id=str(chat.id),
        document_id=str(chat.document_id),
        title=title,
        custom_title=chat.title,
        pinned=chat.pinned_at is not None,
        revision=chat.revision,
    )
