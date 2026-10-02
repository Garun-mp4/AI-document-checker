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
from sqlalchemy import case, func, select, update

from app.config import settings
from app.database import SessionLocal
from app.models import (
    Chat,
    Chunk,
    Document,
    DocumentVersion,
    Insight,
    Message,
    ProcessingJob,
)
from app.schemas import (
    AppVersionOut,
    ChatLibraryPageOut,
    ChatOut,
    ChatSettingsOut,
    ChatSummaryOut,
    ChatUpdateIn,
    CodexPreferencesIn,
    DocumentExportIn,
    DocumentOut,
    DocumentPreviewOut,
    DocumentSearchOut,
    InsightOut,
    MarkdownOut,
    MessageOut,
    OcrReprocessIn,
    ProcessingJobOut,
    SendMessageIn,
    SourceOut,
    TablePreviewOut,
)
from app.services.artifact_response import artifact_response
from app.services.chat_library import get_chat_settings, list_chat_library as query_chat_library
from app.services.citations import format_source_markers
from app.services.codex import (
    CodexModelUnavailable,
    CodexNeedsLogin,
    CodexPreferenceError,
    CodexUnavailable,
)
from app.services.document_export import (
    MAX_EXPORT_SOURCES,
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
from app.services.job_queue import active_chunk_version, cancel, cleanup_files, enqueue
from app.services.parsing import (
    SUPPORTED_EXTENSIONS,
    DocumentParsingError,
    _decode_text_with_encoding,
    safe_filename,
)
from app.services.preview import MAX_PREVIEW_BLOCKS, build_preview
from app.services.retrieval import search_chunks
from app.services.source_locators import versioned_source_locator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


_DOCUMENT_MEDIA_TYPES = {
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


def document_media_type(file_type: str, filename: str) -> str:
    """Return a stable browser MIME type for every supported upload format."""

    normalized = file_type.casefold().lstrip(".")
    return _DOCUMENT_MEDIA_TYPES.get(normalized) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


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


async def _message_out(session, chat: Chat, message: Message) -> MessageOut:
    return MessageOut(
        id=str(message.id), role=message.role, content=message.content,
        citations=await _sources_for_ids(session, chat.document_id, message.citations or []),
        model=message.model, reasoning_effort=message.reasoning_effort,
        created_at=message.created_at,
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
    try:
        await cancel(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Документ не найден.') from exc
    # Wait for child teardown. Lease fencing still prevents writes if the worker died.
    deadline = asyncio.get_running_loop().time() + settings.queue_heartbeat_seconds + 2
    while asyncio.get_running_loop().time() < deadline:
        async with SessionLocal() as session:
            active = await session.scalar(select(ProcessingJob.id).where(
                ProcessingJob.document_id == document_id, ProcessingJob.state == 'cancelling'))
        if active is None:
            break
        await asyncio.sleep(0.1)
    async with SessionLocal() as session:
        document = await session.get(Document, document_id, with_for_update=True)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        versions = (await session.execute(select(DocumentVersion).where(DocumentVersion.document_id == document_id))).scalars().all()
        snapshots = [version.snapshot for version in versions]
        path = document.storage_path
        await session.delete(document)
        await session.commit()
    original_search_source_cache.remove_document(str(document_id))
    for snapshot in snapshots:
        try:
            cleanup_files(snapshot, document_id)
        except (OSError, ValueError, DocumentParsingError):
            logger.warning("Refused unsafe or unavailable version artifact deletion")
    for artifact in (path, document.markdown_path, document.markdown_map_path):
        if artifact:
            try:
                remove_storage(owned_storage(artifact, document_id))
            except (OSError, DocumentParsingError):
                logger.warning('Refused unsafe or unavailable artifact deletion')


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
        chunks = (await session.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id, Chunk.version == await active_chunk_version(session, document))
            .order_by(Chunk.ordinal)
            .limit(MAX_PREVIEW_BLOCKS + 1)
        )).scalars().all()
        payload = build_preview(
            document_id=str(document.id),
            file_type=document.file_type,
            metadata=document.metadata_json or {},
            chunks=chunks,
            original_url=f"/api/v1/documents/{document.id}/file",
            total_blocks=document.chunk_count,
            processing_version=document.active_version or 1,
        )
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
) -> TablePreviewOut:
    """Return a paginated view of the original CSV table.

    The storage path is resolved and checked against the upload root before it
    is read. The endpoint never accepts a filesystem path from the client.
    """

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.file_type not in {"csv", "xlsx", "xls"}:
            raise HTTPException(status_code=400, detail="Табличный просмотр доступен только для CSV, XLSX и XLS.")
        try:
            path = owned_storage(document.storage_path, document_id)
        except DocumentParsingError as exc:
            raise HTTPException(status_code=404, detail='Исходный файл недоступен.') from exc
    try:
        payload = await run_document_operation('table', path, file_type=document.file_type, offset=offset, limit=limit, sheet=sheet)
    except DocumentParsingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TablePreviewOut.model_validate(payload)


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
    try:
        await request.app.state.processor.retry(document_id, 'process')
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Документ не найден.') from exc
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        return _document_out(document)


@router.get("/documents/{document_id}/insights", response_model=list[InsightOut])
async def document_insights(document_id: uuid.UUID) -> list[InsightOut]:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        insights = (await session.execute(
            select(Insight).where(Insight.document_id == document_id, Insight.version == document.active_version).order_by(Insight.created_at, Insight.id)
        )).scalars().all()
        return [InsightOut(
            id=str(item.id), key=item.key, question=item.question, answer=item.answer,
            citations=await _sources_for_ids(session, document_id, item.citations or []),
        ) for item in insights]


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
            export_messages: tuple[ExportMessage, ...] = ()
        else:
            export_insights = ()
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
            messages=export_messages,
        )

    try:
        content = render_export(snapshot, body.scope, body.format, set(body.selected_keys))
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
        return ChatOut(id=str(chat.id), document_id=str(chat.document_id))


@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
async def chat_messages(chat_id: uuid.UUID) -> list[MessageOut]:
    async with SessionLocal() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        messages = (await session.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at, Message.id)
        )).scalars().all()
        return [await _message_out(session, chat, message) for message in messages]


@router.post("/chats/{chat_id}/messages")
async def post_chat_message(chat_id: uuid.UUID, body: SendMessageIn, request: Request) -> StreamingResponse:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Введите вопрос.")
    codex = request.app.state.codex
    try:
        await codex.require_ready()
    except (CodexNeedsLogin, CodexModelUnavailable) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CodexUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response_model = settings.codex_model
    response_reasoning_effort = settings.codex_reasoning_effort

    async with SessionLocal() as session:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")
        document = await session.get(Document, chat.document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        if document.status != "ready":
            raise HTTPException(status_code=409, detail="Документ ещё обрабатывается или требует повторной обработки.")
        history = (await session.execute(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at.desc()).limit(8)
        )).scalars().all()
        history.reverse()
        codex_thread_id = chat.codex_thread_id
        document_id = document.id
        file_type = document.file_type
        session.add(Message(chat_id=chat_id, role="user", content=text, citations=[]))
        await session.commit()

    retrieval_query = " ".join([item.content for item in history[-4:]] + [text])[-1_500:]
    chunks = await search_chunks(document_id, retrieval_query, limit=8)
    if file_type == "csv" and re.search(
        r"сумм|средн|миним|максим|итог|количеств|агрегат|скольк|посчит|вычисл|рассчит|подсчит|average|sum|total",
        retrieval_query,
        re.IGNORECASE,
    ):
        async with SessionLocal() as session:
            derived = (await session.execute(
                select(Chunk).where(Chunk.document_id == document_id, Chunk.is_derived.is_(True), Chunk.version == await active_chunk_version(session, document)).order_by(Chunk.ordinal)
            )).scalars().all()
        table_summary = next((chunk for chunk in derived if not chunk.locator.get("column")), None)
        numeric_sources = [chunk for chunk in derived if chunk.locator.get("column")]
        if numeric_sources:
            query_lower = retrieval_query.casefold()
            named_sources = [chunk for chunk in numeric_sources if str(chunk.locator.get("column", "")).casefold() in query_lower]
            selected_derived = ([table_summary] if table_summary else []) + (named_sources or numeric_sources[:7])
            selected_derived = selected_derived[:8]
            selected_ids = {chunk.id for chunk in selected_derived}
            chunks = (selected_derived + [chunk for chunk in chunks if chunk.id not in selected_ids])[:8]
        elif table_summary:
            chunks = [table_summary, *chunks[:7]]
    sources = []
    source_map: dict[str, Chunk] = {}
    for index, chunk in enumerate(chunks, start=1):
        label = f"S{index:02d}"
        source_map[label] = chunk
        sources.append({"label": label, "location": str(chunk.locator.get("label", "Фрагмент документа")), "excerpt": chunk.text[:1_500]})
    payload = {
        "question": text,
        "previous_messages": [{"role": item.role, "text": item.content[-2_000:]} for item in history[-8:]],
        "sources": sources,
        "instruction": "Ответь по-русски и поставь метку [Sxx] после каждого подтверждённого факта. Если источники не дают ответа, прямо скажи об этом без меток.",
    }

    async def generate():
        answer_parts: list[str] = []
        thread_id: str | None = None
        try:
            yield _sse("sources", {"sources": [{
                "label": label,
                "id": str(chunk.id),
                "text": chunk.text[:2_500],
                "locator": chunk.locator,
                "ordinal": chunk.ordinal,
                "is_derived": chunk.is_derived,
            } for label, chunk in source_map.items()]})
            async for item in codex.stream_chat(
                payload,
                codex_thread_id,
                model=response_model,
                reasoning_effort=response_reasoning_effort,
            ):
                if item["kind"] == "thread":
                    thread_id = item["thread_id"]
                    async with SessionLocal() as session:
                        chat = await session.get(Chat, chat_id)
                        if chat:
                            chat.codex_thread_id = thread_id
                            await session.commit()
                    yield _sse("thread", {"thread_id": thread_id})
                elif item["kind"] == "delta":
                    answer_parts.append(item["text"])
                    yield _sse("delta", {"text": item["text"]})
            raw_answer = "".join(answer_parts).strip()
            found_labels = re.findall(r"\[(S\d{2})\]", raw_answer)
            valid_labels = list(dict.fromkeys(label for label in found_labels if label in source_map))
            citations = [source_map[label] for label in valid_labels]
            if not citations:
                answer = "В документе не найдено достаточно подтверждений для уверенного ответа."
            else:
                answer = format_source_markers(raw_answer, valid_labels)[:8_000].strip()
            async with SessionLocal() as session:
                session.add(Message(
                    chat_id=chat_id,
                    role="assistant",
                    content=answer,
                    citations=[str(chunk.id) for chunk in citations],
                    model=response_model,
                    reasoning_effort=response_reasoning_effort,
                ))
                await session.commit()
            citation_data = [{
                "label": label,
                "id": str(chunk.id), "text": chunk.text[:2_500], "locator": chunk.locator,
                "ordinal": chunk.ordinal, "is_derived": chunk.is_derived,
            } for label, chunk in zip(valid_labels, citations, strict=True)]
            yield _sse("done", {"answer": answer, "citations": citation_data})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Chat answer failed for %s (%s)", chat_id, type(exc).__name__, exc_info=False)
            message = codex_error_message(exc)
            yield _sse("error", {"message": message})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


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
