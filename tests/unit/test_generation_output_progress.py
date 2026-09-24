from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.budget import request_for
from novel_writer.generation.diagnostics import output_diagnostic, plan_retry_preview
from novel_writer.generation.novel import model_for
from novel_writer.generation.schemas import (
    FrozenGenerationSpec,
    GenerationSpec,
    NovelRunSpec,
    RoleModel,
)
from novel_writer.services.errors import WorkflowError
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_genre_generation import spec


def failed_plan():
    call = GenerationCallRecord(
        action="plan",
        status="local_failure",
        request={"model_request": {"max_output_tokens": 6000}},
        response={
            "text": "",
            "error_code": "token_limit_exceeded",
            "terminal": {"terminal_event_seen": True, "finish_reason": "length"},
            "usage": {"output_tokens": 6000, "content_tokens": 0, "reasoning_tokens": 6000},
        },
    )
    batch = GenerationBatchRecord(
        id=uuid4(),
        status="needs_attention",
        spec={"workflow": "novel-run-v1", "chief_model": "test", "chief_output_limit": 6000},
        snapshot={"profile": {"models": [{"id": "test", "max_output_tokens": 64000}]}},
    )
    return batch, call


def test_new_output_defaults_are_100k_and_explicit_history_is_preserved():
    payload = spec().model_dump(mode="json")
    payload.pop("chief_output_limit")
    payload.pop("auxiliary_output_limit")
    payload.pop("writer_output_limit")
    payload["workflow"] = "novel-run-v1"
    old = FrozenGenerationSpec.model_validate(payload)
    new = NovelRunSpec.model_validate(payload)
    assert model_for(old, "plan")[1] == model_for(old, "checker")[1] == 6000
    for action in ("plan", "chief:1", "write:1", "memory:1", "checker", "reader", "editor"):
        assert model_for(new, action)[1] == 100000
    assert RoleModel(model="test").output_limit == 100000
    saved = spec(workflow="novel-run-v1").model_dump(mode="json")
    assert NovelRunSpec.model_validate(saved).model_dump(mode="json") == saved
    explicit = NovelRunSpec.model_validate({**payload, "chief_output_limit": 32000})
    assert model_for(explicit, "plan")[1] == 32000
    with pytest.raises(WorkflowError, match="超过模型能力"):
        request_for(explicit, profile(), "plan", "", "")


def test_saved_reasoning_exhaustion_is_explained_without_modifying_evidence():
    batch, call = failed_plan()
    original = deepcopy(call.response)
    diagnostic = output_diagnostic(call)
    assert diagnostic is not None
    assert diagnostic["visible_characters"] == 0
    assert "推理用量 6000" in diagnostic["message"]
    assert "本地重验无法" in diagnostic["message"]
    assert plan_retry_preview(batch, [call], {"compilation"}) == {
        "reason": "output_limit",
        "input_limit": 200000,
        "chief_output_limit": 100000,
        "writer_output_limit": 100000,
        "auxiliary_output_limit": 100000,
        "previous_output_limit": 6000,
        "requires_new_cost_confirmation": True,
    }
    assert call.response == original and batch.spec["chief_output_limit"] == 6000


@pytest.mark.parametrize(
    "condition", ["unknown", "plan", "candidate", "units", "more_calls", "maximum", "running"]
)
def test_no_quick_restart_for_unknown_or_existing_work(condition):
    batch, call = failed_plan()
    kinds = set()
    calls = [call]
    if condition == "unknown":
        call.status = "outcome_uncertain"
        call.response["terminal"]["terminal_event_seen"] = False
    elif condition in {"plan", "candidate", "units"}:
        kinds.add(condition)
    elif condition == "more_calls":
        calls.append(call)
    elif condition == "maximum":
        batch.snapshot["profile"]["models"][0]["max_output_tokens"] = 6000
    else:
        batch.status = condition
    assert plan_retry_preview(batch, calls, kinds) is None


def test_visible_partial_output_is_not_described_as_empty_or_complete():
    _, call = failed_plan()
    call.response["text"] = "已开始但未结束的方案"
    diagnostic = output_diagnostic(call)
    assert diagnostic and diagnostic["visible_characters"] > 0
    assert "不能视为该步骤完成" in diagnostic["message"]


@pytest.mark.parametrize(
    "field", ["input_limit", "chief_output_limit", "auxiliary_output_limit", "writer_output_limit"]
)
def test_stage_token_ceilings_accept_their_limit_and_reject_more(field):
    limit = 200000 if field == "input_limit" else 100000
    payload = spec().model_dump(mode="json")
    for schema in (GenerationSpec, NovelRunSpec):
        assert getattr(schema.model_validate({**payload, field: limit}), field) == limit
        with pytest.raises(ValidationError):
            schema.model_validate({**payload, field: limit + 1})


def test_independent_roles_allow_100000_but_do_not_bypass_provider_capacity():
    role = RoleModel(model="test", output_limit=100000)
    with pytest.raises(ValidationError):
        RoleModel(model="test", output_limit=100001)
    config = spec(workflow="novel-run-v1", roles={"reader": role})
    with pytest.raises(WorkflowError, match="超过模型能力"):
        request_for(config, profile(), "reader", "", "")
    capable = profile()
    capable.models[0].max_output_tokens = 100000
    capable.models[0].context_window = 300000
    assert request_for(config, capable, "reader", "system", "user").max_output_tokens == 100000


def test_larger_chief_suggestion_stops_at_application_or_model_ceiling():
    batch, call = failed_plan()
    batch.spec["chief_output_limit"] = 64000
    batch.snapshot["profile"]["models"][0]["max_output_tokens"] = 200000
    advice = plan_retry_preview(batch, [call], set())
    assert advice and advice["chief_output_limit"] == 100000
    batch.snapshot["profile"]["models"][0]["max_output_tokens"] = 80000
    advice = plan_retry_preview(batch, [call], set())
    assert advice and advice["chief_output_limit"] == 100000
