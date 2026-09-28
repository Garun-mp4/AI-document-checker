from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Chat, Chunk, Document, Insight, Message
from app.schemas import (
    ChatOut,
    ChatSummaryOut,
    CodexPreferencesIn,
    DocumentOut,
    DocumentPreviewOut,
    InsightOut,
    MessageOut,
    SendMessageIn,
    SourceOut,
)
from app.services.chat_library import build_chat_summary
from app.services.citations import format_source_markers
from app.services.codex import (
    CodexModelUnavailable,
    CodexNeedsLogin,
    CodexPreferenceError,
    CodexUnavailable,
)
from app.services.parsing import (
    SUPPORTED_EXTENSIONS,
    DocumentParsingError,
    safe_filename,
)
from app.services.preview import MAX_PREVIEW_BLOCKS, build_preview
from app.services.retrieval import search_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=str(document.id),
        filename=document.filename,
        file_type=document.file_type,
        file_size=document.file_size,
        status=document.status,
        error_message=document.error_message,
        chunk_count=document.chunk_count,
        metadata=document.metadata_json or {},
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
    rows = (await session.execute(
        select(Chunk).where(Chunk.document_id == document_id, Chunk.id.in_(parsed_ids))
    )).scalars().all()
    by_id = {str(chunk.id): chunk for chunk in rows}
    return [
        SourceOut(id=value, text=by_id[value].text[:2_500], locator=by_id[value].locator,
                  ordinal=by_id[value].ordinal, is_derived=by_id[value].is_derived)
        for value in ids if value in by_id
    ]


async def _message_out(session, chat: Chat, message: Message) -> MessageOut:
    return MessageOut(
        id=str(message.id), role=message.role, content=message.content,
        citations=await _sources_for_ids(session, chat.document_id, message.citations or []),
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
    """List persisted document conversations ordered by their last activity."""

    async with SessionLocal() as session:
        pairs = (await session.execute(
            select(Chat, Document)
            .join(Document, Document.id == Chat.document_id)
            .order_by(Chat.created_at.desc())
        )).all()
        if not pairs:
            return []
        chat_ids = [chat.id for chat, _ in pairs]
        messages = (await session.execute(
            select(Message)
            .where(Message.chat_id.in_(chat_ids))
            .order_by(Message.created_at, Message.id)
        )).scalars().all()
        messages_by_chat: dict[uuid.UUID, list[Message]] = {chat_id: [] for chat_id in chat_ids}
        for message in messages:
            messages_by_chat.setdefault(message.chat_id, []).append(message)
        summaries = [
            build_chat_summary(chat, document, messages_by_chat.get(chat.id, []))
            for chat, document in pairs
        ]
        return sorted(summaries, key=lambda item: item.last_activity_at, reverse=True)


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

    pieces: list[bytes] = []
    size = 0
    while piece := await file.read(1024 * 1024):
        size += len(piece)
        if size > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Файл превышает максимальный размер 25 MiB.")
        pieces.append(piece)
    await file.close()
    if not size:
        raise HTTPException(status_code=400, detail="Файл пустой.")

    document_id = uuid.uuid4()
    root = Path(settings.upload_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    storage_path = root / f"{document_id}{extension}"
    content = b"".join(pieces)
    try:
        await asyncio.to_thread(storage_path.write_bytes, content)
        document = Document(
            id=document_id,
            filename=filename,
            storage_path=str(storage_path),
            file_type=extension.removeprefix("."),
            file_size=size,
            status="queued",
            metadata_json={},
        )
        async with SessionLocal() as session:
            session.add(document)
            # Create the durable conversation before processing starts so a
            # queued or failed upload is still visible in the chat library.
            session.add(Chat(document_id=document_id))
            await session.commit()
            await session.refresh(document)
    except Exception:
        storage_path.unlink(missing_ok=True)
        logger.exception("Could not persist uploaded file")
        raise HTTPException(status_code=500, detail="Не удалось сохранить файл локально.")
    request.app.state.processor.schedule(document_id)
    return _document_out(document)


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID) -> DocumentOut:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        return _document_out(document)


@router.post("/documents/{document_id}/retry", response_model=DocumentOut, status_code=202)
async def retry_document(document_id: uuid.UUID, request: Request) -> DocumentOut:
    try:
        await request.app.state.processor.retry(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Документ не найден.") from exc
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        return _document_out(document)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, request: Request) -> None:
    processor = request.app.state.processor
    task = processor._tasks.get(document_id)
    if task and not task.done():
        task.cancel()
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        path = Path(document.storage_path).resolve()
        upload_root = Path(settings.upload_dir).resolve()
        await session.delete(document)
        await session.commit()
    if path.is_relative_to(upload_root):
        path.unlink(missing_ok=True)


@router.get("/documents/{document_id}/chunks", response_model=list[SourceOut])
async def list_chunks(document_id: uuid.UUID, offset: int = 0, limit: int = 50) -> list[SourceOut]:
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset не может быть отрицательным.")
    limit = max(1, min(limit, 200))
    async with SessionLocal() as session:
        if await session.get(Document, document_id) is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        chunks = (await session.execute(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal).offset(offset).limit(limit)
        )).scalars().all()
        return [SourceOut(
            id=str(chunk.id), text=chunk.text[:2_500], locator=chunk.locator,
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
            .where(Chunk.document_id == document_id)
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
        )
        return DocumentPreviewOut.model_validate(payload)


@router.get("/documents/{document_id}/file")
async def document_file(document_id: uuid.UUID) -> FileResponse:
    """Serve the locally stored original for the native PDF viewer.

    The path is checked against the configured upload directory before the
    response is created.  This keeps the endpoint limited to uploaded files.
    """

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        path = Path(document.storage_path).resolve()
        upload_root = Path(settings.upload_dir).resolve()
        if not path.is_relative_to(upload_root) or not path.is_file():
            raise HTTPException(status_code=404, detail="Исходный файл документа недоступен.")
        media_type = mimetypes.guess_type(document.filename)[0] or "application/octet-stream"
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Content-Disposition": "inline"},
        )


@router.get("/documents/{document_id}/insights", response_model=list[InsightOut])
async def document_insights(document_id: uuid.UUID) -> list[InsightOut]:
    async with SessionLocal() as session:
        if await session.get(Document, document_id) is None:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        insights = (await session.execute(
            select(Insight).where(Insight.document_id == document_id).order_by(Insight.created_at, Insight.id)
        )).scalars().all()
        return [InsightOut(
            id=str(item.id), key=item.key, question=item.question, answer=item.answer,
            citations=await _sources_for_ids(session, document_id, item.citations or []),
        ) for item in insights]


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
                select(Chunk).where(Chunk.document_id == document_id, Chunk.is_derived.is_(True)).order_by(Chunk.ordinal)
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
            async for item in codex.stream_chat(payload, codex_thread_id):
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
            logger.exception("Chat answer failed for %s", chat_id)
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
