from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from reader.database import ReaderDatabase
from tests.integration.support import DATABASE_URL, pytestmark  # noqa: F401


@pytest.fixture
def reading_fixture(clean_test_database):
    connection_info = conninfo_to_dict(
        DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    )
    ids = {key: uuid4() for key in ("project", "version", "chapter", "revision", "draft", "other")}
    with psycopg.connect(**connection_info) as connection:
        connection.execute(
            "INSERT INTO story_projects (id,title) VALUES (%s,%s),(%s,%s)",
            (ids["project"], "只读测试作品", ids["other"], "其他作品"),
        )
        connection.execute(
            """INSERT INTO state_versions
            (id,project_id,number,state,chapter_revisions,chapter_titles)
            VALUES (%s,%s,1,%s,%s,%s)""",
            (
                ids["version"],
                ids["project"],
                Jsonb({}),
                Jsonb({str(ids["chapter"]): str(ids["revision"])}),
                Jsonb({str(ids["chapter"]): "当前正式标题"}),
            ),
        )
        connection.execute(
            "UPDATE story_projects SET current_version_id=%s WHERE id=%s",
            (ids["version"], ids["project"]),
        )
        connection.execute(
            """INSERT INTO chapters (id,project_id,ordinal,display_ordinal,title)
            VALUES (%s,%s,9,1,%s)""",
            (ids["chapter"], ids["project"], "旧标题"),
        )
        for key, body, status in [
            ("revision", "正式正文。\n阳光落在书页上。", "committed"),
            ("draft", "尚未采用的私有草稿。", "draft"),
        ]:
            connection.execute(
                """INSERT INTO chapter_revisions (id,chapter_id,base_version_id,body,status)
                VALUES (%s,%s,%s,%s,%s)""",
                (ids[key], ids["chapter"], ids["version"], body, status),
            )
    yield ReaderDatabase(connection_info), connection_info, ids


@pytest.mark.asyncio
async def test_only_current_formal_text_and_search_are_readable(reading_fixture):
    database, info, ids = reading_fixture
    await database.ready()
    assert (await database.projects())[0]["chapter_count"] == 1
    manifest = await database.manifest(ids["project"])
    assert manifest["chapters"][0]["title"] == "当前正式标题"
    assert manifest["chapters"][0]["ordinal"] == 1
    assert "body" not in manifest["chapters"][0]
    body = await database.chapter(ids["project"], ids["chapter"], ids["version"])
    assert body["body"] == "正式正文。\n阳光落在书页上。"
    assert body["revision_id"] == ids["revision"]
    assert len((await database.search(ids["project"], ids["version"], "阳光"))["results"]) == 1
    assert not (await database.search(ids["project"], ids["version"], "私有草稿"))["results"]
    assert not (await database.search(ids["project"], ids["version"], "%' OR true --"))["results"]
    with pytest.raises(HTTPException) as foreign:
        await database.chapter(ids["other"], ids["chapter"], ids["version"])
    assert foreign.value.status_code == 404
    with pytest.raises(HTTPException) as stale:
        await database.chapter(ids["project"], ids["chapter"], uuid4())
    assert stale.value.status_code == 409
    with psycopg.connect(**info) as connection:
        connection.execute(
            "UPDATE story_projects SET archived_at=now() WHERE id=%s", (ids["project"],)
        )
    assert await database.projects() == []
    with pytest.raises(HTTPException):
        await database.manifest(ids["project"])


@pytest.mark.asyncio
async def test_transactions_reject_writes_even_with_owner_account(reading_fixture):
    database, info, ids = reading_fixture
    async with database.connect() as connection:
        row = await (await connection.execute("SHOW transaction_read_only")).fetchone()
        assert row["transaction_read_only"] == "on"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await connection.execute(
                "UPDATE story_projects SET title='changed' WHERE id=%s", (ids["project"],)
            )
    assert (await database.projects())[0]["title"] == "只读测试作品"


@pytest.mark.asyncio
async def test_column_scoped_reader_grants_are_sufficient(reading_fixture):
    database, info, ids = reading_fixture
    script = Path(__file__).resolve().parents[2] / "reader/grant-reader.sql"
    with psycopg.connect(**info, autocommit=True) as connection:
        connection.execute(script.read_text(encoding="utf8"))
        connection.execute("ALTER ROLE novel_reader PASSWORD 'synthetic-reader-db-password'")
    try:
        limited = ReaderDatabase(
            {**info, "user": "novel_reader", "password": "synthetic-reader-db-password"}
        )
        await limited.ready()
        assert len(await limited.projects()) == 1
        assert len((await limited.manifest(ids["project"]))["chapters"]) == 1
        assert (await limited.chapter(ids["project"], ids["chapter"], ids["version"]))["body"]
        assert (await limited.search(ids["project"], ids["version"], "阳光"))["results"]
        async with limited.connect() as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute("SELECT state FROM public.state_versions")
    finally:
        with psycopg.connect(**info, autocommit=True) as connection:
            connection.execute("DROP OWNED BY novel_reader")
            connection.execute("DROP ROLE novel_reader")
