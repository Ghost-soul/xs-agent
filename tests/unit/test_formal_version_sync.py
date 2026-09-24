from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from novel_writer.db.models import (
    NovelAutomationRunRecord,
    WritingSessionRecord,
)
from novel_writer.services.errors import ConflictError
from novel_writer.services.formal_version_sync import (
    invalidate_formal_dependents,
    rebase_title_only_dependents,
)


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _LockUnavailable(Exception):
    sqlstate = "55P03"


def _run(project_id: object, version_id: object, session_id: object) -> NovelAutomationRunRecord:
    return NovelAutomationRunRecord(
        id=uuid4(),
        project_id=project_id,
        base_version_id=version_id,
        current_session_id=session_id,
        status="paused",
        phase="write",
        spec={},
        state={"status": "paused", "pause_reason": None},
    )


def _writing(project_id: object, version_id: object) -> WritingSessionRecord:
    return WritingSessionRecord(
        id=uuid4(),
        project_id=project_id,
        base_version_id=version_id,
        direction="",
        author_guidance={},
        story_intent={},
        must_include=[],
        avoid=[],
        target_min_chars=1000,
        target_max_chars=2000,
        scene_target_chars=1000,
        chapter_target_chars=1000,
        status="writing",
        stage="writing",
    )


@pytest.mark.asyncio
async def test_title_only_version_rebases_run_and_session_together() -> None:
    project_id = uuid4()
    previous_id = uuid4()
    new_id = uuid4()
    writing = _writing(project_id, previous_id)
    run = _run(project_id, previous_id, writing.id)
    session = AsyncMock()
    session.add = Mock()
    session.scalars.side_effect = [[], _Rows([writing]), _Rows([run])]
    session.scalar.side_effect = [None, 0]

    result = await rebase_title_only_dependents(
        session,
        project_id,
        previous_id,
        new_id,
        8,
    )

    assert writing.base_version_id == new_id
    assert run.base_version_id == new_id
    assert result == {"active_runs_rebased": 1, "writing_sessions_rebased": 1}
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_content_change_archives_other_sessions_and_cancels_unrelated_run() -> None:
    project_id = uuid4()
    version_id = uuid4()
    adopted = _writing(project_id, version_id)
    other = _writing(project_id, version_id)
    source_run = _run(project_id, version_id, adopted.id)
    other_run = _run(project_id, version_id, other.id)
    session = AsyncMock()
    session.scalars.side_effect = [[], _Rows([adopted, other]), _Rows([source_run, other_run])]
    session.scalar.side_effect = [None, None]

    result = await invalidate_formal_dependents(
        session,
        project_id,
        "正式版本变化",
        exempt_session_id=adopted.id,
    )

    assert adopted.archived_at is None
    assert other.archived_at is not None
    assert source_run.status == "paused"
    assert other_run.status == "cancelled"
    assert result["archived_session_count"] == 1
    assert result["cancelled_run_count"] == 1


@pytest.mark.asyncio
async def test_content_change_lock_conflict_fails_closed_for_merge() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    session.scalars.side_effect = OperationalError("SELECT", {}, _LockUnavailable())

    with pytest.raises(ConflictError, match="another project workflow"):
        await invalidate_formal_dependents(
            session,
            uuid4(),
            "正式版本变化",
            fail_fast_on_lock_conflict=True,
        )

    statement = session.scalars.await_args.args[0]
    assert statement._for_update_arg.nowait is True
