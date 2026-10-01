"""PostgreSQL queue. Every result write is fenced by a document lock and lease."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, func, select, text

from app.config import settings
from app.database import SessionLocal
from app.models import Chunk, Document, DocumentVersion, ProcessingJob
from app.services.document_security import remove_storage

ACTIVE = ('queued', 'running', 'cancelling')
TERMINAL = ('cancelled', 'succeeded', 'failed')


class LeaseLost(Exception):
    pass


async def active_chunk_version(session, document: Document) -> int:
    version = await session.get(DocumentVersion, (document.id, document.active_version))
    return version.chunk_version if version else 0


async def enqueue(session, document: Document, operation: str = 'process', *, parameters: dict | None = None,
                  reject_if_active: bool = False) -> ProcessingJob:
    """Caller holds the document row lock (or is inserting a new document)."""
    existing = (await session.execute(select(ProcessingJob).where(
        ProcessingJob.document_id == document.id, ProcessingJob.state.in_(ACTIVE)
    ))).scalar_one_or_none()
    if existing:
        if reject_if_active:
            raise ValueError('Для документа уже выполняется обработка. Дождитесь её завершения и повторите OCR.')
        return existing
    base = None
    if operation in ('retry', 'analysis'):
        base = (await session.execute(select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.state.in_(('indexed', 'ready')),
        ).order_by(DocumentVersion.number.desc()).limit(1))).scalar_one_or_none()
        if operation == 'analysis' and base is None:
            raise ValueError('Нет сохранённого индекса для повторного анализа.')
        operation = 'analysis' if base else 'process'
    number = document.next_version or 1
    document.next_version = number + 1
    session.add(DocumentVersion(document_id=document.id, number=number,
                                chunk_version=base.chunk_version if base else number,
                                state='indexed' if base else 'staging',
                                snapshot=dict(base.snapshot) if base else {}))
    job_parameters = {'model': settings.codex_model,
                      'reasoning_effort': settings.codex_reasoning_effort,
                      'ocr': {'enabled': settings.ocr_enabled, 'languages': settings.ocr_languages,
                              'dpi': settings.ocr_dpi, 'max_pages': settings.ocr_max_pages},
                      'converter_version': '0.1.8'}
    if parameters:
        job_parameters.update(parameters)
    job = ProcessingJob(document_id=document.id, operation=operation, version=number,
                        input_version=document.input_checksum, state='queued', stage='queued',
                        parameters=job_parameters)
    session.add(job)
    if document.status != 'ready':
        document.status = 'queued'
    document.error_message = None
    return job


async def submit(document_id: uuid.UUID, operation: str = 'retry', *, parameters: dict | None = None,
                 reject_if_active: bool = False) -> ProcessingJob:
    async with SessionLocal() as session, session.begin():
        document = (await session.execute(select(Document).where(Document.id == document_id)
                                         .with_for_update())).scalar_one_or_none()
        if document is None:
            raise KeyError(document_id)
        job = await enqueue(session, document, operation, parameters=parameters,
                            reject_if_active=reject_if_active)
    return job


@asynccontextmanager
async def fenced(job_id: uuid.UUID, owner: uuid.UUID):
    async with SessionLocal() as session, session.begin():
        document_id = await session.scalar(select(ProcessingJob.document_id).where(ProcessingJob.id == job_id))
        document = (await session.execute(select(Document).where(Document.id == document_id)
                                         .with_for_update())).scalar_one_or_none()
        job = await session.get(ProcessingJob, job_id, with_for_update=True)
        now = await session.scalar(select(func.clock_timestamp()))
        if (document is None or job is None or job.owner != owner or job.state != 'running'
                or job.lease_until is None or job.lease_until <= now):
            raise LeaseLost()
        yield session, document, job


def cleanup_files(snapshot: dict, document_id: uuid.UUID) -> None:
    """Only attempt-specific artifacts, never immutable originals or other versions."""
    from pathlib import Path
    prefix = snapshot.get('attempt_prefix')
    if not prefix:
        return
    import re
    if not re.fullmatch(re.escape(str(document_id)) + r'\.v[1-9][0-9]*\.[0-9a-f]{32}', prefix):
        raise ValueError('Invalid processing artifact prefix')
    root = Path(settings.upload_dir).resolve()
    if not root.exists():
        return
    for path in root.iterdir():
        if path.name.startswith(prefix + '.') or path.name.startswith('.artifact-' + prefix + '.'):
            remove_storage(path)


async def discard(session, job: ProcessingJob) -> None:
    version = await session.get(DocumentVersion, (job.document_id, job.version))
    if version and version.state == 'staging':
        cleanup_files(version.snapshot, job.document_id)
        await session.execute(delete(Chunk).where(Chunk.document_id == job.document_id,
                                                  Chunk.version == job.version))
        version.snapshot = {}


async def recover() -> None:
    async with SessionLocal() as session, session.begin():
        documents = (await session.execute(select(Document).join(
            ProcessingJob, ProcessingJob.document_id == Document.id
        ).where(ProcessingJob.state.in_(('running', 'cancelling')),
                ProcessingJob.lease_until < func.clock_timestamp()).with_for_update(of=Document, skip_locked=True))).scalars().all()
        now = await session.scalar(select(func.clock_timestamp()))
        for document in documents:
            job = (await session.execute(select(ProcessingJob).where(
                ProcessingJob.document_id == document.id, ProcessingJob.state.in_(('running', 'cancelling')),
                ProcessingJob.lease_until < func.clock_timestamp()).with_for_update())).scalar_one_or_none()
            if job is None:
                continue
            interrupted = job.stage == 'analysis_request'
            cancelled = job.state == 'cancelling'
            exhausted = job.attempts >= job.max_attempts
            await discard(session, job)
            job.owner = None
            job.lease_until = None
            if cancelled or interrupted or exhausted:
                job.state = 'cancelled' if cancelled else 'failed'
                job.finished_at = now
                job.error_code = 'cancelled' if cancelled else ('analysis_interrupted' if interrupted else 'attempts_exhausted')
                job.error = ('Обработка отменена.' if cancelled else
                             'Запрос к модели был прерван. Повторите анализ явно; автоматический повтор отключён.' if interrupted else
                             'Превышено число попыток восстановления. Повторите обработку.')
                if document.status != 'ready':
                    document.status = 'cancelled' if cancelled else 'error'
                document.error_message = job.error
            else:
                job.state = 'queued'
                job.stage = 'queued'
                job.queued_at = now
                job.started_at = None
                job.stage_started_at = None
                job.error_code = 'worker_recovered'
                if document.status != 'ready':
                    document.status = 'queued'


async def claim() -> ProcessingJob | None:
    async with SessionLocal() as session, session.begin():
        # Serialize the short admission transaction across worker replicas.
        await session.execute(text('SELECT pg_advisory_xact_lock(73402103)'))
        count = await session.scalar(select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.state.in_(('running', 'cancelling')), ProcessingJob.lease_until > func.clock_timestamp()))
        if count >= settings.queue_concurrency:
            return None
        document = (await session.execute(select(Document).join(
            ProcessingJob, ProcessingJob.document_id == Document.id
        ).where(ProcessingJob.state == 'queued').order_by(ProcessingJob.created_at, ProcessingJob.id)
           .with_for_update(of=Document, skip_locked=True).limit(1))).scalar_one_or_none()
        if document is None:
            return None
        job = (await session.execute(select(ProcessingJob).where(
            ProcessingJob.document_id == document.id, ProcessingJob.state == 'queued'
        ).with_for_update())).scalar_one()
        now = await session.scalar(select(func.clock_timestamp()))
        job.state = 'running'
        job.owner = uuid.uuid4()
        job.attempts += 1
        job.heartbeat = now
        job.started_at = now
        job.stage_started_at = now
        job.lease_until = now + timedelta(seconds=settings.queue_lease_seconds)
    return job


async def heartbeat(job_id, owner) -> bool:
    try:
        async with fenced(job_id, owner) as (session, _, job):
            now = await session.scalar(select(func.clock_timestamp()))
            job.heartbeat = now
            job.lease_until = now + timedelta(seconds=settings.queue_lease_seconds)
        return True
    except LeaseLost:
        return False


async def cancel(document_id) -> None:
    async with SessionLocal() as session, session.begin():
        document = (await session.execute(select(Document).where(Document.id == document_id)
                                         .with_for_update())).scalar_one_or_none()
        if document is None:
            raise KeyError(document_id)
        job = (await session.execute(select(ProcessingJob).where(
            ProcessingJob.document_id == document_id, ProcessingJob.state.in_(ACTIVE)
        ).with_for_update())).scalar_one_or_none()
        if job is None:
            return
        if job.state == 'queued':
            await discard(session, job)
            job.state = 'cancelled'
            job.finished_at = await session.scalar(select(func.clock_timestamp()))
            if document.status != 'ready':
                document.status = 'cancelled'
        else:
            job.state = 'cancelling'


async def enter_analysis(job_id, owner) -> bool:
    async with fenced(job_id, owner) as (session, _, job):
        await session.execute(text('SELECT pg_advisory_xact_lock(73402104)'))
        count = await session.scalar(select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.state == 'running', ProcessingJob.stage == 'analysis_request',
            ProcessingJob.lease_until > func.clock_timestamp(), ProcessingJob.id != job_id))
        if count >= settings.analysis_concurrency:
            if job.stage != 'waiting_analysis':
                job.stage = 'waiting_analysis'
                job.stage_started_at = await session.scalar(select(func.clock_timestamp()))
            return False
        if job.stage != 'analysis_request':
            job.stage = 'analysis_request'
            job.stage_started_at = await session.scalar(select(func.clock_timestamp()))
        return True
