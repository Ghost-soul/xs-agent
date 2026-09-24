import secrets

from fastapi import status
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from novel_writer.core.config import Settings

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS = {"/health", "/health/live", "/health/ready", "/docs", "/openapi.json"}


class LocalSecurityMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        rejection = self._validate(request)
        if rejection is not None:
            await rejection(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _validate(self, request: Request) -> Response | None:
        if request.url.path in _PUBLIC_PATHS:
            return None

        expected_token = self.settings.local_token.get_secret_value()
        supplied_token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not expected_token or not secrets.compare_digest(expected_token, supplied_token):
            return JSONResponse(
                {"detail": "Invalid local access token"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if request.method in _SAFE_METHODS:
            return None
        origin = request.headers.get("origin")
        if origin not in self.settings.allowed_origins:
            return JSONResponse(
                {"detail": "Origin is not allowed"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if request.headers.get("x-csrf-token") != expected_token:
            return JSONResponse(
                {"detail": "Invalid CSRF token"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return None
