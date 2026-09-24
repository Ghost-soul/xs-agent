from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings


def make_settings(tmp_path) -> Settings:
    return Settings(
        local_token="test-token", local_task_worker_enabled=False, _env_file=None,
        database_url="postgresql+psycopg://fixture@127.0.0.1:9/api_test",
        content_store_root=tmp_path / "content",
        provider_profiles_path=tmp_path / "profiles.json",
        log_dir=tmp_path / "logs",
    )


def test_liveness_is_public(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_database_failure(tmp_path) -> None:
    with (
        patch("novel_writer.db.engine.Database.ping", new=AsyncMock(return_value=False)),
        TestClient(create_app(make_settings(tmp_path))) as client,
    ):
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_private_routes_require_local_token_before_routing(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/not-yet-implemented")

    assert response.status_code == 401


def test_oversized_request_body_is_rejected_after_local_authentication(tmp_path) -> None:
    settings = make_settings(tmp_path).model_copy(update={"max_request_body_bytes": 64 * 1024})
    headers = {
        "Authorization": "Bearer test-token",
        "Origin": "http://127.0.0.1:5173",
        "X-CSRF-Token": "test-token",
    }
    with TestClient(create_app(settings)) as client:
        rejected = client.post(
            "/not-yet-implemented",
            content=b"x" * (64 * 1024 + 1),
            headers=headers,
        )
        unauthenticated = client.post(
            "/not-yet-implemented",
            content=b"x" * (64 * 1024 + 1),
        )

    assert rejected.status_code == 413
    assert "65536" in rejected.json()["detail"]
    assert unauthenticated.status_code == 401


def test_large_json_responses_use_gzip_when_supported(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
