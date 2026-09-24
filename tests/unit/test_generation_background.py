import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_writer.generation import background
from novel_writer.generation.content import json_text
from novel_writer.generation.diagnostics import plan_distribution_diagnostic, plan_retry_preview
from novel_writer.generation.schemas import BackgroundPlan, NovelRunSpec, PlanEdit
from tests.unit.test_generation_casting import roster, snapshot
from tests.unit.test_generation_creation import creative
from tests.unit.test_genre_generation import plan


def config(**changes):
    return creative(**{"writing_policy": "background-v1", "feedback_policy": "logic-v1", **changes})


def test_historical_contracts_and_new_default():
    saved = json.loads(Path("tests/fixtures/generation-pre-background-contracts.json").read_text())
    for key, sha in saved.items():
        stage, feedback, writing = key.split("|")
        s = creative(
            stage_mode=stage,
            unit_limit=1 if stage == "single-unit-v1" else 2,
            feedback_policy=feedback,
            writing_policy=writing,
        )
        assert background.contract_for(s) == sha
    payload = config().model_dump()
    payload.pop("writing_policy")
    assert NovelRunSpec.model_validate(payload).writing_policy == "guided-v1"


@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
@pytest.mark.parametrize("action", ["plan", "write", "rewrite"])
def test_cards_only_reach_chief_and_writer_keeps_voices_world_and_effective_plan(stage, action):
    spec = config(stage_mode=stage, unit_limit=1 if stage == "single-unit-v1" else 2)
    snap = snapshot(spec, roster())
    snap["context"]["world_rules"] = [{"rule": "渡口夜间闭门"}]
    snap["context"]["reference_style"] = {"positive_contract": "近距离叙事"}
    original = plan([c["id"] for c in snap["context"]["characters"]][:2])
    p = BackgroundPlan.model_validate(original).model_dump(mode="json")
    p["world_context"] = "夜间渡口关闭，来自正式规则"
    before = deepcopy(snap)
    if stage == "longform-v1" and action == "write":
        action = "write:1"
    system, raw = background.render_for(spec, snap, action, p, "已写正文")
    data = json.loads(raw)
    assert data["formal_reference"]["characters"][0]["speech_style"] == "林青完整声音档案"
    assert data["formal_reference"]["reference_style"]["positive_contract"] == "近距离叙事"
    assert data["formal_reference"]["world_rules"][0]["rule"] == "渡口夜间闭门"
    assert "至少70" not in system and "完整主导题材卡" not in system
    if action == "plan":
        assert data["background_cards"] == snap["cards"]
        assert "focus_percent" not in json_text(data["output_schema"])
        assert "卡内的篇幅配额" in system
    else:
        assert (
            "完整题材卡正文" not in raw and "background_cards" not in data and "cards" not in data
        )
        assert data["effective_plan"] == p
        assert data["world_context"] == p["world_context"]
        assert "focus_percent" not in raw
    assert snap == before


def test_six_per_unit_percentages_do_not_block_plot_or_modify_source():
    spec = config(unit_limit=6)
    snap = snapshot(spec, roster())
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    p["scenes"] = [
        dict(p["scenes"][0], focus_percent=35, transition_percent=15, other_percent=50)
        for _ in range(6)
    ]
    before = deepcopy(p)
    result = background.parse_stage(json_text(p), spec, snap)
    assert len(result.scenes) == 6 and result.scenes[0].event == p["scenes"][0]["event"]
    assert "focus_percent" not in result.model_dump_json()
    assert p == before
    assert PlanEdit(plan=p, author_note="保留事件，去掉旧配额").plan.scenes
    with pytest.raises(ValueError, match="份额"):
        background.parse_stage(
            json_text(p),
            spec.model_copy(
                update={"writing_policy": "creative-v1", "automation_policy": "legacy-v1"}
            ),
            snap,
        )


def test_real_continuity_boundaries_still_apply():
    spec = config()
    snap = snapshot(spec, roster())
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    p["scenes"][0]["character_ids"] = ["invented-id"]
    with pytest.raises(ValueError, match="人物越界"):
        background.parse_stage(json_text(p), spec, snap)
    with pytest.raises(ValueError, match="作者问题"):
        BackgroundPlan.model_validate({**p, "questions": ["是否继续？"]})
    with pytest.raises(ValueError):
        BackgroundPlan.model_validate({**p, "scenes": [None]})


def test_saved_quota_failure_has_precise_read_only_repreview_guidance():
    payload = plan(["a", "b"])
    payload["scenes"] = [
        dict(payload["scenes"][0], focus_percent=55, transition_percent=15, other_percent=30)
        for _ in range(6)
    ]
    call = SimpleNamespace(
        action="plan",
        status="local_failure",
        error_code="local_validation_failed",
        request={"feedback_options": {"writing_policy": "creative-v1"}},
        response={
            "text": json_text(payload),
            "terminal": {"finish_reason": "stop", "terminal_event_seen": True},
        },
    )
    batch = SimpleNamespace(status="needs_attention", spec=config().model_dump(), snapshot={})
    assert plan_distribution_diagnostic(call)["total_percent"] == 600
    assert plan_retry_preview(batch, [call], set())["reason"] == "obsolete_genre_quota"
    assert plan_retry_preview(batch, [call], {"candidate"}) is None
    call.request["feedback_options"]["writing_policy"] = "background-v1"
    assert plan_distribution_diagnostic(call) is None
