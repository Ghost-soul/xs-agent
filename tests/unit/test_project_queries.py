from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_writer.services.errors import NotFoundError
from novel_writer.services.project_queries import ProjectQueryService


@pytest.mark.asyncio
async def test_versions_select_only_list_fields_and_preserve_history_links() -> None:
    project_id, version_id, parent_id, revision_id = (uuid4() for _ in range(4))
    version = SimpleNamespace(
        id=version_id, number=3, parent_id=parent_id, parent_number=2,
        rollback_of_id=revision_id, rollback_of_number=1,
        restart_of_id=None, restart_of_number=None, restart_base_number=None,
        chapter_revisions={str(uuid4()): str(uuid4())}, created_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(id=project_id)
    session.execute.return_value = SimpleNamespace(all=lambda: [version])

    result = await ProjectQueryService(session).list_versions(project_id)

    assert result[0]["chapter_count"] == 1
    assert result[0]["parent_id"] == str(parent_id)
    assert result[0]["rollback_of_number"] == 1
    assert result[0]["restart_of_id"] is None
    selected = set(session.execute.call_args.args[0].selected_columns.keys())
    assert "state" not in selected and "chapter_titles" not in selected
    session.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_directory_omits_archived_chapters_and_does_not_fetch_story_state() -> None:
    project_id, version_id, chapter_id, revision_id, pending_id = (uuid4() for _ in range(5))
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(id=project_id, current_version_id=version_id)
    session.execute.return_value = SimpleNamespace(one_or_none=lambda: SimpleNamespace(
        number=4, chapter_revisions={str(chapter_id): str(revision_id)},
    ))
    session.scalars.return_value = [
        SimpleNamespace(id=pending_id, ordinal=3, display_ordinal=2,
                        title="手工目录项", current_revision_id=None),
        SimpleNamespace(id=chapter_id, ordinal=2, display_ordinal=1,
                        title="正式标题", current_revision_id=revision_id),
        SimpleNamespace(id=uuid4(), ordinal=1, display_ordinal=None,
                        title="旧章", current_revision_id=uuid4()),
    ]

    result = await ProjectQueryService(session).list_chapters(project_id)

    assert [item["chapter_id"] for item in result] == [str(chapter_id), str(pending_id)]
    assert result[0]["revision_id"] == str(revision_id)
    assert result[1]["revision_id"] is None
    assert set(session.execute.call_args.args[0].selected_columns.keys()) == {
        "number", "chapter_revisions",
    }


@pytest.mark.asyncio
async def test_directory_rejects_missing_current_version() -> None:
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(current_version_id=uuid4())
    session.execute.return_value = SimpleNamespace(one_or_none=lambda: None)
    with pytest.raises(NotFoundError, match="state version not found"):
        await ProjectQueryService(session).list_chapters(uuid4())
    session.scalars.assert_not_awaited()
