from pathlib import Path

from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


def test_manuscript_management_round_trip_without_generation(
    clean_test_database: None,
    tmp_path: Path,
) -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        database_url=DATABASE_URL,
        local_token="integration-token",
        content_store_root=tmp_path / "content",
        project_workspace_root=tmp_path / "projects",
        local_task_worker_enabled=False,
        log_dir=tmp_path / "logs",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:

        def read(url):
            response = client.get(url, headers=headers())
            assert response.status_code == 200, response.text
            return response.json()

        imported = client.post(
            "/api/data/import-manuscript",
            json={
                "title": "保留管理能力",
                "text": (
                    "# 小说\n\n## 第1章 入城\n\n风从城门吹进来。"
                    "\n\n## 第2章 夜雨\n\n守军举起灯。"
                ),
                "confirmed": True,
            },
            headers=headers("management-import"),
        )
        assert imported.status_code == 201, imported.text
        project_id = imported.json()["project_id"]
        base = f"/api/projects/{project_id}"
        chapters = read(base + "/chapters")
        assert len(chapters) == 2
        reader_url = base + f"/reader/chapters/{chapters[0]['chapter_id']}"
        original = read(reader_url)
        assert "风从城门吹进来" in original["body"]
        world = read(base + "/story-blueprint/sections/world")
        changed = client.put(
            base + "/story-blueprint/sections/world",
            json={
                "expected_state_version": world["state_version"],
                "data": {**world["data"], "world_rules": [{"statement": "潮印必须付出记忆"}]},
                "confirmed": True,
                "reason": "作者手工补充世界规则",
            },
            headers=headers("management-world"),
        )
        assert changed.status_code == 201, changed.text
        current = changed.json()["state_version"]
        renamed = client.put(
            base + "/formal-chapter-titles",
            json={
                "base_version": current,
                "confirmed": True,
                "reason": "作者手工更改章节标题",
                "titles": [
                    {"chapter_id": c["chapter_id"], "title": f"新标题{c['ordinal']}"}
                    for c in chapters
                ],
            },
            headers=headers("management-titles"),
        )
        assert renamed.status_code == 201, renamed.text
        assert read(reader_url)["title"] == "新标题1"
        assert read(reader_url)["body_sha256"] == original["body_sha256"]
        current = read(base + "/versions")[0]["number"]
        rolled = client.post(
            base + "/rollback",
            json={
                "target_version": world["state_version"],
                "base_version": current,
                "confirmed": True,
                "reason": "恢复导入时的正式内容",
            },
            headers=headers("management-rollback"),
        )
        assert rolled.status_code == 201, rolled.text
        assert read(reader_url)["title"] == original["title"]
        assert read(reader_url)["body_sha256"] == original["body_sha256"]
        archive = read(base + "/backup")
        restored = client.post(
            "/api/data/backup/restore",
            json={
                "archive": archive,
                "title": "备份恢复副本",
                "confirmed": True,
            },
            headers=headers("management-restore-backup"),
        )
        assert restored.status_code == 201, restored.text
        assert len(read(f"/api/projects/{restored.json()['project_id']}/chapters")) == 2
        exported = client.get(base + "/export/markdown", headers=headers())
        assert exported.status_code == 200 and "守军举起灯" in exported.text
        for action in ("archive", "restore"):
            response = client.post(
                base + f"/{action}",
                json={
                    "confirmed": True,
                    "confirmed_title": "保留管理能力",
                },
                headers=headers(f"management-{action}"),
            )
            assert response.status_code == 200, response.text
        assert read(reader_url)["body_sha256"] == original["body_sha256"]
