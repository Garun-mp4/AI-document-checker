from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

from app.config import settings


class UploadBodyLimitMiddleware:
    """Bound multipart bytes before Starlette spools/parses the upload."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or scope['path'] != '/api/v1/documents':
            return await self.app(scope, receive, send)
        ceiling = settings.max_upload_bytes + 1024 * 1024
        headers = dict(scope.get('headers', []))
        length = headers.get(b'content-length')
        if length:
            try:
                value = int(length)
                if value < 0:
                    raise ValueError
            except ValueError:
                return await JSONResponse({'detail': 'Некорректный размер запроса.'}, status_code=400)(scope, receive, send)
            if value > ceiling:
                return await JSONResponse({'detail': 'Файл превышает максимальный размер загрузки.'}, status_code=413)(scope, receive, send)
        total = 0
        exceeded = False
        async def limited_receive():
            nonlocal total, exceeded
            message = await receive()
            if message['type'] == 'http.request':
                total += len(message.get('body', b''))
                if total > ceiling:
                    exceeded = True
                    # Starlette closes its partial SpooledTemporaryFile on
                    # MultiPartException. Suppress its generic 400 response.
                    raise MultiPartException('Upload limit exceeded')
            return message
        async def limited_send(message):
            if not exceeded:
                await send(message)
        await self.app(scope, limited_receive, limited_send)
        if exceeded:
            await JSONResponse({'detail': 'Файл превышает максимальный размер загрузки.'}, status_code=413)(scope, receive, send)
