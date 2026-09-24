from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from novel_writer.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderResponseError,
)

_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|secret)([\"']?\s*[:=]\s*[\"']?)([^\s,;}\"']+)"
)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HANDLER_MARKER = "_novel_writer_handler"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 5


def redact_secrets(value: str) -> str:
    """Remove common credentials before a value is written to a log."""
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


def _redact_log_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_log_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_log_value(item) for item in value)
    return value


def _add_runtime_context(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    event_dict.setdefault("pid", os.getpid())
    return event_dict


def _redact_log_event(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    return {key: _redact_log_value(value) for key, value in event_dict.items()}


def _processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        _add_runtime_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_log_event,
    ]


def _close_owned_handlers(root: logging.Logger) -> None:
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()


def configure_logging(
    level: str,
    log_dir: Path | str = Path("logs"),
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
) -> Path:
    """Configure console and rotating local system logging.

    The function is safe to call repeatedly. This matters for the FastAPI app
    factory, which is instantiated more than once by tests and development tooling.
    The returned path is the active system log path.
    """
    normalized_level = level.upper()
    numeric_level = getattr(logging, normalized_level, logging.INFO)
    resolved_dir = Path(log_dir)
    resolved_dir.mkdir(parents=True, exist_ok=True)
    log_path = resolved_dir / "system.log"

    root = logging.getLogger()
    _close_owned_handlers(root)
    root.setLevel(numeric_level)

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=_processors(),
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,
    )
    setattr(file_handler, _HANDLER_MARKER, True)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    setattr(console_handler, _HANDLER_MARKER, True)
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    structlog.configure(
        processors=[*_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return log_path


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    """Bind a request id for all logs emitted during one request."""
    with structlog.contextvars.bound_contextvars(request_id=request_id):
        yield


def _request_id(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == b"x-request-id":
            candidate = str(value.decode("ascii", errors="ignore"))
            if _SAFE_REQUEST_ID.fullmatch(candidate):
                return candidate
            break
    return uuid4().hex


class RequestLoggingMiddleware:
    """Log every HTTP request without recording headers, query strings, or bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = get_logger("novel_writer.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        method = str(scope.get("method", ""))
        path = str(scope.get("path", ""))
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                if not any(key.lower() == b"x-request-id" for key, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with request_context(request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception as error:
                self.logger.exception(
                    "http.request.exception",
                    method=method,
                    path=path,
                    error_type=type(error).__name__,
                )
                raise
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                log_method = self.logger.warning if status_code >= 500 else self.logger.info
                log_method(
                    "http.request.completed",
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )


def log_provider_success(
    provider: str,
    request: ModelRequest,
    response: ModelResponse,
    duration_ms: float,
) -> None:
    get_logger("novel_writer.provider").info(
        "provider.request.completed",
        provider=provider,
        model=request.model,
        operation=request.json_schema_name,
        request_id=response.request_id,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cached_input_tokens=response.usage.cached_input_tokens,
        reasoning_tokens=response.usage.reasoning_tokens,
        duration_ms=round(duration_ms, 2),
    )


def log_provider_failure(
    provider: str,
    request: ModelRequest,
    error: Exception,
    duration_ms: float,
) -> None:
    fields: dict[str, Any] = {
        "provider": provider,
        "model": request.model,
        "operation": request.json_schema_name,
        "error_type": type(error).__name__,
        "error": redact_secrets(str(error))[:500],
        "duration_ms": round(duration_ms, 2),
    }
    if isinstance(error, ProviderResponseError):
        fields["error_code"] = error.code
        fields["request_id"] = error.request_id
        if error.usage is not None:
            fields.update(
                input_tokens=error.usage.input_tokens,
                output_tokens=error.usage.output_tokens,
                cached_input_tokens=error.usage.cached_input_tokens,
                reasoning_tokens=error.usage.reasoning_tokens,
            )
    get_logger("novel_writer.provider").error("provider.request.failed", **fields)




