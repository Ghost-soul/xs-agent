import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.content import json_text
from novel_writer.generation.craft_plans import parse_stage
from novel_writer.generation.diagnostics import revalidation_blocker
from novel_writer.generation.output_contract import KEY, binding, prepare_output
from novel_writer.generation.output_failures import (
    failure_diagnostic,
    refusal_message,
    response_parser,
)
from novel_writer.generation.plan_format import PlanQuestionFormatError, normalize_questions
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import (
    ProviderModelOption,
    ProviderProfile,
    build_provider,
)
from tests.unit.test_stage_craft import craft_fixture

REASON = {
    "kind": "missing_canonical_fact", "source": "人物档案", "why_blocked": "身份记载冲突",
}


def call(body, action="plan", **response):
    return GenerationCallRecord(
        id=uuid4(), action=action, status="local_failure", error_code="local_validation_failed",
        request={}, response={
            "text": body,
            "terminal": {"terminal_event_seen": True, "finish_reason": "stop"},
            **response,
        },
    )


def profile(mode="json_object", capabilities=frozenset()):
    return ProviderProfile(
        id="fixture", display_name="fixture", protocol="openai_chat_completions",
        base_url="https://example.test", default_model="fixture", structured_output_mode=mode,
        models=[ProviderModelOption(
            id="fixture", context_window=20000, max_output_tokens=4000,
            structured_output_modes=capabilities,
            input_price_cny_per_million=0, output_price_cny_per_million=0,
        )],
    )


def request():
    return ModelRequest(
        model="fixture", system_prompt="return JSON", user_prompt="frozen prompt",
        max_output_tokens=2000, json_schema_name="fixture", json_schema={},
        skip_structured_output=True,
    )


SCHEMA = {
    "type": "object", "properties": {"answer": {"type": "string"}},
    "required": ["answer"], "additionalProperties": False,
}


def test_single_question_compatibility_preserves_evidence_and_blocks_writing():
    spec, snapshot, plan = craft_fixture()
    plan.update(questions=["身份是什么？"], author_question_reasons=REASON,
                question_scopes="current_unit")
    original = deepcopy(plan)
    parsed = parse_stage(json_text(plan), spec, snapshot)
    assert parsed.author_question_reasons["身份是什么？"].source == "人物档案"
    assert parsed.question_scopes == {"身份是什么？": "current_unit"}
    assert plan == original


def test_explicit_question_names_allow_reordered_list_without_guessing():
    value = {"questions": ["甲？", "乙？"], "author_question_reasons": [
        {"question": "乙？", **REASON}, {"question": "甲？", **REASON},
    ]}
    assert set(normalize_questions(value)["author_question_reasons"]) == {"甲？", "乙？"}
    value["author_question_reasons"].append({"question": "甲？", **REASON})
    with pytest.raises(PlanQuestionFormatError):
        normalize_questions(value)


def test_shared_reason_for_multiple_questions_is_not_silently_assigned():
    value = {"questions": ["甲？", "乙？", "丙？"], "author_question_reasons": REASON,
             "question_scopes": {"current_unit": "later"}}
    with pytest.raises(PlanQuestionFormatError, match="一份总原因"):
        normalize_questions(value)
    failed = call(json.dumps(value))
    assert failure_diagnostic(failed)["code"] == "plan_question_format_invalid"
    batch = GenerationBatchRecord(status="needs_attention", spec={"craft_policy": "stage-craft-v1"},
                                  revision="genre-led-longform-v1", state={})
    assert "一份总原因" in revalidation_blocker(batch, failed, None)


def test_scope_keys_cannot_silently_reclassify_questions_as_nonblocking():
    with pytest.raises(PlanQuestionFormatError, match="键必须"):
        normalize_questions({"questions": ["甲？"], "author_question_reasons": {"甲？": REASON},
                             "question_scopes": {"current_unit": "later"}})


@pytest.mark.parametrize("text", [
    "我不能按这个要求写。请调整要求。", "抱歉，我无法规划这个情节。",
    "I can't help with that request.", '{"refusal":"Cannot comply"}',
])
def test_refusals_have_actionable_labels_and_no_local_revalidation(text):
    failed = call(text)
    assert failure_diagnostic(failed)["code"] == "provider_refusal"
    batch = GenerationBatchRecord(status="needs_attention", state={})
    assert "拒绝" in revalidation_blocker(batch, failed, None)


def test_refusal_detection_does_not_misread_dialogue_or_partial_streams():
    assert refusal_message(call('我不能写下那个名字。他放下笔。', action="write:1")) is None
    assert refusal_message(call('{"event":"我不能帮你，她说。"}')) is None
    unfinished = call("我不能按这个要求写。", terminal={"terminal_event_seen": False})
    assert refusal_message(unfinished) is None
    partial = call("", error_code="outcome_uncertain", terminal={"terminal_event_seen": False})
    assert failure_diagnostic(partial)["code"] == "outcome_uncertain"
    assert failure_diagnostic(call("ordinary prose", action="write:1")) is None
    assert failure_diagnostic(call("我无法提供完整判断，缺少前文。", action="checker")) is None


def test_legacy_question_contract_is_not_rejected_for_missing_new_reason_fields():
    failed = call('{"questions":["身份？"],"question_scopes":{"身份？":"current_unit"}}')
    assert failure_diagnostic(failed) is None


def test_independent_amendment_parser_uses_its_own_contract():
    failed = call('{}')
    failed.request = {"feedback_options": {"craft_policy": "stage-craft-v1"}}
    batch = GenerationBatchRecord(spec={}, snapshot={}, revision="genre-led-longform-v1")
    assert response_parser(batch, failed) == "craft-plan-format-v4"


def test_old_authorizations_and_prose_requests_remain_identical():
    old = request()
    assert prepare_output(old, profile(), "plan", {}, {}) is old
    assert prepare_output(old, profile(), "write:1", {KEY: binding()}, {}) is old
    with pytest.raises(WorkflowError, match="冻结授权"):
        prepare_output(old, profile(), "plan", {KEY: {"revision": "unknown"}}, {})
    # An old independent amendment must not inherit the newer parent wire policy.
    assert prepare_output(old, profile(), "plan", {KEY: binding()}, {KEY: None}) is old


def test_free_text_checker_keeps_existing_report_contract():
    reports = {"prompt_template_source": {"candidate": [], "formal_start": {}}}
    original = request()
    assert prepare_output(original, profile(), "checker", {KEY: binding()}, reports) is original
    assert reports["output_format"]["mode"] == "prompt_only"


@pytest.mark.parametrize("source,key", [({"chapters": []}, "chapters"), ({"body": "稿"}, "titles")])
def test_title_schema_preserves_single_and_multiple_chapter_shapes(source, key):
    reports = {"prompt_template_source": source}
    prepared = prepare_output(request(), profile(), "title", {KEY: binding()}, reports)
    assert set(prepared.json_schema["properties"]) == {key}
    assert not prepared.skip_structured_output


@pytest.mark.parametrize("mode,caps,schema,expected", [
    ("json_object", frozenset({"json_object"}), SCHEMA, "json_object"),
    ("json_schema", frozenset({"json_schema_strict"}), SCHEMA, "json_schema"),
    ("prompt_only", frozenset({"prompt_only_schema"}), SCHEMA, "prompt_only"),
    ("json_object", frozenset({"prompt_only_schema"}), SCHEMA, "prompt_only"),
    ("json_schema", frozenset({"json_schema_strict"}), {"type": "object"}, "prompt_only"),
])
def test_capability_bound_output_policy(mode, caps, schema, expected):
    reports = {"prompt_template_source": {"output_schema": schema}}
    result = prepare_output(request(), profile(mode, caps), "plan", {KEY: binding()}, reports)
    assert result.skip_structured_output == (expected == "prompt_only")
    assert reports["output_format"]["mode"] == expected
    assert '"questions":[' in result.system_prompt
    assert result.json_schema == ({} if expected == "prompt_only" else schema)


@pytest.mark.asyncio
async def test_json_object_mode_reaches_actual_http_body_once():
    sent = []

    def handle(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={
            "id": "fixture", "choices": [{"message": {"content": '{"answer":"ok"}'},
                                             "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    configured = profile()
    prepared = prepare_output(request(), configured, "checker", {KEY: binding()},
                              {"prompt_template_source": {"output_schema": SCHEMA}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await build_provider(configured, client=client).generate(prepared, "fixture")
    assert response.text == '{"answer":"ok"}'
    assert len(sent) == 1
    assert sent[0]["response_format"] == {"type": "json_object"}
