"""Synthetic provider bridge for the real queue worker; never in production."""
import asyncio
import json
import urllib.request

import httpx

from app.services.codex import CodexModelUnavailable, CodexNeedsLogin, CodexUnavailable
from app.services.markitdown_service import MarkdownConversionError, MarkItDownService
from app.worker import QueueWorker


def request(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request('http://api:8000/api/v1/__e2e/' + path, data=body,
                                headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


class RemoteCodex:
    async def complete(self, payload, schema, **preferences):
        async with httpx.AsyncClient(trust_env=False, timeout=120) as client:
            reply = await client.post('http://api:8000/api/v1/__e2e/complete', json={
                'payload': payload,
                'schema': schema,
                'preferences': preferences,
            })
            reply.raise_for_status()
            response = reply.json()
        if 'error' in response:
            cls = {'CodexNeedsLogin': CodexNeedsLogin, 'CodexModelUnavailable': CodexModelUnavailable}.get(response['error'], CodexUnavailable)
            raise cls(response['message'])
        return response['result']


real_convert = MarkItDownService.convert
async def convert(self, path):
    state = await asyncio.to_thread(request, 'worker-control')
    if state['markdown_failure']:
        raise MarkdownConversionError('Synthetic conversion failure')
    return await real_convert(self, path)
MarkItDownService.convert = convert


async def stage_hook(job, stage):
    while (await asyncio.to_thread(request, 'worker-control'))['hold_stage'] == stage:
        await asyncio.sleep(0.1)


async def main():
    import signal

    from app.database import engine
    worker = QueueWorker(RemoteCodex(), stage_hook)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop_event.set)
    try:
        await worker.run()
    finally:
        await engine.dispose()

asyncio.run(main())
