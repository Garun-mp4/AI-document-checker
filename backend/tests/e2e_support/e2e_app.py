"""Test-only entrypoint, excluded from production images by .dockerignore.

Only Codex is replaced. Storage, parsers, OCR, embeddings and analysis use
the application's actual implementations. Never import this from app/.
"""
import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy import delete, select

from app import main
from app.config import settings
from app.database import SessionLocal
from app.models import Chat, Chunk, Document, Message
from app.services.codex import CodexService, CodexUnavailable
from app.services.codex_preferences import model_display_name


class DeterministicCodex(CodexService):
    mode = "ready"

    def __init__(self):
        super().__init__()
        self.stream_release = asyncio.Event()
        self.stream_release.set()
        self.complete_calls = 0
        self.chat_calls = 0
        self.structured_calls = 0
        self.ui_target_override: str | None = None
        self.last_request_metadata = {"document_evidence_count": 0, "application_evidence_count": 0, "ephemeral": None}

    async def start(self):
        pass

    async def close(self):
        self.stream_release.set()

    async def status(self, refresh=False):
        return {
            "authenticated": self.mode != "disconnected",
            "model": settings.codex_model,
            "model_label": model_display_name(settings.codex_model),
            "reasoning_effort": settings.codex_reasoning_effort,
            "model_available": self.mode != "unavailable",
            "reasoning_available": True,
            "models": [
                {"id": model, "label": model_display_name(model), "description": "Synthetic E2E provider",
                 "reasoning_efforts": [{"value": value, "label": value.capitalize(), "description": value}
                                       for value in ("low", "medium", "high")]}
                for model in ("gpt-6-luna", "gpt-6.1-sol")
            ],
            "login_state": "idle", "login_error": None, "verification_url": None,
            "user_code": None, "error": "Synthetic unavailable model" if self.mode == "unavailable" else None,
        }

    async def complete(self, payload, output_schema, **preferences):
        self.complete_calls += 1
        await self.require_ready()
        sources = {source["label"]: source["excerpt"] for source in payload["sources"]}
        return json.dumps({"insights": [
            {"key": question["key"], "answer": sources.get(next(iter(question["available_sources"]), ""), ""),
             "citations": question["available_sources"][:1], "not_found": not question["available_sources"]}
            for question in payload["questions"]
        ]}, ensure_ascii=False)

    async def validate_choice(self, model, reasoning_effort):
        options = await self.status()
        selected = next((item for item in options["models"] if item["id"] == model), None)
        if not options["authenticated"] or not selected or reasoning_effort not in {
            item["value"] for item in selected["reasoning_efforts"]
        }:
            from app.services.codex import CodexModelUnavailable
            raise CodexModelUnavailable("Synthetic model unavailable")
        return model, reasoning_effort

    async def stream_chat(self, payload, existing_thread_id=None, *, model=None, reasoning_effort=None,
                          output_schema=None, base_instructions=None, ephemeral=None):
        self.chat_calls += 1
        await self.require_ready()
        yield {"kind": "thread", "thread_id": existing_thread_id or str(uuid.uuid4())}
        if output_schema is not None:
            self.structured_calls += 1
            application_evidence = payload.get("application_evidence", [])
            document_evidence = payload.get("document_evidence", [])
            comparison_documents = payload.get("selected_documents", [])
            self.last_request_metadata = {
                "document_evidence_count": len(document_evidence),
                "application_evidence_count": len(application_evidence),
                "comparison_documents_count": len(comparison_documents),
                "comparison_source_document_ids": sorted({item.get("document_id") for item in payload.get("sources", []) if item.get("document_id")}),
                "ephemeral": ephemeral,
                "scope": payload.get("request_scope"),
            }
            citation_items = []
            if comparison_documents:
                sources_by_document = {
                    document["document_id"]: [source["source_id"] for source in payload.get("sources", [])
                                              if source.get("document_id") == document["document_id"]]
                    for document in comparison_documents
                }
                findings = []
                for document in comparison_documents:
                    source_ids = sources_by_document[document["document_id"]]
                    findings.append({
                        "document_id": document["document_id"],
                        "status": "supported" if source_ids else "not_found",
                        "answer": "Синтетическое подтверждение из этого документа." if source_ids else "",
                        "citations": source_ids[:1],
                    })
                supported_ids = [source_ids[0] for source_ids in sources_by_document.values() if source_ids]
                response = {
                    "documents": findings,
                    "comparison": {
                        "status": "supported" if len(supported_ids) >= 2 else "not_found",
                        "answer": "В документах найдены отдельные подтверждения для сопоставления." if len(supported_ids) >= 2 else "",
                        "citations": supported_ids[:2] if len(supported_ids) >= 2 else [],
                    },
                }
            elif application_evidence:
                application_source = next(
                    (source for source in application_evidence
                     if self.ui_target_override in source.get("ui_target_ids", [])),
                    application_evidence[0],
                )
                citation_items.append({"source_type": "application", "source_id": application_source["source_id"]})
            if not comparison_documents:
                if document_evidence and payload.get("request_scope") == "mixed":
                    citation_items.append({"source_type": "document", "source_id": document_evidence[0]["source_id"]})
                if citation_items:
                    scope = "mixed" if len(citation_items) == 2 else citation_items[0]["source_type"]
                    response = {
                        "answer": "Подтверждённая возможность приложения." + (" Также найден фрагмент документа." if document_evidence else ""),
                        "status": "answered",
                        "scope": scope,
                        "citations": citation_items,
                        "ui_target_id": (
                            self.ui_target_override
                            if self.ui_target_override is not None
                            else (application_source.get("ui_target_ids") or [None])[0]
                        ) if application_evidence else None,
                    }
                else:
                    response = {"answer": "Подтверждений нет.", "status": "not_found", "scope": "unknown", "citations": [], "ui_target_id": None}
            serialized = json.dumps(response, ensure_ascii=False)
            split_at = max(1, len(serialized) // 2)
            yield {"kind": "delta", "text": serialized[:split_at]}
            await self.stream_release.wait()
            if self.mode == "stream_error":
                raise RuntimeError("Synthetic stream failure")
            yield {"kind": "delta", "text": serialized[split_at:]}
            return

        yield {"kind": "delta", "text": "**Синтетический ответ**\n\n"}
        # Test controls release this after asserting an intermediate UI state.
        await self.stream_release.wait()
        if self.mode == "stream_error":
            raise RuntimeError("Synthetic stream failure")
        sources = payload["sources"]
        label = sources[0]["label"] if sources else "S99"
        text = "Нет подтверждений." if "нет доказательств" in payload["question"] else (
            f"- Проверенный источник [{label}]\n- История: {len(payload['previous_messages'])} сообщений.\n\n"
            "## Итог\n\n| Поле | Значение |\n| --- | --- |\n| Проверка | Готово |\n\n"
            "> Тестовая цитата\n\n`inline code`\n\n```python\nprint('synthetic')\n```\n\n"
            "<script>window.__unsafeChatExecuted = true</script>"
        )
        yield {"kind": "delta", "text": text}


main.CodexService = DeterministicCodex
app = main.app


@app.post("/api/v1/__e2e/provider")
async def control(request: Request):
    data = await request.json()
    provider = request.app.state.codex
    provider.mode = data.get("mode", "ready")
    target_id = data.get("ui_target_id")
    if target_id is not None and (not isinstance(target_id, str) or not target_id or len(target_id) > 120):
        raise HTTPException(status_code=422, detail="ui_target_id must be a short string or null")
    provider.ui_target_override = target_id
    if data.get("hold_stream"):
        provider.stream_release.clear()
    else:
        provider.stream_release.set()
    app.state.markdown_failure = data.get('markdown_failure', False)
    app.state.hold_stage = data.get('hold_stage')
    app.state.hold_complete = data.get('hold_complete', False)
    if data.get('reset_counters'):
        provider.complete_calls = 0
        provider.chat_calls = 0
        provider.structured_calls = 0
        provider.last_request_metadata = {"document_evidence_count": 0, "application_evidence_count": 0, "ephemeral": None}
    return {'mode': provider.mode, 'complete_calls': provider.complete_calls, 'chat_calls': provider.chat_calls,
            'structured_calls': provider.structured_calls, 'last_request_metadata': provider.last_request_metadata}


@app.post('/api/v1/__e2e/maintenance/seed')
async def seed_maintenance_artifacts():
    """Seed uniquely named disposable files for local-data maintenance acceptance."""
    fixture_id = uuid.uuid4().hex
    token = f'M15_PRIVATE_AUTH_SENTINEL_{fixture_id}'
    upload_root = Path(settings.upload_dir)
    embedding_root = Path(settings.embedding_cache_dir)
    auth_root = Path(settings.codex_home)
    for directory in (upload_root, embedding_root, auth_root):
        directory.mkdir(parents=True, exist_ok=True)

    temp_file = upload_root / f'.artifact-abandoned-m15-{fixture_id}.tmp'
    parser_cache = upload_root / f'.parsed-cache-{hashlib.sha256(fixture_id.encode()).hexdigest()}.json'
    embedding_cache = embedding_root / f'.embedding-cache-{hashlib.sha256(fixture_id.encode()).hexdigest()}.json'
    auth_file = auth_root / f'm15-auth-{fixture_id}.json'
    temp_file.write_text('stale synthetic temporary artifact', encoding='utf-8')
    parser_cache.write_text('synthetic parser cache', encoding='utf-8')
    embedding_cache.write_bytes(b'synthetic disposable embedding cache')
    auth_file.write_text(json.dumps({'refresh_token': token}), encoding='utf-8')
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    for path in (temp_file, parser_cache, embedding_cache):
        os.utime(path, (stale, stale))
    return {'fixture_id': fixture_id, 'auth_token': token}


@app.get('/api/v1/__e2e/maintenance/verify/{fixture_id}')
async def verify_maintenance_artifacts(fixture_id: str):
    """Report booleans for E2E sentinels without returning their contents."""
    if not re.fullmatch(r'[0-9a-f]{32}', fixture_id):
        raise HTTPException(status_code=422, detail='Invalid maintenance fixture id')
    token = f'M15_PRIVATE_AUTH_SENTINEL_{fixture_id}'
    upload_root = Path(settings.upload_dir)
    embedding_root = Path(settings.embedding_cache_dir)
    auth_file = Path(settings.codex_home) / f'm15-auth-{fixture_id}.json'
    parser_cache = upload_root / f'.parsed-cache-{hashlib.sha256(fixture_id.encode()).hexdigest()}.json'
    embedding_cache = embedding_root / f'.embedding-cache-{hashlib.sha256(fixture_id.encode()).hexdigest()}.json'
    try:
        auth_preserved = token in auth_file.read_text(encoding='utf-8')
    except OSError:
        auth_preserved = False
    return {
        'temporary_removed': not (upload_root / f'.artifact-abandoned-m15-{fixture_id}.tmp').exists(),
        'parser_cache_removed': not parser_cache.exists(),
        'embedding_cache_removed': not embedding_cache.exists(),
        'authorization_preserved': auth_preserved,
    }


@app.post("/api/v1/__e2e/citation")
async def seed_citation(request: Request):
    """Persist a citation chosen by its real source locator for browser tests."""
    data = await request.json()
    try:
        document_id = uuid.UUID(str(data.get("document_id", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid document id") from exc
    row = data.get("row")
    sheet = data.get("sheet")
    if isinstance(row, bool) or not isinstance(row, int) or row < 1 or (sheet is not None and not isinstance(sheet, str)):
        raise HTTPException(status_code=422, detail="A positive row and optional sheet are required")

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        chat = await session.scalar(select(Chat).where(Chat.document_id == document_id))
        if document is None or chat is None or document.status != "ready":
            raise HTTPException(status_code=404, detail="Ready document chat not found")
        chunks = (await session.execute(select(Chunk).where(
            Chunk.document_id == document_id,
            Chunk.version == document.active_version,
            Chunk.is_derived.is_(False),
        ).order_by(Chunk.ordinal))).scalars().all()
        source = next((chunk for chunk in chunks if
            chunk.locator.get("row_start", chunk.locator.get("row")) is not None
            and chunk.locator.get("row_start", chunk.locator.get("row")) <= row
            <= chunk.locator.get("row_end", chunk.locator.get("row_start", chunk.locator.get("row")))
            and (sheet is None or chunk.locator.get("sheet") == sheet)), None)
        if source is None:
            raise HTTPException(status_code=404, detail="Source locator not found")
        message = Message(
            chat_id=chat.id,
            role="assistant",
            content="Тестовая проверка источника〔1〕",
            citations=[str(source.id)],
        )
        session.add(message)
        await session.commit()
        return {"chat_id": str(chat.id), "message_id": str(message.id), "source_id": str(source.id)}


@app.post("/api/v1/__e2e/library/seed")
async def seed_chat_library(request: Request):
    """Create isolated newer chats and older messages for library acceptance tests."""
    data = await request.json()
    try:
        target_document_id = uuid.UUID(str(data.get("target_document_id", "")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid target document id") from exc
    count = data.get("count", 31)
    marker = data.get("marker", "")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 50:
        raise HTTPException(status_code=422, detail="Seed count must be between 0 and 50")
    if not isinstance(marker, str) or not marker or len(marker) > 120:
        raise HTTPException(status_code=422, detail="A marker of at most 120 characters is required")

    now = datetime.now(timezone.utc)
    created_ids = []
    async with SessionLocal() as session:
        target = await session.get(Document, target_document_id)
        target_chat = await session.scalar(select(Chat).where(Chat.document_id == target_document_id))
        if target is None or target_chat is None:
            raise HTTPException(status_code=404, detail="Target document chat not found")
        for index in range(16):
            session.add(Message(
                chat_id=target_chat.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"Тестовое старое сообщение {index + 1}",
                citations=[],
                created_at=now - timedelta(days=32 - index),
            ))
        matched_message = Message(
            chat_id=target_chat.id,
            role="assistant",
            content=f"Найденное старое сообщение: {marker}",
            citations=[],
            created_at=now - timedelta(days=15),
        )
        session.add(matched_message)
        base = now + timedelta(minutes=5)
        for index in range(count):
            document_id = uuid.uuid4()
            moment = base + timedelta(seconds=index)
            session.add(Document(
                id=document_id,
                filename=f"library-seed-{index + 1:02}.txt",
                storage_path=f"/tmp/library-seed-{document_id}.txt",
                file_type="txt",
                file_size=1,
                status="ready",
                error_message=None,
                chunk_count=0,
                metadata_json={},
                created_at=moment,
                updated_at=moment,
            ))
            await session.flush()
            session.add(Chat(document_id=document_id, created_at=moment))
            created_ids.append(str(document_id))
        await session.commit()
        await session.refresh(matched_message)
        return {"document_ids": created_ids, "target_message_id": str(matched_message.id)}


@app.delete("/api/v1/__e2e/library/seed")
async def remove_seeded_chats(request: Request):
    data = await request.json()
    raw_ids = data.get("document_ids", [])
    if not isinstance(raw_ids, list) or len(raw_ids) > 50:
        raise HTTPException(status_code=422, detail="Seed document ids must be a list of at most 50 items")
    try:
        document_ids = [uuid.UUID(str(value)) for value in raw_ids]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid seeded document id") from exc
    async with SessionLocal() as session:
        await session.execute(delete(Document).where(Document.id.in_(document_ids)))
        await session.commit()
    return {"deleted": len(document_ids)}


@app.get('/api/v1/__e2e/worker-control')
async def worker_control():
    return {'mode': app.state.codex.mode, 'markdown_failure': getattr(app.state, 'markdown_failure', False),
            'hold_stage': getattr(app.state, 'hold_stage', None),
            'complete_calls': getattr(app.state, 'complete_calls', 0),
            'last_complete_preferences': getattr(app.state, 'last_complete_preferences', {})}


@app.post('/api/v1/__e2e/complete')
async def complete(request: Request):
    data = await request.json()
    app.state.complete_calls = getattr(app.state, 'complete_calls', 0) + 1
    app.state.last_complete_preferences = data.get('preferences', {})
    while getattr(app.state, 'hold_complete', False):
        await asyncio.sleep(.1)
    try:
        return {'result': await app.state.codex.complete(data['payload'], data['schema'])}
    except CodexUnavailable as exc:
        return {'error': type(exc).__name__, 'message': str(exc)}
