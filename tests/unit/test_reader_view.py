import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    StateVersionRecord,
)
from novel_writer.services.errors import NotFoundError
from novel_writer.services.reader_view import (
    ReaderViewService,
    build_reader_manifest,
    build_reader_view,
)


@pytest.mark.asyncio
async def test_single_chapter_read_does_not_load_the_full_chapter_collection() -> None:
    project_id = uuid4()
    version_id = uuid4()
    chapter_id = uuid4()
    revision_id = uuid4()
    chapter = ChapterRecord(
        id=chapter_id,
        project_id=project_id,
        ordinal=7,
        display_ordinal=7,
        title="数据库标题",
    )
    revision = ChapterRevisionRecord(
        id=revision_id,
        chapter_id=chapter_id,
        base_version_id=version_id,
        body="只读取这一章。",
        status="committed",
    )
    version = StateVersionRecord(
        id=version_id,
        project_id=project_id,
        number=3,
        state={},
        chapter_revisions={str(chapter_id): str(revision_id)},
        chapter_titles={str(chapter_id): "正式标题"},
    )
    session = AsyncMock()
    session.get.side_effect = [
        SimpleNamespace(id=project_id, current_version_id=version_id),
        version,
        revision,
    ]
    session.scalar.return_value = chapter

    result = await ReaderViewService(session).build_chapter(project_id, chapter_id)

    assert result["body"] == revision.body
    assert result["title"] == "正式标题"
    session.scalars.assert_not_awaited()


def test_reader_view_only_returns_formal_mapped_revisions_in_display_order() -> None:
    project_id = uuid4()
    version_id = uuid4()
    first = ChapterRecord(
        id=uuid4(),
        project_id=project_id,
        ordinal=3,
        display_ordinal=1,
        title="城门将闭",
    )
    second = ChapterRecord(
        id=uuid4(),
        project_id=project_id,
        ordinal=4,
        display_ordinal=2,
        title="雨夜补录",
    )
    draft_only = ChapterRecord(
        id=uuid4(),
        project_id=project_id,
        ordinal=5,
        display_ordinal=3,
        title="未合并候选",
    )
    first_revision = ChapterRevisionRecord(
        id=uuid4(),
        chapter_id=first.id,
        base_version_id=version_id,
        body="第一章正式正文",
        status="committed",
    )
    second_revision = ChapterRevisionRecord(
        id=uuid4(),
        chapter_id=second.id,
        base_version_id=version_id,
        body="第二章正式正文",
        status="committed",
    )
    historical_draft = ChapterRevisionRecord(
        id=uuid4(),
        chapter_id=draft_only.id,
        base_version_id=version_id,
        body="不得出现",
        status="draft",
    )
    version = StateVersionRecord(
        id=version_id,
        project_id=project_id,
        number=7,
        state={},
        chapter_revisions={
            str(second.id): str(second_revision.id),
            str(first.id): str(first_revision.id),
        },
    )

    result = build_reader_view(
        "测试作品",
        version,
        [second, draft_only, first],
        {
            str(first_revision.id): first_revision,
            str(second_revision.id): second_revision,
            str(historical_draft.id): historical_draft,
        },
    )

    assert result["formal_version"] == 7
    assert [item["chapter_id"] for item in result["chapters"]] == [
        str(first.id),
        str(second.id),
    ]
    assert [item["ordinal"] for item in result["chapters"]] == [1, 2]
    assert result["chapters"][0]["revision_id"] == str(first_revision.id)
    assert result["chapters"][0]["body_sha256"] == hashlib.sha256(
        first_revision.body.encode("utf-8")
    ).hexdigest()
    assert all(item["body"] != historical_draft.body for item in result["chapters"])


def test_reader_view_rejects_broken_formal_revision_mapping() -> None:
    project_id = uuid4()
    chapter = ChapterRecord(
        id=uuid4(),
        project_id=project_id,
        ordinal=1,
        title="第一章",
    )
    version = StateVersionRecord(
        id=uuid4(),
        project_id=project_id,
        number=2,
        state={},
        chapter_revisions={str(chapter.id): str(uuid4())},
    )

    with pytest.raises(NotFoundError, match="formal revision not found"):
        build_reader_view("测试作品", version, [chapter], {})


def test_reader_manifest_has_no_body_for_five_hundred_chapters() -> None:
    project_id = uuid4()
    version_id = uuid4()
    chapters: list[ChapterRecord] = []
    revisions: dict[UUID, ChapterRevisionRecord] = {}
    revision_map: dict[str, str] = {}
    for ordinal in range(1, 501):
        chapter = ChapterRecord(
            id=uuid4(), project_id=project_id, ordinal=ordinal, title=f"第{ordinal}章"
        )
        revision = ChapterRevisionRecord(
            id=uuid4(), chapter_id=chapter.id, base_version_id=version_id,
            body="测试正文" * 1000, status="committed",
        )
        chapters.append(chapter)
        revisions[revision.id] = revision
        revision_map[str(chapter.id)] = str(revision.id)
    version = StateVersionRecord(
        id=version_id, project_id=project_id, number=9, state={},
        chapter_revisions=revision_map,
    )

    result = build_reader_manifest("长篇性能样本", version, chapters, revisions)

    assert len(result["chapters"]) == 500
    assert all("body" not in item for item in result["chapters"])
    assert result["chapters"][499]["char_count"] == 4000
