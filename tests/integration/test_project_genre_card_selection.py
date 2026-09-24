from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from tests.integration.support import DATABASE_URL, headers
from tests.integration.support import pytestmark as pytestmark


@pytest.mark.parametrize("setup_mode", ["blank", "import"])
@pytest.mark.parametrize("selection_mode", ["unselected", "specified"])
def test_setup_finalization_preserves_only_manual_cards(
    clean_test_database: None, tmp_path: Path, setup_mode: str, selection_mode: str
) -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        database_url=DATABASE_URL,
        local_token="integration-token",
        content_store_root=tmp_path / "content",
        project_workspace_root=tmp_path / "projects",
        local_task_worker_enabled=False,
        _env_file=None,
    )
    primary = "traditional_wuxia_jianghu" if selection_mode == "specified" else None
    secondary = (
        ["western_fantasy_dnd", "girls_love_gl", "farming_infrastructure"] if primary else []
    )
    title = "仙侠宗门修士修炼"
    with TestClient(create_app(settings)) as client:
        binding = None
        if setup_mode == "import":
            preview = client.post(
                f"/api/imports/preview?title={quote(title)}&encoding=utf-8",
                content="# 第一章 修炼\n\n少年吸收灵气，修炼功法突破境界。".encode(),
                headers={**headers(), "Content-Type": "application/octet-stream"},
            )
            assert preview.status_code == 201, preview.text
            binding = {
                key: preview.json()[key]
                for key in (
                    "preview_id", "upload_sha256", "chapter_manifest_sha256",
                    "selected_encoding", "parser_version",
                )
            }
        draft = client.post(
            "/api/project-setup-drafts",
            json={"payload": {
                "title": title, "genre": "东方玄幻仙侠", "mode": setup_mode,
                "genre_selection_mode": selection_mode, "genre_card_id": primary,
                "secondary_genre_card_ids": secondary,
                "import_preview": binding, "step": 6,
            }},
            headers=headers(),
        )
        assert draft.status_code == 201, draft.text
        finalized = client.post(
            f"/api/project-setup-drafts/{draft.json()['draft_id']}/finalize",
            json={"confirmed": True, "import_binding": binding},
            headers=headers("manual-genre-finalize"),
        )
        assert finalized.status_code == 201, finalized.text
        profile = client.get(
            f"/api/projects/{finalized.json()['project_id']}/style-profile", headers=headers()
        )
        assert profile.status_code == 200
        assert profile.json()["selection_mode"] == selection_mode
        assert [card["id"] for card in profile.json()["matched_cards"]] == (
            [primary, *secondary] if primary else []
        )
