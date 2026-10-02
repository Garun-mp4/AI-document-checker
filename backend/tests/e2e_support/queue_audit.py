"""Real PostgreSQL invariants. Run only with the isolated worker stopped."""
import asyncio
import os
import re
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select, update

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Chunk, Document, DocumentVersion, ProcessingJob
from app.services.job_queue import (
    LeaseLost,
    cancel,
    claim,
    enter_analysis,
    fenced,
    heartbeat,
    recover,
    submit,
)

assert re.fullmatch(r'document-checker-e2e-[0-9a-f]{8}', os.environ.get('E2E_AUDIT_PROJECT', ''))
assert settings.database_url.endswith('/e2e')


async def audit():
    ids = []
    passed = 0
    async def document():
        id = uuid.uuid4()
        ids.append(id)
        async with SessionLocal() as session, session.begin():
            session.add(Document(id=id, filename='queue-audit.txt', storage_path=f'{settings.upload_dir}/{id}.txt',
                                 file_type='txt', file_size=1, status='queued', metadata_json={}))
        return id

    async def expire(id):
        async with SessionLocal() as session, session.begin():
            await session.execute(update(ProcessingJob).where(ProcessingJob.id == id).values(
                lease_until=func.now() - timedelta(seconds=1)))

    try:
        id = await document()
        jobs = await asyncio.gather(*(submit(id, 'process') for _ in range(12)))
        assert len({job.id for job in jobs}) == 1
        async with SessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == id)) == 1
            assert (await session.get(Document, id)).next_version == 2
        passed += 1

        others = [await document() for _ in range(3)]
        for other in others:
            await submit(other, 'process')
        claimed = [job for job in await asyncio.gather(*(claim() for _ in range(8))) if job]
        assert len(claimed) == settings.queue_concurrency == 2
        assert len({job.id for job in claimed}) == 2
        passed += 1

        first, second = claimed
        assert await heartbeat(first.id, first.owner)
        assert not await heartbeat(first.id, uuid.uuid4())
        passed += 1
        assert await enter_analysis(first.id, first.owner)
        assert not await enter_analysis(second.id, second.owner)
        passed += 1

        await expire(first.id)
        assert not await heartbeat(first.id, first.owner)
        try:
            async with fenced(first.id, first.owner):
                raise AssertionError('Expired owner admitted')
        except LeaseLost:
            pass
        passed += 1
        await recover()
        async with SessionLocal() as session:
            job = await session.get(ProcessingJob, first.id)
            assert job.state == 'failed' and job.error_code == 'analysis_interrupted'
        passed += 1

        await expire(second.id)
        await recover()
        async with SessionLocal() as session:
            job = await session.get(ProcessingJob, second.id)
            assert job.state == 'queued' and job.owner is None and job.attempts == 1
        passed += 1

        # Claim only one controlled job, with other test jobs cancelled.
        for other in ids:
            if other != second.document_id:
                await cancel(other)
        next_job = await claim()
        assert next_job.id == second.id and next_job.owner != second.owner and next_job.attempts == 2
        try:
            async with fenced(second.id, second.owner):
                raise AssertionError('Stale owner admitted')
        except LeaseLost:
            pass
        passed += 1

        await cancel(next_job.document_id)
        assert not await heartbeat(next_job.id, next_job.owner)
        await expire(next_job.id)
        await recover()
        async with SessionLocal() as session:
            assert (await session.get(ProcessingJob, next_job.id)).state == 'cancelled'
        passed += 1

        fresh = await submit(next_job.document_id, 'process')
        assert fresh.id != next_job.id and fresh.version > next_job.version
        running = await claim()
        async with fenced(running.id, running.owner) as (session, _, job):
            job.attempts = job.max_attempts
        await expire(running.id)
        await recover()
        async with SessionLocal() as session:
            exhausted = await session.get(ProcessingJob, running.id)
            assert exhausted.state == 'failed' and exhausted.error_code == 'attempts_exhausted'
        passed += 1

        # Analysis-only versions reuse index IDs and artifact snapshots.
        async with SessionLocal() as session, session.begin():
            doc = await session.get(Document, id, with_for_update=True)
            version = await session.get(DocumentVersion, (id, 1))
            version.state, version.snapshot = 'indexed', {'file_type': 'txt', 'chunk_count': 1}
            source = Chunk(document_id=id, version=1, ordinal=0, text='synthetic', locator={})
            session.add(source)
            doc.chunk_count, doc.active_version = 1, 1
        retry = await submit(id, 'analysis')
        async with SessionLocal() as session:
            version = await session.get(DocumentVersion, (id, retry.version))
            assert retry.operation == 'analysis' and version.chunk_version == 1
            assert await session.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == id)) == 1
        passed += 1
        await cancel(id)
        async with SessionLocal() as session:
            assert await session.get(Chunk, source.id) is not None
        passed += 1

        # Removing the document cascades jobs/versions and closes its fence.
        deleting = await submit(id, 'process')
        deletion_job = await claim()
        assert deletion_job.id == deleting.id
        async with SessionLocal() as session, session.begin():
            await session.execute(delete(Document).where(Document.id == id))
        assert not await heartbeat(deletion_job.id, deletion_job.owner)
        await recover()
        async with SessionLocal() as session:
            assert await session.get(ProcessingJob, deletion_job.id) is None
            assert await session.get(DocumentVersion, (id, retry.version)) is None
        passed += 1
        print(f'PostgreSQL queue concurrency/lease/fencing/retry/deletion: {passed} passed')
    finally:
        async with SessionLocal() as session, session.begin():
            await session.execute(delete(Document).where(Document.id.in_(ids)))
        await engine.dispose()


asyncio.run(audit())
