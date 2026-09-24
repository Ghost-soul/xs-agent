import sys
from pathlib import Path

from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings


def test_retired_generators_are_absent_from_api_and_import_graph(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            local_token="retirement-test-token",
            local_task_worker_enabled=False,
            log_dir=tmp_path,
            _env_file=None,
        )
    )
    paths = app.openapi()["paths"]
    retired = (
        "novel-runs",
        "creative-policy",
        "active-writer-contract",
        "writing-sessions",
        "agent-prompts",
        "genre-rule-preview",
        "quality-lab",
        "structural-cleanup",
        "story-blueprint/initialize",
    )
    assert not any(name in path for path in paths for name in retired)
    for expected in (
        "/api/projects",
        "/api/projects/{project_id}/story-blueprint",
        "/api/projects/{project_id}/formal-chapter-titles",
        "/api/projects/{project_id}/reader/manifest",
        "/api/projects/{project_id}/backup",
        "/api/projects/{project_id}/rollback",
    ):
        assert expected in paths
    assert not any(name.startswith("novel_writer.services.novel_run") for name in sys.modules)

    headers = {
        "Authorization": "Bearer retirement-test-token",
        "Origin": "http://127.0.0.1:5173",
        "X-CSRF-Token": "retirement-test-token",
    }
    # No database lifespan: a retired URL must fail at routing, before any data or provider access.
    client = TestClient(app)
    for url in (
        "/api/projects/old-project/novel-runs",
        "/api/novel-runs/old-run/advance",
        "/api/writing-sessions/old-session/merge",
        "/api/agent-prompts/sync-current",
        "/api/projects/old-project/story-blueprint/initialize",
    ):
        assert client.post(url, headers=headers, json={}).status_code == 404


def test_startup_ignores_old_generation_worker_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOVEL_WRITER_RUN_TO_REVIEW_WORKER_ENABLED", "true")
    settings = Settings(local_task_worker_enabled=False, log_dir=tmp_path, _env_file=None)
    app = create_app(settings)
    # A deliberately unreachable database proves startup does not run generation recovery,
    # checkpoint migrations, or queue scans. Health can separately report DB availability.
    settings.database_url = "postgresql+psycopg://unavailable@127.0.0.1:1/unused_test"
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
