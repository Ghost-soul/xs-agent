# ruff: noqa: F401 F811
"""Local syntax recovery at the real compilation boundary, with a stub provider."""

import json

import pytest

from novel_writer.generation import chief_json, runtime
from novel_writer.generation.chief_json import ParsedChiefJSON
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft
from tests.unit.test_chief_json import misplaced_braces


@pytest.fixture
def broken_syntax(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            plan = json.loads(response.text)
            body = misplaced_braces(plan)
            control["broken_response"] = body
            response = response.model_copy(update={"text": body, "raw_response": body})
            if control.get("unfinished_chief"):
                response = response.model_copy(update={
                    "terminal": response.terminal.model_copy(update={"finish_reason": "length"}),
                })
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def test_new_complete_response_recovers_before_strict_longform_parse_and_runs_once(broken_syntax):
    client, control = broken_syntax
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]["message"]
    assert done["state"]["units_finished"]
    assert control["calls"].count("plan") == 1
    first = done["calls"][0]
    call = read(client, f"{base}/{done['id']}/calls/{first['id']}")
    assert call["response"]["text"] == control["broken_response"]
    assert call["response"]["raw_response"] == control["broken_response"]
    receipts = [a for a in done["artifacts"] if a["kind"] == "compilation"
                and a["payload"]["call_id"] == first["id"]]
    assert len(receipts) == 1
    assert len(receipts[0]["payload"]["format_compatibility"]["edits"]) == 4
    assert len(current(done, "plan")["payload"]["scenes"]) == 5


def test_old_v3_failure_revalidates_once_without_replay_or_overwriting_evidence(
    broken_syntax, monkeypatch,
):
    client, control = broken_syntax
    upgraded = chief_json.parse_chief_json
    revision = runtime.response_parser
    # Simulate the published strict parser, retaining its original compilation evidence.
    monkeypatch.setattr(
        chief_json, "parse_chief_json", lambda raw, _: ParsedChiefJSON(json.loads(raw))
    )
    monkeypatch.setattr(runtime, "response_parser", lambda *_: "craft-plan-format-v3")
    base, draft = create_craft(client, pause_after_plan=True)
    failed = start(client, base, draft)
    assert failed["calls"][0]["status"] == "local_failure"
    route = f"{base}/{failed['id']}/calls/{failed['calls'][0]['id']}"
    before = read(client, route)
    old_receipt = current(failed, "compilation")
    monkeypatch.setattr(chief_json, "parse_chief_json", upgraded)
    monkeypatch.setattr(runtime, "response_parser", revision)
    ready = read(client, f"{base}/{failed['id']}")
    assert ready["calls"][0]["can_revalidate"]
    assert ready["calls"][0]["diagnostic"]["code"] == "plan_format_compatible", ready["state"]
    response = post(client, route + "/revalidate", {"confirmed": True})
    assert response.status_code == 200, response.text
    assert response.json()["provider_requests"] == "0"
    after = read(client, route)
    assert after["request"] == before["request"]
    assert after["response"] == before["response"]
    done = read(client, f"{base}/{failed['id']}")
    assert done["status"] == "awaiting_plan"
    assert done["calls"][0]["status"] == "completed"
    assert done["calls"][0]["actual_cost_cny"] == failed["calls"][0]["actual_cost_cny"]
    assert old_receipt in done["artifacts"]
    assert current(done, "compilation")["payload"]["format_compatibility"]
    assert post(client, route + "/revalidate", {"confirmed": True}).status_code == 409
    assert control["calls"] == ["plan"]


def test_truncated_response_is_not_repaired_even_if_its_syntax_can_be_recovered(broken_syntax):
    client, control = broken_syntax
    control["unfinished_chief"] = True
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert done["calls"][0]["status"] == "local_failure"
    assert not done["calls"][0]["can_revalidate"]
    assert not any(a["kind"] == "plan" for a in done["artifacts"])
    assert "format_compatibility" not in current(done, "compilation")["payload"]
    assert control["calls"] == ["plan"]


def test_repaired_three_unit_plan_keeps_scale_discrepancy_separate_from_format(broken_syntax):
    client, control = broken_syntax
    control["plan_count"] = 3
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert done["calls"][0]["status"] == "completed", done["state"]["message"]
    assert done["status"] == "awaiting_plan"
    assert done["state"]["plan_scope_discrepancy"]["planned"] == 3
    assert len(current(done, "plan")["payload"]["scenes"]) == 3
    assert control["calls"] == ["plan"]
