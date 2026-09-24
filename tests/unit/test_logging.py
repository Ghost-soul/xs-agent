import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novel_writer.core.logging import (
    RequestLoggingMiddleware,
    configure_logging,
    get_logger,
    redact_secrets,
    request_context,
)


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_redacts_common_secret_fields() -> None:
    message = "authorization=BearerValue api_key:abc123 password='open-sesame'"

    redacted = redact_secrets(message)

    assert "BearerValue" not in redacted
    assert "abc123" not in redacted
    assert "open-sesame" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_configure_logging_writes_rotating_json_system_log(tmp_path: Path) -> None:
    log_path = configure_logging("INFO", tmp_path, max_bytes=1024, backup_count=2)
    try:
        with request_context("req-test-123"):
            get_logger("test.logging").info(
                "test.event",
                detail="authorization=should-not-leak",
                operation="health",
            )
        _flush_root_handlers()

        assert log_path == tmp_path / "system.log"
        record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert record["event"] == "test.event"
        assert record["logger"] == "test.logging"
        assert isinstance(record["pid"], int)
        assert record["request_id"] == "req-test-123"
        assert record["operation"] == "health"
        assert "should-not-leak" not in log_path.read_text(encoding="utf-8")
        file_handler = next(
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "_novel_writer_handler", False)
            and isinstance(handler, RotatingFileHandler)
        )
        assert file_handler.maxBytes == 1024
        assert file_handler.backupCount == 2
    finally:
        configure_logging("INFO", Path("logs"))


def test_request_logging_middleware_propagates_and_logs_request_id(tmp_path: Path) -> None:
    configure_logging("INFO", tmp_path)
    try:
        app = FastAPI()
        app.add_middleware(RequestLoggingMiddleware)

        @app.get("/ping")
        async def ping() -> dict[str, str]:
            return {"status": "ok"}

        with TestClient(app) as client:
            response = client.get("/ping", headers={"x-request-id": "req-visible"})

        _flush_root_handlers()
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "req-visible"
        records = [
            json.loads(line)
            for line in (tmp_path / "system.log").read_text(encoding="utf-8").splitlines()
        ]
        completed = next(
            record for record in records if record["event"] == "http.request.completed"
        )
        assert completed["request_id"] == "req-visible"
        assert completed["path"] == "/ping"
        assert completed["status_code"] == 200
    finally:
        configure_logging("INFO", Path("logs"))




