# ruff: noqa: F401 F811
import json
from copy import deepcopy
from uuid import uuid4

import pytest

from novel_writer.generation import request_preparation
from novel_writer.knowledge.service import KnowledgeService
from novel_writer.services.errors import WorkflowError
from tests.integration import test_role_context as previous
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_role_context import (
    OPTIONS,
    automated,
    current,
    generation,
    longform,
    novel_generation,
    post,
    read,
    recovery,
    settle,
    stage_create,
    start,
    test_failed_writer_replays_exact_new_request_without_search,
    test_five_units_actual_requests_opening_world_queries_and_handoffs,
    test_memory_capacity_recovery_preserves_new_opening_and_receipt,
    test_single_unit_actual_requests_complete_all_planned_scenes,
)


@pytest.fixture(autouse=True)
def new_context_policy(monkeypatch):
    monkeypatch.setitem(previous.OPTIONS, "context_policy", "role-key-v3")


@pytest.fixture
def local_capacity_failure(automated, monkeypatch):
    client, control = automated
    validate = request_preparation.validate_capacity

    def capacity(text, request, spec, profile, counting):
        packet = json.loads(request.user_prompt)
        if (
            packet.get("stage_context", {}).get("unit_position", {}).get("current") == 2
            and spec.input_limit < 68000
        ):
            raise WorkflowError("最终输入超过容量，尚未发送")
        return validate(text, request, spec, profile, counting)

    monkeypatch.setattr(request_preparation, "validate_capacity", capacity)
    base, draft = stage_create(client, unit_limit=2, **{**OPTIONS, "input_limit": 58000})
    before = start(client, base, draft)
    assert before["status"] == "needs_attention", before["state"]
    assert before["next_action"] == "write:2"
    assert before["input_recovery_available"]
    assert control["calls"] == ["plan", "write:1", "memory:1"]
    return client, control, base, before


def test_actual_local_capacity_failure_after_prose_resumes_only_unused_action(
    local_capacity_failure,
    monkeypatch,
):
    client, control, base, before = local_capacity_failure
    target = f"{base}/{before['id']}"
    saved = current(before, "input_preparation_failure")["payload"]
    assert saved["not_dispatched"] and saved["action"] == "write:2"
    assert saved["candidate_sha256"] == current(before, "candidate")["sha256"]
    assert "第1次选择" in saved["model_request"]["user_prompt"]
    assert before["snapshot"]["plan_context_selection"]["policy"] == "role-key-v3"
    assert read(client, target + "/input-preview?input_limit=58000&all_roles=false")["blockers"]
    preview = read(client, target + "/input-preview?input_limit=68000&all_roles=false")
    assert not preview["blockers"], preview
    assert read(client, target)["calls"] == before["calls"]
    retrieve = KnowledgeService.retrieve

    async def forbid_retry_search(self, *args, **kwargs):
        # Only the first next action is bound to the saved failed preparation.
        if control["calls"] == ["plan", "write:1", "memory:1"]:
            raise AssertionError("Capacity recovery must reuse the saved lookup")
        return await retrieve(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeService, "retrieve", forbid_retry_search)
    reply = post(
        client,
        target + "/input-authorize",
        {
            "input_limit": 68000,
            "all_roles": False,
            "preview_sha256": preview["preview_sha256"],
            "confirmed": True,
        },
    )
    assert reply.status_code == 200, reply.text
    done = settle(client, base, before["id"])
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2", "checker"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert done["calls"][:3] == before["calls"]
    assert done["spec"] == before["spec"] and done["snapshot"] == before["snapshot"]
    assert current(before, "candidate") in done["artifacts"]
    call = next(c for c in done["calls"] if c["action"] == "write:2")
    request = read(client, target + f"/calls/{call['id']}")["request"]
    assert request["knowledge_retrieval"] == saved["knowledge_retrieval"]
    assert request["key_context_selection"]["policy"] == "role-key-v3"
    assert "key_context_selection" not in request["model_request"]["user_prompt"]


def test_capacity_preview_invalidates_after_author_edits_unwritten_suffix(local_capacity_failure):
    client, control, base, before = local_capacity_failure
    target = f"{base}/{before['id']}"
    preview = read(client, target + "/input-preview?input_limit=68000&all_roles=false")
    plan = current(before, "plan")
    changed = deepcopy(plan["payload"])
    changed["scenes"][1]["event"] += "，由作者补充新的行动条件"
    edited = client.put(
        target + "/plan",
        json={
            "plan": changed,
            "author_note": "修改未写部分",
            "expected_plan_sha256": plan["sha256"],
        },
        headers=headers(str(uuid4())),
    )
    assert edited.status_code == 200, edited.text
    response = post(
        client,
        target + "/input-authorize",
        {
            "input_limit": 68000,
            "all_roles": False,
            "preview_sha256": preview["preview_sha256"],
            "confirmed": True,
        },
    )
    assert response.status_code == 409
    assert control["calls"] == ["plan", "write:1", "memory:1"]
    assert current(read(client, target), "plan")["payload"] == changed


def test_local_capacity_failures_keep_each_saved_evidence_and_never_send(local_capacity_failure):
    client, control, base, before = local_capacity_failure
    target = f"{base}/{before['id']}"
    first = current(before, "input_preparation_failure")
    response = read(client, target + "/input-preview?input_limit=58000&all_roles=false")
    rejected = post(
        client,
        target + "/input-authorize",
        {
            "input_limit": 58000,
            "all_roles": False,
            "preview_sha256": response["preview_sha256"],
            "confirmed": True,
        },
    )
    assert rejected.status_code == 409
    assert current(read(client, target), "input_preparation_failure") == first
    assert control["calls"] == ["plan", "write:1", "memory:1"]
