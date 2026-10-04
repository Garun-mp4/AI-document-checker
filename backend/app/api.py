from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models import (
    AdditionalAnalysis,
    Chat,
    ChatDocument,
    Chunk,
    Document,
    DocumentBookmark,
    DocumentVersion,
    Insight,
    Message,
    ProcessingJob,
)
from app.schemas import (
    AdditionalAnalysisCreateIn,
    AdditionalAnalysisOut,
    AppVersionOut,
    ChatComparisonCreateIn,
    ChatComparisonUpdateIn,
    ChatDocumentOut,
    ChatLibraryPageOut,
    ChatOut,
    ChatSettingsOut,
    ChatSummaryOut,
    ChatUpdateIn,
    CodexPreferencesIn,
    DeleteMessagesOut,
    DocumentAnalysisVersionOut,
    DocumentBookmarkCreateIn,
    DocumentBookmarkOut,
    DocumentBookmarkUpdateIn,
    DocumentExportIn,
    DocumentOut,
    DocumentPreviewOut,
    DocumentSearchOut,
    InsightOut,
    MarkdownOut,
    MessageOut,
    OcrReprocessIn,
    ProcessingJobOut,
    ReanalyzeIn,
    SendMessageIn,
    SourceOut,
    StartChatContextOut,
    TableCalculationIn,
    TableCalculationOut,
    TablePreviewOut,
)
from app.services.additional_analysis import analyze_additional
from app.services.app_help import (
    APP_HELP_CATALOG,
    APP_HELP_CATALOG_VERSION,
    AppHelpAvailableSource,
    AppHelpResponse,
    app_help_output_schema,
    search_app_capabilities,
    validate_app_help_response,
)
from app.services.application_assistant import (
    APP_ASSISTANT_INSTRUCTIONS,
    build_application_assistant_payload,
    classify_assistant_scope,
    format_validated_app_answer,
)
from app.services.artifact_response import artifact_response
from app.services.chat_context import bounded_history
from app.services.chat_library import get_chat_settings
from app.services.chat_library import list_chat_library as query_chat_library
from app.services.citations import format_source_markers
from app.services.codex import (
    CodexModelUnavailable,
    CodexNeedsLogin,
    CodexPreferenceError,
    CodexUnavailable,
)
from app.services.comparison import (
    COMPARISON_INSTRUCTIONS,
    PER_DOCUMENT_RETRIEVAL_LIMIT,
    ComparisonDocumentFinding,
    ComparisonResponse,
    ComparisonSynthesis,
    citation_snapshot,
    comparison_output_schema,
    interleave_document_results,
    render_comparison_answer,
    validate_comparison_response,
)
from app.services.document_export import (
    MAX_EXPORT_SOURCES,
    ExportAdditionalAnalysis,
    ExportAnswer,
    ExportError,
    ExportMessage,
    ExportSnapshot,
    ExportSource,
    render_export,
)
from app.services.document_export import (
    safe_filename as export_filename,
)
from app.services.document_formats import PREFERRED_MIME_TYPES
from app.services.document_search import (
    original_search_source_cache,
    search_markdown,
    search_source_blocks,
)
from app.services.document_security import (
    download_name,
    owned_storage,
    read_storage,
    remove_storage,
    validate_mime,
)
from app.services.isolated_documents import run_document_operation
from app.services.job_queue import active_chunk_version, cancel, enqueue
from app.services.maintenance import delete_documents
from app.services.parsing import (
    SUPPORTED_EXTENSIONS,
    DocumentParsingError,
    _decode_text_with_encoding,
    safe_filename,
)
from app.services.preview import MAX_PREVIEW_BLOCKS, build_preview
from app.services.preview_cache import (
    preview_cache_key,
    preview_response_cache,
    table_preview_cache_key,
    table_response_cache,
    table_search_cache_key,
    table_search_response_cache,
)
from app.services.retrieval import search_chunks
from app.services.source_locators import versioned_source_locator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


def _reject_during_cache_cleanup(request: Request) -> None:
    if getattr(request.app.state, "maintenance_cache_cleanup", False):
        raise HTTPException(status_code=409, detail="Выполняется очистка кэша. Повторите запрос через несколько секунд.")
    if getattr(request.app.state, "maintenance_delete_all", False):
        raise HTTPException(status_code=409, detail="Выполняется очистка библиотеки. Повторите запрос после её завершения.")


def document_media_type(file_type: str, filename: str) -> str:
    """Return the preferred browser MIME type from the upload capability contract."""

    normalized = file_type.casefold().lstrip(".")
    return PREFERRED_MIME_TYPES.get(f".{normalized}") or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        active_version=getattr(document, "active_version", 0),
        id=str(document.id),
        filename=document.filename,
        file_type=document.file_type,
        file_size=document.file_size,
        status=document.status,
        error_message=document.error_message,
        chunk_count=document.chunk_count,
        metadata=document.metadata_json or {},
        markdown_status=document.markdown_status,
        analysis_source=document.analysis_source,
        markdown_error=document.markdown_error,
        markdown_converter_version=document.markdown_converter_version,
        markdown_char_count=document.markdown_char_count,
        markdown_line_count=document.markdown_line_count,
        markdown_checksum=document.markdown_checksum,
        markdown_mapping=document.markdown_mapping_json or {},
        ocr_status=getattr(document, "ocr_status", "not_needed"),
        ocr_language=getattr(document, "ocr_language", None),
        ocr_page_count=getattr(document, "ocr_page_count", None),
        ocr_confidence=getattr(document, "ocr_confidence", None),
        ocr_error=getattr(document, "ocr_error", None),
        ocr_engine_version=getattr(document, "ocr_engine_version", None),
        ocr_char_count=getattr(document, "ocr_char_count", 0),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


async def _sources_for_ids(session, document_id: uuid.UUID, ids: list[str]) -> list[SourceOut]:
    parsed_ids: list[uuid.UUID] = []
    for value in ids:
        try:
            parsed_ids.append(uuid.UUID(value))
        except (ValueError, TypeError):
            continue
    if not parsed_ids:
        return []
    document = await session.get(Document, document_id)
    if document is None:
        return []
    rows = (await session.execute(
        select(Chunk).where(Chunk.document_id == document_id, Chunk.id.in_(parsed_ids))
    )).scalars().all()
    by_id = {str(chunk.id): chunk for chunk in rows}
    return [
        SourceOut(id=value, text=str((by_id[value].locator or {}).get("source_text") or by_id[value].text)[:2_500], locator=versioned_source_locator(
                  by_id[value].locator, document_id=str(document.id), processing_version=by_id[value].version,
                  file_type=document.file_type, is_derived=by_id[value].is_derived),
                  ordinal=by_id[value].ordinal, is_derived=by_id[value].is_derived)
        for value in ids if value in by_id
    ]


async def _sources_for_chat_ids(session, chat: Chat, ids: list[str]) -> list[SourceOut]:
    """Resolve citations only inside this chat's document scope and trusted app catalog."""

    app_by_id = {entry.source_id: entry for entry in APP_HELP_CATALOG}
    requested_app = {
        value: app_by_id[value]
        for value in ids
        if value in app_by_id and chat.scope in {"application", "document"}
    }
    document_ids = [value for value in ids if value not in app_by_id]
    if chat.scope == "comparison":
        return await _comparison_sources_for_chat(session, chat, document_ids)
    document_sources = (
        await _sources_for_ids(session, chat.document_id, document_ids)
        if chat.document_id is not None and document_ids
        else []
    )
    document_by_id = {item.id: item for item in document_sources}
    resolved: list[SourceOut] = []
    for value in ids:
        if value in requested_app:
            capability = requested_app[value]
            resolved.append(SourceOut(
                id=value,
                text=capability.evidence_text[:2_500],
                locator={},
                ordinal=0,
                is_derived=False,
                source_type="application",
                title=capability.title,
            ))
        elif value in document_by_id:
            resolved.append(document_by_id[value])
    return resolved


async def _comparison_sources_for_chat(session, chat: Chat, ids: list[str]) -> list[SourceOut]:
    requested: list[uuid.UUID] = []
    for value in ids:
        try:
            requested.append(uuid.UUID(value))
        except (TypeError, ValueError):
            continue
    # This helper only resolves sources tied to a document historically linked
    # to this chat, so citation IDs cannot widen retrieval to the whole library.
    if not requested:
        return []
    linked_document_ids = (await session.execute(
        select(ChatDocument.document_id).where(ChatDocument.chat_id == chat.id)
    )).scalars().all()
    if not linked_document_ids:
        return []
    rows = (await session.execute(
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(requested), Chunk.document_id.in_(linked_document_ids))
    )).all()
    chunks_by_id = {str(chunk.id): (chunk, document) for chunk, document in rows}
    return [
        SourceOut(
            id=value,
            text=str((chunk.locator or {}).get("source_text") or chunk.text)[:2_500],
            locator=versioned_source_locator(
                chunk.locator, document_id=str(document.id), processing_version=chunk.version,
                file_type=document.file_type, is_derived=chunk.is_derived,
            ),
            ordinal=chunk.ordinal,
            is_derived=chunk.is_derived,
            document_id=str(document.id),
            document_filename=document.filename,
            source_version=chunk.version,
        )
        for value in ids
        if value in chunks_by_id
        for chunk, document in [chunks_by_id[value]]
    ]


async def _message_out(session, chat: Chat, message: Message) -> MessageOut:
    citations = await _sources_for_chat_ids(session, chat, message.citations or [])
    if chat.scope == "comparison":
        snapshot_by_id = {
            str(item.get("source_id")): item
            for item in (message.citation_snapshots or [])
            if isinstance(item, dict)
        }
        resolved = {item.id for item in citations}
        for snapshot in message.citation_snapshots or []:
            source_id = str(snapshot.get("source_id", ""))
            if not source_id or source_id in resolved or source_id not in (message.citations or []):
                continue
            locator = snapshot.get("locator") if isinstance(snapshot.get("locator"), dict) else {}
            citations.append(SourceOut(
                id=source_id,
                text="",
                locator=locator,
                ordinal=int(snapshot.get("ordinal", 0)),
                is_derived=bool(snapshot.get("is_derived", False)),
                document_id=str(snapshot.get("document_id", "")) or None,
                document_filename=str(snapshot.get("document_filename", "")) or None,
                source_version=int(snapshot.get("source_version", 0)) or None,
                available=False,
            ))
        for citation in citations:
            snapshot = snapshot_by_id.get(citation.id)
            if snapshot:
                citation.document_filename = str(snapshot.get("document_filename", "")) or citation.document_filename
                citation.source_version = int(snapshot.get("source_version", 0)) or citation.source_version
        citations.sort(key=lambda item: (message.citations or []).index(item.id) if item.id in (message.citations or []) else len(message.citations or []))
    return MessageOut(
        id=str(message.id), role=message.role, content=message.content,
        citations=citations,
        model=message.model, reasoning_effort=message.reasoning_effort,
        created_at=message.created_at,
        context_epoch=message.context_epoch,
        reply_to_message_id=str(message.reply_to_message_id) if message.reply_to_message_id else None,
        generation_status=message.generation_status,
        generation_error=message.generation_error,
        source_version=message.source_version,
        ui_target_id=message.ui_target_id,
        ui_target_catalog_version=message.ui_target_catalog_version,
        ui_target_build_id=message.ui_target_build_id,
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/codex/status")
async def codex_status(request: Request) -> dict[str, Any]:
    return await request.app.state.codex.status()


@router.post("/codex/preferences")
async def codex_preferences(body: CodexPreferencesIn, request: Request) -> dict[str, Any]:
    try:
        return await request.app.state.codex.set_preferences(body.model, body.reasoning_effort)
    except CodexNeedsLogin as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodexPreferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/codex/login/device-code")
async def codex_device_login(request: Request) -> dict[str, Any]:
    try:
        return await request.app.state.codex.begin_device_login()
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents() -> list[DocumentOut]:
    async with SessionLocal() as session:
        documents = (await session.execute(select(Document).order_by(Document.created_at.desc()))).scalars().all()
        return [_document_out(document) for document in documents]


@router.get("/chats", response_model=list[ChatSummaryOut])
async def list_chat_library() -> list[ChatSummaryOut]:
    """Compatibility list for API clients; summaries never load full histories."""
    async with SessionLocal() as session:
        page = await query_chat_library(session, limit=None)
        return page.items


@router.get("/chats/library", response_model=ChatLibraryPageOut)
async def paginated_chat_library(
    q: Annotated[str | None, Query(max_length=120)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ChatLibraryPageOut:
    """Search and page through chat summaries without fetching message bodies."""
    async with SessionLocal() as session:
        return await query_chat_library(session, query=q, offset=offset, limit=limit)


@router.post("/chats/application", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
async def create_application_chat(request: Request) -> ChatOut:
    """Create a durable app-help session that is not linked to a document."""

    _reject_during_cache_cleanup(request)
    async with SessionLocal() as session, session.begin():
        chat = Chat(scope="application", document_id=None)
        session.add(chat)
        await session.flush()
        return ChatOut(id=str(chat.id), scope="application", document_id=None, context_epoch=chat.context_epoch)


async def _resolve_ready_comparison_sources(session, document_ids: list[uuid.UUID]) -> list[tuple[Document, int]]:
    if not 2 <= len(document_ids) <= 5 or len(document_ids) != len(set(document_ids)):
        raise HTTPException(status_code=422, detail="Выберите от двух до пяти разных документов.")
    documents = (await session.execute(
        select(Document).where(Document.id.in_(document_ids))
    )).scalars().all()
    by_id = {document.id: document for document in documents}
    if len(by_id) != len(document_ids):
        raise HTTPException(status_code=404, detail="Один из выбранных документов больше не существует.")
    result: list[tuple[Document, int]] = []
    for document_id in document_ids:
        document = by_id[document_id]
        if document.status != "ready":
            raise HTTPException(status_code=409, detail=f"Документ «{document.filename}» ещё не готов к сравнению.")
        version = await session.get(DocumentVersion, (document.id, document.active_version))
        if version is None or version.state != "ready" or version.chunk_version < 1:
            raise HTTPException(status_code=409, detail=f"Для документа «{document.filename}» нет готовой версии источников.")
        result.append((document, version.chunk_version))
    return result


def _comparison_document_out(document: Document, source_version: int) -> ChatDocumentOut:
    return ChatDocumentOut(
        id=str(document.id), filename=document.filename, file_type=document.file_type,
        status=document.status, source_version=source_version,
    )


@router.post("/chats/comparison", response_model=ChatOut, status_code=status.HTTP_201_CREATED)
async def create_comparison_chat(body: ChatComparisonCreateIn, request: Request) -> ChatOut:
    """Create a conversation whose retrieval scope is exactly the supplied ready documents."""

    _reject_during_cache_cleanup(request)
    async with SessionLocal() as session, session.begin():
        sources = await _resolve_ready_comparison_sources(session, body.document_ids)
        chat = Chat(scope="comparison", document_id=None)
        session.add(chat)
        await session.flush()
        for position, (document, source_version) in enumerate(sources):
            session.add(ChatDocument(
                chat_id=chat.id,
                document_id=document.id,
                position=position,
                source_version=source_version,
                is_selected=True,
            ))
        await session.flush()
        return ChatOut(
            id=str(chat.id), scope="comparison", document_id=None,
            context_epoch=chat.context_epoch, revision=chat.revision,
            documents=[_comparison_document_out(document, source_version) for document, source_version in sources],
        )


@router.patch("/chats/{chat_id}/documents", response_model=ChatSettingsOut)
async def update_comparison_documents(
    chat_id: uuid.UUID,
    body: ChatComparisonUpdateIn,
    request: Request,
) -> ChatSettingsOut:
    """Replace the explicit source set and start a clean model context."""

    _reject_during_cache_cleanup(request)
    tasks = _chat_generation_tasks(request)
    async with SessionLocal() as session, session.begin():
        chat = (await session.execute(select(Chat).where(Chat.id == chat_id).with_for_update())).scalar_one_or_none()
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        if chat.scope != "comparison" or chat.document_id is not None:
            raise HTTPException(status_code=409, detail="Изменять набор источников можно только в чате сравнения.")
        if chat.revision != body.expected_revision:
            raise HTTPException(status_code=409, detail="Состав чата изменился в другой вкладке. Обновите чат и повторите действие.")
        active = (await session.execute(select(Message).where(
            Message.chat_id == chat_id,
            Message.generation_status == "streaming",
        ).with_for_update())).scalars().all()
        if any(str(message.id) in tasks and not tasks[str(message.id)].done() for message in active):
            raise HTTPException(status_code=409, detail="Сначала остановите текущий ответ, затем меняйте документы.")
        for message in active:
            message.generation_status = "interrupted"
            message.generation_error = "Набор источников изменён. Сохранённый фрагмент можно повторить в новом контексте."
        sources = await _resolve_ready_comparison_sources(session, body.document_ids)
        existing = (await session.execute(select(ChatDocument).where(ChatDocument.chat_id == chat_id).with_for_update())).scalars().all()
        by_document = {link.document_id: link for link in existing}
        for link in existing:
            link.is_selected = False
        for position, (document, source_version) in enumerate(sources):
            link = by_document.get(document.id)
            if link is None:
                link = ChatDocument(chat_id=chat.id, document_id=document.id)
                session.add(link)
            link.position = position
            link.source_version = source_version
            link.is_selected = True
            link.selected_at = datetime.now(timezone.utc)
        chat.context_epoch += 1
        chat.revision += 1
        chat.codex_thread_id = None
    async with SessionLocal() as session:
        result = await get_chat_settings(session, chat_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        return result


@router.delete("/chats/{chat_id}")
async def delete_application_chat(chat_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Delete a standalone application-help or comparison conversation."""

    _reject_during_cache_cleanup(request)
    deleting = getattr(request.app.state, "maintenance_deleting_chats", None)
    if deleting is None:
        deleting = set()
        request.app.state.maintenance_deleting_chats = deleting
    key = str(chat_id)
    if key in deleting:
        raise HTTPException(status_code=409, detail="Удаление этого чата уже выполняется.")
    async with SessionLocal() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        if chat.scope not in {"application", "comparison"} or chat.document_id is not None:
            raise HTTPException(status_code=409, detail="Этот чат удаляется вместе со связанным документом.")

    deleting.add(key)
    cancelled = 0
    try:
        from app.services.maintenance import _active_chat_tasks

        cancelled = await _active_chat_tasks(request, [key])
        async with SessionLocal() as session, session.begin():
            chat = (await session.execute(select(Chat).where(Chat.id == chat_id).with_for_update())).scalar_one_or_none()
            if chat is None:
                raise HTTPException(status_code=404, detail="Чат не найден.")
            if chat.scope not in {"application", "comparison"} or chat.document_id is not None:
                raise HTTPException(status_code=409, detail="Область чата изменилась; он не был удалён.")
            await session.delete(chat)
        return {"id": key, "deleted": True, "cancelled_generations": cancelled}
    finally:
        deleting.discard(key)


@router.get("/chats/{chat_id}", response_model=ChatSettingsOut)
async def read_chat_settings(chat_id: uuid.UUID) -> ChatSettingsOut:
    async with SessionLocal() as session:
        result = await get_chat_settings(session, chat_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        return result


@router.patch("/chats/{chat_id}", response_model=ChatSettingsOut)
async def update_chat_settings(chat_id: uuid.UUID, body: ChatUpdateIn) -> ChatSettingsOut:
    changes: dict[str, Any] = {"revision": Chat.revision + 1}
    if "title" in body.model_fields_set:
        changes["title"] = body.title
    if "pinned" in body.model_fields_set and body.pinned is not None:
        changes["pinned_at"] = (
            case((Chat.pinned_at.is_(None), datetime.now(timezone.utc)), else_=Chat.pinned_at)
            if body.pinned
            else None
        )

    async with SessionLocal() as session:
        changed_id = await session.scalar(
            update(Chat)
            .where(Chat.id == chat_id, Chat.revision == body.expected_revision)
            .values(**changes)
            .returning(Chat.id)
        )
        if changed_id is None:
            if await session.get(Chat, chat_id) is None:
                raise HTTPException(status_code=404, detail="Чат не найден.")
            raise HTTPException(status_code=409, detail="Чат изменился в другой вкладке. Обновите библиотеку и повторите действие.")
        await session.commit()
        result = await get_chat_settings(session, chat_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        return result


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(request: Request, file: Annotated[UploadFile, File()]) -> DocumentOut:
    _reject_during_cache_cleanup(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="У файла отсутствует имя.")
    try:
        filename = safe_filename(file.filename)
    except DocumentParsingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Формат не поддерживается. Допустимы: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.")

    try:
        validate_mime(filename, file.content_type)
    except DocumentParsingError as exc:
        await file.close()
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    document_id = uuid.uuid4()
    root = Path(settings.upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    storage_path = root / f"{document_id}{extension}"
    committed = False
    created = False
    try:
        size = 0
        digest = hashlib.sha256()
        fd = os.open(storage_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        created = True
        with os.fdopen(fd, 'wb') as destination:
            while piece := await file.read(1024 * 1024):
                size += len(piece)
                if size > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail='Файл превышает максимальный размер загрузки.')
                digest.update(piece)
                destination.write(piece)
        if not size:
            raise HTTPException(status_code=400, detail='Файл пустой.')
        await run_document_operation('validate', storage_path, filename=filename)
        _reject_during_cache_cleanup(request)
        document = Document(
            id=document_id,
            filename=filename,
            storage_path=str(storage_path),
            file_type=extension.removeprefix("."),
            file_size=size,
            status="queued",
            metadata_json={},
            input_checksum=digest.hexdigest(),
            active_version=0, next_version=1,
        )
        async with SessionLocal() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock(73402105)"))
            _reject_during_cache_cleanup(request)
            session.add(document)
            # Create the durable conversation before processing starts so a
            # queued or failed upload is still visible in the chat library.
            session.add(Chat(document_id=document_id))
            await enqueue(session, document)
            commit_task = asyncio.create_task(session.commit())
            try:
                await asyncio.shield(commit_task)
            except asyncio.CancelledError:
                await commit_task
                committed = True
                raise
            committed = True
            await session.refresh(document)
        return _document_out(document)
    except DocumentParsingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Could not persist upload (%s)', type(exc).__name__, exc_info=False)
        raise HTTPException(status_code=500, detail='Не удалось сохранить файл локально.') from None
    finally:
        await file.close()
        if created and not committed:
            try:
                remove_storage(storage_path)
            except (OSError, DocumentParsingError):
                logger.warning('Could not clean incomplete upload')


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID) -> DocumentOut:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        return _document_out(document)


@router.post("/documents/{document_id}/retry", response_model=DocumentOut, status_code=202)
async def retry_document(document_id: uuid.UUID, request: Request, operation: str = "retry") -> DocumentOut:
    _reject_during_cache_cleanup(request)
    try:
        if operation not in {"retry", "analysis", "process"}:
            raise HTTPException(status_code=422, detail="Неизвестная операция.")
        await request.app.state.processor.retry(document_id, operation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Документ не найден.") from exc
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        return _document_out(document)


@router.post("/documents/{document_id}/ocr/reprocess", response_model=DocumentOut, status_code=202)
async def reprocess_document_ocr(
    document_id: uuid.UUID,
    body: OcrReprocessIn,
    request: Request,
) -> DocumentOut:
    _reject_during_cache_cleanup(request)
    quality_dpi = {"fast": 150, "balanced": 200, "high": 300}
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.file_type != "pdf":
            raise HTTPException(status_code=422, detail="Повторное распознавание доступно только для PDF.")
        page_count = (document.metadata_json or {}).get("page_count")
        if not isinstance(page_count, int) or page_count < 1:
            raise HTTPException(status_code=409, detail="Сначала дождитесь извлечения страниц PDF.")
        if body.pages is not None:
            if len(body.pages) > settings.ocr_max_pages:
                raise HTTPException(
                    status_code=422,
                    detail=f"За один запуск можно выбрать не более {settings.ocr_max_pages} страниц.",
                )
            if any(page > page_count for page in body.pages):
                raise HTTPException(status_code=422, detail=f"В PDF {page_count} страниц.")
    try:
        await request.app.state.processor.retry(
            document_id,
            "process",
            parameters={
                "ocr": {
                    "enabled": True,
                    "languages": body.language,
                    "dpi": quality_dpi[body.quality],
                    "quality": body.quality,
                    "max_pages": settings.ocr_max_pages,
                },
                **({"ocr_pages": body.pages} if body.pages is not None else {}),
            },
            reject_if_active=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Документ не найден.") from exc
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        return _document_out(document)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, request: Request) -> None:
    await delete_documents(request, [document_id], action="delete_selected")


@router.get("/documents/{document_id}/chunks", response_model=list[SourceOut])
async def list_chunks(document_id: uuid.UUID, offset: int = 0, limit: int = 50) -> list[SourceOut]:
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset не может быть отрицательным.")
    limit = max(1, min(limit, 200))
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        chunks = (await session.execute(
            select(Chunk).where(Chunk.document_id == document_id, Chunk.version == await active_chunk_version(session, document)).order_by(Chunk.ordinal).offset(offset).limit(limit)
        )).scalars().all()
        return [SourceOut(
            id=str(chunk.id), text=str((chunk.locator or {}).get("source_text") or chunk.text)[:2_500], locator=versioned_source_locator(
                chunk.locator, document_id=str(document.id), processing_version=chunk.version,
                file_type=document.file_type, is_derived=chunk.is_derived,
            ),
            ordinal=chunk.ordinal, is_derived=chunk.is_derived,
        ) for chunk in chunks]


@router.get("/documents/{document_id}/preview", response_model=DocumentPreviewOut)
async def document_preview(document_id: uuid.UUID) -> DocumentPreviewOut:
    """Return a citation-aware, document-shaped view for the workspace."""

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        metadata = document.metadata_json or {}
        processing_version = await active_chunk_version(session, document)
        cache_key = preview_cache_key(
            document_id=str(document.id),
            input_checksum=document.input_checksum,
            storage_path=document.storage_path,
            file_type=document.file_type,
            processing_version=processing_version,
            chunk_count=document.chunk_count,
            metadata=metadata,
        )
        payload = preview_response_cache.get(cache_key)
        if payload is None:
            chunks = (await session.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id, Chunk.version == processing_version)
                .order_by(Chunk.ordinal)
                .limit(MAX_PREVIEW_BLOCKS + 1)
            )).scalars().all()
            payload = build_preview(
                document_id=str(document.id),
                file_type=document.file_type,
                metadata=metadata,
                chunks=chunks,
                original_url=f"/api/v1/documents/{document.id}/file",
                total_blocks=document.chunk_count,
                processing_version=processing_version,
            )
            preview_response_cache.put(cache_key, DocumentPreviewOut.model_validate(payload).model_dump(mode="json"))
        return DocumentPreviewOut.model_validate(payload)


@router.get("/documents/{document_id}/file")
async def document_file(document_id: uuid.UUID, request: Request) -> StreamingResponse:
    """Serve the locally stored original for the native PDF viewer.

    The path is checked against the configured upload directory before the
    response is created.  This keeps the endpoint limited to uploaded files.
    """

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        media_type = document_media_type(document.file_type, document.filename)
        if document.file_type in {'html', 'htm', 'xml'}:
            media_type = 'text/plain'
        try:
            path = owned_storage(document.storage_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail='Исходный файл недоступен.') from exc
        return artifact_response(path, request, media_type, document.filename)


@router.get("/documents/{document_id}/preview/table", response_model=TablePreviewOut)
async def document_preview_table(
    document_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    sheet: str | None = Query(default=None, max_length=128),
    sort_column: int | None = Query(default=None, ge=0, le=499),
    sort_direction: Literal["asc", "desc"] = Query(default="asc"),
    filter_column: int | None = Query(default=None, ge=0, le=499),
    filter_kind: Literal["text", "number", "empty"] | None = Query(default=None),
    filter_operator: Literal["contains", "equals", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"] | None = Query(default=None),
    filter_value: str | None = Query(default=None, max_length=256),
    focus_row: int | None = Query(default=None, ge=1),
) -> TablePreviewOut:
    """Return a page from the original, fully queried table.

    The storage path is resolved and checked against the upload root before it
    is read. Sorting and filtering operate on every source row, while returned
    row numbers remain the physical source coordinates.
    """

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.file_type not in {"csv", "xlsx", "xls"}:
            raise HTTPException(status_code=400, detail="Табличный просмотр доступен только для CSV, XLSX и XLS.")
        cache_key = table_preview_cache_key(
            document_id=str(document.id),
            input_checksum=document.input_checksum,
            storage_path=document.storage_path,
            file_type=document.file_type,
            offset=offset,
            limit=limit,
            sheet=sheet,
            sort_column=sort_column,
            sort_direction=sort_direction,
            filter_column=filter_column,
            filter_kind=filter_kind,
            filter_operator=filter_operator,
            filter_value=filter_value,
            focus_row=focus_row,
        )
        try:
            path = owned_storage(document.storage_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail='Исходный файл недоступен.') from exc
    payload = table_response_cache.get(cache_key)
    if payload is not None:
        return TablePreviewOut.model_validate(payload)
    try:
        payload = await run_document_operation(
            'table', path, file_type=document.file_type, offset=offset, limit=limit, sheet=sheet,
            sort_column=sort_column, sort_direction=sort_direction, filter_column=filter_column,
            filter_kind=filter_kind, filter_operator=filter_operator, filter_value=filter_value,
            focus_row=focus_row,
        )
    except DocumentParsingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    serialized = TablePreviewOut.model_validate(payload).model_dump(mode="json")
    table_response_cache.put(cache_key, serialized)
    return TablePreviewOut.model_validate(serialized)


def _table_calculation_text(result: dict[str, Any], scope: str) -> str:
    metric = result[scope]
    scope_label = "весь документ" if scope == "document" else "текущий фильтр"
    lines = [
        f"Проверяемый расчёт · {scope_label}",
        f"Лист: {result.get('sheet') or 'CSV'}",
        f"Столбец: {result['column']}",
        f"Строк учтено: {metric['count']}; непустых значений: {metric['non_empty_count']}; числовых: {metric['numeric_count']}; нечисловых: {metric['nonnumeric_count']}.",
    ]
    for label, key in (("Сумма", "sum"), ("Среднее", "average"), ("Минимум", "minimum"), ("Максимум", "maximum")):
        if metric[key] is not None:
            lines.append(f"{label}: {metric[key]}" + (" (округлено до 2 знаков, ROUND_HALF_UP)" if key == "average" else ""))
    if metric.get("formula_count"):
        lines.append(
            f"Формул: {metric['formula_count']}; без сохранённого результата: {metric['formula_cache_missing_count']}. Формулы не вычислялись."
        )
    if result.get("filter"):
        lines.append("Фильтр: " + json.dumps(result["filter"], ensure_ascii=False, sort_keys=True))
    lines.append("Правило: " + result["rounding_rule"])
    return "\n".join(lines)


@router.post("/documents/{document_id}/preview/table/calculations", response_model=TableCalculationOut)
async def calculate_document_table(document_id: uuid.UUID, request_body: TableCalculationIn) -> TableCalculationOut:
    """Calculate exact column metrics and persist both audit scopes as sources."""

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.file_type not in {"csv", "xlsx", "xls"}:
            raise HTTPException(status_code=400, detail="Расчёты доступны только для CSV, XLSX и XLS.")
        if document.status != "ready":
            raise HTTPException(status_code=409, detail="Дождитесь завершения обработки документа.")
        try:
            path = owned_storage(document.storage_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail="Исходный файл недоступен.") from exc
        expected_version = await active_chunk_version(session, document)
        source_checksum = document.input_checksum

    filter_spec = request_body.filter.model_dump() if request_body.filter is not None else None
    try:
        result = await run_document_operation(
            "table_calculate", path, file_type=document.file_type,
            sheet=request_body.sheet, column_index=request_body.column_index,
            filter=filter_spec,
        )
    except DocumentParsingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with SessionLocal() as session, session.begin():
        document = (await session.execute(
            select(Document).where(Document.id == document_id).with_for_update()
        )).scalar_one_or_none()
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        current_version = await active_chunk_version(session, document)
        if current_version != expected_version or document.status != "ready":
            raise HTTPException(status_code=409, detail="Версия документа изменилась. Повторите расчёт.")
        chunks = (await session.execute(select(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.version == current_version,
            Chunk.is_derived.is_(True),
        ))).scalars().all()
        by_key = {
            str((chunk.locator or {}).get("calculation_key")): chunk
            for chunk in chunks if (chunk.locator or {}).get("calculation_key")
        }
        def calculation_key_for_scope(calculation_scope: str) -> str:
            identity = {
                "version": current_version,
                "checksum": source_checksum,
                "sheet": result.get("sheet"),
                "column_index": result["column_index"],
                "scope": calculation_scope,
                "filter": result.get("filter"),
            }
            return hashlib.sha256(json.dumps(
                identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()

        requested_keys = {
            calculation_key_for_scope("document"),
            calculation_key_for_scope("current_filter"),
        }
        if len(set(by_key).union(requested_keys)) > 200:
            raise HTTPException(status_code=409, detail="Для этой версии документа сохранено слишком много расчётов.")

        maximum_ordinal = await session.scalar(select(func.max(Chunk.ordinal)).where(
            Chunk.document_id == document_id, Chunk.version == current_version,
        ))
        next_ordinal = int(maximum_ordinal or 0) + 1
        source_by_scope: dict[str, Chunk] = {}
        created_count = 0
        for scope in ("document", "filtered"):
            calculation_scope = "document" if scope == "document" else "current_filter"
            calculation_key = calculation_key_for_scope(calculation_scope)
            metric = result[scope]
            locator = {
                "kind": f"{document.file_type}_derived",
                "source_type": "calculation",
                "derived": True,
                "label": f"Расчёт · {result.get('sheet') or 'CSV'} · {result['column']} · {calculation_scope}",
                "calculation_schema": 1,
                "calculation_key": calculation_key,
                "calculation_scope": calculation_scope,
                "sheet": result.get("sheet"),
                "column": result["column"],
                "column_index": result["column_index"],
                "filter": result.get("filter"),
                "source_row_count": metric["source_row_count"],
                "source_row_start": metric.get("source_row_start"),
                "source_row_end": metric.get("source_row_end"),
                "source_checksum": source_checksum,
                "rounding_rule": result["rounding_rule"],
                "metrics": metric,
                "calculated_at": datetime.now(timezone.utc).isoformat(),
            }
            text = _table_calculation_text(result, scope)
            chunk = by_key.get(calculation_key)
            if chunk is None:
                chunk = Chunk(
                    document_id=document_id,
                    ordinal=next_ordinal,
                    version=current_version,
                    text=text,
                    locator=locator,
                    embedding=None,
                    is_derived=True,
                    content_source="native",
                )
                session.add(chunk)
                next_ordinal += 1
                created_count += 1
                by_key[calculation_key] = chunk
            else:
                chunk.text = text
                chunk.locator = locator
            source_by_scope[scope] = chunk
        if created_count:
            document.chunk_count += created_count
        await session.flush()

        def source_out(chunk: Chunk) -> SourceOut:
            return SourceOut(
                id=str(chunk.id),
                text=chunk.text[:2_500],
                locator=versioned_source_locator(
                    chunk.locator, document_id=str(document.id), processing_version=chunk.version,
                    file_type=document.file_type, is_derived=True,
                ),
                ordinal=chunk.ordinal,
                is_derived=True,
            )

        return TableCalculationOut(
            **result,
            document_source=source_out(source_by_scope["document"]),
            filtered_source=source_out(source_by_scope["filtered"]),
        )


@router.get("/documents/{document_id}/markdown", response_model=MarkdownOut)
async def document_markdown(
    document_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100_000, ge=1, le=250_000),
) -> MarkdownOut:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        text = ""
        if document.markdown_path:
            try:
                text = read_storage(owned_storage(document.markdown_path, document_id), settings.document_worker_max_output_bytes).decode('utf-8')
            except (OSError, UnicodeError, DocumentParsingError):
                text = ''
        total_chars = len(text)
        total_lines = len(text.splitlines())
        return MarkdownOut(
            document_id=str(document.id),
            status=document.markdown_status,
            source=document.analysis_source,
            converter_version=document.markdown_converter_version,
            markdown=text[offset:offset + limit],
            offset=offset,
            limit=limit,
            line_offset=text.count("\n", 0, min(offset, len(text))) + 1,
            total_chars=total_chars,
            total_lines=total_lines,
            checksum=document.markdown_checksum,
            mapping_quality={str(key): int(value) for key, value in (document.markdown_mapping_json or {}).items() if isinstance(value, (int, float))},
            error=document.markdown_error,
        )


@router.get("/documents/{document_id}/search", response_model=DocumentSearchOut)
async def search_document(
    document_id: uuid.UUID,
    q: str = Query(min_length=1, max_length=256),
    scope: Literal["original", "markdown"] = Query(default="original"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> DocumentSearchOut:
    """Search original content or the complete Markdown artifact without invoking Codex."""
    if not q.strip():
        raise HTTPException(status_code=422, detail="Введите текст для поиска.")
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.status != "ready":
            raise HTTPException(status_code=409, detail="Поиск станет доступен после обработки документа.")
        version = await active_chunk_version(session, document)
        document_id_text = str(document.id)
        document_filename = document.filename
        file_type = document.file_type
        storage_path = document.storage_path
        input_checksum = document.input_checksum or document.storage_path
        markdown_path_value = document.markdown_path
        markdown_status = document.markdown_status
        markdown_available = bool(markdown_path_value and markdown_status == "ready")
        chunk_rows = []
        if scope == "markdown" or file_type == "pdf":
            chunk_query = select(
                Chunk.id, Chunk.ordinal, Chunk.text, Chunk.locator, Chunk.is_derived,
                Chunk.markdown_char_start, Chunk.markdown_char_end,
            ).where(Chunk.document_id == document_id, Chunk.version == version).order_by(Chunk.ordinal)
            chunk_rows = (await session.execute(chunk_query)).all()

    if scope == "markdown":
        if not markdown_available:
            raise HTTPException(status_code=409, detail="Markdown недоступен для этого документа.")
        try:
            markdown_path = owned_storage(markdown_path_value, document_id)
            markdown_bytes = await asyncio.to_thread(
                read_storage, markdown_path, settings.document_worker_max_output_bytes,
            )
            markdown_text = markdown_bytes.decode("utf-8")
        except (OSError, UnicodeError, DocumentParsingError) as exc:
            raise HTTPException(status_code=404, detail="Файл Markdown недоступен.") from exc
        result = await asyncio.to_thread(
            search_markdown,
            markdown_text,
            q,
            chunks=chunk_rows,
            document_id=document_id_text,
            processing_version=version,
            file_type=file_type,
            offset=offset,
            limit=limit,
        )
    elif file_type in {"csv", "xlsx", "xls"}:
        try:
            original_path = owned_storage(storage_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail="Исходный файл недоступен.") from exc
        table_search_key = table_search_cache_key(
            document_id=document_id_text,
            input_checksum=input_checksum,
            file_type=file_type,
            processing_version=version,
            query=q,
            offset=offset,
            limit=limit,
        )
        result = table_search_response_cache.get(table_search_key)
        if result is None:
            try:
                result = await run_document_operation(
                    "search_table", original_path, file_type=file_type, query=q,
                    offset=offset, limit=limit,
                )
            except DocumentParsingError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            for match in result["matches"]:
                match["locator"] = versioned_source_locator(
                    match["locator"],
                    document_id=document_id_text,
                    processing_version=version,
                    file_type=file_type,
                )
            table_search_response_cache.put(table_search_key, result)
    elif file_type == "pdf" and any(isinstance(row.locator, dict) and row.locator.get("ocr") is True for row in chunk_rows):
        # Keep mixed/scanned PDF search on the active OCR source map so the
        # returned range and word boxes always refer to the same OCR version.
        from types import SimpleNamespace
        sources = [SimpleNamespace(**row._mapping) for row in chunk_rows]
        result = await asyncio.to_thread(
            search_source_blocks,
            sources,
            q,
            file_type=file_type,
            document_id=document_id_text,
            processing_version=version,
            offset=offset,
            limit=limit,
        )
    else:
        try:
            original_path = owned_storage(storage_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail="Исходный файл недоступен.") from exc
        cache_key = (document_id_text, version, input_checksum)
        sources = original_search_source_cache.get(cache_key)
        if sources is None:
            try:
                if file_type in {"txt", "md", "xml", "json", "html", "htm"}:
                    original_bytes = await asyncio.to_thread(read_storage, original_path, settings.max_upload_bytes)
                    original_text, _ = await asyncio.to_thread(_decode_text_with_encoding, original_bytes)
                    if len(original_text) > settings.document_max_chars:
                        raise DocumentParsingError("Текст документа превышает безопасный предел.")
                    from types import SimpleNamespace
                    sources = (SimpleNamespace(
                        id="original:0",
                        ordinal=0,
                        text=original_text,
                        locator={
                            "kind": file_type,
                            "char_start": 0,
                            "char_end": len(original_text),
                            "line_start": 1,
                            "line_end": original_text.count("\n") + 1,
                        },
                        is_derived=False,
                    ),)
                else:
                    from app.services.isolated_documents import parse_uploaded
                    parsed = await parse_uploaded(original_path, document_filename)
                    from types import SimpleNamespace
                    sources = tuple(SimpleNamespace(
                        id=f"native:{index}",
                        ordinal=index,
                        text=block.text,
                        locator=block.locator,
                        is_derived=block.derived,
                    ) for index, block in enumerate(parsed.blocks))
                original_search_source_cache.put(cache_key, sources)
            except (DocumentParsingError, OSError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            result = await asyncio.to_thread(
                search_source_blocks,
                sources,
                q,
                file_type=file_type,
                document_id=document_id_text,
                processing_version=version,
                offset=offset, limit=limit,
            )
        except DocumentParsingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DocumentSearchOut(
        document_id=document_id_text,
        scope=scope,
        query=q,
        total=result["total"],
        offset=result["offset"],
        limit=result["limit"],
        matches=result["matches"],
    )


@router.get("/documents/{document_id}/markdown/download")
async def download_document_markdown(document_id: uuid.UUID, request: Request) -> StreamingResponse:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if not document.markdown_path:
            raise HTTPException(status_code=404, detail="Markdown для этого документа ещё не создан.")
        try:
            path = owned_storage(document.markdown_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail='Markdown недоступен.') from exc
        return artifact_response(path, request, 'text/markdown; charset=utf-8',
                                 download_name(document.filename, '.md'), attachment=True)


@router.post("/documents/{document_id}/markdown/rebuild", response_model=DocumentOut, status_code=202)
async def rebuild_document_markdown(document_id: uuid.UUID, request: Request) -> DocumentOut:
    _reject_during_cache_cleanup(request)
    try:
        await request.app.state.processor.retry(document_id, 'process')
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Документ не найден.') from exc
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        return _document_out(document)


@router.get("/documents/{document_id}/insights", response_model=list[InsightOut])
async def document_insights(document_id: uuid.UUID, version: int | None = Query(default=None, ge=1)) -> list[InsightOut]:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        selected_number = version if version is not None else document.active_version
        analysis_version = await session.get(DocumentVersion, (document_id, selected_number))
        if analysis_version is None or analysis_version.state != "ready":
            raise HTTPException(status_code=404, detail="Готовая версия анализа не найдена.")
        insights = (await session.execute(
            select(Insight).where(Insight.document_id == document_id, Insight.version == selected_number).order_by(Insight.created_at, Insight.id)
        )).scalars().all()
        return [InsightOut(
            id=str(item.id), key=item.key, question=item.question, answer=item.answer,
            citations=await _sources_for_ids(session, document_id, item.citations or []),
            version=analysis_version.number, source_version=analysis_version.chunk_version,
            model=analysis_version.analysis_model,
            reasoning_effort=analysis_version.analysis_reasoning_effort,
        ) for item in insights]


async def _bookmark_out(session, document_id: uuid.UUID, item: DocumentBookmark) -> DocumentBookmarkOut:
    sources = await _sources_for_ids(session, document_id, [str(item.source_id)])
    if not sources:
        raise HTTPException(status_code=409, detail="Источник закладки больше недоступен.")
    return DocumentBookmarkOut(
        id=str(item.id), note=item.note, source_version=item.source_version,
        created_at=item.created_at, updated_at=item.updated_at, source=sources[0],
    )


@router.get("/documents/{document_id}/bookmarks", response_model=list[DocumentBookmarkOut])
async def document_bookmarks(document_id: uuid.UUID) -> list[DocumentBookmarkOut]:
    async with SessionLocal() as session:
        if await session.get(Document, document_id) is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        rows = (await session.execute(
            select(DocumentBookmark).where(DocumentBookmark.document_id == document_id)
            .order_by(DocumentBookmark.created_at.desc(), DocumentBookmark.id.desc())
        )).scalars().all()
        return [await _bookmark_out(session, document_id, item) for item in rows]


@router.post("/documents/{document_id}/bookmarks", response_model=DocumentBookmarkOut, status_code=201)
async def create_document_bookmark(document_id: uuid.UUID, body: DocumentBookmarkCreateIn, request: Request) -> DocumentBookmarkOut:
    _reject_during_cache_cleanup(request)
    try:
        async with SessionLocal() as session, session.begin():
            document = await session.get(Document, document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="Документ не найден.")
            if document.status != "ready":
                raise HTTPException(status_code=409, detail="Добавить источник в закладки можно после обработки документа.")
            chunk = (await session.execute(
                select(Chunk).where(
                    Chunk.id == body.source_id,
                    Chunk.document_id == document_id,
                    Chunk.version == body.source_version,
                )
            )).scalar_one_or_none()
            if chunk is None:
                raise HTTPException(status_code=409, detail="Источник не относится к указанной версии документа.")
            existing = (await session.execute(
                select(DocumentBookmark).where(
                    DocumentBookmark.document_id == document_id,
                    DocumentBookmark.source_id == body.source_id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                raise HTTPException(status_code=409, detail="Этот источник уже сохранён в закладках.")
            item = DocumentBookmark(
                document_id=document_id, source_id=chunk.id, source_version=chunk.version, note=body.note,
            )
            session.add(item)
            await session.flush()
            return await _bookmark_out(session, document_id, item)
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_document_bookmarks_source":
            raise HTTPException(status_code=409, detail="Этот источник уже сохранён в закладках.") from exc
        raise


@router.patch("/documents/{document_id}/bookmarks/{bookmark_id}", response_model=DocumentBookmarkOut)
async def update_document_bookmark(
    document_id: uuid.UUID, bookmark_id: uuid.UUID, body: DocumentBookmarkUpdateIn, request: Request,
) -> DocumentBookmarkOut:
    _reject_during_cache_cleanup(request)
    async with SessionLocal() as session, session.begin():
        item = (await session.execute(select(DocumentBookmark).where(
            DocumentBookmark.id == bookmark_id,
            DocumentBookmark.document_id == document_id,
        ))).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Закладка не найдена.")
        item.note = body.note
        await session.flush()
        return await _bookmark_out(session, document_id, item)


@router.delete("/documents/{document_id}/bookmarks/{bookmark_id}", status_code=204)
async def delete_document_bookmark(document_id: uuid.UUID, bookmark_id: uuid.UUID, request: Request) -> Response:
    _reject_during_cache_cleanup(request)
    async with SessionLocal() as session, session.begin():
        item = (await session.execute(select(DocumentBookmark).where(
            DocumentBookmark.id == bookmark_id,
            DocumentBookmark.document_id == document_id,
        ))).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Закладка не найдена.")
        await session.delete(item)
    return Response(status_code=204)


@router.get("/documents/{document_id}/analysis/additional", response_model=list[AdditionalAnalysisOut])
async def list_additional_analyses(
    document_id: uuid.UUID, version: int | None = Query(default=None, ge=1),
) -> list[AdditionalAnalysisOut]:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        selected_number = version if version is not None else document.active_version
        analysis_version = await session.get(DocumentVersion, (document_id, selected_number))
        if analysis_version is None or analysis_version.state != "ready":
            raise HTTPException(status_code=404, detail="Готовая версия анализа не найдена.")
        rows = (await session.execute(
            select(AdditionalAnalysis).where(
                AdditionalAnalysis.document_id == document_id,
                AdditionalAnalysis.analysis_version == selected_number,
            ).order_by(AdditionalAnalysis.created_at, AdditionalAnalysis.id)
        )).scalars().all()
        result = []
        for item in rows:
            result.append(AdditionalAnalysisOut(
                id=str(item.id), mode=item.mode, answer=item.answer,
                citations=await _sources_for_ids(session, document_id, item.citations or []),
                analysis_version=item.analysis_version, source_version=item.source_version,
                model=item.model, reasoning_effort=item.reasoning_effort, created_at=item.created_at,
            ))
        return result


@router.post("/documents/{document_id}/analysis/additional", response_model=AdditionalAnalysisOut, status_code=201)
async def create_additional_analysis(
    document_id: uuid.UUID, body: AdditionalAnalysisCreateIn, request: Request,
) -> AdditionalAnalysisOut:
    _reject_during_cache_cleanup(request)
    codex = request.app.state.codex
    try:
        model, effort = await codex.validate_choice(body.model, body.reasoning_effort)
    except CodexNeedsLogin as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodexModelUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.status != "ready":
            raise HTTPException(status_code=409, detail="Дождитесь завершения обработки документа.")
        version = await session.get(DocumentVersion, (document_id, body.analysis_version))
        if version is None or version.state != "ready":
            raise HTTPException(status_code=409, detail="Выбранная версия анализа недоступна.")
        if version.chunk_version != body.expected_source_version:
            raise HTTPException(status_code=409, detail="Источники выбранной версии изменились. Обновите страницу.")
        source_version = version.chunk_version

    try:
        result = await analyze_additional(
            document_id, codex, source_version=source_version, mode=body.mode,
            model=model, reasoning_effort=effort,
        )
    except CodexNeedsLogin as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodexModelUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async with SessionLocal() as session, session.begin():
        version = await session.get(DocumentVersion, (document_id, body.analysis_version))
        if version is None or version.state != "ready" or version.chunk_version != source_version:
            raise HTTPException(status_code=409, detail="Версия документа изменилась во время анализа. Повторите запрос.")
        item = AdditionalAnalysis(
            document_id=document_id,
            analysis_version=body.analysis_version,
            source_version=source_version,
            mode=body.mode,
            answer=result.answer,
            citations=result.citations,
            model=model,
            reasoning_effort=effort,
        )
        session.add(item)
        await session.flush()
        return AdditionalAnalysisOut(
            id=str(item.id), mode=item.mode, answer=item.answer,
            citations=await _sources_for_ids(session, document_id, item.citations),
            analysis_version=item.analysis_version, source_version=item.source_version,
            model=item.model, reasoning_effort=item.reasoning_effort, created_at=item.created_at,
        )


@router.get("/documents/{document_id}/versions", response_model=list[DocumentAnalysisVersionOut])
async def document_analysis_versions(document_id: uuid.UUID) -> list[DocumentAnalysisVersionOut]:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        versions = (await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.state == "ready",
            ).order_by(DocumentVersion.number.desc())
        )).scalars().all()
        return [DocumentAnalysisVersionOut(
            number=item.number, source_version=item.chunk_version, state=item.state,
            model=item.analysis_model, reasoning_effort=item.analysis_reasoning_effort,
            created_at=item.created_at, is_active=item.number == document.active_version,
        ) for item in versions]


@router.post("/documents/{document_id}/analysis/rebuild", status_code=202)
async def rebuild_document_analysis(document_id: uuid.UUID, body: ReanalyzeIn, request: Request) -> dict[str, Any]:
    _reject_during_cache_cleanup(request)
    codex = request.app.state.codex
    try:
        model, effort = await codex.validate_choice(body.model, body.reasoning_effort)
    except CodexNeedsLogin as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodexModelUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.status != "ready":
            raise HTTPException(status_code=409, detail="Дождитесь завершения текущей обработки документа.")
        active = await session.get(DocumentVersion, (document_id, document.active_version))
        if active is None or active.state != "ready":
            raise HTTPException(status_code=409, detail="Нет готовой версии анализа для повторного запуска.")
        if active.chunk_version != body.expected_source_version:
            raise HTTPException(status_code=409, detail="Источник документа изменился. Обновите страницу и повторите запуск.")
    try:
        job = await request.app.state.processor.retry(
            document_id, "analysis",
            parameters={"model": model, "reasoning_effort": effort},
            reject_if_active=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Документ не найден.") from exc
    return {"job_id": str(job.id), "version": job.version, "source_version": body.expected_source_version,
            "model": model, "reasoning_effort": effort, "state": job.state}


@router.post("/documents/{document_id}/export")
async def export_document(document_id: uuid.UUID, body: DocumentExportIn) -> Response:
    """Export the currently persisted analysis or chat without calling Codex."""
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.status != "ready":
            raise HTTPException(status_code=409, detail="Экспорт станет доступен после завершения обработки документа.")

        version = await session.get(DocumentVersion, (document_id, document.active_version))
        if version is None or version.state != "ready":
            raise HTTPException(status_code=409, detail="Для документа нет готовой версии результатов.")

        insights: list[Insight] = []
        additional_analyses: list[AdditionalAnalysis] = []
        messages: list[Message] = []
        citation_lists: list[list[str]] = []
        if body.scope in {"analysis", "selected_answers"}:
            insights = (await session.execute(
                select(Insight)
                .where(Insight.document_id == document_id, Insight.version == version.number)
                .order_by(Insight.created_at, Insight.id)
            )).scalars().all()
            if not insights:
                raise HTTPException(status_code=409, detail="В сохранённой версии нет ответов анализа для экспорта.")
            if body.scope == "selected_answers":
                existing_keys = {item.key for item in insights}
                missing_keys = set(body.selected_keys) - existing_keys
                if missing_keys:
                    raise HTTPException(status_code=422, detail="Один или несколько выбранных ответов больше недоступны.")
                insights = [item for item in insights if item.key in set(body.selected_keys)]
            citation_lists.extend([list(item.citations or []) for item in insights])
            requested_additional_ids = set(body.selected_additional_analysis_ids)
            if requested_additional_ids:
                additional_analyses = (await session.execute(
                    select(AdditionalAnalysis).where(
                        AdditionalAnalysis.document_id == document_id,
                        AdditionalAnalysis.analysis_version == version.number,
                        AdditionalAnalysis.id.in_(requested_additional_ids),
                    ).order_by(AdditionalAnalysis.created_at, AdditionalAnalysis.id)
                )).scalars().all()
                if {item.id for item in additional_analyses} != requested_additional_ids:
                    raise HTTPException(status_code=422, detail="Один или несколько дополнительных результатов больше недоступны для этой версии.")
                citation_lists.extend([list(item.citations or []) for item in additional_analyses])
        else:
            chat = (await session.execute(select(Chat).where(Chat.document_id == document_id))).scalar_one_or_none()
            if chat is None:
                raise HTTPException(status_code=409, detail="Для документа ещё нет сохранённой переписки.")
            messages = (await session.execute(
                select(Message).where(Message.chat_id == chat.id)
                .order_by(Message.created_at, Message.id).limit(2_001)
            )).scalars().all()
            if len(messages) > 2_000:
                raise HTTPException(status_code=413, detail="Переписка слишком длинная для одного экспорта.")
            citation_lists.extend([list(item.citations or []) for item in messages])

        flattened_ids = [value for citations in citation_lists for value in citations]
        if len(flattened_ids) > MAX_EXPORT_SOURCES:
            raise HTTPException(status_code=413, detail="В экспорте слишком много ссылок на источники.")
        parsed_ids: list[uuid.UUID] = []
        for value in flattened_ids:
            try:
                parsed_ids.append(uuid.UUID(value))
            except (ValueError, TypeError, AttributeError):
                if body.scope != "conversation":
                    raise HTTPException(status_code=409, detail="Не удалось проверить связь ответа с источником.") from None
        chunk_rows = (await session.execute(
            select(Chunk).where(Chunk.document_id == document_id, Chunk.id.in_(set(parsed_ids)))
        )).scalars().all() if parsed_ids else []
        chunks_by_id = {str(chunk.id): chunk for chunk in chunk_rows}

        def resolve_sources(values: list[str], *, strict_active: bool) -> tuple[ExportSource, ...]:
            result: list[ExportSource] = []
            for index, value in enumerate(values):
                try:
                    source_id = str(uuid.UUID(value))
                except (ValueError, TypeError, AttributeError):
                    source_id = ""
                chunk = chunks_by_id.get(source_id) if source_id else None
                if chunk is None or (strict_active and chunk.version != version.chunk_version):
                    if strict_active:
                        raise HTTPException(status_code=409, detail="Источник ответа не соответствует сохранённой версии документа.")
                    result.append(ExportSource(
                        id=f"unavailable-{index}", text="", locator={}, ordinal=index,
                        unavailable=True,
                    ))
                    continue
                locator = chunk.locator if isinstance(chunk.locator, dict) else {}
                result.append(ExportSource(
                    id=source_id,
                    text=str(locator.get("source_text") or chunk.text)[:MAX_EXPORT_SOURCES],
                    locator=locator,
                    ordinal=chunk.ordinal,
                    is_derived=chunk.is_derived,
                    version=chunk.version,
                ))
            return tuple(result)

        if body.scope in {"analysis", "selected_answers"}:
            export_insights = tuple(ExportAnswer(
                key=item.key,
                question=item.question,
                answer=item.answer,
                citations=resolve_sources(list(item.citations or []), strict_active=True),
            ) for item in insights)
            export_additional = tuple(ExportAdditionalAnalysis(
                id=str(item.id), mode=item.mode, answer=item.answer,
                citations=resolve_sources(list(item.citations or []), strict_active=True),
                model=item.model, reasoning_effort=item.reasoning_effort,
            ) for item in additional_analyses)
            export_messages: tuple[ExportMessage, ...] = ()
        else:
            export_insights = ()
            export_additional = ()
            export_messages = tuple(ExportMessage(
                role=item.role,
                content=item.content,
                citations=resolve_sources(list(item.citations or []), strict_active=False),
                created_at=item.created_at,
                model=item.model,
                reasoning_effort=item.reasoning_effort,
            ) for item in messages)

        job = (await session.execute(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document_id,
                   ProcessingJob.version == version.number,
                   ProcessingJob.state == "succeeded")
            .order_by(ProcessingJob.finished_at.desc().nullslast(), ProcessingJob.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        parameters = job.parameters if job and isinstance(job.parameters, dict) else {}
        snapshot = ExportSnapshot(
            filename=document.filename,
            file_type=document.file_type,
            exported_at=datetime.now(timezone.utc),
            processing_version=version.number,
            chunk_version=version.chunk_version,
            model=parameters.get("model") if isinstance(parameters.get("model"), str) else None,
            reasoning_effort=parameters.get("reasoning_effort") if isinstance(parameters.get("reasoning_effort"), str) else None,
            analysis_source=document.analysis_source,
            markdown_status=document.markdown_status,
            markdown_converter_version=document.markdown_converter_version,
            ocr_status=document.ocr_status,
            ocr_language=document.ocr_language,
            ocr_page_count=document.ocr_page_count,
            ocr_confidence=document.ocr_confidence,
            ocr_error=document.ocr_error,
            insights=export_insights,
            additional_analyses=export_additional,
            messages=export_messages,
        )

    try:
        content = render_export(
            snapshot, body.scope, body.format, set(body.selected_keys),
            {str(item) for item in body.selected_additional_analysis_ids},
        )
    except ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document export failed for %s (%s)", document_id, type(exc).__name__, exc_info=False)
        raise HTTPException(status_code=500, detail="Не удалось сформировать экспорт. Повторите попытку.") from None

    filename = export_filename(snapshot.filename, body.format)
    ascii_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._") or "report"
    media_type = "text/markdown; charset=utf-8" if body.format == "markdown" else "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename, safe="")}',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/documents/{document_id}/chat", response_model=ChatOut)
async def document_chat(document_id: uuid.UUID) -> ChatOut:
    async with SessionLocal() as session:
        chat = (await session.execute(select(Chat).where(Chat.document_id == document_id))).scalar_one_or_none()
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат появится после завершения индексации документа.")
        return ChatOut(id=str(chat.id), scope="document", document_id=str(chat.document_id), context_epoch=chat.context_epoch)


def _chat_generation_tasks(request: Request) -> dict[str, asyncio.Task]:
    tasks = getattr(request.app.state, "chat_generation_tasks", None)
    if tasks is None:
        tasks = {}
        request.app.state.chat_generation_tasks = tasks
    return tasks


def _chat_generation_buffers(request: Request) -> dict[str, list[str]]:
    buffers = getattr(request.app.state, "chat_generation_buffers", None)
    if buffers is None:
        buffers = {}
        request.app.state.chat_generation_buffers = buffers
    return buffers


@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
async def chat_messages(chat_id: uuid.UUID, request: Request) -> list[MessageOut]:
    tasks = _chat_generation_tasks(request)
    async with SessionLocal() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        messages = (await session.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id)
        )).scalars().all()
        recovered = False
        for message in messages:
            if message.generation_status == "streaming" and str(message.id) not in tasks:
                message.generation_status = "interrupted"
                message.generation_error = "Ответ был прерван до завершения. Сохранённый фрагмент можно повторить."
                recovered = True
        if recovered:
            await session.commit()
        return [await _message_out(session, chat, message) for message in messages]


async def _create_chat_generation(chat_id: uuid.UUID, request: Request, *, text: str | None = None,
                                  retry_user_id: uuid.UUID | None = None) -> StreamingResponse:
    _reject_during_cache_cleanup(request)
    codex = request.app.state.codex
    try:
        await codex.require_ready()
    except (CodexNeedsLogin, CodexModelUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response_model = settings.codex_model
    response_reasoning_effort = settings.codex_reasoning_effort
    tasks = _chat_generation_tasks(request)
    buffers = _chat_generation_buffers(request)
    user: Message | None = None
    history: list[Message] = []
    document: Document | None = None
    comparison_sources: list[tuple[ChatDocument, Document]] = []
    chunks: list[Chunk] = []
    app_matches = ()
    request_scope: Literal["application", "document", "mixed"] = "document"
    use_app_contract = False
    document_id: uuid.UUID | None = None
    file_type: str | None = None
    source_version: int | None = None
    async with SessionLocal() as session, session.begin():
        chat = (await session.execute(select(Chat).where(Chat.id == chat_id).with_for_update())).scalar_one_or_none()
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        if str(chat_id) in getattr(request.app.state, "maintenance_deleting_chats", set()):
            raise HTTPException(status_code=409, detail="Чат удаляется. Обновите библиотеку и повторите запрос.")
        if chat.scope == "document":
            if chat.document_id is None:
                raise HTTPException(status_code=409, detail="У документного чата отсутствует документ.")
            document = await session.get(Document, chat.document_id)
            if document is None:
                raise HTTPException(status_code=404, detail="Документ не найден.")
            if str(document.id) in getattr(request.app.state, "maintenance_deleting_documents", set()):
                raise HTTPException(status_code=409, detail="Документ удаляется. Повторите запрос после обновления библиотеки.")
            if document.status != "ready":
                raise HTTPException(status_code=409, detail="Документ ещё обрабатывается или требует повторной обработки.")
        elif chat.scope == "comparison":
            if chat.document_id is not None:
                raise HTTPException(status_code=409, detail="У чата сравнения некорректная область источников.")
            links = (await session.execute(
                select(ChatDocument).where(ChatDocument.chat_id == chat.id, ChatDocument.is_selected.is_(True))
                .order_by(ChatDocument.position).with_for_update()
            )).scalars().all()
            if not 2 <= len(links) <= 5:
                raise HTTPException(status_code=409, detail="Выберите от двух до пяти готовых документов для продолжения сравнения.")
            for link in links:
                selected_document = await session.get(Document, link.document_id)
                if selected_document is None or selected_document.status != "ready":
                    filename = selected_document.filename if selected_document else "Документ"
                    raise HTTPException(status_code=409, detail=f"Документ «{filename}» сейчас не готов. Обновите набор источников после обработки.")
                pinned_version = (await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == link.document_id,
                        DocumentVersion.chunk_version == link.source_version,
                        DocumentVersion.state == "ready",
                    ).order_by(DocumentVersion.number.desc()).limit(1)
                )).scalar_one_or_none()
                if pinned_version is None:
                    raise HTTPException(status_code=409, detail=f"Версия источников документа «{selected_document.filename}» недоступна. Обновите набор сравнения.")
                if str(selected_document.id) in getattr(request.app.state, "maintenance_deleting_documents", set()):
                    raise HTTPException(status_code=409, detail="Один из выбранных документов удаляется. Повторите запрос после обновления библиотеки.")
                comparison_sources.append((link, selected_document))
        elif chat.scope != "application" or chat.document_id is not None:
            raise HTTPException(status_code=409, detail="Область этого чата настроена некорректно.")

        active_rows = (await session.execute(select(Message).where(
            Message.chat_id == chat_id,
            Message.generation_status == "streaming",
        ).with_for_update())).scalars().all()
        for active in active_rows:
            if str(active.id) in tasks:
                raise HTTPException(status_code=409, detail="Дождитесь завершения или остановите текущий ответ.")
            active.generation_status = "interrupted"
            active.generation_error = "Ответ был прерван до завершения. Сохранённый фрагмент можно повторить."

        if retry_user_id is not None:
            user = await session.get(Message, retry_user_id)
            if user is None or user.chat_id != chat_id or user.role != "user" or user.context_epoch != chat.context_epoch:
                raise HTTPException(status_code=404, detail="Вопрос не найден в текущем контексте чата.")
            text = user.content
            history = (await session.execute(select(Message).where(
                Message.chat_id == chat_id,
                Message.context_epoch == chat.context_epoch,
                or_(Message.created_at < user.created_at,
                    and_(Message.created_at == user.created_at, Message.id < user.id)),
            ).order_by(Message.created_at.desc(), Message.id.desc()).limit(32))).scalars().all()
            history.reverse()
        else:
            history = (await session.execute(select(Message).where(
                Message.chat_id == chat_id,
                Message.context_epoch == chat.context_epoch,
            ).order_by(Message.created_at.desc(), Message.id.desc()).limit(32))).scalars().all()
            history.reverse()

        app_matches = search_app_capabilities(text or "") if chat.scope != "comparison" else ()
        request_scope = "document" if chat.scope == "comparison" else classify_assistant_scope(text or "", app_matches, chat_scope=chat.scope)
        use_app_contract = chat.scope == "application" or request_scope in {"application", "mixed"}
        document_id = document.id if document else None
        file_type = document.file_type if document else None
        context_epoch = chat.context_epoch
        if document:
            active_version = await session.get(DocumentVersion, (document.id, document.active_version))
            if active_version is None or active_version.state != "ready":
                raise HTTPException(status_code=409, detail="Нет готовой версии источников для ответа.")
            source_version = active_version.chunk_version
        previous_messages = bounded_history(history, exclude_id=user.id if user else None)
        retrieval_query = " ".join([item["text"] for item in previous_messages[-4:]] + [text or ""])[-1_500:]

    if comparison_sources:
        ranked_per_document = await asyncio.gather(*(
            search_chunks(
                selected_document.id,
                retrieval_query,
                limit=PER_DOCUMENT_RETRIEVAL_LIMIT,
                version=link.source_version,
            )
            for link, selected_document in comparison_sources
        ))
        chunks = interleave_document_results(ranked_per_document)
    elif document_id is not None and request_scope in {"document", "mixed"}:
        chunks = await search_chunks(document_id, retrieval_query, limit=8, version=source_version)
    if document_id is not None and request_scope in {"document", "mixed"} and file_type == "csv" and re.search(
        r"сумм|средн|миним|максим|итог|количеств|агрегат|скольк|посчит|вычисл|рассчит|подсчит|average|sum|total",
        retrieval_query, re.IGNORECASE,
    ):
        async with SessionLocal() as session:
            derived = (await session.execute(select(Chunk).where(
                Chunk.document_id == document_id, Chunk.is_derived.is_(True), Chunk.version == source_version,
            ).order_by(Chunk.ordinal))).scalars().all()
        table_summary = next((chunk for chunk in derived if not chunk.locator.get("column")), None)
        numeric_sources = [chunk for chunk in derived if chunk.locator.get("column")]
        if numeric_sources:
            query_lower = retrieval_query.casefold()
            named_sources = [chunk for chunk in numeric_sources if str(chunk.locator.get("column", "")).casefold() in query_lower]
            selected_derived = (([table_summary] if table_summary else []) + (named_sources or numeric_sources[:7]))[:8]
            selected_ids = {chunk.id for chunk in selected_derived}
            chunks = (selected_derived + [chunk for chunk in chunks if chunk.id not in selected_ids])[:8]
        elif table_summary:
            chunks = [table_summary, *chunks[:7]]

    source_map: dict[str, Chunk] = {}
    structured_sources: dict[str, AppHelpAvailableSource] = {}
    structured_source_events: list[dict[str, Any]] = []
    comparison_source_documents = {str(document.id): document for _, document in comparison_sources}
    comparison_document_manifest = [
        {"document_id": str(document.id), "filename": document.filename, "source_version": link.source_version}
        for link, document in comparison_sources
    ]
    comparison_available_sources: dict[str, str] = {}
    if comparison_sources:
        comparison_evidence: list[dict[str, Any]] = []
        for chunk in chunks:
            source_id = str(chunk.id)
            source_document = comparison_source_documents[str(chunk.document_id)]
            comparison_available_sources[source_id] = str(source_document.id)
            label = str((chunk.locator or {}).get("label") or "Фрагмент документа")[:160]
            comparison_evidence.append({
                "source_id": source_id,
                "document_id": str(source_document.id),
                "filename": source_document.filename,
                "source_version": chunk.version,
                "location": label,
                "excerpt": chunk.text[:1_500],
            })
            structured_source_events.append({
                "id": source_id,
                "text": str((chunk.locator or {}).get("source_text") or chunk.text)[:2_500],
                "locator": chunk.locator,
                "ordinal": chunk.ordinal,
                "is_derived": chunk.is_derived,
                "source_type": "document",
                "document_id": str(source_document.id),
                "document_filename": source_document.filename,
                "source_version": chunk.version,
            })
        payload = {
            "question": text,
            "previous_messages": previous_messages,
            "selected_documents": comparison_document_manifest,
            "sources": comparison_evidence,
            "instruction": COMPARISON_INSTRUCTIONS,
        }
    elif use_app_contract:
        for match in app_matches:
            capability = match.capability
            structured_sources[capability.source_id] = AppHelpAvailableSource(
                source_type="application", ui_target_ids=capability.ui_target_ids,
            )
            structured_source_events.append({
                "id": capability.source_id,
                "text": capability.evidence_text[:2_500],
                "locator": {},
                "ordinal": 0,
                "is_derived": False,
                "source_type": "application",
                "title": capability.title,
            })
        document_evidence: list[dict[str, Any]] = []
        for chunk in chunks:
            source_id = str(chunk.id)
            structured_sources[source_id] = AppHelpAvailableSource(source_type="document")
            label = str((chunk.locator or {}).get("label") or "Фрагмент документа")
            document_evidence.append({
                "source_id": source_id,
                "source_type": "document",
                "location": label[:160],
                "excerpt": chunk.text[:1_500],
            })
            structured_source_events.append({
                "id": source_id,
                "text": str((chunk.locator or {}).get("source_text") or chunk.text)[:2_500],
                "locator": chunk.locator,
                "ordinal": chunk.ordinal,
                "is_derived": chunk.is_derived,
                "source_type": "document",
                "title": label[:160],
            })
        payload = build_application_assistant_payload(
            text or "", app_matches, previous_messages=previous_messages,
            scope=request_scope, document_evidence=document_evidence,
        )
    else:
        legacy_sources: list[dict[str, str]] = []
        for index, chunk in enumerate(chunks, start=1):
            label = f"S{index:02d}"
            source_map[label] = chunk
            legacy_sources.append({"label": label, "location": str(chunk.locator.get("label", "Фрагмент документа")), "excerpt": chunk.text[:1_500]})
        payload = {
            "question": text,
            "previous_messages": previous_messages,
            "sources": legacy_sources,
            "instruction": "Ответь по-русски и поставь метку [Sxx] после каждого подтверждённого факта. Если источники не дают ответа, прямо скажи об этом без меток.",
        }

    async with SessionLocal() as session, session.begin():
        chat = (await session.execute(select(Chat).where(Chat.id == chat_id).with_for_update())).scalar_one_or_none()
        if chat is None or chat.context_epoch != context_epoch:
            raise HTTPException(status_code=409, detail="Контекст чата изменился. Повторите запрос.")
        active = (await session.execute(select(Message.id).where(
            Message.chat_id == chat_id, Message.generation_status == "streaming",
        ).limit(1))).scalar_one_or_none()
        if active is not None:
            raise HTTPException(status_code=409, detail="В этом чате уже формируется ответ.")
        if document_id is not None:
            latest_document = await session.get(Document, document_id)
            latest_version = await session.get(DocumentVersion, (document_id, latest_document.active_version)) if latest_document else None
            if latest_version is None or latest_version.chunk_version != source_version:
                raise HTTPException(status_code=409, detail="Версия документа изменилась. Повторите запрос.")
        elif comparison_sources:
            latest_links = (await session.execute(
                select(ChatDocument.document_id, ChatDocument.source_version)
                .where(ChatDocument.chat_id == chat_id, ChatDocument.is_selected.is_(True))
                .order_by(ChatDocument.position)
            )).all()
            expected_links = [(link.document_id, link.source_version) for link, _ in comparison_sources]
            if [(row[0], row[1]) for row in latest_links] != expected_links:
                raise HTTPException(status_code=409, detail="Набор или версия источников изменились. Повторите запрос.")
            if any(str(selected_document.id) in getattr(request.app.state, "maintenance_deleting_documents", set())
                   for _, selected_document in comparison_sources):
                raise HTTPException(status_code=409, detail="Один из выбранных документов удаляется. Повторите запрос позже.")
        elif chat.scope != "application" or chat.document_id is not None:
            raise HTTPException(status_code=409, detail="Чат приложения изменился. Повторите запрос.")
        if retry_user_id is None:
            user = Message(chat_id=chat_id, role="user", content=text or "", citations=[],
                           model=response_model, reasoning_effort=response_reasoning_effort,
                           context_epoch=context_epoch, source_version=source_version)
            session.add(user)
            await session.flush()
        else:
            user = await session.get(Message, retry_user_id)
            if user is None or user.context_epoch != chat.context_epoch:
                raise HTTPException(status_code=404, detail="Вопрос больше недоступен для повтора.")
        assistant = Message(chat_id=chat_id, role="assistant", content="", citations=[],
                             model=response_model, reasoning_effort=response_reasoning_effort,
                             context_epoch=context_epoch, reply_to_message_id=user.id,
                             generation_status="streaming", source_version=source_version)
        session.add(assistant)
        await session.flush()
        user_id, assistant_id = user.id, assistant.id
        chat.codex_thread_id = None

    async def persist_interrupted(content: str, error: str) -> None:
        async with SessionLocal() as session, session.begin():
            message = await session.get(Message, assistant_id, with_for_update=True)
            if message and message.generation_status == "streaming":
                message.content = content[-8_000:]
                message.generation_status = "interrupted"
                message.generation_error = error[:500]

    async def generate():
        if document_id is not None and str(document_id) in getattr(request.app.state, "maintenance_deleting_documents", set()):
            await persist_interrupted("", "Ответ прерван из-за удаления документа.")
            return
        if str(chat_id) in getattr(request.app.state, "maintenance_deleting_chats", set()):
            await persist_interrupted("", "Ответ прерван из-за удаления чата.")
            return
        current_task = asyncio.current_task()
        if current_task is not None:
            tasks[str(assistant_id)] = current_task
        answer_parts: list[str] = []
        buffers[str(assistant_id)] = answer_parts
        persisted_length = 0
        try:
            yield _sse("started", {"user_message_id": str(user_id), "assistant_message_id": str(assistant_id),
                                   "context_epoch": context_epoch, "model": response_model,
                                   "reasoning_effort": response_reasoning_effort, "source_version": source_version,
                                   "source_versions": [{"document_id": str(document.id), "source_version": link.source_version}
                                                       for link, document in comparison_sources]})
            if comparison_sources:
                yield _sse("sources", {"sources": structured_source_events})
                if not comparison_available_sources:
                    validated_comparison = ComparisonResponse(
                        documents=[ComparisonDocumentFinding(
                            document_id=item["document_id"], status="not_found", answer="", citations=[],
                        ) for item in comparison_document_manifest],
                        comparison=ComparisonSynthesis(status="not_found", answer="", citations=[]),
                    )
                else:
                    structured_parts: list[str] = []
                    async for item in codex.stream_chat(
                        payload, None, model=response_model, reasoning_effort=response_reasoning_effort,
                        output_schema=comparison_output_schema(), base_instructions=COMPARISON_INSTRUCTIONS,
                        ephemeral=True,
                    ):
                        if item["kind"] != "delta":
                            continue
                        structured_parts.append(item["text"])
                        if sum(map(len, structured_parts)) > 32_000:
                            raise ValueError("Ответ сравнения превысил допустимый размер.")
                    validated_comparison = validate_comparison_response(
                        "".join(structured_parts),
                        selected_document_ids=[item["document_id"] for item in comparison_document_manifest],
                        available_sources=comparison_available_sources,
                    )
                answer, citation_ids = render_comparison_answer(
                    validated_comparison,
                    selected_documents=[(item["document_id"], item["filename"]) for item in comparison_document_manifest],
                )
                async with SessionLocal() as session:
                    current_chat = await session.get(Chat, chat_id)
                    citation_sources = await _sources_for_chat_ids(session, current_chat, citation_ids) if current_chat else []
                if len(citation_sources) != len(citation_ids):
                    raise ValueError("Не удалось разрешить одну из цитат выбранных документов.")
                source_by_id = {str(chunk.id): chunk for chunk in chunks}
                document_by_id = {str(document.id): document for _, document in comparison_sources}
                snapshots = [citation_snapshot(
                    source_id,
                    document_id=str(source_by_id[source_id].document_id),
                    filename=document_by_id[str(source_by_id[source_id].document_id)].filename,
                    source_version=source_by_id[source_id].version,
                    locator=source_by_id[source_id].locator or {},
                    ordinal=source_by_id[source_id].ordinal,
                    is_derived=source_by_id[source_id].is_derived,
                ) for source_id in citation_ids]
                async with SessionLocal() as session, session.begin():
                    current_chat = await session.get(Chat, chat_id)
                    message = await session.get(Message, assistant_id, with_for_update=True)
                    if (current_chat is None or current_chat.context_epoch != context_epoch or
                            message is None or message.generation_status != "streaming"):
                        return
                    if str(chat_id) in getattr(request.app.state, "maintenance_deleting_chats", set()):
                        message.content = ""
                        message.generation_status = "interrupted"
                        message.generation_error = "Ответ прерван из-за удаления чата."
                        return
                    message.content = answer
                    message.citations = citation_ids
                    message.citation_snapshots = snapshots
                    message.generation_status = "complete"
                    message.generation_error = None
                if answer:
                    answer_parts.append(answer)
                    yield _sse("delta", {"text": answer})
                yield _sse("done", {"assistant_message_id": str(assistant_id), "answer": answer,
                                     "citations": [source.model_dump() for source in citation_sources]})
            elif use_app_contract:
                yield _sse("sources", {"sources": structured_source_events})
                if request_scope == "application" and not app_matches:
                    validated = AppHelpResponse(
                        answer="В текущей версии приложения я не нашёл подтверждённой информации об этой функции.",
                        status="not_found", scope="unknown", citations=[], ui_target_id=None,
                    )
                elif not structured_sources:
                    validated = AppHelpResponse(
                        answer="В доступных источниках не нашлось подтверждения для уверенного ответа.",
                        status="not_found", scope="unknown", citations=[], ui_target_id=None,
                    )
                else:
                    structured_parts: list[str] = []
                    async for item in codex.stream_chat(
                        payload, None, model=response_model, reasoning_effort=response_reasoning_effort,
                        output_schema=app_help_output_schema(), base_instructions=APP_ASSISTANT_INSTRUCTIONS,
                        ephemeral=True,
                    ):
                        if item["kind"] != "delta":
                            continue
                        structured_parts.append(item["text"])
                        if sum(map(len, structured_parts)) > 32_000:
                            raise ValueError("Ответ помощника превысил допустимый размер.")
                    validated = validate_app_help_response(
                        "".join(structured_parts), available_sources=structured_sources,
                    )
                answer = format_validated_app_answer(validated)[:8_000].strip()
                citation_ids = [citation.source_id for citation in validated.citations]
                async with SessionLocal() as session:
                    current_chat = await session.get(Chat, chat_id)
                    citation_sources = (
                        await _sources_for_chat_ids(session, current_chat, citation_ids)
                        if current_chat
                        else []
                    )
                if len(citation_sources) != len(citation_ids):
                    raise ValueError("Не удалось разрешить одну из проверенных ссылок ответа.")
                citation_data = [source.model_dump() for source in citation_sources]
                async with SessionLocal() as session, session.begin():
                    current_chat = await session.get(Chat, chat_id)
                    message = await session.get(Message, assistant_id, with_for_update=True)
                    if (current_chat is None or current_chat.context_epoch != context_epoch or
                            message is None or message.generation_status != "streaming"):
                        return
                    if str(chat_id) in getattr(request.app.state, "maintenance_deleting_chats", set()):
                        message.content = ""
                        message.generation_status = "interrupted"
                        message.generation_error = "Ответ прерван из-за удаления чата."
                        return
                    message.content = answer
                    message.citations = citation_ids
                    message.generation_status = "complete"
                    message.generation_error = None
                    message.ui_target_id = validated.ui_target_id
                    message.ui_target_catalog_version = APP_HELP_CATALOG_VERSION if validated.ui_target_id else None
                    message.ui_target_build_id = settings.app_build_id if validated.ui_target_id else None
                if answer:
                    answer_parts.append(answer)
                    yield _sse("delta", {"text": answer})
                yield _sse("done", {"assistant_message_id": str(assistant_id), "answer": answer,
                                     "citations": citation_data, "ui_target_id": validated.ui_target_id,
                                     "ui_target_catalog_version": APP_HELP_CATALOG_VERSION if validated.ui_target_id else None,
                                     "ui_target_build_id": settings.app_build_id if validated.ui_target_id else None,
                                     "response_status": validated.status, "response_scope": validated.scope})
            else:
                yield _sse("sources", {"sources": [{
                    "label": label, "id": str(chunk.id), "text": chunk.text[:2_500], "locator": chunk.locator,
                    "ordinal": chunk.ordinal, "is_derived": chunk.is_derived, "source_type": "document",
                } for label, chunk in source_map.items()]})
                async for item in codex.stream_chat(payload, None, model=response_model,
                                                    reasoning_effort=response_reasoning_effort):
                    if item["kind"] != "delta":
                        continue
                    answer_parts.append(item["text"])
                    buffers[str(assistant_id)] = answer_parts
                    answer_now = "".join(answer_parts)
                    if len(answer_now) - persisted_length >= 512:
                        async with SessionLocal() as session, session.begin():
                            message = await session.get(Message, assistant_id, with_for_update=True)
                            if message is None or message.generation_status != "streaming":
                                return
                            message.content = answer_now[-8_000:]
                        persisted_length = len(answer_now)
                    yield _sse("delta", {"text": item["text"]})
                raw_answer = "".join(answer_parts).strip()
                found_labels = re.findall(r"\[(S\d{2})\]", raw_answer)
                valid_labels = list(dict.fromkeys(label for label in found_labels if label in source_map))
                citations = [source_map[label] for label in valid_labels]
                answer = (format_source_markers(raw_answer, valid_labels)[:8_000].strip() if citations else
                          "В документе не найдено достаточно подтверждений для уверенного ответа.")
                async with SessionLocal() as session, session.begin():
                    message = await session.get(Message, assistant_id, with_for_update=True)
                    if message is None or message.generation_status != "streaming":
                        return
                    message.content = answer
                    message.citations = [str(chunk.id) for chunk in citations]
                    message.generation_status = "complete"
                    message.generation_error = None
                citation_data = [{
                    "label": label, "id": str(chunk.id), "text": chunk.text[:2_500], "locator": chunk.locator,
                    "ordinal": chunk.ordinal, "is_derived": chunk.is_derived, "source_type": "document",
                } for label, chunk in zip(valid_labels, citations, strict=True)]
                yield _sse("done", {"assistant_message_id": str(assistant_id), "answer": answer,
                                     "citations": citation_data})
        except asyncio.CancelledError:
            await persist_interrupted("".join(answer_parts), "Ответ остановлен или соединение было прервано. Частичный текст сохранён; вопрос можно повторить.")
            raise
        except GeneratorExit:
            await persist_interrupted("".join(answer_parts), "Соединение было прервано до завершения. Сохранённый фрагмент можно повторить.")
            raise
        except Exception as exc:
            logger.exception("Chat answer failed for %s (%s)", chat_id, type(exc).__name__, exc_info=False)
            message = codex_error_message(exc)
            await persist_interrupted("".join(answer_parts), message)
            yield _sse("error", {"assistant_message_id": str(assistant_id), "message": message})
        finally:
            if current_task is not None and tasks.get(str(assistant_id)) is current_task:
                tasks.pop(str(assistant_id), None)
            buffers.pop(str(assistant_id), None)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.post("/chats/{chat_id}/messages")
async def post_chat_message(chat_id: uuid.UUID, body: SendMessageIn, request: Request) -> StreamingResponse:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Введите вопрос.")
    return await _create_chat_generation(chat_id, request, text=text)


@router.post("/chats/{chat_id}/messages/{message_id}/retry")
async def retry_chat_question(chat_id: uuid.UUID, message_id: uuid.UUID, request: Request) -> StreamingResponse:
    return await _create_chat_generation(chat_id, request, retry_user_id=message_id)


@router.post("/chats/{chat_id}/messages/{message_id}/stop")
async def stop_chat_generation(chat_id: uuid.UUID, message_id: uuid.UUID, request: Request) -> dict[str, str]:
    tasks = _chat_generation_tasks(request)
    buffers = _chat_generation_buffers(request)
    partial = "".join(buffers.get(str(message_id), []))
    async with SessionLocal() as session, session.begin():
        chat = await session.get(Chat, chat_id)
        message = await session.get(Message, message_id, with_for_update=True)
        if chat is None or message is None or message.chat_id != chat_id or message.role != "assistant":
            raise HTTPException(status_code=404, detail="Формируемый ответ не найден.")
        if message.generation_status == "complete":
            raise HTTPException(status_code=409, detail="Ответ уже завершён.")
        if message.generation_status == "streaming":
            message.content = partial[-8_000:]
            message.generation_status = "interrupted"
            message.generation_error = "Ответ остановлен. Частичный текст сохранён; вопрос можно повторить."
    task = tasks.get(str(message_id))
    if task and task is not asyncio.current_task():
        task.cancel()
    return {"status": "interrupted", "message_id": str(message_id)}


@router.post("/chats/{chat_id}/context", response_model=StartChatContextOut)
async def start_chat_context(chat_id: uuid.UUID, request: Request) -> StartChatContextOut:
    tasks = _chat_generation_tasks(request)
    async with SessionLocal() as session, session.begin():
        chat = (await session.execute(select(Chat).where(Chat.id == chat_id).with_for_update())).scalar_one_or_none()
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        active = (await session.execute(select(Message).where(
            Message.chat_id == chat_id, Message.generation_status == "streaming",
        ).with_for_update())).scalars().all()
        if any(str(item.id) in tasks for item in active):
            raise HTTPException(status_code=409, detail="Сначала остановите текущий ответ.")
        for item in active:
            item.generation_status = "interrupted"
            item.generation_error = "Ответ был прерван до завершения. Сохранённый фрагмент можно повторить."
        count = await session.scalar(select(func.count(Message.id)).where(Message.chat_id == chat_id)) or 0
        chat.context_epoch += 1
        chat.codex_thread_id = None
        return StartChatContextOut(context_epoch=chat.context_epoch, preserved_message_count=count)


@router.delete("/chats/{chat_id}/messages/{message_id}", response_model=DeleteMessagesOut)
async def delete_chat_message(chat_id: uuid.UUID, message_id: uuid.UUID) -> DeleteMessagesOut:
    async with SessionLocal() as session, session.begin():
        chat = (await session.execute(select(Chat).where(Chat.id == chat_id).with_for_update())).scalar_one_or_none()
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        selected = await session.get(Message, message_id, with_for_update=True)
        if selected is None or selected.chat_id != chat_id:
            raise HTTPException(status_code=404, detail="Сообщение не найдено.")
        parent = selected if selected.role == "user" else None
        if selected.role == "assistant":
            parent = await session.get(Message, selected.reply_to_message_id) if selected.reply_to_message_id else None
            if parent is None:
                parent = (await session.execute(select(Message).where(
                    Message.chat_id == chat_id, Message.role == "user",
                    or_(Message.created_at < selected.created_at,
                        and_(Message.created_at == selected.created_at, Message.id < selected.id)),
                ).order_by(Message.created_at.desc(), Message.id.desc()).limit(1))).scalar_one_or_none()
        if parent is None:
            ids = [selected.id]
        else:
            related = (await session.execute(select(Message).where(
                Message.chat_id == chat_id,
                or_(Message.id == parent.id, Message.reply_to_message_id == parent.id),
            ).with_for_update())).scalars().all()
            ids = [item.id for item in related]
        if any(item.generation_status == "streaming" for item in (related if parent else [selected])):
            raise HTTPException(status_code=409, detail="Сначала остановите формируемый ответ.")
        await session.execute(delete(Message).where(Message.id.in_(ids)))
        chat.codex_thread_id = None
    return DeleteMessagesOut(deleted_ids=[str(value) for value in ids])


def codex_error_message(exc: Exception) -> str:
    if isinstance(exc, CodexUnavailable):
        return str(exc)
    return "Не удалось получить ответ от Codex. Проверьте состояние входа и интернета, затем повторите вопрос."


@router.get('/documents/{document_id}/jobs', response_model=list[ProcessingJobOut])
async def document_jobs(document_id: uuid.UUID):
    async with SessionLocal() as session:
        if await session.get(Document, document_id) is None:
            raise HTTPException(status_code=404, detail='Документ не найден.')
        jobs = (await session.execute(select(ProcessingJob).where(ProcessingJob.document_id == document_id)
                .order_by(ProcessingJob.created_at.desc()).limit(100))).scalars().all()
        now = await session.scalar(select(func.clock_timestamp()))
        queued_positions = (await session.execute(
            select(
                ProcessingJob.id,
                func.row_number().over(order_by=(ProcessingJob.created_at, ProcessingJob.id)).label('position'),
            ).where(ProcessingJob.state == 'queued')
        )).all()
        positions = {job_id: int(position) for job_id, position in queued_positions}
        return [ProcessingJobOut(id=str(j.id), operation=j.operation, version=j.version, input_version=j.input_version,
                     state=j.state, stage=j.stage, progress=j.progress, attempts=j.attempts,
                     heartbeat=j.heartbeat, queued_at=j.queued_at, started_at=j.started_at, stage_started_at=j.stage_started_at,
                     lease_until=j.lease_until, queue_position=positions.get(j.id),
                     queue_wait_seconds=max(0, int(((now if j.state == 'queued' else (j.started_at or j.finished_at)) - j.queued_at).total_seconds())) if j.state == 'queued' or j.started_at or j.finished_at else None,
                     stage_elapsed_seconds=max(0, int((now - j.stage_started_at).total_seconds())) if j.stage_started_at and j.state in {'running', 'cancelling'} else None,
                     max_attempts=j.max_attempts,
                     error=j.error, error_code=j.error_code,
                     parameters=j.parameters, created_at=j.created_at, finished_at=j.finished_at) for j in jobs]


@router.get('/version', response_model=AppVersionOut)
async def app_version(request: Request, response: Response) -> AppVersionOut:
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return AppVersionOut(
        version=request.app.version,
        build_id=settings.app_build_id,
        commit=settings.app_build_commit,
        built_at=settings.app_build_time,
    )


@router.post('/documents/{document_id}/cancel', status_code=202)
async def cancel_processing(document_id: uuid.UUID):
    try:
        await cancel(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Документ не найден.') from exc
    return {'status': 'accepted'}
