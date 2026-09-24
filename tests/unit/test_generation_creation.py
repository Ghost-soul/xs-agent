from copy import deepcopy

import pytest

from novel_writer.generation.budget import request_for
from novel_writer.generation.content import parse_object
from novel_writer.generation.creation import contract_for, render_for
from novel_writer.generation.feedback import contract_for as previous_contract
from novel_writer.generation.feedback import render_for as previous_render
from novel_writer.generation.novel import slots_for
from novel_writer.generation.schemas import FrozenGenerationSpec, NovelRunSpec
from novel_writer.services.provider_profiles import ProviderModelOption, ProviderProfile
from tests.unit.test_generation_casting import automatic, roster, snapshot
from tests.unit.test_genre_generation import plan


def creative(**changes):
    return automatic(
        **{
            "stage_mode": "longform-v1",
            "unit_limit": 2,
            "relationship_scope": "genre-led",
            "viewpoint": "",
            "feedback_policy": "advisory-v1",
            "automation_policy": "stage-auto-v1",
            "writing_policy": "creative-v1",
            **changes,
        }
    )


def test_new_defaults_do_not_upgrade_frozen_batches_or_add_calls():
    payload = creative().model_dump()
    payload.pop("writing_policy")
    new = NovelRunSpec.model_validate(payload)
    old = FrozenGenerationSpec.model_validate(payload)
    assert new.writing_policy == "guided-v1"
    assert old.writing_policy == "legacy-v1"
    assert contract_for(old) == previous_contract(old)
    assert contract_for(new.model_copy(update={"writing_policy": "creative-v1"})) != previous_contract(new)
    assert slots_for(new) == slots_for(old)


@pytest.mark.parametrize("action", ["plan", "chief:1", "write:1", "rewrite", "reader", "memory:1"])
def test_legacy_prompts_are_exactly_unchanged(action):
    spec = creative(writing_policy="legacy-v1")
    snap = snapshot(spec, roster())
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    assert render_for(spec, snap, action, p, "前稿") == previous_render(
        spec, snap, action, p, "前稿"
    )


@pytest.mark.parametrize("action", ["plan", "chief:1"])
def test_chief_receives_voice_intent_and_existing_plan_schema_without_mutating_sources(action):
    spec = creative(author_boundaries="本阶段不表白")
    snap = snapshot(spec, roster())
    snap["context"]["reference_style"] = {"positive_contract": "近距离叙事"}
    before = deepcopy(snap)
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    system, raw = render_for(spec, snap, action, p, "已经写出的选择")
    data = parse_object(raw)
    assert "不需要再次询问是否允许恋爱" in system
    assert data["author_boundaries"] == "本阶段不表白"
    assert data["formal_reference"]["characters"][0]["speech_style"] == "林青完整声音档案"
    assert data["formal_reference"]["reference_style"] == {"positive_contract": "近距离叙事"}
    assert raw.count("完整题材卡正文") == 1
    previous = parse_object(previous_render(spec, snap, action, p, "已经写出的选择")[1])

    def without_descriptions(value):
        if isinstance(value, list):
            return [without_descriptions(item) for item in value]
        if isinstance(value, dict):
            return {k: without_descriptions(v) for k, v in value.items() if k != "description"}
        return value

    assert without_descriptions(data["output_schema"]) == without_descriptions(
        previous["output_schema"]
    )
    assert snap == before


@pytest.mark.parametrize("action", ["write:1", "write:2", "rewrite"])
def test_writer_keeps_full_cards_effective_plan_and_continuity_with_correct_scope(action):
    spec = creative()
    snap = snapshot(spec, roster())
    p = plan([c["id"] for c in snap["context"]["characters"]][:2])
    p["scenes"][0]["event"] = "作者已修订的事件"
    reports = {"memory": {"position": {"current_location": "门口"}, "outcome": "已经相邀"}}
    _, raw = render_for(spec, snap, action, p, "她把船票放回口袋。", "继续保持克制", reports)
    data = parse_object(raw)
    assert data["effective_plan"] == p
    assert data["cards"] == snap["cards"]
    assert data["already_written"] == "她把船票放回口袋。"
    assert data["author_decisions"] == "继续保持克制"
    assert data["candidate_handoff"]["position"] == {"current_location": "门口"}
    assert "不应进入Writer的档案" not in raw
    if action == "rewrite":
        assert "unit_position" not in data
        assert data["unit_target_characters"] == spec.chapter_count * spec.target_characters
    else:
        ordinal = int(action.split(":")[1])
        assert data["current_task"] == p["scenes"][ordinal - 1]
        assert data["unit_position"] == {
            "current": ordinal,
            "total": 2,
            "last_unit": ordinal == 2,
        }


def test_reader_does_not_receive_creative_guidance_or_private_materials():
    spec = creative()
    snap = snapshot(spec, roster())
    args = (spec, snap, "reader", plan(), "公开正文", "秘密作者方向")
    assert render_for(*args) == previous_render(*args)
    assert set(parse_object(render_for(*args)[1])) == {"candidate", "public_preceding"}


@pytest.mark.parametrize("support", [True, False])
def test_writer_reasoning_respects_capabilities_and_preserves_output_and_slots(support):
    spec = creative(writer_output_limit=100000)
    profile = ProviderProfile(
        id="local",
        display_name="替身",
        protocol="openai_chat_completions",
        base_url="http://127.0.0.1:1/v1",
        credential_required=False,
        is_local=True,
        default_model="test",
        supports_reasoning_effort=support,
        models=[
            ProviderModelOption(
                id="test",
                context_window=300000,
                max_output_tokens=100000,
                input_price_cny_per_million=0,
                output_price_cny_per_million=0,
            )
        ],
    )
    request = request_for(spec, profile, "write:1", "system", "user")
    assert request.reasoning_effort == ("medium" if support else None)
    assert request.max_output_tokens == 100000
    old = spec.model_copy(update={"writing_policy": "legacy-v1"})
    assert request_for(old, profile, "write:1", "s", "u").reasoning_effort == (
        "none" if support else None
    )
    assert request_for(spec, profile, "checker", "s", "u").reasoning_effort == (
        "none" if support else None
    )
