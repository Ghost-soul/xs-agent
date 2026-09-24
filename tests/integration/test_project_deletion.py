from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.db.models import (
    ApprovalRecord,
    ChapterRecord,
    ChapterRevisionRecord,
    IdempotencyKeyRecord,
    LocalTaskRecord,
    ProjectRecord,
)
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


@pytest.fixture
def deletion_client(clean_test_database: None, tmp_path: Path) -> Iterator[TestClient]:
    assert DATABASE_URL is not None
    settings = Settings(
        database_url=DATABASE_URL,
        local_token="integration-token",
        _env_file=None,
        content_store_root=tmp_path / "content",
        project_workspace_root=tmp_path / "projects",
        local_task_worker_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def deletion_engine() -> Iterator[Engine]:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    yield engine
    engine.dispose()


def _project(client: TestClient, title: str = "需要删除的正文秘密") -> dict[str, Any]:
    response = client.post("/api/projects", json={"title": title}, headers=headers(str(uuid4())))
    assert response.status_code == 201, response.text
    return response.json()


def _preview(client: TestClient, project: dict[str, Any]) -> dict[str, Any]:
    response = client.get(
        f"/api/projects/{project['project_id']}/delete-preview", headers=headers()
    )
    assert response.status_code == 200, response.text
    return response.json()


def _delete(
    client: TestClient, project: dict[str, Any], preview: dict[str, Any], key: str | None = None
) -> Any:
    return client.post(
        f"/api/projects/{project['project_id']}/delete",
        headers=headers(key or str(uuid4())),
        json={
            "confirmed": True,
            "confirmed_title": preview["title"],
            "binding_sha256": preview["binding_sha256"],
        },
    )


def test_running_local_task_blocks_deletion(
    deletion_client: TestClient, deletion_engine: Engine
) -> None:
    project = _project(deletion_client)
    with Session(deletion_engine) as session, session.begin():
        session.add(
            LocalTaskRecord(
                project_id=UUID(project["project_id"]),
                kind="export",
                status="running",
                input_sha256="a" * 64,
                payload={},
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
    preview = _preview(deletion_client, project)
    assert preview["blockers"]
    assert _delete(deletion_client, project, preview).status_code == 409


def test_database_read_transaction_prevents_delete_and_leaves_files_untouched(
    deletion_client: TestClient,
    deletion_engine: Engine,
) -> None:
    project = _project(deletion_client)
    preview = _preview(deletion_client, project)
    with deletion_engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT id FROM story_projects"))
        response = _delete(deletion_client, project, preview)
        assert response.status_code == 409, response.text
    assert _preview(deletion_client, project)["binding_sha256"] == preview["binding_sha256"]


def test_cascading_chapter_versions_delete_atomically(
    deletion_client: TestClient,
    deletion_engine: Engine,
) -> None:
    project = _project(deletion_client)
    with Session(deletion_engine) as session, session.begin():
        chapter = ChapterRecord(project_id=UUID(project["project_id"]), ordinal=1, title="私密章节")
        session.add(chapter)
        session.flush()
        session.add(
            ChapterRevisionRecord(
                chapter_id=chapter.id,
                base_version_id=UUID(project["version_id"]),
                body="私密正文",
                status="accepted",
            )
        )
    response = _delete(deletion_client, project, _preview(deletion_client, project))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed", response.text
    with Session(deletion_engine) as session:
        assert session.scalar(select(ChapterRevisionRecord)) is None


def test_external_foreign_key_reference_blocks_without_harming_either_project(
    deletion_client: TestClient,
    deletion_engine: Engine,
) -> None:
    project = _project(deletion_client)
    other = _project(deletion_client, "使用共享资源的作品")
    with Session(deletion_engine) as session, session.begin():
        chapter = ChapterRecord(project_id=UUID(other["project_id"]), ordinal=1, title="保留章节")
        session.add(chapter)
        session.flush()
        session.add(
            ChapterRevisionRecord(
                chapter_id=chapter.id,
                base_version_id=UUID(project["version_id"]),
                body="不能误删",
                status="accepted",
            )
        )
    preview = _preview(deletion_client, project)
    assert any("共享" in item for item in preview["blockers"])
    assert _delete(deletion_client, project, preview).status_code == 409
    with Session(deletion_engine) as session:
        assert len(list(session.scalars(select(ProjectRecord)))) == 2
        assert session.scalar(select(ChapterRevisionRecord)) is not None


def test_user_supplied_keys_or_approval_text_cannot_claim_another_projects_records(
    deletion_client: TestClient,
    deletion_engine: Engine,
) -> None:
    project = _project(deletion_client)
    other = _project(deletion_client, "保留审计的作品")
    with Session(deletion_engine) as session, session.begin():
        record = IdempotencyKeyRecord(
            command="other_project_operation",
            key=f"key-{project['project_id']}",
            response={"project_id": other["project_id"], "body": "另一小说的响应"},
        )
        approval = ApprovalRecord(
            target_type="project",
            target_id=UUID(other["project_id"]),
            decision="approved",
            reason=f"参考编号 {project['project_id']}",
        )
        session.add_all([record, approval])
        session.flush()
        record_id, approval_id = record.id, approval.id
    response = _delete(deletion_client, project, _preview(deletion_client, project))
    assert response.status_code == 200, response.text
    with Session(deletion_engine) as session:
        assert session.get(IdempotencyKeyRecord, record_id) is not None
        assert session.get(ApprovalRecord, approval_id) is not None
