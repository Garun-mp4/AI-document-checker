"""Durable queue consumer, separate from the HTTP process."""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

from app.config import settings
from app.database import engine
from app.services.codex import CodexService
from app.services.embeddings import _model
from app.services.job_queue import claim, heartbeat, recover
from app.services.processing_engine import ProcessingAttempt

logger = logging.getLogger(__name__)
HEALTH_FILE = Path('/tmp/document-worker-health.json')


class JobCodex:
    def __init__(self, service, parameters):
        self.service = service
        self.parameters = parameters

    async def complete(self, payload, schema):
        return await self.service.complete(payload, schema, model=self.parameters['model'],
                                           reasoning_effort=self.parameters['reasoning_effort'])


class QueueWorker:
    def __init__(self, codex, stage_hook=None):
        self.codex = codex
        self.stage_hook = stage_hook
        self.tasks = set()
        self.stop_event = asyncio.Event()

    async def execute(self, job):
        attempt = ProcessingAttempt(job, JobCodex(self.codex, job.parameters), self.stage_hook)
        task = asyncio.create_task(attempt.run())
        interval = min(settings.queue_heartbeat_seconds, settings.queue_lease_seconds / 3)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=interval)
                if not task.done() and not await heartbeat(job.id, job.owner):
                    task.cancel()
            await task
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except Exception as exc:
            # Do not serialize document text, DB values or third-party errors.
            logger.exception('Queue attempt stopped (%s)', type(exc).__name__, exc_info=False)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # The lease now expires naturally; recovery applies fencing.

    async def run(self):
        try:
            # Download once in the trusted worker; untrusted children have no network.
            await asyncio.to_thread(_model, settings.embedding_cache_dir)
            while not self.stop_event.is_set():
                try:
                    await recover()
                    job = await claim()
                    HEALTH_FILE.write_text(json.dumps({'heartbeat': time.time()}), encoding='utf-8')
                    if job:
                        task = asyncio.create_task(self.execute(job))
                        self.tasks.add(task)
                        task.add_done_callback(self.tasks.discard)
                        continue
                except Exception as exc:
                    logger.exception('Queue poll failed (%s)', type(exc).__name__, exc_info=False)
                try:
                    await asyncio.wait_for(self.stop_event.wait(), settings.queue_poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)


async def main():
    codex = CodexService()
    await codex.start()
    worker = QueueWorker(codex)
    loop = asyncio.get_running_loop()
    if sys.platform != 'win32':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, worker.stop_event.set)
    try:
        await worker.run()
    finally:
        await codex.close()
        await engine.dispose()


if __name__ == '__main__':
    if '--healthcheck' in sys.argv:
        try:
            healthy = time.time() - json.loads(HEALTH_FILE.read_text())['heartbeat'] < 30
        except (OSError, ValueError, KeyError):
            healthy = False
        sys.exit(0 if healthy else 1)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
