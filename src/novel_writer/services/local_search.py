"""Provider-free search projection, rebuild, status, and project-scoped queries."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    LocalTaskRecord,
    ProjectRecord,
    SearchDocumentRecord,
    StateVersionRecord,
    WritingSessionRecord,
)
from novel_writer.services.canonical_json import canonical_json_sha256
from novel_writer.services.errors import NotFoundError, WorkflowError

SearchScope = Literal["all", "chapters", "characters", "world", "plot", "review"]
SearchIndexStatus = Literal["not_built", "rebuilding", "ready", "failed", "stale"]
SEARCH_DOCUMENT_SCHEMA_VERSION = "search-document-v1"
SEARCH_SCOPES: frozenset[str] = frozenset(
    {"all", "chapters", "characters", "world", "plot", "review"}
)
SEARCH_SNIPPET_MAX_CHARS = 280

_SCOPE_KINDS: dict[str, frozenset[str]] = {
    "chapters": frozenset({"chapter", "candidate_chapter"}),
    "characters": frozenset({"characters", "relationships"}),
    "world": frozenset({"world_rules", "world_lore", "story_forces"}),
    "plot": frozenset(
        {
            "plot_threads",
            "foreshadowings",
            "reader_promises",
            "open_questions",
            "scheduled_developments",
            "narrative_phases",
            "plot_history",
            "story_foundation",
            "narrative_position",
        }
    ),
    "review": frozenset({"review", "candidate_chapter"}),
}

_STATE_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("characters", "人物"),
    ("relationships", "人物关系"),
    ("world_rules", "世界规则"),
    ("world_lore", "世界设定"),
    ("story_forces", "故事力量"),
    ("plot_threads", "剧情线"),
    ("foreshadowings", "伏笔"),
    ("reader_promises", "读者承诺"),
    ("open_questions", "开放问题"),
    ("scheduled_developments", "计划发展"),
    ("narrative_phases", "叙事阶段"),
    ("plot_history", "剧情历史"),
)

_REVIEW_SEARCH_FIELDS: tuple[str, ...] = (
    "checker_report",
    "checker_issue_decisions",
    "reader_feedback",
    "reader_report",
    "repair_advisory",
    "issues",
    "blocking_risks",
    "naturalness_report",
)


@dataclass(frozen=True)
class SearchDocument:
    source_kind: str
    source_id: str
    title: str
    text: str
    route: str
    version_id: UUID | None
    version_number: int | None
    revision_id: UUID | None
    is_current: bool
    locator_metadata: dict[str, str | int | None]


@dataclass(frozen=True)
class SearchRebuildResult:
    project_id: UUID
    document_count: int
    current_count: int
    historical_count: int
    index_sha256: str


class LocalSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rebuild_project_index(self, project_id: UUID) -> SearchRebuildResult:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        documents = await self._source_documents(project)
        documents.sort(
            key=lambda item: (
                not item.is_current,
                -(item.version_number or 0),
                item.source_kind,
                item.source_id,
                str(item.revision_id or ""),
            )
        )
        await self.session.execute(
            delete(SearchDocumentRecord).where(SearchDocumentRecord.project_id == project_id)
        )
        records = [_record_from_document(project_id, item) for item in documents]
        self.session.add_all(records)
        await self.session.flush()
        current_count = sum(item.is_current for item in documents)
        manifest = [_document_manifest(item) for item in documents]
        return SearchRebuildResult(
            project_id=project_id,
            document_count=len(documents),
            current_count=current_count,
            historical_count=len(documents) - current_count,
            index_sha256=canonical_json_sha256(manifest),
        )

    async def search(
        self,
        project_id: UUID,
        query: str,
        *,
        scope: SearchScope = "all",
        include_history: bool = False,
        limit: int = 50,
        cursor: int = 0,
    ) -> dict[str, Any]:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        if scope not in SEARCH_SCOPES:
            raise WorkflowError("search scope is not allowlisted")
        needle = query.strip()
        bounded_limit = max(1, min(limit, 100))
        bounded_cursor = max(0, cursor)
        status = await self.status(project_id, project=project)
        if not needle:
            return {
                "query": query,
                "scope": scope,
                "include_history": include_history,
                "results": [],
                "next_cursor": None,
                "total": 0,
                "provider_called": False,
                "index_status": status["status"],
            }

        escaped = _escape_like(needle)
        pattern = f"%{escaped}%"
        predicates = [
            SearchDocumentRecord.project_id == project_id,
            or_(
                SearchDocumentRecord.title.ilike(pattern, escape="\\"),
                SearchDocumentRecord.normalized_text.ilike(pattern, escape="\\"),
            ),
        ]
        if not include_history:
            predicates.append(SearchDocumentRecord.is_current.is_(True))
        allowed_kinds = _SCOPE_KINDS.get(scope)
        if allowed_kinds is not None:
            predicates.append(SearchDocumentRecord.source_kind.in_(allowed_kinds))

        total = int(
            await self.session.scalar(
                select(func.count(SearchDocumentRecord.id)).where(*predicates)
            )
            or 0
        )
        rows = list(
            await self.session.scalars(
                select(SearchDocumentRecord)
                .where(*predicates)
                .order_by(
                    SearchDocumentRecord.is_current.desc(),
                    func.coalesce(SearchDocumentRecord.version_number, 0).desc(),
                    SearchDocumentRecord.source_kind,
                    SearchDocumentRecord.source_id,
                    SearchDocumentRecord.id,
                )
                .offset(bounded_cursor)
                .limit(bounded_limit)
            )
        )
        results = [_search_result(item, needle) for item in rows]
        page = [item for item in results if item is not None]
        next_cursor = (
            bounded_cursor + len(rows)
            if bounded_cursor + len(rows) < total
            else None
        )
        return {
            "query": query,
            "scope": scope,
            "include_history": include_history,
            "results": page,
            "next_cursor": next_cursor,
            "total": total,
            "provider_called": False,
            "index_status": status["status"],
        }

    async def status(
        self,
        project_id: UUID,
        *,
        project: ProjectRecord | None = None,
    ) -> dict[str, Any]:
        resolved_project = project or await self.session.get(ProjectRecord, project_id)
        if resolved_project is None:
            raise NotFoundError("project not found")
        document_count = int(
            await self.session.scalar(
                select(func.count(SearchDocumentRecord.id)).where(
                    SearchDocumentRecord.project_id == project_id
                )
            )
            or 0
        )
        current_count = int(
            await self.session.scalar(
                select(func.count(SearchDocumentRecord.id)).where(
                    SearchDocumentRecord.project_id == project_id,
                    SearchDocumentRecord.is_current.is_(True),
                )
            )
            or 0
        )
        stale_document_count = int(
            await self.session.scalar(
                select(func.count(SearchDocumentRecord.id)).where(
                    SearchDocumentRecord.project_id == project_id,
                    SearchDocumentRecord.is_current.is_(True),
                    SearchDocumentRecord.source_kind.notin_(("review", "candidate_chapter")),
                    SearchDocumentRecord.version_id.is_not(None),
                    SearchDocumentRecord.version_id != resolved_project.current_version_id,
                )
            )
            or 0
        )
        last_indexed_at = await self.session.scalar(
            select(func.max(SearchDocumentRecord.updated_at)).where(
                SearchDocumentRecord.project_id == project_id
            )
        )
        latest_task = await self.session.scalar(
            select(LocalTaskRecord)
            .where(
                LocalTaskRecord.project_id == project_id,
                LocalTaskRecord.kind == "search_rebuild",
            )
            .order_by(LocalTaskRecord.created_at.desc(), LocalTaskRecord.id.desc())
            .limit(1)
        )
        index_status: SearchIndexStatus
        if latest_task is not None and latest_task.status in {"queued", "running"}:
            index_status = "rebuilding"
        elif latest_task is not None and latest_task.status == "failed":
            index_status = "failed"
        elif stale_document_count:
            index_status = "stale"
        elif document_count or (latest_task is not None and latest_task.status == "completed"):
            index_status = "ready"
        else:
            index_status = "not_built"
        return {
            "project_id": project_id,
            "status": index_status,
            "document_count": document_count,
            "current_document_count": current_count,
            "historical_document_count": document_count - current_count,
            "stale_document_count": stale_document_count,
            "last_indexed_at": _isoformat(last_indexed_at),
            "latest_task_id": latest_task.id if latest_task is not None else None,
            "latest_task_status": latest_task.status if latest_task is not None else None,
            "latest_task_error_code": latest_task.error_code if latest_task is not None else None,
            "provider_called": False,
        }

    async def _source_documents(self, project: ProjectRecord) -> list[SearchDocument]:
        versions = list(
            await self.session.scalars(
                select(StateVersionRecord)
                .where(StateVersionRecord.project_id == project.id)
                .order_by(StateVersionRecord.number.desc(), StateVersionRecord.id)
            )
        )
        chapters = list(
            await self.session.scalars(
                select(ChapterRecord)
                .where(ChapterRecord.project_id == project.id)
                .order_by(ChapterRecord.ordinal, ChapterRecord.id)
            )
        )
        chapter_ids = [item.id for item in chapters]
        revisions: dict[str, ChapterRevisionRecord] = {}
        if chapter_ids:
            revisions = {
                str(item.id): item
                for item in await self.session.scalars(
                    select(ChapterRevisionRecord).where(
                        ChapterRevisionRecord.chapter_id.in_(chapter_ids)
                    )
                )
            }
        documents: list[SearchDocument] = []
        for version in versions:
            is_current = version.id == project.current_version_id
            title_map = version.chapter_titles if isinstance(version.chapter_titles, dict) else {}
            revision_map = (
                version.chapter_revisions if isinstance(version.chapter_revisions, dict) else {}
            )
            for chapter in chapters:
                raw_revision_id = revision_map.get(str(chapter.id))
                revision = revisions.get(str(raw_revision_id))
                if revision is None:
                    continue
                title = str(title_map.get(str(chapter.id)) or chapter.title)
                route = f"/projects/{project.id}/read?chapter={chapter.id}"
                documents.append(
                    SearchDocument(
                        source_kind="chapter",
                        source_id=str(revision.id),
                        title=title,
                        text=_normalize_text(revision.body),
                        route=route,
                        version_id=version.id,
                        version_number=version.number,
                        revision_id=revision.id,
                        is_current=is_current,
                        locator_metadata={
                            "route": route,
                            "chapter_id": str(chapter.id),
                            "resource_id": str(chapter.id),
                            "session_id": None,
                            "section": "chapter",
                            "ordinal": chapter.display_ordinal or chapter.ordinal,
                        },
                    )
                )
            documents.extend(_state_documents(project.id, version, is_current=is_current))

        version_numbers = {item.id: item.number for item in versions}
        sessions = list(
            await self.session.scalars(
                select(WritingSessionRecord)
                .where(WritingSessionRecord.project_id == project.id)
                .order_by(WritingSessionRecord.created_at, WritingSessionRecord.id)
            )
        )
        for writing in sessions:
            package = writing.review_package
            if not isinstance(package, dict):
                continue
            is_current = writing.archived_at is None and writing.merged_at is None
            documents.extend(
                _review_documents(
                    project.id,
                    writing,
                    package,
                    writing.base_version_id,
                    version_numbers.get(writing.base_version_id),
                    is_current=is_current,
                )
            )
        return documents


def _state_documents(
    project_id: UUID,
    version: StateVersionRecord,
    *,
    is_current: bool,
) -> list[SearchDocument]:
    documents: list[SearchDocument] = []
    state = version.state if isinstance(version.state, dict) else {}
    route_root = f"/projects/{project_id}/story"
    for collection, label in _STATE_COLLECTIONS:
        values = state.get(collection, [])
        if isinstance(values, dict):
            source_values: list[Any] = [values]
        elif isinstance(values, list):
            source_values = values
        else:
            continue
        for index, value in enumerate(source_values):
            text = _flatten(value)
            if not text:
                continue
            item_id = _item_id(value, index)
            route = f"{route_root}?section={collection}"
            documents.append(
                SearchDocument(
                    source_kind=collection,
                    source_id=item_id,
                    title=_item_title(value, label),
                    text=_normalize_text(text),
                    route=route,
                    version_id=version.id,
                    version_number=version.number,
                    revision_id=None,
                    is_current=is_current,
                    locator_metadata={
                        "route": route,
                        "chapter_id": None,
                        "resource_id": item_id,
                        "session_id": None,
                        "section": collection,
                        "ordinal": index,
                    },
                )
            )
    for collection, label in (
        ("story_foundation", "故事核心"),
        ("narrative_position", "叙事位置"),
    ):
        value = state.get(collection)
        text = _flatten(value)
        if not text:
            continue
        route = f"{route_root}?section={collection}"
        documents.append(
            SearchDocument(
                source_kind=collection,
                source_id=collection,
                title=label,
                text=_normalize_text(text),
                route=route,
                version_id=version.id,
                version_number=version.number,
                revision_id=None,
                is_current=is_current,
                locator_metadata={
                    "route": route,
                    "chapter_id": None,
                    "resource_id": collection,
                    "session_id": None,
                    "section": collection,
                    "ordinal": None,
                },
            )
        )
    return documents


def _review_documents(
    project_id: UUID,
    writing: WritingSessionRecord,
    package: dict[str, Any],
    version_id: UUID,
    version_number: int | None,
    *,
    is_current: bool,
) -> list[SearchDocument]:
    route = f"/projects/{project_id}/review?session={writing.id}"
    documents: list[SearchDocument] = []
    segments = package.get("segments", [])
    if isinstance(segments, list):
        for index, raw_segment in enumerate(segments):
            if not isinstance(raw_segment, dict):
                continue
            body = raw_segment.get("body")
            if not isinstance(body, str) or not body:
                continue
            raw_chapter_id = raw_segment.get("chapter_id")
            chapter_id = str(raw_chapter_id) if raw_chapter_id is not None else None
            ordinal = raw_segment.get("ordinal")
            source_id = f"{writing.id}:{chapter_id or ordinal or index}"
            title = str(raw_segment.get("title") or f"待审章节 {index + 1}")
            documents.append(
                SearchDocument(
                    source_kind="candidate_chapter",
                    source_id=source_id,
                    title=title,
                    text=_normalize_text(body),
                    route=route,
                    version_id=version_id,
                    version_number=version_number,
                    revision_id=None,
                    is_current=is_current,
                    locator_metadata={
                        "route": route,
                        "chapter_id": chapter_id,
                        "resource_id": source_id,
                        "session_id": str(writing.id),
                        "section": "candidate",
                        "ordinal": ordinal if isinstance(ordinal, int) else index + 1,
                    },
                )
            )
    audit = {key: package[key] for key in _REVIEW_SEARCH_FIELDS if key in package}
    audit_text = _flatten(audit)
    if audit_text:
        documents.append(
            SearchDocument(
                source_kind="review",
                source_id=str(writing.id),
                title="审核证据",
                text=_normalize_text(audit_text),
                route=route,
                version_id=version_id,
                version_number=version_number,
                revision_id=None,
                is_current=is_current,
                locator_metadata={
                    "route": route,
                    "chapter_id": None,
                    "resource_id": str(writing.id),
                    "session_id": str(writing.id),
                    "section": "review",
                    "ordinal": None,
                },
            )
        )
    return documents


def _record_from_document(project_id: UUID, document: SearchDocument) -> SearchDocumentRecord:
    manifest = _document_manifest(document)
    return SearchDocumentRecord(
        schema_version=SEARCH_DOCUMENT_SCHEMA_VERSION,
        project_id=project_id,
        document_key=canonical_json_sha256(
            {
                "source_kind": document.source_kind,
                "source_id": document.source_id,
                "version_id": str(document.version_id) if document.version_id else None,
                "version_number": document.version_number,
                "revision_id": str(document.revision_id) if document.revision_id else None,
            }
        ),
        source_kind=document.source_kind,
        source_id=document.source_id,
        version_id=document.version_id,
        version_number=document.version_number,
        revision_id=document.revision_id,
        title=document.title,
        normalized_text=document.text,
        text_sha256=str(manifest["text_sha256"]),
        locator_metadata=dict(document.locator_metadata),
        is_current=document.is_current,
    )


def _document_manifest(document: SearchDocument) -> dict[str, Any]:
    return {
        "source_kind": document.source_kind,
        "source_id": document.source_id,
        "version_id": str(document.version_id) if document.version_id else None,
        "version_number": document.version_number,
        "revision_id": str(document.revision_id) if document.revision_id else None,
        "title": document.title,
        "text_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
        "locator_metadata": document.locator_metadata,
        "is_current": document.is_current,
    }


def _search_result(record: SearchDocumentRecord, query: str) -> dict[str, Any] | None:
    title_match = re.search(re.escape(query), record.title, flags=re.IGNORECASE)
    text_match = re.search(re.escape(query), record.normalized_text, flags=re.IGNORECASE)
    if title_match is not None:
        match_field = "title"
        offset = title_match.start()
        snippet = record.title[:SEARCH_SNIPPET_MAX_CHARS]
    elif text_match is not None:
        match_field = "text"
        offset = text_match.start()
        start = max(0, offset - 90)
        end = min(len(record.normalized_text), offset + len(query) + 140)
        snippet = record.normalized_text[start:end].replace("\r", " ").replace("\n", " ")
        snippet = snippet[:SEARCH_SNIPPET_MAX_CHARS]
    else:
        return None
    locator = dict(record.locator_metadata or {})
    route = str(locator.get("route") or "")
    raw_chapter_id = locator.get("chapter_id")
    return {
        "source_kind": record.source_kind,
        "source_id": record.source_id,
        "title": record.title,
        "snippet": snippet,
        "offset": offset,
        "match_field": match_field,
        "version_id": record.version_id,
        "version": record.version_number,
        "revision_id": record.revision_id,
        "source_sha256": record.text_sha256,
        "route": route,
        "chapter_id": str(raw_chapter_id) if raw_chapter_id is not None else None,
        "locator": locator,
        "historical": not record.is_current,
        "source_scope": "current" if record.is_current else "history",
    }


def _item_id(value: Any, index: int) -> str:
    if isinstance(value, dict):
        for key in ("id", "name", "title", "summary", "statement", "question"):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate)
    return str(index)


def _item_title(value: Any, fallback: str) -> str:
    if isinstance(value, dict):
        for key in ("name", "title", "summary", "statement", "question", "content"):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate)[:500]
    return fallback


def _normalize_text(value: str) -> str:
    # PostgreSQL text rejects NUL; replacement preserves all character offsets.
    return value.replace("\x00", "\ufffd")


def _flatten(value: Any) -> str:
    if isinstance(value, dict):
        parts = [(str(key), _flatten(item)) for key, item in value.items()]
        return "；".join(f"{key}：{item}" for key, item in parts if item)
    if isinstance(value, list | tuple):
        return "；".join(item for item in (_flatten(item) for item in value) if item)
    return str(value).strip() if value is not None else ""


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
