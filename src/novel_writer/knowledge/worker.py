"""Bounded local indexing; transactions release all locks on process failure."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path

from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import ColumnElement

from novel_writer.db.engine import Database
from novel_writer.knowledge import embedding
from novel_writer.knowledge.models import KnowledgeChunkRecord, KnowledgeIndexRecord
from novel_writer.knowledge.service import KnowledgeService, schema_ready
from novel_writer.knowledge.text import chunks, corpus_hash

logger = logging.getLogger(__name__)


class KnowledgeWorker:
    def __init__(self, database: Database, model_root: Path) -> None:
        self.database, self.model_root = database, model_root

    async def tick(self) -> bool:
        try:
            model_key = embedding.identity(self.model_root)
        except (OSError, ValueError):
            model_key = None
        async with self.database.session() as session, session.begin():
            if not await schema_ready(session):
                return False
            conditions: list[ColumnElement[bool]] = [
                KnowledgeIndexRecord.status.in_(("queued", "embedding"))
            ]
            if model_key:
                conditions.append(
                    KnowledgeIndexRecord.status.in_(("lexical", "ready"))
                    & KnowledgeIndexRecord.model_key.is_distinct_from(model_key)
                    & KnowledgeIndexRecord.error_code.is_(None)
                )
            job = await session.scalar(
                select(KnowledgeIndexRecord)
                .where(or_(*conditions))
                .order_by(KnowledgeIndexRecord.updated_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return False
            if job.status == "queued":
                sources = await KnowledgeService(session).sources(job.project_id, job.version_id)
                pieces = await asyncio.to_thread(chunks, sources)
                await session.execute(
                    delete(KnowledgeChunkRecord).where(
                        KnowledgeChunkRecord.project_id == job.project_id,
                        KnowledgeChunkRecord.version_id == job.version_id,
                    )
                )
                for start in range(0, len(pieces), 100):
                    await session.execute(
                        insert(KnowledgeChunkRecord),
                        [
                            {
                                "project_id": job.project_id,
                                "version_id": job.version_id,
                                "key": p["id"],
                                "text_sha256": p["text_sha256"],
                                "text": p["text"],
                                "payload": p,
                            }
                            for p in pieces[start : start + 100]
                        ],
                    )
                job.manifest_sha256, job.chunk_count = corpus_hash(sources), len(pieces)
                job.error_code = None
            job.model_key = model_key
            if model_key is None:
                job.status = "lexical"
                return True
            pending = list(
                (
                    await session.execute(
                        text("""
                SELECT DISTINCT c.text_sha256, c.text FROM knowledge_chunks c
                WHERE c.project_id = :project AND c.version_id = :version
                  AND NOT EXISTS (SELECT 1 FROM knowledge_vectors v
                    WHERE v.project_id = c.project_id AND v.model_key = :model
                      AND v.text_sha256 = c.text_sha256)
                ORDER BY c.text_sha256 LIMIT 16
            """),
                        {"project": job.project_id, "version": job.version_id, "model": model_key},
                    )
                ).all()
            )
            if not pending:
                job.status = "ready"
                return True
            try:
                vectors = await asyncio.to_thread(
                    embedding.embed,
                    [p.text for p in pending],
                    root=self.model_root,
                )
            except Exception:
                job.status, job.error_code = "lexical", "local_model_unavailable"
                return True
            for item, vector in zip(pending, vectors, strict=True):
                await session.execute(
                    text("""
                    INSERT INTO knowledge_vectors(project_id, model_key, text_sha256, embedding)
                    VALUES (:project, :model, :sha, CAST(:vector AS vector)) ON CONFLICT DO NOTHING
                """),
                    {
                        "project": job.project_id,
                        "model": model_key,
                        "sha": item.text_sha256,
                        "vector": json.dumps(vector),
                    },
                )
            job.status = "embedding"
            return True

    async def run(self, stopped: asyncio.Event, poll_seconds: float = 5) -> None:
        while not stopped.is_set():
            try:
                progressed = await self.tick()
            except Exception:
                # Local DB/save failures are retryable; there are no paid calls in this worker.
                logger.warning("knowledge.index_retry_pending", exc_info=False)
                progressed = False
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), timeout=0.1 if progressed else poll_seconds)
