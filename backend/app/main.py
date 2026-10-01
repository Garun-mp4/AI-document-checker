from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import router
from app.config import settings
from app.database import engine
from app.services.codex import CodexService
from app.services.processing import DocumentProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    codex = CodexService()
    await codex.start()
    processor = DocumentProcessor(codex)
    codex.on_authenticated = processor.schedule_pending_analysis
    app.state.codex = codex
    app.state.processor = processor
    await processor.start()
    yield
    await processor.stop()
    await codex.close()
    await engine.dispose()


app = FastAPI(title="AI Document Checker API", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def protect_local_writes(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            allowed = set(settings.local_ui_origins)
            if f"{parsed.scheme}://{parsed.netloc}" not in allowed:
                return JSONResponse(status_code=403, content={"detail": "Запрос разрешён только из локального интерфейса."})
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok"}
