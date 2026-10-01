"""Test-only entrypoint, excluded from production images by .dockerignore.

Only Codex is replaced. Storage, parsers, OCR, embeddings and analysis use
the application's actual implementations. Never import this from app/.
"""
import asyncio
import json
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import select

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

    async def complete(self, payload, output_schema):
        await self.require_ready()
        sources = {source["label"]: source["excerpt"] for source in payload["sources"]}
        return json.dumps({"insights": [
            {"key": question["key"], "answer": sources.get(next(iter(question["available_sources"]), ""), ""),
             "citations": question["available_sources"][:1], "not_found": not question["available_sources"]}
            for question in payload["questions"]
        ]}, ensure_ascii=False)

    async def stream_chat(self, payload, existing_thread_id=None):
        await self.require_ready()
        yield {"kind": "thread", "thread_id": existing_thread_id or str(uuid.uuid4())}
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
    if data.get("hold_stream"):
        provider.stream_release.clear()
    else:
        provider.stream_release.set()
    app.state.markdown_failure = data.get('markdown_failure', False)
    app.state.hold_stage = data.get('hold_stage')
    app.state.hold_complete = data.get('hold_complete', False)
    return {'mode': provider.mode}


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


@app.get('/api/v1/__e2e/worker-control')
async def worker_control():
    return {'mode': app.state.codex.mode, 'markdown_failure': getattr(app.state, 'markdown_failure', False),
            'hold_stage': getattr(app.state, 'hold_stage', None),
            'complete_calls': getattr(app.state, 'complete_calls', 0)}


@app.post('/api/v1/__e2e/complete')
async def complete(request: Request):
    data = await request.json()
    app.state.complete_calls = getattr(app.state, 'complete_calls', 0) + 1
    while getattr(app.state, 'hold_complete', False):
        await asyncio.sleep(.1)
    try:
        return {'result': await app.state.codex.complete(data['payload'], data['schema'])}
    except CodexUnavailable as exc:
        return {'error': type(exc).__name__, 'message': str(exc)}
