import logging

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class PrivateErrorsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started = False
        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)
        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            # SQLAlchemy/SDK exceptions can embed document values and secrets.
            logger.exception('Request failed (%s)', type(exc).__name__, exc_info=False)
            if not started:
                await JSONResponse({'detail': 'Внутренняя ошибка. Повторите запрос.'}, status_code=500)(scope, receive, send)
            else:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
