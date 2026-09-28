"""Current formal text only, queried inside read-only PostgreSQL transactions."""

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row

CURRENT = """
    SELECT p.id AS project_id, p.title, v.id AS version_id, v.number AS formal_version,
           v.chapter_revisions
    FROM public.story_projects p
    JOIN public.state_versions v ON v.id = p.current_version_id AND v.project_id = p.id
    WHERE p.id = %s AND p.archived_at IS NULL
"""
CHAPTERS = """
    FROM public.state_versions v
    JOIN public.chapters c ON c.project_id = v.project_id
    JOIN public.chapter_revisions r ON r.chapter_id = c.id
        AND r.id::text = v.chapter_revisions ->> c.id::text
    WHERE v.id = %s AND v.project_id = %s
"""
TITLE = "COALESCE(v.chapter_titles ->> c.id::text, c.title)"
ORDINAL = "COALESCE(c.display_ordinal, c.ordinal)"


class ReaderDatabase:
    def __init__(self, connection: dict[str, Any]):
        self.connection = connection

    @asynccontextmanager
    async def connect(self):
        async with await psycopg.AsyncConnection.connect(
            **self.connection,
            row_factory=dict_row,
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=10000 "
            "-c lock_timeout=2000 -c search_path=public -c application_name=xs-agent-reader",
        ) as connection:
            await connection.set_read_only(True)
            await connection.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
            yield connection

    async def ready(self) -> None:
        async with self.connect() as connection:
            # Check schema access without loading any novel text or mutating anything.
            await connection.execute("""
                SELECT p.id, p.title, p.archived_at, p.current_version_id,
                       v.project_id, v.number, v.chapter_revisions, v.chapter_titles,
                       c.id, c.project_id, c.title, c.ordinal, c.display_ordinal,
                       r.id, r.chapter_id, r.body
                FROM public.story_projects p, public.state_versions v,
                     public.chapters c, public.chapter_revisions r LIMIT 0
            """)

    async def projects(self) -> list[dict[str, Any]]:
        async with self.connect() as connection:
            cursor = await connection.execute("""
                SELECT p.id, p.title, v.number AS formal_version,
                       (SELECT count(*) FROM jsonb_each(v.chapter_revisions)) AS chapter_count
                FROM public.story_projects p
                JOIN public.state_versions v
                  ON v.id = p.current_version_id AND v.project_id = p.id
                WHERE p.archived_at IS NULL
                ORDER BY p.created_at DESC, p.id
            """)
            return await cursor.fetchall()

    async def _current(self, connection, project_id: UUID, expected: UUID | None = None):
        row = await (await connection.execute(CURRENT, (project_id,))).fetchone()
        if row is None:
            raise HTTPException(404, "作品不存在或已归档")
        if expected is not None and row["version_id"] != expected:
            raise HTTPException(409, "正式版本已更新，请刷新目录后继续阅读")
        return row

    async def manifest(self, project_id: UUID) -> dict[str, Any]:
        async with self.connect() as connection:
            current = await self._current(connection, project_id)
            cursor = await connection.execute(
                f"""SELECT c.id AS chapter_id, r.id AS revision_id,
                           {ORDINAL} AS ordinal, {TITLE} AS title,
                           char_length(r.body) AS char_count
                    {CHAPTERS} ORDER BY {ORDINAL}, c.ordinal""",
                (current["version_id"], project_id),
            )
            chapters = await cursor.fetchall()
            if len(chapters) != len(current.pop("chapter_revisions")):
                raise HTTPException(409, "正式目录与正文来源不一致，请在创作系统核查")
            return {**current, "chapters": chapters}

    async def chapter(self, project_id: UUID, chapter_id: UUID, version_id: UUID):
        async with self.connect() as connection:
            await self._current(connection, project_id, version_id)
            cursor = await connection.execute(
                f"""SELECT c.id AS chapter_id, r.id AS revision_id, {ORDINAL} AS ordinal,
                           {TITLE} AS title, r.body
                    {CHAPTERS} AND c.id = %s""",
                (version_id, project_id, chapter_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise HTTPException(404, "当前正式版本中没有此章节")
            return row

    async def search(self, project_id: UUID, version_id: UUID, query: str):
        async with self.connect() as connection:
            await self._current(connection, project_id, version_id)
            cursor = await connection.execute(
                f"""SELECT c.id AS chapter_id, r.id AS revision_id, {ORDINAL} AS ordinal,
                           {TITLE} AS title,
                           substring(r.body FROM greatest(1, strpos(lower(r.body),
                             lower(%s)) - 60) FOR 220) AS snippet
                    {CHAPTERS}
                    AND (strpos(lower({TITLE}), lower(%s)) > 0
                         OR strpos(lower(r.body), lower(%s)) > 0)
                    ORDER BY {ORDINAL}, c.ordinal LIMIT 51""",
                (query, version_id, project_id, query, query),
            )
            rows = await cursor.fetchall()
            return {"results": rows[:50], "has_more": len(rows) > 50}
