# ruff: noqa: F401 F811
"""Offline provider faults, immutable recovery and complete handoffs in PostgreSQL."""

import asyncio
import json

import httpx
import pytest

from novel_writer.generation import memory_compatibility, runtime
from novel_writer.generation import reliability_contract as contract
from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.services.provider_profiles import build_provider
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft

REAL_DISPATCH = GenerationRuntime.dispatch
REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def misplaced(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1].startswith("memory"):
            data = json.loads(response.text)
            async with self.database.session() as session:
                from novel_writer.db.models import GenerationCallRecord

                call = await session.get(GenerationCallRecord, call_id)
                data["reference_boundary"] = call.request["prompt_template_source"][
                    "reference_boundary"
                ]
            data["position"]["unresolved"] = ["后续行动仍待决定"]
            if control.get("incomplete"):
                return response.model_copy(update={"text": '```json\n{"position":{"x":"cut<|eos|>'})
            return response.model_copy(update={"text": json_text(data)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def test_complete_flow_normalizes_memory_and_freezes_structural_guidance(misplaced):
    client, control = misplaced
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert len(control["calls"]) == 11
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert draft["snapshot"][contract.KEY] == done["snapshot"][contract.KEY] == contract.binding()
    handoffs = [a for a in done["artifacts"] if a["kind"] == "handoff"]
    assert len(handoffs) == 5 and all(len(a["payload"]["format_notes"]) >= 2 for a in handoffs)
    chief = done["calls"][0]
    receipt = read(client, f"{base}/{done['id']}/calls/{chief['id']}")["request"]
    assert receipt[contract.KEY] == contract.binding()
    assert receipt["structured_delivery"]["required_example"]["scenes"][0]["character_ids"]
    preview = post(client, "/api/prompt-templates/preview", {
        "project_id": base.split("/")[3], "batch_id": done["id"], "variant": "chief",
    })
    assert preview.status_code == 200, preview.text
    assert "【结构化交付】" in preview.json()["system_prompt"]
    assert preview.json()["source_bindings"]["structured_delivery"] == contract.binding()


def test_v4_memory_failure_revalidates_locally_once_without_changing_calls(misplaced, monkeypatch):
    client, control = misplaced
    base, draft = create_craft(client)
    parser = runtime.response_parser
    with monkeypatch.context() as old:
        old.setattr(memory_compatibility, "normalize", lambda obj, boundary: (obj, []))
        old.setattr(runtime, "response_parser", lambda batch, call: "memory-evidence-v4"
                    if call.action.startswith("memory") else parser(batch, call))
        failed = start(client, base, draft)
    call = failed["calls"][-1]
    assert call["status"] == "local_failure"
    route = f"{base}/{failed['id']}"
    before = read(client, route + f"/calls/{call['id']}")
    count = len(control["calls"])
    result = post(client, route + f"/calls/{call['id']}/revalidate", {"confirmed": True})
    assert result.status_code == 200, result.text
    after = read(client, route + f"/calls/{call['id']}")
    assert before["response"] == after["response"] and before["request"] == after["request"]
    batch = read(client, route)
    assert batch["calls"][-1]["status"] == "completed"
    assert len(control["calls"]) == count
    assert batch["status"] not in {"queued", "running"}
    again = post(client, route + f"/calls/{call['id']}/revalidate", {"confirmed": True})
    assert again.status_code == 409


def test_stop_marker_with_incomplete_memory_keeps_written_prose_and_disables_local_retry(misplaced):
    client, control = misplaced
    control["incomplete"] = True
    base, draft = create_craft(client)
    failed = start(client, base, draft)
    assert len(control["calls"]) == 3
    assert current(failed, "candidate")["payload"]["body"]
    last = failed["calls"][-1]
    assert last["diagnostic"]["code"] == "plan_output_incomplete"
    assert not last["can_revalidate"]
    assert "JSON 内容未写完整" in failed["state"]["message"]


def install_transport(monkeypatch, handle):
    monkeypatch.setattr(runtime, "build_provider", build_provider)
    monkeypatch.setattr(runtime.httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(
        **kwargs, transport=httpx.MockTransport(handle),
    ))


def test_real_dispatch_uses_authorized_timeout_and_blocks_400_replay(craft, monkeypatch):
    client, control = craft
    base, draft = create_craft(client)
    requests = []

    async def handle(req):
        requests.append(req)
        assert req.extensions["timeout"]["read"] == draft["spec"]["timeout_seconds"]
        assert req.extensions["timeout"]["read"] > 90
        return httpx.Response(400, json={"error": {
            "code": 400, "message": "Output token limit must be between 1 and 32768",
        }})

    install_transport(monkeypatch, handle)
    monkeypatch.setattr(GenerationRuntime, "dispatch", REAL_DISPATCH)
    failed = start(client, base, draft)
    last = failed["calls"][-1]
    assert last["diagnostic"]["code"] == "provider_parameter_invalid"
    assert "32768" in failed["state"]["message"]
    route = f"{base}/{failed['id']}"
    preview = read(client, route + "/step-recovery-preview")
    assert preview["blockers"]
    receipt = read(client, route + f"/calls/{last['id']}")
    observation = receipt["request"]["transport_observation"]
    assert observation["received_bytes"] > 0 and observation["headers_received_at"]
    assert len(requests) == 1


def test_cancelled_real_writer_stream_saves_partial_response_and_unknown_cost(craft, monkeypatch):
    client, control = craft
    base, draft = create_craft(client)
    original = GenerationRuntime.dispatch
    calls = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"received prose"}}]}\n\n'
            await asyncio.sleep(10)

    async def handle(req):
        calls.append(req)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Stream())

    install_transport(monkeypatch, handle)

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        if control["calls"] == ["plan"]:
            profile = profile.model_copy(update={"streaming_enabled": True})
            return await asyncio.wait_for(
                REAL_DISPATCH(self, call_id, request, profile, spec, counting, api_key), 0.5,
            )
        return await original(self, call_id, request, profile, spec, counting, api_key)

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    failed = start(client, base, draft)
    assert len(calls) == 1
    assert failed["status"] == "outcome_uncertain"
    last = failed["calls"][-1]
    assert last["action"] == "write:1" and last["actual_cost_cny"] is None
    receipt = read(client, f"{base}/{failed['id']}/calls/{last['id']}")
    assert receipt["response"]["text"] == "received prose"
    assert "received prose" in receipt["response"]["raw_response"]
    candidate = current(failed, "candidate")["payload"]
    assert candidate["body"] == "received prose" and not candidate["complete"]
    assert not last["can_revalidate"]
