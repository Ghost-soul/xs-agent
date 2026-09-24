from uuid import uuid4

from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


def test_blueprint_relationships_round_trip_and_omission_preserves_them(
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
            json={"title": f"人物关系蓝图-{nonce}"},
            headers=headers(f"relationship-project-{nonce}"),
        ).json()
        seeded = client.put(
            f"/api/projects/{project['project_id']}/story-blueprint",
            json={
                "base_version": 1,
                "characters": [{"name": "林遥"}, {"name": "周宁", "tier": "B"}],
                "confirmed": True,
            },
            headers=headers(f"relationship-characters-{nonce}"),
        ).json()
        character_ids = {item["name"]: item["id"] for item in seeded["characters"]}
        relationship = {
            "source_character_id": character_ids["林遥"],
            "target_character_id": character_ids["周宁"],
            "relation_type": "conditional_strategic_alliance",
            "description": "双方合作，但各自保留拒绝条件。",
        }
        related = client.put(
            f"/api/projects/{project['project_id']}/story-blueprint",
            json={
                "base_version": 2,
                "relationships": [relationship],
                "confirmed": True,
            },
            headers=headers(f"relationship-save-{nonce}"),
        )
        assert related.status_code == 201, related.text
        saved_relationship = related.json()["relationships"][0]
        assert {key: saved_relationship[key] for key in relationship} == relationship

        omitted = client.put(
            f"/api/projects/{project['project_id']}/story-blueprint",
            json={
                "base_version": 3,
                "story_foundation": {"theme": "关系必须带来选择代价"},
                "confirmed": True,
            },
            headers=headers(f"relationship-omitted-{nonce}"),
        )
        assert omitted.status_code == 201, omitted.text
        assert omitted.json()["relationships"] == [saved_relationship]
