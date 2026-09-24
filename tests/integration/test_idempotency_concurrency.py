import asyncio
from typing import Any

import pytest

from novel_writer.db.engine import Database
from novel_writer.services.errors import ConflictError
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)
from tests.integration.support import DATABASE_URL
from tests.integration.support import pytestmark as pytestmark


@pytest.mark.asyncio
async def test_concurrent_bound_idempotency_executes_domain_change_once(
    clean_test_database: None,
) -> None:
    assert DATABASE_URL is not None
    database = Database(DATABASE_URL)
    first_has_lock = asyncio.Event()
    second_started = asyncio.Event()
    mutation_count = 0

    async def execute(*, first: bool) -> dict[str, Any]:
        nonlocal mutation_count
        async with database.session() as session, session.begin():
            if not first:
                second_started.set()
            cached = await load_idempotent_response(
                session,
                "concurrent_test",
                "same-key",
                {"value": 1},
                resource_scope="test:shared",
            )
            if cached is not None:
                return cached
            mutation_count += 1
            if first:
                first_has_lock.set()
                await second_started.wait()
            response = {"mutation": mutation_count}
            await save_idempotent_response(
                session,
                "concurrent_test",
                "same-key",
                response,
                {"value": 1},
                resource_scope="test:shared",
            )
            return response

    try:
        first_task = asyncio.create_task(execute(first=True))
        await first_has_lock.wait()
        second_task = asyncio.create_task(execute(first=False))
        first_result, second_result = await asyncio.gather(first_task, second_task)
    finally:
        await database.dispose()

    assert first_result == {"mutation": 1}
    assert second_result == first_result
    assert mutation_count == 1


@pytest.mark.asyncio
async def test_bound_idempotency_rejects_same_key_with_different_payload(
    clean_test_database: None,
) -> None:
    assert DATABASE_URL is not None
    database = Database(DATABASE_URL)
    try:
        async with database.session() as session, session.begin():
            await save_idempotent_response(
                session,
                "payload_test",
                "same-key",
                {"status": "saved"},
                {"value": 1},
                resource_scope="test:shared",
            )

        async with database.session() as session, session.begin():
            with pytest.raises(ConflictError, match="different request payload"):
                await load_idempotent_response(
                    session,
                    "payload_test",
                    "same-key",
                    {"value": 2},
                    resource_scope="test:shared",
                )
    finally:
        await database.dispose()
