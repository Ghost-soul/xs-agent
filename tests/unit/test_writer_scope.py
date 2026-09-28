from copy import deepcopy

import pytest

from novel_writer.generation import output_contract as v1
from novel_writer.generation import output_contract_v2 as v2
from novel_writer.generation import output_contract_v3 as v3
from novel_writer.generation.content import digest, json_text
from novel_writer.generation.writer_observations import current_units, observe
from novel_writer.services.errors import WorkflowError
from tests.unit.test_output_reliability import SCHEMA, call, profile, request

SCALE = {
    "scale_mode": "stage-range", "min_characters": 15000, "max_characters": 20000,
    "preferred_units": 3, "authorized_unit_limit": 3, "written_characters": 0,
    "remaining_units": 1,
    "current_unit_reference": {"min_characters": 7500, "max_characters": 10000},
}


@pytest.mark.parametrize("policy", [None, v1.binding(), v2.binding()])
@pytest.mark.parametrize("action", ["plan", "write:1", "rewrite", "memory"])
def test_legacy_requests_and_reports_remain_identical(policy, action):
    snapshot = {v3.KEY: policy}
    reports = {"prompt_template_source": {"output_schema": SCHEMA, "stage_scale": SCALE}}
    old = deepcopy(reports)
    expected = v2.prepare_output(request(), profile(), action, snapshot, old)
    assert v3.prepare_output(request(), profile(), action, snapshot, reports) == expected
    assert reports == old


@pytest.mark.parametrize("template", [False, True])
def test_unit_target_is_explicit_in_actual_system_without_changing_author_task(template):
    source = {"stage_scale": deepcopy(SCALE), "output_schema": SCHEMA}
    original = request().model_copy(update={"user_prompt": json_text(source)})
    reports = {"prompt_template_source": source} if template else {}
    result = v3.prepare_output(original, profile(), "write:1", {v3.KEY: v3.binding()}, reports)
    assert "当前单元目标 7,500–10,000 字" in result.system_prompt
    assert result.system_prompt.count(v3.WRITER_SCOPE) == 1
    assert result.user_prompt == original.user_prompt
    assert result.reasoning_effort == original.reasoning_effort  # No unsupported switch forced.
    assert result.max_output_tokens == original.max_output_tokens
    assert reports["writer_scale"] == SCALE and reports["writer_scale"] is not SCALE
    assert reports[v3.KEY] == v3.binding()
    assert reports["output_format"]["revision"] == v3.REVISION


def test_chief_keeps_language_and_schema_and_gains_event_capacity_guidance():
    reports = {"prompt_template_source": {"stage_scale": SCALE, "output_schema": SCHEMA}}
    result = v3.prepare_output(request(), profile(), "plan", {v3.KEY: v3.binding()}, reports)
    assert "首选 3 个单元" in result.system_prompt
    assert result.system_prompt.count(v3.CHIEF_SCOPE) == 1
    assert result.system_prompt.count(v2.CHIEF_LANGUAGE) == 1
    assert result.system_prompt.count(v1.QUESTION_GUIDANCE) == 1
    assert result.json_schema == SCHEMA and not result.skip_structured_output


@pytest.mark.parametrize("action", ["write", "rewrite", "memory", "checker"])
def test_natural_size_and_independent_revisions_never_inherit_longform_target(action):
    reports = {"prompt_template_source": {
        "stage_scale": {**SCALE, "scale_mode": "natural"}, "output_schema": SCHEMA,
    }}
    result = v3.prepare_output(request(), profile(), action, {v3.KEY: v3.binding()}, reports)
    assert result.system_prompt == request().system_prompt
    assert "writer_scale" not in reports
    reports["prompt_template_source"]["stage_scale"] = SCALE
    if action != "write":
        result = v3.prepare_output(request(), profile(), action, {v3.KEY: v3.binding()}, reports)
        assert v3.WRITER_SCOPE not in result.system_prompt


def test_forged_binding_fails_and_explicit_legacy_override_remains_legacy():
    with pytest.raises(WorkflowError, match="冻结"):
        v3.prepare_output(request(), profile(), "plan", {
            v3.KEY: {"revision": v3.REVISION, "sha256": "changed"},
        }, {})
    assert v3.prepare_output(request(), profile(), "write", {v3.KEY: v3.binding()}, {
        v3.KEY: None,
    }) == request()


def test_observation_uses_saved_target_keeps_raw_and_reports_actual_reasoning():
    record = call("𠮷" * 999 + "。\n<|eos|>\n", "write:1", usage={"reasoning_tokens": 1300})
    record.status = "completed"
    record.request = {"prompt_template_source": {"stage_scale": SCALE}, "model_request": {}}
    before = deepcopy(record.response)
    report = observe(record)
    assert report["characters"] == 1000 and report["saved_characters"] == 1007
    assert report["percent_of_minimum"] == 13.3 and report["status"] == "below"
    assert report["reasoning_tokens"] == 1300 and report["reasoning_effort"] is None
    assert report["terminal_marker"] == "<|eos|>"
    assert record.response == before and record.status == "completed"


@pytest.mark.parametrize("source", [None, {}, [], {"stage_scale": None}])
def test_missing_source_does_not_invent_a_historical_target(source):
    record = call("文中提及 <|eos|> 的字样。", "write")
    record.request = {"model_request": {"user_prompt": json_text(source)}}
    assert observe(record)["target"] is None
    assert observe(record)["terminal_marker"] is None


def test_current_unit_display_excludes_replaced_responses_and_edited_prose():
    record = call("保存原文。", "write:1")
    record.request = {"writer_scale": SCALE}
    body = record.response["text"]
    units = [{"source_call_id": str(record.id), "ordinal": 1, "start": 0, "end": len(body),
              "body_sha256": digest(body), "complete": False}]
    assert current_units(body, units, [record])[0]["complete"] is False
    assert current_units("作者新稿。", units, [record]) == []
    assert current_units(body, [{**units[0], "source_call_id": "replaced"}], [record]) == []
