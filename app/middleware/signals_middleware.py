import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.monitoring.signals import ApiSignals


class SignalsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, signals: ApiSignals):
        super().__init__(app)
        self._signals = signals

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self._signals.on_request_start()
        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            self._signals.on_request_end(duration_ms=dt_ms, status_code=status_code)

