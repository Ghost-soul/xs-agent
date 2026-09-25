import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from novel_writer.generation import card_selection, focused_context
from novel_writer.generation.budget import request_for, validate_capacity
from novel_writer.generation.schemas import AmendmentRequest, FrozenGenerationSpec, NovelRunSpec
from novel_writer.services.errors import WorkflowError
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_generation_guidance import setup


def fixture():
    spec, snap, plan = setup(context_policy="focused-v1", input_limit=200000)
    ref = snap["context"]
    character = ref["characters"][0]
    character.update(speech_style="完整声音样本", mind_state={"beliefs": ["她尚不知道真相"]})
    ref["narrative_position"] = {"current_location": "渡口", "recent_major_event": "约定同行"}
    ref["recent_chapters"] = [{"revision_id": "r1", "body": "最后一章的完整正文"}]
    ref["formal_summaries"] = [
        {
            "revision_id": "r1",
            "coverage": "complete",
            "outcome": "两人约定同行",
            "position": deepcopy(ref["narrative_position"]),
            "factual_changes": {
                "add_characters": [deepcopy(character)],
                "add_events": [],
                "set_narrative_position": deepcopy(ref["narrative_position"]),
            },
        }
    ]
    snap["macro_diagnostic"] = {
        "narrative_position": deepcopy(ref["narrative_position"]),
        "actual_recent_changes": [
            {"revision_id": "omitted", "outcome": "本轮未选取的旧事件"},
            {"revision_id": "r1", "position": deepcopy(ref["narrative_position"])},
        ],
    }
    return spec, snap, plan


def test_chief_shares_identical_objects_preserves_full_creative_sources_and_local_originals():
    spec, snap, plan = fixture()
    before = deepcopy(snap)
    system, text = focused_context.render_for(spec, snap, "plan", plan)
    value = json.loads(text)
    ref = value["formal_reference"]
    change = ref["formal_summaries"][0]["factual_changes"]
    assert change["add_characters"][0] == {"same_as": "formal_reference.characters[0]"}
    assert ref["characters"][0] == snap["context"]["characters"][0]
    assert ref["recent_chapters"] == snap["context"]["recent_chapters"]
    assert ref["reference_style"] == snap["context"]["reference_style"]
    assert value["background_cards"] == snap["cards"]
    assert value["author_direction"] == spec.direction
    assert "add_events" not in change
    assert value["context_selection"]["shared_objects"] == 5
    assert "本轮未选取的旧事件" not in text
    assert "时间与动作含义仍保留" in system
    assert snap == before


def test_distinct_historical_values_are_never_replaced_with_current_state():
    spec, snap, plan = fixture()
    changes = snap["context"]["formal_summaries"][0]["factual_changes"]
    changes["add_characters"][0]["current_state"] = "当时仍在城内"
    changes["set_narrative_position"] = {"current_location": "城内"}
    value = json.loads(focused_context.render_for(spec, snap, "plan", plan)[1])
    delta = value["formal_reference"]["formal_summaries"][0]["factual_changes"]
    assert delta["add_characters"][0]["current_state"] == "当时仍在城内"
    assert delta["set_narrative_position"] == {"current_location": "城内"}


def test_writer_keeps_current_cast_and_relationship_neighbours_with_voices():
    spec, snap, plan = fixture()
    chars = snap["context"]["characters"]
    active, partner, later = chars[:3]
    snap["context"]["relationships"] = [
        {
            "source_character_id": active["id"],
            "target_character_id": partner["id"],
            "description": "约定产生的后果",
            "relation_type": "盟友",
        }
    ]
    plan["scenes"][0]["character_ids"] = [active["id"]]
    plan["scenes"][1]["character_ids"] = [partner["id"], later["id"]]
    value = json.loads(focused_context.render_for(spec, snap, "write:1", plan)[1])
    ref = value["formal_reference"]
    assert {c["id"] for c in ref["characters"]} == {active["id"], partner["id"]}
    assert ref["characters"][0]["speech_style"] == "完整声音样本"
    assert ref["other_character_overview"][0]["id"] == later["id"]
    assert value["effective_plan"] == plan
    assert "background_cards" not in value and "cards" not in value


def test_world_selection_retains_rules_high_risk_and_causal_references():
    spec, snap, plan = fixture()
    ref = snap["context"]
    ref["world_rules"] = [{"id": "rule", "statement": "跨越海峡必须搭船"}]
    ref["world_lore"] = [
        {
            "id": "high",
            "name": "远古契约",
            "summary": "守约",
            "risk_level": "high",
            "details": ["即使未被点名也保留"],
        },
        {
            "id": "remote",
            "name": "荒漠",
            "summary": "干旱",
            "risk_level": "low",
            "details": ["远方无关地貌" * 100],
        },
        {
            "id": "dep",
            "name": "契约出处",
            "summary": "旧址",
            "risk_level": "low",
            "details": ["因果依赖资料"],
        },
    ]
    ref["world_lore"][0]["details"].append("dep")
    value = json.loads(focused_context.render_for(spec, snap, "plan", plan)[1])
    projected = value["formal_reference"]
    assert projected["world_rules"] == ref["world_rules"]
    assert [x["id"] for x in projected["world_lore"]] == ["high", "dep"]
    assert projected["world_lore_overview"] == [
        {
            "id": "remote",
            "name": "荒漠",
            "summary": "干旱",
        }
    ]


@pytest.mark.parametrize("action", ["memory:1", "checker"])
def test_evidence_roles_keep_knowledge_and_body_but_omit_performance_examples(action):
    spec, snap, plan = fixture()
    value = json.loads(focused_context.render_for(spec, snap, action, plan, "完整证据正文")[1])
    ref = value["formal_facts" if action.startswith("memory") else "formal_start"]
    assert "speech_style" not in ref["characters"][0]
    assert ref["characters"][0]["mind_state"] == {"beliefs": ["她尚不知道真相"]}
    assert value["candidate"][0]["text"] == "完整证据正文"


def test_reader_stays_cold_and_old_contracts_and_requests_stay_exact():
    spec, snap, plan = fixture()
    for policy in ["full-v1", "bounded-v1"]:
        old = spec.model_copy(update={"context_policy": policy})
        assert focused_context.contract_for(old) == card_selection.contract_for(old)
        for action in ["plan", "write:1", "memory:1", "checker", "reader", "amend"]:
            assert focused_context.render_for(old, snap, action, plan, "正文") == (
                card_selection.render_for(old, snap, action, plan, "正文")
            )
    previous = focused_context.previous_spec(spec)
    assert focused_context.render_for(spec, snap, "reader", plan, "正文") == (
        card_selection.render_for(previous, snap, "reader", plan, "正文")
    )
    assert focused_context.contract_for(spec) != card_selection.contract_for(previous)


def test_200k_input_defaults_limits_and_capacity_still_respect_model_window():
    spec, _, _ = fixture()
    data = spec.model_dump(mode="json")
    data.pop("input_limit")
    data.pop("context_policy")
    assert NovelRunSpec.model_validate(data).input_limit == 200000
    assert NovelRunSpec.model_validate(data).context_policy == "chief-focus-v4"
    assert FrozenGenerationSpec.model_validate(data).input_limit == 100000
    assert AmendmentRequest.model_fields["input_limit"].default == 200000
    with pytest.raises(ValidationError):
        NovelRunSpec.model_validate({**data, "input_limit": 200001})
    with pytest.raises(ValidationError):
        NovelRunSpec.model_validate({**data, "writer_output_limit": 100001})
    capable = profile()
    capable.models[0].context_window = 300000
    request = request_for(spec, capable, "write:1", "system", "x" * 150000)
    assert (
        validate_capacity(
            request.user_prompt, request, spec, capable, {"method": "utf8-byte-upper-bound"}
        )
        == 152048
    )
    capable.models[0].context_window = 160000
    with pytest.raises(WorkflowError, match="超过允许值"):
        validate_capacity(
            request.user_prompt, request, spec, capable, {"method": "utf8-byte-upper-bound"}
        )


def test_small_budget_preserves_causal_dependencies_and_archives_optional_material():
    spec, snap, _ = fixture()
    spec = spec.model_copy(update={"input_limit": 12000})
    ref = snap["context"]
    predecessor = {"id": "before", "summary": "出发前已把船借走", "happens_before": ["now"]}
    current = {"id": "now", "summary": "抵达渡口"}
    optional = {"id": "unrelated", "summary": "旧材料" * 1000}
    ref["recent_events"] = [current]
    ref["critical_facts"] = [predecessor, optional]
    ref["related_state"] = {"scenes": []}
    snap["counting"] = {"chief": {"method": "utf8-byte-upper-bound"}}
    focused_context.fit_context(spec, snap, profile(), "r1")
    assert ref["critical_facts"] == [predecessor]
    assert snap["context_archive"]["0:critical_facts"] == [predecessor, optional]
    assert snap["context_budget"]["optional_items"][0]["selected"] is False


@pytest.mark.asyncio
async def test_saved_memory_authorization_keeps_its_original_input_limit():
    from novel_writer.generation.memory_recovery import authorized_spec, with_output

    spec, _, _ = fixture()
    spec = spec.model_copy(update={"input_limit": 100000})
    batch = SimpleNamespace(state={}, preview_sha256="saved")
    data = {
        "batch_preview_sha256": "saved", "previous_input_limit": 100000,
        "input_limit": 100000, "output_limit": 100000, "all_roles": True,
        "previous_max_cost_cny": str(spec.max_cost_cny), "max_cost_cny": "10",
        "total_cost_upper_cny": "5", "action": "memory:2",
    }

    class Service:
        async def artifact(self, batch, kind):
            return SimpleNamespace(payload=data)

    effective = await authorized_spec(Service(), batch, spec)
    assert effective.input_limit == 100000
    assert with_output(spec, "memory:2", 100000, all_roles=True).input_limit == 200000
    assert spec.input_limit == 100000
