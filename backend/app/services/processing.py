from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import Chat, Chunk, Document
from app.services.analysis import analyze_document
from app.services.codex import (
    CodexModelUnavailable,
    CodexNeedsLogin,
    CodexService,
    CodexUnavailable,
)
from app.services.embeddings import EmbeddingConfigurationError, embed_passages
from app.services.parsing import (
    DocumentParsingError,
    ParsedDocument,
    SourceBlock,
    parse_document,
)

logger = logging.getLogger(__name__)

PROCESSING_STATES = {"extracting", "indexing", "analyzing"}


def _computed_blocks(parsed: ParsedDocument) -> list[SourceBlock]:
    if parsed.file_type != "csv":
        return []
    metadata = parsed.metadata
    row_end = int(metadata.get("row_count", 0)) + 1
    blocks = [SourceBlock(
        f"Локальная структура таблицы: {metadata.get('row_count', 0)} строк данных, {metadata.get('column_count', 0)} столбцов.",
        {"kind": "csv_derived", "label": "Сводка таблицы", "row_start": 1, "row_end": row_end, "derived": True},
        derived=True,
    )]
    for item in metadata.get("numeric_columns", []):
        blocks.append(SourceBlock(
            f"Локальный расчёт по столбцу «{item['name']}» и значениям строк 2–{row_end}: "
            f"числовых значений {item['count']}; сумма {item['sum']}; среднее {item['average']}; "
            f"минимум {item['minimum']}; максимум {item['maximum']}.",
            {
                "kind": "csv_derived",
                "label": f"Показатели столбца «{item['name']}» · строки 2–{row_end}",
                "column": item["name"],
                "row_start": 2,
                "row_end": row_end,
                "derived": True,
            },
            derived=True,
        ))
    return blocks


class DocumentProcessor:
    def __init__(self, codex: CodexService) -> None:
        self.codex = codex
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    async def start(self) -> None:
        async with SessionLocal() as session:
            interrupted = (await session.execute(
                select(Document).where(Document.status.in_(PROCESSING_STATES))
            )).scalars().all()
            for document in interrupted:
                document.status = "queued"
                document.error_message = None
            queued = (await session.execute(select(Document.id).where(Document.status == "queued"))).scalars().all()
            await session.commit()
        for document_id in queued:
            self.schedule(document_id)
        try:
            state = await self.codex.status(refresh=True)
            if state["authenticated"] and state["model_available"] and state["reasoning_available"]:
                await self.schedule_pending_analysis()
        except Exception:
            logger.exception("Could not resume pending document analysis")

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def schedule(self, document_id: uuid.UUID) -> None:
        task = self._tasks.get(document_id)
        if task and not task.done():
            return
        self._tasks[document_id] = asyncio.create_task(self._process(document_id))

    async def schedule_pending_analysis(self) -> None:
        async with SessionLocal() as session:
            ids = (await session.execute(
                select(Document.id).where(Document.status.in_(["needs_auth", "model_unavailable"]))
            )).scalars().all()
        for document_id in ids:
            self.schedule_analysis(document_id)

    def schedule_analysis(self, document_id: uuid.UUID) -> None:
        task = self._tasks.get(document_id)
        if task and not task.done():
            return
        self._tasks[document_id] = asyncio.create_task(self._analyze(document_id))

    async def retry(self, document_id: uuid.UUID) -> None:
        async with SessionLocal() as session:
            document = await session.get(Document, document_id)
            if document is None:
                raise KeyError(document_id)
            if document.status in {"needs_auth", "model_unavailable"} and document.chunk_count:
                document.status = "analyzing"
                document.error_message = None
                await session.commit()
                self.schedule_analysis(document_id)
            else:
                document.status = "queued"
                document.error_message = None
                await session.commit()
                self.schedule(document_id)

    async def _process(self, document_id: uuid.UUID) -> None:
        try:
            async with SessionLocal() as session:
                document = await session.get(Document, document_id)
                if document is None:
                    return
                path = Path(document.storage_path)
                filename = document.filename
                document.status = "extracting"
                document.error_message = None
                await session.commit()

            content = await asyncio.to_thread(path.read_bytes)
            parsed = await asyncio.to_thread(parse_document, filename, content)
            if not parsed.blocks:
                raise DocumentParsingError("Не удалось извлечь текст из файла.")

            async with SessionLocal() as session:
                document = await session.get(Document, document_id)
                if document is None:
                    return
                await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
                document.file_type = parsed.file_type
                document.metadata_json = parsed.metadata
                document.status = "indexing"
                document.chunk_count = 0
                await session.commit()

            original_blocks = parsed.blocks
            derived_blocks = _computed_blocks(parsed)
            for start in range(0, len(original_blocks), 48):
                batch = original_blocks[start:start + 48]
                vectors = await asyncio.to_thread(embed_passages, [block.text for block in batch], settings.embedding_cache_dir)
                async with SessionLocal() as session:
                    session.add_all([
                        Chunk(
                            document_id=document_id,
                            ordinal=start + index,
                            text=block.text,
                            locator=block.locator,
                            embedding=vector,
                            is_derived=False,
                        )
                        for index, (block, vector) in enumerate(zip(batch, vectors, strict=True))
                    ])
                    document = await session.get(Document, document_id)
                    if document:
                        document.chunk_count = min(start + len(batch), len(original_blocks))
                    await session.commit()

            async with SessionLocal() as session:
                for index, block in enumerate(derived_blocks, start=len(original_blocks)):
                    session.add(Chunk(
                        document_id=document_id,
                        ordinal=index,
                        text=block.text,
                        locator=block.locator,
                        embedding=None,
                        is_derived=True,
                    ))
                document = await session.get(Document, document_id)
                if document:
                    document.chunk_count = len(original_blocks) + len(derived_blocks)
                    existing_chat = (await session.execute(select(Chat.id).where(Chat.document_id == document_id))).scalar_one_or_none()
                    if existing_chat is None:
                        session.add(Chat(document_id=document_id))
                await session.commit()

            await self._analyze(document_id)
        except asyncio.CancelledError:
            raise
        except DocumentParsingError as exc:
            await self._set_error(document_id, "error", str(exc))
        except CodexNeedsLogin as exc:
            await self._set_error(document_id, "needs_auth", str(exc))
        except CodexModelUnavailable as exc:
            await self._set_error(document_id, "model_unavailable", str(exc))
        except Exception as exc:
            logger.exception("Document processing failed for %s", document_id)
            await self._set_error(document_id, "error", self._processing_error(exc))
        finally:
            self._tasks.pop(document_id, None)

    async def _analyze(self, document_id: uuid.UUID) -> None:
        try:
            await analyze_document(document_id, self.codex)
        except asyncio.CancelledError:
            raise
        except CodexNeedsLogin as exc:
            await self._set_error(document_id, "needs_auth", str(exc))
        except CodexModelUnavailable as exc:
            await self._set_error(document_id, "model_unavailable", str(exc))
        except CodexUnavailable as exc:
            await self._set_error(document_id, "error", str(exc))
        except Exception as exc:
            logger.exception("Insight generation failed for %s", document_id)
            await self._set_error(document_id, "error", self._processing_error(exc))
        finally:
            self._tasks.pop(document_id, None)

    async def _set_error(self, document_id: uuid.UUID, status: str, message: str) -> None:
        async with SessionLocal() as session:
            document = await session.get(Document, document_id)
            if document:
                document.status = status
                document.error_message = message[:1_000]
                await session.commit()

    @staticmethod
    def _processing_error(exc: Exception) -> str:
        if isinstance(exc, EmbeddingConfigurationError):
            return str(exc)
        lowered = str(exc).lower()
        if "connect" in lowered or "network" in lowered or "download" in lowered or "https" in lowered:
            return "Не удалось загрузить локальную модель или связаться с Codex. Проверьте интернет и повторите обработку."
        if "out of memory" in lowered or "memory" in lowered:
            return "Не хватило памяти для обработки документа. Закройте другие приложения и повторите попытку."
        return "Не удалось обработать документ. Проверьте файл и повторите попытку."
