from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse

from app.services.document_security import download_name, open_storage
from app.services.parsing import DocumentParsingError


def artifact_response(path, request: Request | None, media_type: str, filename: str, *, attachment: bool = False):
    try:
        stream = open_storage(path)
    except (OSError, DocumentParsingError) as exc:
        raise HTTPException(status_code=404, detail='Файл документа недоступен.') from exc
    info = os.fstat(stream.fileno())
    headers = dict(FileResponse(
        path, media_type=media_type, filename=download_name(filename), stat_result=info,
        content_disposition_type='attachment' if attachment else 'inline',
    ).headers)
    headers.update({'x-content-type-options': 'nosniff', 'content-security-policy': "sandbox; default-src 'none'",
                    'cache-control': 'no-store', 'referrer-policy': 'no-referrer'})
    start, end = 0, info.st_size - 1
    response_status = 200
    range_header = request.headers.get('range') if request else None
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
        try:
            if not match or not any(match.groups()):
                raise ValueError
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError
                start = max(0, info.st_size - suffix)
            if start > end or start >= info.st_size:
                raise ValueError
        except ValueError:
            stream.close()
            raise HTTPException(status_code=416, detail='Недопустимый диапазон файла.',
                                headers={'Content-Range': f'bytes */{info.st_size}'}) from None
        headers['content-range'] = f'bytes {start}-{end}/{info.st_size}'
        headers['content-length'] = str(end - start + 1)
        response_status = 206
    def chunks():
        try:
            stream.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = stream.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            stream.close()
    # Descriptor stays pinned if an attacker replaces the pathname after
    # validation. Closing/cancellation never reads or removes its new target.
    return StreamingResponse(chunks(), status_code=response_status, media_type=media_type,
                             headers=headers, background=BackgroundTask(stream.close))
