from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_writer.services.errors import NotFoundError
from novel_writer.services.project_queries import ProjectQueryService


@pytest.mark.asyncio
async def test_project_listing_loads_current_versions_in_one_query() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        title="当前作品",
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    result = SimpleNamespace(all=lambda: [(project, 7)])
    session = AsyncMock()
    session.execute.return_value = result

    payload = await ProjectQueryService(session).list_projects()

    assert payload == [
        {
            "project_id": str(project.id),
            "title": "当前作品",
            "current_version": 7,
            "created_at": project.created_at.isoformat(),
        }
    ]
    session.execute.assert_awaited_once()
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_archived_project_listing_loads_current_versions_in_one_query() -> None:
    archived_at = datetime(2026, 8, 15, tzinfo=UTC)
    project = SimpleNamespace(
        id=uuid4(),
        title="归档作品",
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        archived_at=archived_at,
    )
    result = SimpleNamespace(all=lambda: [(project, 4)])
    session = AsyncMock()
    session.execute.return_value = result

    payload = await ProjectQueryService(session).list_archived_projects()

    assert payload == [
        {
            "project_id": str(project.id),
            "title": "归档作品",
            "current_version": 4,
            "created_at": project.created_at.isoformat(),
            "archived_at": archived_at.isoformat(),
        }
    ]
    session.execute.assert_awaited_once()
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_listing_fails_closed_when_current_version_is_missing() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        title="损坏作品",
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(all=lambda: [(project, None)])

    with pytest.raises(NotFoundError, match="state version not found"):
        await ProjectQueryService(session).list_projects()

    session.execute.assert_awaited_once()
    session.get.assert_not_awaited()
