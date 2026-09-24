import json
from uuid import uuid4

import pytest

from novel_writer.domain.character import Character
from novel_writer.domain.state import StoryState
from novel_writer.domain.story import NarrativePosition
from novel_writer.generation.casting import contract_for, prepare_cast, render_for, selected_plan
from novel_writer.generation.content import json_text
from novel_writer.generation.roles import contract_for as original_contract
from novel_writer.generation.roles import render_for as original_render
from novel_writer.services.errors import WorkflowError
from tests.unit.test_genre_generation import plan, spec


def automatic(**changes):
    return spec(
        **{
            "workflow": "novel-run-v1",
            "character_selection": "chief-auto-v1",
            "character_ids": [],
            "relationship_character_ids": [],
            **changes,
        }
    )


def roster():
    return [
        Character(name="林青", description="成年女性，守卫", speech_style="林青完整声音档案"),
        Character(name="江月", description="成年女性，船主", speech_style="江月完整声音档案"),
        Character(name="王掌柜", description="旧手续经手人", speech_style="不应进入Writer的档案"),
    ]


def snapshot(specification, characters):
    state = StoryState(characters=tuple(characters)).model_dump(mode="json")
    return {
        "cast_selection": prepare_cast(specification, state),
        "cards": [{"id": "gl", "sha256": "sha", "text": "完整题材卡正文"}],
        "context": {
            "characters": state["characters"],
            "recent_chapters": [],
            "beliefs": [],
            "relationships": [],
            "narrative_position": {},
        },
    }


def test_auto_cast_is_explicit_and_legacy_stays_manual():
    assert spec().character_selection == "manual"
    with pytest.raises(ValueError, match="手选模式"):
        spec(character_ids=[], relationship_character_ids=[])
    with pytest.raises(ValueError, match="新版 NovelRun"):
        automatic(workflow="single-chapter-v1")
    assert automatic().character_ids == []
    with pytest.raises(ValueError, match="至少两位"):
        automatic(relationship_character_ids=["one"])


def test_candidate_recall_keeps_named_future_carrier_and_pins_over_recent_extras():
    old = [Character(name=f"背景人物{i}", tier="B") for i in range(30)]
    future = Character(name="江月", aliases=("小月",))
    fixed = Character(name="林青")
    retired = Character(name="旧人", library_status="retired")
    chars = [*old, future, fixed, retired]
    specification = automatic(character_ids=[str(fixed.id)], direction="小月主动邀请林青离开")
    state = StoryState(
        characters=tuple(chars),
        narrative_position=NarrativePosition(
            current_characters=tuple(c.name for c in old[:10]),
        ),
    ).model_dump(mode="json")
    selection = prepare_cast(specification, state)
    ids = {c["id"] for c in selection["candidates"]}
    assert len(ids) == 24 and {str(future.id), str(fixed.id)} <= ids
    assert str(retired.id) in selection["omitted_ids"]
    assert selection["required_ids"] == [str(fixed.id)]
    assert selection["recall_complete"] is False
    assert (
        state["characters"]
        == StoryState(characters=tuple(chars)).model_dump(mode="json")["characters"]
    )


@pytest.mark.parametrize("unknown", [False, True])
def test_fixed_unknown_or_retired_character_is_not_silently_replaced(unknown):
    c = Character(name="已退役", library_status="retired")
    identifier = str(uuid4()) if unknown else str(c.id)
    with pytest.raises(WorkflowError, match="不存在或已退役"):
        prepare_cast(
            automatic(character_ids=[identifier]),
            StoryState(characters=(c,)).model_dump(mode="json"),
        )


def test_chief_selects_writer_receives_full_selected_profiles_reader_stays_cold():
    chars = roster()
    s = automatic()
    snap = snapshot(s, chars)
    chosen = [str(c.id) for c in chars[:2]]
    p = plan(chosen)
    system, chief = render_for(s, snap, "plan")
    assert "先依据完整主导题材" in system
    assert "王掌柜" in chief and "不应进入Writer的档案" not in chief
    assert "林青完整声音档案" not in chief
    _, writer = render_for(s, snap, "write", p)
    assert "林青完整声音档案" in writer and "江月完整声音档案" in writer
    assert "不应进入Writer的档案" not in writer and "王掌柜" not in writer
    assert chief.count("完整题材卡正文") == writer.count("完整题材卡正文") == 1
    _, reader = render_for(s, snap, "reader", p, "林青递出了船票。")
    assert set(json.loads(reader)) == {
        "candidate",
        "public_preceding",
        "output_schema",
        "finding_schema",
    }
    assert "cast_scope" not in reader and "完整题材卡正文" not in reader
    assert len(snap["context"]["characters"]) == 3  # Rendering never mutates frozen sources.


def test_plan_cannot_drop_fixed_cast_or_expand_beyond_pool_and_actor_limit():
    chars = [*roster(), *[Character(name=f"路人{i}") for i in range(11)]]
    s = automatic(character_ids=[str(chars[0].id)])
    snap = snapshot(s, chars)
    with pytest.raises(ValueError, match="遗漏作者固定"):
        selected_plan(json_text(plan([str(c.id) for c in chars[1:3]])), s, snap)
    with pytest.raises(ValueError, match="范围外"):
        selected_plan(json_text(plan([str(chars[0].id), str(uuid4())])), s, snap)
    oversized = plan([str(c.id) for c in chars[:7]])
    oversized["scenes"][1]["character_ids"] = [str(c.id) for c in chars[7:]]
    with pytest.raises(ValueError, match="超过12"):
        selected_plan(json_text(oversized), s, snap)


def test_specified_pair_is_fixed_and_exploration_allowlist_is_not_a_cast_mandate():
    chars = [*roster(), Character(name="第四位")]
    ids = [str(c.id) for c in chars]
    s = automatic(relationship_scope="specified_pair", relationship_character_ids=ids[:2])
    snap = snapshot(s, chars)
    with pytest.raises(ValueError, match="遗漏作者固定"):
        selected_plan(json_text(plan(ids[1:3])), s, snap)
    exploratory = automatic(relationship_character_ids=ids[:3])
    snap = snapshot(exploratory, chars)
    assert not snap["cast_selection"]["required_ids"]
    selected_plan(json_text(plan(ids[:2])), exploratory, snap)
    with pytest.raises(ValueError, match="允许的关系范围"):
        selected_plan(json_text(plan(ids[2:])), exploratory, snap)


def test_manual_prompt_and_contract_are_unchanged():
    s = spec(workflow="novel-run-v1")
    snap = {"cards": [], "context": {"characters": []}}
    assert contract_for(s) == original_contract(s)
    for action in ("plan", "write"):
        assert render_for(s, snap, action, plan()) == original_render(s, snap, action, plan())
    assert contract_for(automatic()) != original_contract(automatic())
