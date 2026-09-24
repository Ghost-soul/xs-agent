from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from novel_writer.db.models import (
    IdempotencyKeyRecord,
)
from novel_writer.services.errors import ConflictError
from novel_writer.services.idempotency import (
    load_idempotent_response,
    save_idempotent_response,
)


@pytest.mark.asyncio
async def test_idempotency_response_is_persisted_and_loaded_as_a_copy() -> None:
    session = AsyncMock()
    session.add = Mock()
    session.scalar.return_value = None
    response = {"revision_id": str(uuid4()), "nested": {"status": "ready"}}
    request_payload = {"base_version": 3}
    resource_scope = "revision:chapter-1"

    await save_idempotent_response(
        session,
        "create_revision",
        "stable-key",
        response,
        request_payload,
        resource_scope=resource_scope,
    )

    added = session.add.call_args.args[0]
    assert isinstance(added, IdempotencyKeyRecord)
    assert added.response == response
    assert added.request_schema_version == "idempotency-request-v1"
    assert added.resource_scope == resource_scope
    session.flush.assert_awaited_once()

    session.scalar.return_value = added
    loaded = await load_idempotent_response(
        session,
        "create_revision",
        "stable-key",
        request_payload,
        resource_scope=resource_scope,
    )
    assert loaded == response
    assert loaded is not response
    assert loaded is not None
    loaded["nested"]["status"] = "changed"
    assert added.response["nested"]["status"] == "ready"


@pytest.mark.asyncio
async def test_idempotency_key_rejects_a_different_request_payload() -> None:
    session = AsyncMock()
    session.add = Mock()
    session.scalar.return_value = None
    await save_idempotent_response(
        session,
        "update_review",
        "stable-key",
        {"status": "saved"},
        {"body": "first"},
        resource_scope="review:session-1",
    )
    record = session.add.call_args.args[0]
    session.scalar.return_value = record

    assert await load_idempotent_response(
        session,
        "update_review",
        "stable-key",
        {"body": "first"},
        resource_scope="review:session-1",
    ) == {"status": "saved"}
    with pytest.raises(ConflictError, match="different request payload"):
        await load_idempotent_response(
            session,
            "update_review",
            "stable-key",
            {"body": "second"},
            resource_scope="review:session-1",
        )


@pytest.mark.asyncio
async def test_bound_idempotency_rejects_legacy_unbound_record() -> None:
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(
        request_fingerprint_sha256=None,
        request_schema_version=None,
        resource_scope=None,
        response={"status": "historic"},
    )

    with pytest.raises(ConflictError, match="legacy unbound request"):
        await load_idempotent_response(
            session,
            "confirm_plan",
            "historic-key",
            {"confirmed": True},
            resource_scope="plan:1",
        )
