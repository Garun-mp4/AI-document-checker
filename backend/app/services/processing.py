from __future__ import annotations

import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Chat, Document
from app.services.job_queue import enqueue, submit


class DocumentProcessor:
    """API queue facade; it never runs document processing in memory."""
    def __init__(self, codex):
        self.codex = codex

    async def start(self):
        async with SessionLocal() as session, session.begin():
            documents = (await session.execute(select(Document).with_for_update())).scalars().all()
            chat_ids = set((await session.execute(select(Chat.document_id))).scalars().all())
            for document in documents:
                if document.id not in chat_ids:
                    session.add(Chat(document_id=document.id))
                if document.status in {'queued', 'extracting', 'ocr', 'indexing', 'analyzing'}:
                    # Existing queue jobs are preserved by enqueue's partial unique invariant.
                    await enqueue(session, document, 'process')

        try:
            state = await self.codex.status(refresh=True)
            if state["authenticated"] and state["model_available"] and state["reasoning_available"]:
                await self.schedule_pending_analysis()
        except Exception as exc:
            logging.getLogger(__name__).exception("Could not resume pending analysis (%s)", type(exc).__name__, exc_info=False)

    async def stop(self):
        pass

    async def retry(self, document_id, operation='retry', *, parameters=None, reject_if_active=False):
        return await submit(document_id, operation, parameters=parameters,
                            reject_if_active=reject_if_active)

    async def schedule_pending_analysis(self):
        async with SessionLocal() as session:
            ids = (await session.execute(select(Document.id).where(
                Document.status.in_(('needs_auth', 'model_unavailable'))))).scalars().all()
        for document_id in ids:
            await submit(document_id, 'retry')
