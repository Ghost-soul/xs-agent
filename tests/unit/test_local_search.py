from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_writer.db.models import SearchDocumentRecord, StateVersionRecord
from novel_writer.services.local_search import (
    LocalSearchService,
    SearchDocument,
    _record_from_document,
    _review_documents,
    _search_result,
    _state_documents,
)


def test_state_search_documents_include_character_aliases_and_exact_routes() -> None:
    project_id = uuid4()
    version = StateVersionRecord(
        id=uuid4(),
        project_id=project_id,
        number=3,
        state={
            "characters": [
                {
                    "id": "character-1",
                    "name": "林遥",
                    "aliases": ["阿遥", "潮印使"],
                    "goal": "找到失踪的兄长",
                }
            ]
        },
        chapter_revisions={},
        chapter_titles={},
    )

    documents = _state_documents(project_id, version, is_current=True)

    character = next(item for item in documents if item.source_kind == "characters")
    assert "阿遥" in character.text
    assert "潮印使" in character.text
    assert character.route == f"/projects/{project_id}/story?section=characters"
    assert character.is_current is True


def test_review_search_documents_keep_candidate_and_evidence_separate() -> None:
    project_id = uuid4()
    session_id = uuid4()
    version_id = uuid4()
    writing = SimpleNamespace(id=session_id)

    documents = _review_documents(
        project_id,
        writing,
        {
            "segments": [{"ordinal": 4, "title": "潮声", "body": "林遥发现阵眼。"}],
            "checker_report": {"issues": [{"quote": "阵眼", "status": "open"}]},
            "reader_feedback": {"confusion": "潮印代价尚不明确"},
        },
        version_id,
        8,
        is_current=True,
    )

    assert {item.source_kind for item in documents} == {"candidate_chapter", "review"}
    review = next(item for item in documents if item.source_kind == "review")
    assert "阵眼" in review.text
    assert "潮印代价" in review.text
    assert review.route == f"/projects/{project_id}/review?session={session_id}"


def test_search_result_binds_offset_route_and_source_sha() -> None:
    document = SearchDocument(
        source_kind="chapter",
        source_id="revision-1",
        title="潮声",
        text="林遥穿过长廊，看见潮印正在熄灭。",
        route="/projects/project-1/read?chapter=chapter-1",
        version_id=uuid4(),
        version_number=2,
        revision_id=uuid4(),
        is_current=True,
        locator_metadata={
            "route": "/projects/project-1/read?chapter=chapter-1",
            "chapter_id": "chapter-1",
            "resource_id": "chapter-1",
            "session_id": None,
            "section": "chapter",
            "ordinal": 1,
        },
    )
    record = _record_from_document(uuid4(), document)

    result = _search_result(record, "潮印")

    assert result is not None
    assert result["match_field"] == "text"
    assert result["offset"] == document.text.index("潮印")
    assert result["route"] == document.route
    assert result["source_sha256"] == record.text_sha256
    assert result["historical"] is False


@pytest.mark.asyncio
async def test_search_status_marks_an_old_formal_projection_stale() -> None:
    project_id = uuid4()
    current_version_id = uuid4()
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(
        id=project_id,
        current_version_id=current_version_id,
    )
    session.scalar.side_effect = [5, 3, 2, datetime.now(UTC), None]

    status = await LocalSearchService(session).status(project_id)

    assert status["status"] == "stale"
    assert status["stale_document_count"] == 2
    assert status["provider_called"] is False


@pytest.mark.asyncio
async def test_search_response_is_bounded_and_never_calls_a_provider() -> None:
    project_id = uuid4()
    version_id = uuid4()
    record = SearchDocumentRecord(
        project_id=project_id,
        document_key="d" * 64,
        source_kind="chapter",
        source_id="revision-1",
        version_id=version_id,
        version_number=1,
        revision_id=uuid4(),
        title="第一章 潮声",
        normalized_text="林遥听见潮声。",
        text_sha256="a" * 64,
        locator_metadata={
            "route": f"/projects/{project_id}/read?chapter=chapter-1",
            "chapter_id": "chapter-1",
            "resource_id": "chapter-1",
            "session_id": None,
            "section": "chapter",
            "ordinal": 1,
        },
        is_current=True,
    )
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(id=project_id, current_version_id=version_id)
    session.scalar.side_effect = [1, 1, 0, datetime.now(UTC), None, 1]
    session.scalars.return_value = [record]

    result = await LocalSearchService(session).search(
        project_id,
        "潮声",
        limit=500,
    )

    assert result["total"] == 1
    assert len(result["results"]) == 1
    assert result["provider_called"] is False
    assert result["index_status"] == "ready"
