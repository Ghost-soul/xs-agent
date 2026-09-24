import json

import pytest

from novel_writer.domain.character import Character
from novel_writer.domain.state import StoryState
from novel_writer.generation.intent import contract_for, prepare_cast, render_for
from novel_writer.generation.longform_prompts import contract_for as previous_contract
from novel_writer.generation.longform_prompts import render_for as previous_render
from novel_writer.generation.schemas import GenerationSpec, NovelRunSpec
from tests.unit.test_generation_casting import automatic, roster, snapshot
from tests.unit.test_genre_generation import plan, spec


@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
def test_intent_is_explicit_in_frozen_spec_and_keeps_old_contract(stage):
    old = spec(workflow="novel-run-v1", stage_mode=stage)
    new = NovelRunSpec.model_validate(
        {
            k: v
            for k, v in old.model_dump().items()
            if k not in {"viewpoint", "relationship_scope", "relationship_character_ids"}
        }
    )
    assert new.relationship_scope == "genre-led" and new.viewpoint == ""
    assert GenerationSpec.model_validate(new.model_dump()).model_dump() == new.model_dump()
    assert contract_for(old) == previous_contract(old)
    assert contract_for(new) != previous_contract(new)
    with pytest.raises(ValueError, match="独立关系名单"):
        new.model_validate({**new.model_dump(), "relationship_character_ids": ["a", "b"]})
    with pytest.raises(ValueError, match="视角范围"):
        spec(viewpoint="")


@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
def test_chief_writer_receive_intent_and_full_cards_without_relationship_gate(stage):
    s = automatic(
        stage_mode=stage,
        unit_limit=2 if stage == "longform-v1" else 1,
        relationship_scope="genre-led",
        viewpoint="",
        author_boundaries="仅林青与江月发展，本阶段不表白",
    )
    snap = snapshot(s, roster())
    p = plan([c["id"] for c in snap["cast_selection"]["characters"]][:2])
    for action in ["plan", "write:1" if stage == "longform-v1" else "write", "rewrite"]:
        system, user = render_for(s, snap, action, p)
        assert "不需要再次询问是否允许恋爱" in system
        assert "不得自动决定最终CP" not in system
        assert '"relationship_scope"' not in user
        assert '"relationship_character_ids"' not in user
        assert "仅林青与江月发展，本阶段不表白" in user
        assert "由 Chief 随场面设计确定" in user
        assert user.count("完整题材卡正文") == 1
    _, reader = render_for(s, snap, "reader", p, "林青递出船票。")
    assert "creative_policy" not in reader and "题材卡正文" not in reader
    assert "不表白" not in reader
    assert set(json.loads(reader)) == {
        "candidate",
        "public_preceding",
        "output_schema",
        "finding_schema",
    }


@pytest.mark.parametrize("stage", ["single-unit-v1", "longform-v1"])
def test_old_explicit_relationship_and_viewpoint_prompts_are_unchanged(stage):
    s = automatic(stage_mode=stage)
    snap = snapshot(s, roster())
    assert render_for(s, snap, "plan") == previous_render(s, snap, "plan")


def test_author_boundary_mentions_are_recalled_without_forcing_a_pair_or_appearance():
    characters = [Character(name=f"背景人物{i}", tier="B") for i in range(30)]
    chosen = Character(name="江月")
    excluded = Character(name="林青")
    state = StoryState(characters=tuple([*characters, chosen, excluded])).model_dump(mode="json")
    s = automatic(
        relationship_scope="genre-led",
        viewpoint="",
        direction="接着写",
        author_boundaries="江月主动作出选择；林青本次不要出场",
    )
    result = prepare_cast(s, state)
    assert {str(chosen.id), str(excluded.id)} <= {c["id"] for c in result["candidates"]}
    assert result["required_ids"] == []
    assert result["author_input_sha256"]
    assert s.direction == "接着写"
