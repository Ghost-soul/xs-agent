import json
from copy import deepcopy

import pytest

from novel_writer.generation import plan_capacity, unit_scope
from novel_writer.generation.amendments import with_limits
from novel_writer.generation.content import digest
from novel_writer.generation.schemas import AmendmentRequest, FrozenGenerationSpec, NovelRunSpec
from novel_writer.services.errors import WorkflowError
from tests.unit.test_narrative_prompts import fixture


@pytest.mark.parametrize("policy", ["exact-v1", "bounded-v1"])
@pytest.mark.parametrize(
    "action",
    ["plan", "write:1", "memory:1", "chief:1", "checker", "reader", "rewrite", "amend", "title"],
)
def test_historical_contracts_and_requests_are_unchanged(policy, action):
    spec, snapshot, plan = fixture(plan_policy=policy)
    assert unit_scope.contract_for(spec) == plan_capacity.contract_for(spec)
    assert unit_scope.amendment_contract(spec) == plan_capacity.amendment_contract(spec)
    args = spec, snapshot, action, plan, "已写事实"
    assert unit_scope.render_for(*args) == plan_capacity.render_for(*args)


@pytest.mark.parametrize(
    "action",
    ["plan", "write:1", "memory:1", "chief:1", "checker", "reader", "rewrite", "amend", "title"],
)
def test_new_requests_have_no_length_targets_and_keep_sources(action):
    old, snapshot, plan = fixture(plan_policy="bounded-v1")
    spec = NovelRunSpec.model_validate({**old.model_dump(), "length_policy": "unit-v1"})
    assert spec.chapter_count is None and spec.target_characters is None
    before = deepcopy((spec, snapshot, plan))
    system, raw = unit_scope.render_for(spec, snapshot, action, plan, "已写事实")
    payload = json.loads(raw)
    for name in ["estimated_chapters", "total_target_characters", "unit_target_characters"]:
        assert name not in payload
    assert "篇幅目标是节奏参考" not in system
    if action == "write:1":
        assert "自然结束处" in system and "不设字数目标" in system
        assert payload["plot_execution"]["current_task"] == plan["scenes"][0]
        assert payload["unit_position"]["total"] == len(plan["scenes"])
    if action == "plan":
        assert payload["narrative_design"]["selected_cards"] == [snapshot["cards"][1]]
    if action == "reader":
        assert "author_direction" not in payload and "effective_plan" not in payload
    assert (spec, snapshot, plan) == before
    assert unit_scope.contract_for(spec) != unit_scope.contract_for(old)


def test_new_and_frozen_defaults_and_saved_amendment_authorizations():
    old, _, _ = fixture()
    data = old.model_dump(mode="json")
    data.pop("length_policy")
    new = NovelRunSpec.model_validate(data)
    assert new.length_policy == "unit-v1" and new.target_characters is None
    saved = FrozenGenerationSpec.model_validate(data)
    assert saved.length_policy == "legacy-v1" and saved.target_characters == 4000
    assert with_limits(saved, {"max_cost_cny": "2"}).length_policy == "legacy-v1"
    assert AmendmentRequest.model_fields["length_policy"].default == "unit-v1"
    assert (
        with_limits(saved, {"max_cost_cny": "2", "length_policy": "unit-v1"}).length_policy
        == "unit-v1"
    )


@pytest.mark.parametrize("lengths", [[80], [1500, 8000, 130], [101] * 6])
def test_complete_units_form_lossless_chapters_without_minimum_or_target(lengths):
    parts = ["事" * size for size in lengths]
    body = "\n\n".join(parts)
    units, cursor = [], 0
    for part in parts:
        units.append(
            {
                "start": cursor,
                "end": cursor + len(part),
                "body_sha256": digest(part),
                "complete": True,
            }
        )
        cursor += len(part) + 2
    result = unit_scope.segment_units(body, units, "test")
    assert len(result["segments"]) == len(parts) and result["tail"] is None
    assert "".join(body[s["start"] : s["end"]] for s in result["segments"]) == body
    assert result == unit_scope.segment_units(body, units, "test")
    units[-1]["complete"] = False
    partial = unit_scope.segment_units(body, units, "test")
    assert len(partial["segments"]) == len(parts) - 1 and partial["tail"]
    units[0]["body_sha256"] = "wrong"
    with pytest.raises(WorkflowError, match="来源失配"):
        unit_scope.segment_units(body, units, "test")


def test_author_manuscript_has_no_stale_boundaries_and_incomplete_body_stays_tail():
    body = "作者修改后的完整单元。"
    assert unit_scope.segment_units(body, [], "author")["segments"][0]["end"] == len(body)
    incomplete = unit_scope.segment_units(body, [], "truncated", complete=False)
    assert not incomplete["segments"] and incomplete["tail"] == {"start": 0, "end": len(body)}


def test_single_unit_and_independent_rewrite_also_have_no_length_target():
    old, snapshot, plan = fixture(stage_mode="single-unit-v1", unit_limit=1)
    spec = NovelRunSpec.model_validate({**old.model_dump(), "length_policy": "unit-v1"})
    for action in ["plan", "write", "rewrite"]:
        system, raw = unit_scope.render_for(spec, snapshot, action, plan, "原稿")
        payload = json.loads(raw)
        assert (
            not {"unit_target_characters", "total_target_characters", "estimated_chapters"}
            & payload.keys()
        )
        assert "篇幅目标是节奏参考" not in system
    assert "完整处理授权原稿" in system
