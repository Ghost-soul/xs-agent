from copy import deepcopy

import pytest

from novel_writer.generation import output_contract as v1
from novel_writer.generation import output_contract_v2 as v2
from novel_writer.generation.content import json_text
from novel_writer.generation.craft_plans import parse_stage
from novel_writer.generation.output_failures import failure_diagnostic
from novel_writer.generation.plan_format import (
    PlanIncompleteError,
    PlanQuestionFormatError,
    normalize_optional_text,
    normalize_questions,
    parse_plan_object,
)
from tests.unit.test_output_reliability import REASON, SCHEMA, call, profile, request
from tests.unit.test_stage_craft import craft_fixture


def test_only_default_empty_text_can_accept_null():
    spec, snapshot, plan = craft_fixture()
    plan.update(future_proposal=None, world_context=None, macro_progression=None)
    plan["scenes"][0]["development"] = {"onstage_process": None, "local_freedom": None}
    original = deepcopy(plan)
    result = parse_stage(json_text(plan), spec, snapshot)
    assert result.future_proposal == result.world_context == result.macro_progression == ""
    assert result.scenes[0].development.onstage_process == ""
    assert plan == original
    plan["scenes"][0]["event"] = None
    with pytest.raises(ValueError, match="event"):
        parse_stage(json_text(plan), spec, snapshot)


def test_nonempty_invalid_values_are_not_discarded():
    for value in (12, [], {}, False):
        assert normalize_optional_text({"future_proposal": value}) == {"future_proposal": value}
    assert normalize_optional_text({"questions": None, "scenes": None}) == {
        "questions": None, "scenes": None,
    }


def test_complete_question_prefix_maps_bijectively_with_explanation_preserved():
    questions = ["身份是什么？档案甲和乙不一致。", "地点在哪里？地名记载有冲突。"]
    value = {
        "questions": questions,
        "author_question_reasons": {"地点在哪里？": REASON, "身份是什么？": REASON},
        "question_scopes": {"身份是什么？": "current_unit", "地点在哪里？": "later"},
    }
    old = deepcopy(value)
    result = normalize_questions(value)
    assert result["questions"] == questions
    assert result["question_scopes"] == {questions[0]: "current_unit", questions[1]: "later"}
    assert result["author_question_reasons"] == {q: REASON for q in questions}
    assert value == old


@pytest.mark.parametrize("questions,reasons", [
    (["身份是什么？说明甲", "身份是什么？说明乙"], {"身份是什么？": REASON}),
    (["身份是什么？说明甲"], {"身份是什么？": REASON, "身份是什么？说明甲": REASON}),
    (["身份是什么？说明甲"], {"身份是什么": REASON}),
    (["身份是什么？说明甲"], {"请确认身份？": REASON}),
])
def test_ambiguous_duplicate_and_paraphrased_question_keys_still_fail(questions, reasons):
    with pytest.raises(PlanQuestionFormatError):
        normalize_questions({"questions": questions, "author_question_reasons": reasons})


@pytest.mark.parametrize("raw", ['{"bridge":"unfinished', '{"bridge":"unfinished<|eos|>'])
def test_stopped_transport_does_not_make_unterminated_json_complete(raw):
    with pytest.raises(PlanIncompleteError):
        parse_plan_object(raw)
    assert failure_diagnostic(call(raw))["code"] == "plan_output_incomplete"


def test_old_v1_requests_remain_exactly_the_same():
    reports = {"prompt_template_source": {"output_schema": SCHEMA}}
    old = v1.prepare_output(request(), profile(), "plan", {v1.KEY: v1.binding()}, deepcopy(reports))
    result = v2.prepare_output(request(), profile(), "plan", {v1.KEY: v1.binding()}, reports)
    assert result == old
    assert v2.CHIEF_LANGUAGE not in result.system_prompt
    assert v2.prepare_output(request(), profile(), "plan", {}, {}) == request()
    assert v2.prepare_output(request(), profile(), "plan", {v1.KEY: v2.binding()}, {
        v1.KEY: None,
    }) == request()


def test_new_language_guidance_preserves_machine_fields_and_existing_output_mode():
    reports = {"prompt_template_source": {"output_schema": SCHEMA}}
    result = v2.prepare_output(request(), profile(), "plan", {v1.KEY: v2.binding()}, reports)
    assert result.system_prompt.count(v2.CHIEF_LANGUAGE) == 1
    assert "简体中文" in result.system_prompt and "枚举值和给定 ID" in result.system_prompt
    assert result.user_prompt == request().user_prompt
    assert result.json_schema == SCHEMA and not result.skip_structured_output
    assert reports[v1.KEY] == v2.binding()
    assert reports["output_format"]["revision"] == v2.REVISION
    prose = v2.prepare_output(request(), profile(), "write:1", {v1.KEY: v2.binding()}, {})
    assert prose == request()


def test_repairable_saved_format_gets_local_only_diagnostic():
    _, _, plan = craft_fixture()
    plan["future_proposal"] = None
    assert failure_diagnostic(call(json_text(plan)))["code"] == "plan_format_compatible"
