import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from novel_writer.domain.state import StoryState
from novel_writer.generation import reliability_contract as contract
from novel_writer.generation import unit_delivery as previous
from novel_writer.generation.budget import current_capacity_blocker
from novel_writer.generation.content import json_text
from novel_writer.generation.memory_compatibility import normalize
from novel_writer.generation.output_failures import failure_diagnostic, replay_blocker
from novel_writer.generation.reports import memory_result
from novel_writer.generation.runtime import ProgressStream
from novel_writer.generation.structured_json import IncompleteJSONError, parse_report
from novel_writer.providers.base import ProviderResponseError
from novel_writer.providers.transport import capture_response_body, network_timeout
from novel_writer.services.provider_profiles import build_provider
from tests.unit.test_editable_rules import bind
from tests.unit.test_novel_run_rebuild import BODY, memory
from tests.unit.test_output_reliability import call, profile, request


def prepared(mode, capabilities):
    spec, snapshot, plan = bind()
    contract.bind_snapshot(spec, snapshot)
    reports = {}
    system, task = contract.render_for(spec, snapshot, "plan", plan, reports=reports)
    provider = profile(mode, frozenset(capabilities))
    wire = contract.prepare_output(request().model_copy(update={
        "system_prompt": system, "user_prompt": task,
    }), provider, "plan", snapshot, reports)
    return provider, wire, reports


@pytest.mark.parametrize("mode,capabilities,expected", [
    ("json_schema", ["json_schema_strict", "json_object"], "json_object"),
    ("json_schema", ["json_schema_strict"], "prompt_only"),
    ("json_object", ["json_object"], "json_object"),
    ("json_object", [], "prompt_only"),
    ("prompt_only", ["json_object"], "prompt_only"),
])
def test_fallback_uses_only_recorded_capabilities_and_preserves_schema(
    mode, capabilities, expected,
):
    provider, wire, reports = prepared(mode, capabilities)
    assert reports["output_format"]["mode"] == expected
    assert wire.skip_structured_output == (expected == "prompt_only")
    assert reports["structured_delivery"]["required_example"]["scenes"][0]["character_ids"]
    if expected != "prompt_only":
        assert wire.json_schema == reports["prompt_template_source"]["output_schema"]
    assert contract.dispatch_profile(provider, reports).structured_output_mode == expected


def test_published_requests_unchanged_and_old_revision_does_not_inherit_new_binding():
    spec, snapshot, plan = bind()
    previous.bind_snapshot(spec, snapshot)
    reports = {}
    system, task = previous.render_for(spec, snapshot, "plan", plan, reports=reports)
    original = request().model_copy(update={"system_prompt": system, "user_prompt": task})
    old = previous.prepare_output(original, profile(), "plan", snapshot, deepcopy(reports))
    assert contract.prepare_output(original, profile(), "plan", snapshot, reports) == old
    assert contract.contract_for(spec, snapshot) == previous.contract_for(spec, snapshot)
    snapshot[contract.KEY] = contract.binding()
    reports[contract.KEY] = None
    assert contract.prepare_output(original, profile(), "plan", snapshot, reports) == old


@pytest.mark.asyncio
async def test_selected_json_mode_reaches_wire_once():
    provider, wire, reports = prepared("json_schema", ["json_schema_strict", "json_object"])
    requests = []

    async def handle(req):
        requests.append(json.loads(req.content))
        assert req.extensions["timeout"]["read"] == 600
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle),
                                timeout=network_timeout(600)) as client:
        adapter = build_provider(contract.dispatch_profile(provider, reports), client=client)
        await adapter.generate(wire, "fixture")
    assert len(requests) == 1
    assert requests[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("wrapper", ["{}", "```JSON\r\n{}\r\n```", "```json\n{}"])
def test_complete_objects_can_have_wrappers(wrapper):
    assert parse_report(wrapper) == {}


@pytest.mark.parametrize("raw", ['```json\n{"position":{"x":"unfinished<|eos|>', '{"a":1'])
def test_incomplete_content_is_not_repaired(raw):
    with pytest.raises(IncompleteJSONError, match="未写完整"):
        parse_report(raw)
    assert failure_diagnostic(call(raw, "memory:1"))["code"] == "plan_output_incomplete"


def test_duplicate_fields_are_not_silently_overwritten():
    with pytest.raises(ValueError, match="重复字段"):
        parse_report('{"position":{"x":1,"x":2}}')


def test_memory_moves_pending_notes_and_verified_echo_without_changing_facts():
    raw = memory()
    boundary = {"opening": "frozen", "source_version_id": "same-version"}
    raw["reference_boundary"] = boundary
    raw["position"]["unresolved"] = ["下一步尚未确定"]
    before = deepcopy(raw)
    result = memory_result(json_text(raw), BODY, StoryState(), 1, reference_boundary=boundary)
    normal = memory_result(json_text(memory()), BODY, StoryState(), 1)
    assert result["factual_changes"] == normal["factual_changes"]
    assert result["position"] == normal["position"]
    assert len(result["format_notes"]) == 2
    assert raw == before


@pytest.mark.parametrize("boundary", [None, {"opening": "different"}])
def test_memory_rejects_unknown_or_conflicting_source(boundary):
    with pytest.raises(ValueError, match="来源说明"):
        normalize({"reference_boundary": {"opening": "frozen"}}, boundary)


def test_memory_rejects_ambiguous_pending_items_and_preserves_unknown_fact_fields():
    with pytest.raises(ValueError, match="不能猜测"):
        normalize({"position": {"unresolved": {"fact": "x"}}}, None)
    raw = memory()
    raw["position"]["new_fact"] = "unknown"
    with pytest.raises(ValueError, match="未知字段"):
        memory_result(json_text(raw), BODY, StoryState(), 1)


def test_parameter_failure_blocks_identical_paid_replay_and_timeout_remains_unknown():
    failed = call("", raw_response=json.dumps({"error": {
        "code": 400, "message": "Output token limit must be between 1 and 32768",
    }}), terminal={"terminal_status": "http_400", "terminal_event_seen": True})
    assert "32768" in replay_blocker(failed)
    failed.response = None
    failed.error_code = "ReadTimeout"
    failed.status = "outcome_uncertain"
    assert failure_diagnostic(failed)["code"] == "transport_timeout"
    assert replay_blocker(failed) is None


def test_updated_endpoint_capacity_blocks_old_requests_without_mutation_or_cross_endpoint_rules():
    frozen = profile()
    current = frozen.model_copy(deep=True)
    current.models[0].max_output_tokens = 3000
    old = request().model_copy(update={"max_output_tokens": 4000})
    assert "3,000" in current_capacity_blocker(old, frozen, current)
    assert old.max_output_tokens == 4000
    assert current_capacity_blocker(old, frozen, frozen) is None
    current.base_url = "https://different.example.test"
    assert current_capacity_blocker(old, frozen, current) is None


class InterruptedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"saved fragment"}}]}\n\n'
        await asyncio.sleep(10)


@pytest.mark.asyncio
async def test_whole_call_cancellation_keeps_raw_stream_and_partial_text_without_resend():
    count = 0

    async def handle(req):
        nonlocal count
        count += 1
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              stream=InterruptedStream())

    p = profile().model_copy(update={"streaming_enabled": True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = build_provider(p, client=client)
        with pytest.raises(ProviderResponseError) as failure:
            await asyncio.wait_for(adapter.generate(request(), "fixture"), timeout=0.02)
    assert count == 1
    assert failure.value.code == "outcome_uncertain"
    assert "saved fragment" in failure.value.raw_response
    assert failure.value.extracted_content == "saved fragment"
    assert failure.value.usage is None
    assert failure.value.terminal.incomplete_reason == "CancelledError"
    saved = call("", terminal=failure.value.terminal.model_dump())
    saved.status = "outcome_uncertain"
    assert failure_diagnostic(saved)["code"] == "transport_cancelled"


@pytest.mark.asyncio
async def test_cancellation_during_progress_save_cannot_drop_received_chunk():
    async def progress(count):
        await asyncio.sleep(10)

    from novel_writer.providers.transport import ProviderTransportReadError

    response = httpx.Response(200, stream=ProgressStream(InterruptedStream(), progress))
    with pytest.raises(ProviderTransportReadError) as failure:
        await asyncio.wait_for(capture_response_body(response), timeout=0.02)
    assert "saved fragment" in failure.value.transport.text
