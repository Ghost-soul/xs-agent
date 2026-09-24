"""Authenticated same-origin UI gateway; internal API tokens never reach the browser."""

import base64
import binascii
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class WebGateway:
    def __init__(
        self, api: ASGIApp, *, directory: Path, username: str, password: str,
        internal_token: str, origin: str,
    ) -> None:
        if not username or len(password) < 24 or len(internal_token) < 24:
            raise ValueError("部署登录配置缺失或过短")
        self.api = api
        self.directory = directory
        self.static = StaticFiles(directory=directory)
        self.username, self.password = username, password
        self.internal_token = internal_token
        self.host = urlsplit(origin).netloc.lower()

    def authenticated(self, header: str) -> bool:
        try:
            scheme, encoded = header.split(" ", 1)
            if scheme.lower() != "basic":
                return False
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            username, password = decoded.split(":", 1)
            return secrets.compare_digest(username.encode(), self.username.encode()) & (
                secrets.compare_digest(password.encode(), self.password.encode())
            )
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.api(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        request = Request(scope)
        path = scope["path"]
        if path in {"/health/live", "/health/ready"}:
            await self.api(scope, receive, send)
            return
        if not self.authenticated(request.headers.get("authorization", "")):
            await JSONResponse(
                {"detail": "请使用部署时生成的账号和密码登录"}, status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Novel Writer", charset="UTF-8"',
                         "Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        if request.headers.get("host", "").lower() != self.host:
            await JSONResponse({"detail": "网站地址与部署配置不一致"}, status_code=403)(
                scope, receive, send,
            )
            return

        async def protected_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"cache-control", b"private, no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                ])
                message = {**message, "headers": headers}
            await send(message)

        if path == "/backend" or path.startswith("/backend/"):
            headers = [
                (key, value) for key, value in scope["headers"]
                if key.lower() not in {b"authorization", b"x-csrf-token"}
            ]
            headers.extend([
                (b"authorization", f"Bearer {self.internal_token}".encode("ascii")),
                (b"x-csrf-token", self.internal_token.encode("ascii")),
            ])
            rewritten = path.removeprefix("/backend") or "/"
            api_scope = {
                **scope, "path": rewritten, "raw_path": rewritten.encode(), "headers": headers,
            }
            # The API still checks the original browser Origin on every write.
            await self.api(api_scope, receive, protected_send)
            return
        if request.method not in {"GET", "HEAD"}:
            await Response(status_code=405)(scope, receive, protected_send)
            return
        try:
            response = await self.static.get_response(path.lstrip("/"), scope)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            if "text/html" in request.headers.get("accept", "") and not Path(path).suffix:
                response = FileResponse(self.directory / "index.html")
            else:
                response = Response(status_code=404)
        await response(scope, receive, protected_send)
