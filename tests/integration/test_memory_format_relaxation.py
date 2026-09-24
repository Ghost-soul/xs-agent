# ruff: noqa: F401, F811
"""Format variants must work on first receipt and in a bounded local revalidation."""

import json

import pytest

from novel_writer.generation import diagnostics, reports, runtime
from novel_writer.generation.novel import parser_for, role_for
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_memory_evidence_repair import (
    draft_for,
    repaired,
)
from tests.integration.test_memory_evidence_repair import (
    test_long_references_and_scene_dependency_work_in_creation_and_amendment as verify_amendment,
)
from tests.integration.test_novel_run_rebuild import current, novel_generation


@pytest.fixture
def numeric_memory(repaired, monkeypatch):
    client, control = repaired
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1].startswith("memory"):
            data = json.loads(response.text)
            data["position_paragraph_ids_note"] = None
            data["position_paragraph_ids"] = [
                i.rsplit(":", 1)[1] if not i.startswith("foreign:") else i
                for i in data["position_paragraph_ids"]
            ]
            for change in data["changes"]:
                change["paragraph_ids"] = [int(i.rsplit(":", 1)[1])
                                           for i in change["paragraph_ids"]]
                change["notes"] = "只作提取说明"
            response = response.model_copy(update={"text": json.dumps(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def test_variants_complete_normal_creation_and_independent_amendment(numeric_memory):
    verify_amendment(numeric_memory)


@pytest.mark.parametrize("foreign", [False, True])
def test_failed_v3_response_gets_one_local_attempt_without_paid_retry(
    numeric_memory, monkeypatch, foreign
):
    client, control = numeric_memory
    control["foreign_position"] = foreign
    base, draft = draft_for(client, control)

    def old_parser(revision, action=None):
        if action and role_for(action) == "memory":
            return "memory-evidence-v3"
        return parser_for(revision, action)

    with monkeypatch.context() as old:
        old.setattr(reports, "normalize_memory_format", lambda data, body: (data, []))
        old.setattr(runtime, "parser_for", old_parser)
        old.setattr(diagnostics, "parser_for", old_parser)
        failed = start(client, base, draft)
    call = next(c for c in failed["calls"] if c["action"].startswith("memory"))
    assert call["status"] == "local_failure"
    target = f"{base}/{failed['id']}"
    route = f"{target}/calls/{call['id']}"
    before = read(client, route)
    count = len(control["calls"])
    response = post(client, route + "/revalidate", {"confirmed": True})
    assert response.status_code == 200 and response.json()["provider_requests"] == "0"
    after = read(client, target)
    saved = read(client, route)
    assert saved["request"] == before["request"] and saved["response"] == before["response"]
    assert len(control["calls"]) == count
    assert current(after, "candidate") == current(failed, "candidate")
    assert after["spec"] == failed["spec"] and after["snapshot"] == failed["snapshot"]
    compiled = next(c for c in after["calls"] if c["id"] == call["id"])
    assert compiled["actual_cost_cny"] == call["actual_cost_cny"]
    assert compiled["status"] == ("local_failure" if foreign else "completed")
    assert after["status"] not in {"queued", "running"}
    assert f"{call['id']}:memory-evidence-v3" in after["state"]["compiled"]
    assert f"{call['id']}:memory-evidence-v4" in after["state"]["compiled"]
    assert post(client, route + "/revalidate", {"confirmed": True}).status_code == 409
