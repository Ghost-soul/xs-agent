"""Read immutable formal sources; retrieve only within an explicitly bound novel/version."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import ChapterRecord, ChapterRevisionRecord, StateVersionRecord
from novel_writer.generation.content import fingerprint
from novel_writer.knowledge import embedding, lookup
from novel_writer.knowledge.models import KnowledgeIndexRecord
from novel_writer.knowledge.text import corpus_hash, source, state_sources
from novel_writer.services.errors import NotFoundError, WorkflowError


async def schema_ready(session: AsyncSession) -> bool:
    return bool(await session.scalar(text("SELECT to_regclass('public.knowledge_indexes')")))


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.model_root = session.info.get("knowledge_model_root")

    async def sources(self, project_id: UUID, version_id: UUID) -> list[dict[str, Any]]:
        version = await self.session.get(StateVersionRecord, version_id)
        if version is None or version.project_id != project_id:
            raise NotFoundError("知识版本不属于本作品")
        result = state_sources(version.state)
        rows = await self.session.execute(
            select(
                ChapterRecord.id.label("chapter_id"), ChapterRecord.ordinal,
                ChapterRevisionRecord.id.label("revision_id"), ChapterRevisionRecord.body,
            )
            .join(ChapterRevisionRecord, ChapterRevisionRecord.chapter_id == ChapterRecord.id)
            .where(
                ChapterRecord.project_id == project_id,
                ChapterRevisionRecord.id.in_([UUID(v) for v in version.chapter_revisions.values()]),
            )
            .order_by(ChapterRecord.ordinal)
        )
        seen = set()
        for row in rows:
            if version.chapter_revisions.get(str(row.chapter_id)) != str(row.revision_id):
                raise WorkflowError("正式版本的章节来源绑定不一致")
            seen.add(str(row.chapter_id))
            result.append(
                source(
                    "chapter",
                    str(row.revision_id),
                    row.body,
                    chapter_id=str(row.chapter_id),
                    ordinal=row.ordinal,
                    title=version.chapter_titles.get(str(row.chapter_id), ""),
                )
            )
        if seen != set(version.chapter_revisions):
            raise WorkflowError("正式知识来源缺少章节修订")
        return result

    async def status(self, project_id: UUID, version_id: UUID) -> dict[str, Any]:
        if not await schema_ready(self.session):
            return {"status": "migration_required", "mode": "lexical", "chunk_count": 0}
        item = await self.session.get(KnowledgeIndexRecord, version_id)
        if item is None or item.project_id != project_id:
            return {"status": "queued", "mode": "lexical", "chunk_count": 0}
        return {
            "status": item.status,
            "mode": "hybrid" if item.status == "ready" else "lexical",
            "chunk_count": item.chunk_count,
            "error_code": item.error_code,
        }

    async def retrieve(
        self,
        project_id: UUID,
        version_id: UUID,
        sources: list[dict[str, Any]],
        queries: list[str],
        *,
        cutoff: int | None = None,
        model_key: str | None = None,
    ) -> dict[str, Any]:
        version = await self.session.get(StateVersionRecord, version_id)
        if version is None or version.project_id != project_id:
            raise NotFoundError("知识版本不属于本作品")
        for item in sources:
            if (
                item["kind"] == "chapter"
                and version.chapter_revisions.get(str(item.get("chapter_id", "")))
                != item["source_id"]
            ):
                raise WorkflowError("检索来源超出冻结版本")
        # A later state snapshot cannot describe an earlier scene's opening state.
        allow_state = cutoff is None or cutoff >= max(
            (s.get("ordinal", 0) for s in sources if s["kind"] == "chapter"),
            default=0,
        )
        corpus = await asyncio.to_thread(
            lookup.prepare,
            f"{self.session.get_bind().engine.url}:{project_id}:{version_id}",
            [
                item
                for item in sources
                if (item["kind"] != "chapter" and allow_state)
                or (item["kind"] == "chapter" and (cutoff is None or item["ordinal"] <= cutoff))
            ],
        )
        semantic: list[list[str]] = []
        mode, reason = "lexical", "index_pending"
        if model_key and await schema_ready(self.session):
            index = await self.session.get(KnowledgeIndexRecord, version_id)
            if (
                index
                and index.project_id == project_id
                and index.status == "ready"
                and index.model_key == model_key
                and index.manifest_sha256 == corpus_hash(sources)
            ):
                try:
                    if embedding.identity(self.model_root) != model_key:
                        raise ValueError("模型已变化")
                    vectors = await asyncio.to_thread(
                        embedding.embed,
                        [q[:300] for q in queries],
                        query=True,
                        root=self.model_root,
                    )
                except Exception:
                    # ONNX native errors do not all inherit RuntimeError. This boundary
                    # covers local inference only; database failures must not be swallowed.
                    vectors = []
                    reason = "local_model_unavailable"
                if vectors:
                    try:
                        async with self.session.begin_nested():
                            # Apply version and chapter filters before distance ordering.
                            for vector in vectors:
                                values = await self.session.scalars(
                                    text("""
                                    SELECT c.key FROM knowledge_chunks c
                                    JOIN knowledge_vectors v ON v.project_id = c.project_id
                                      AND v.text_sha256 = c.text_sha256 AND v.model_key = :model
                                    WHERE c.project_id = :project AND c.version_id = :version
                                      AND ((:allow_state AND c.payload->>'kind' <> 'chapter') OR
                                        (c.payload->>'kind' = 'chapter' AND
                                          (CAST(:cutoff AS integer) IS NULL OR
                                            (c.payload->>'ordinal')::integer <= :cutoff)))
                                    ORDER BY v.embedding <=> CAST(:vector AS vector), c.key LIMIT 40
                                """),
                                    {
                                        "project": project_id,
                                        "version": version_id,
                                        "model": model_key,
                                        "cutoff": cutoff,
                                        "vector": json.dumps(vector),
                                        "allow_state": allow_state,
                                    },
                                )
                                semantic.append(list(values))
                        mode, reason = "hybrid", "ready"
                    except SQLAlchemyError:
                        semantic = []
                        reason = "index_unavailable"
        hits = await asyncio.to_thread(corpus.rank, queries, semantic)
        receipt = {
            "policy": "knowledge-rag-v1",
            "project_id": str(project_id),
            "version_id": str(version_id),
            "corpus_sha256": corpus_hash(sources),
            "queries": queries,
            "cutoff": cutoff,
            "model_key": model_key,
            "mode": mode,
            "reason": reason,
            "hits": hits,
        }
        return {**receipt, "sha256": fingerprint(receipt)}
