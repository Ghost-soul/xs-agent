from uuid import uuid4

from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


def test_retired_chapter_workflow_routes_are_not_exposed(
    clean_test_database: None,
) -> None:
    assert DATABASE_URL is not None
    settings = Settings(local_task_worker_enabled=False,
        database_url=DATABASE_URL,
        local_token="integration-token",
        _env_file=None,
    )
    nonce = uuid4().hex
    with TestClient(create_app(settings)) as client:
        project = client.post(
            "/api/projects",
            json={"title": f"恢复测试-{nonce}"},
            headers=headers(f"recovery-project-{nonce}"),
        ).json()
        chapter = client.post(
            f"/api/projects/{project['project_id']}/chapters",
            json={"ordinal": 1, "title": "恢复章"},
            headers=headers(f"recovery-chapter-{nonce}"),
        ).json()
        chapters = client.get(
            f"/api/projects/{project['project_id']}/chapters",
            headers=headers(),
        )
        assert chapters.status_code == 200
        assert chapters.json()[0]["phase"] == "pending"

        retired_brief = client.post(
            f"/api/chapters/{chapter['chapter_id']}/briefs",
            headers=headers(f"recovery-brief-{nonce}"),
        )
        assert retired_brief.status_code == 404
        assert retired_brief.json() == {"detail": "Not Found"}

        retired_workflow = client.get(
            f"/api/chapters/{chapter['chapter_id']}/workflow",
            headers=headers(),
        )
        assert retired_workflow.status_code == 404
        assert retired_workflow.json() == {"detail": "Not Found"}
