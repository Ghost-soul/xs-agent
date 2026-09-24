import json
from copy import deepcopy
from pathlib import Path

import pytest

from novel_writer.generation import creation, logic
from novel_writer.generation.content import paragraphs
from novel_writer.generation.novel import slots_for
from novel_writer.generation.schemas import AmendmentRequest, FrozenGenerationSpec, NovelRunSpec
from tests.unit.test_generation_casting import roster, snapshot
from tests.unit.test_generation_creation import creative
from tests.unit.test_genre_generation import plan


def test_all_eight_preexisting_contracts_remain_identical():
    saved = json.loads(Path("tests/fixtures/generation-pre-logic-contracts.json").read_text())
    for key, sha in saved.items():
        stage, feedback, writing = key.split("|")
        spec = creative(
            stage_mode=stage,
            unit_limit=1 if stage == "single-unit-v1" else 2,
            feedback_policy=feedback,
            writing_policy=writing,
        )
        assert logic.contract_for(spec) == sha


@pytest.mark.parametrize(
    "action", ["plan", "chief:1", "write:1", "write:2", "rewrite", "memory:1", "reader"]
)
def test_logic_preserves_creative_requests_and_memory_reader_boundaries(action):
    old = creative()
    new = old.model_copy(update={"feedback_policy": "logic-v1"})
    snap = snapshot(old, roster())
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    assert logic.render_for(new, snap, action, p, "正文") == creation.render_for(
        old, snap, action, p, "正文"
    )
    assert logic.contract_for(new) != creation.contract_for(old)


@pytest.mark.parametrize("action", ["checker", "checker_amend", "checker_edit"])
@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
def test_review_uses_opening_facts_and_prose_without_future_or_end_state(action, stage):
    spec = creative(
        feedback_policy="logic-v1",
        stage_mode=stage,
        unit_limit=1 if stage == "single-unit-v1" else 2,
    )
    snap = snapshot(spec, roster())
    snap["context"].update(
        {
            "narrative_position": {"current_location": "渡口"},
            "reference_style": {"instruction": "STYLE_SENTINEL"},
            "future_material_not_obligations": {"plot_threads": ["FUTURE_SENTINEL"]},
            "related_state": {
                "timeline_constraints": ["次日才能抵达"],
                "reader_promises": ["PROMISE_SENTINEL"],
            },
        }
    )
    before = deepcopy(snap)
    body = "她从渡口出发。\n\n次日抵达城门。"
    system, raw = logic.render_for(
        spec,
        snap,
        action,
        {"chapter_goal": "PLAN_SENTINEL"},
        body,
        reports={
            "working_context": {"narrative_position": {"current_location": "END_STATE_SENTINEL"}}
        },
    )
    data = json.loads(raw)
    assert data["formal_start"]["narrative_position"]["current_location"] == "渡口"
    assert data["formal_start"]["related_state"] == {"timeline_constraints": ["次日才能抵达"]}
    assert data["candidate"] == [{"id": p["id"], "text": p["text"]} for p in paragraphs(body)]
    assert "SENTINEL" not in raw and "完整题材卡正文" not in raw
    assert "没有写出每一步不等于因果不成立" in system
    assert snap == before


@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
def test_checker_cannot_trigger_edit_or_extra_reviews(stage):
    spec = creative(
        feedback_policy="logic-v1",
        enable_editor=True,
        stage_mode=stage,
        unit_limit=1 if stage == "single-unit-v1" else 2,
    )
    body = "她推开门。"
    raw = json.dumps(
        {
            "issues": [
                {
                    "observation": "重复用词",
                    "paragraph_ids": [paragraphs(body)[0]["id"]],
                    "severity": "warning",
                    "local_edit": True,
                }
            ]
        }
    )
    result = logic.checker_feedback(raw, body, spec)
    assert not result["blocking"] and not result["issues"][0]["local_edit"]
    assert result["raw_feedback"] == raw
    slots = slots_for(spec)
    assert slots[-1] == "checker" and slots.count("checker") == 1
    assert not {"editor", "memory_edit", "checker_edit", "reader_early", "chief:1", "reader"} & set(
        slots
    )
    assert slots_for(spec.model_copy(update={"enable_checker": False}))[-1].startswith("memory")


def test_new_defaults_leave_frozen_specs_and_token_limits_intact():
    payload = creative().model_dump()
    payload.pop("feedback_policy")
    spec = NovelRunSpec.model_validate(payload)
    assert spec.feedback_policy == "logic-v1" and not spec.enable_reader
    assert FrozenGenerationSpec.model_validate(payload).feedback_policy == "legacy-v1"
    amendment = AmendmentRequest(
        candidate_sha256="a" * 64, mode="verify", instruction="更新事实", max_cost_cny="10"
    )
    assert amendment.feedback_policy == "logic-v1" and not amendment.enable_reader
    assert amendment.input_limit == 200000 and amendment.output_limit == 100000
