import json

import pytest
from pydantic import ValidationError

from novel_writer.generation import card_selection, guidance
from novel_writer.generation.schemas import FrozenGenerationSpec, NovelRunSpec
from novel_writer.services.errors import WorkflowError
from novel_writer.services.style_profiles import (
    load_quality_cards,
    validate_genre_card_selection,
    validate_project_card_selection,
)
from tests.unit.test_generation_guidance import setup

WORLD = "western_fantasy_dnd"
SIDE = "traditional_wuxia_jianghu"
NARRATIVES = ["girls_love_gl", "farming_infrastructure", "wish_fulfillment"]


def test_new_world_pair_and_many_narratives_are_independent_of_old_mixed_pool():
    spec, _, _ = setup()
    spec = spec.model_copy(update={
        "card_selection_policy": "separate-v1", "focus_card_id": WORLD,
        "supporting_card_id": SIDE, "narrative_card_ids": NARRATIVES,
    })
    assert card_selection.selected_ids(spec, {"selection_mode": "unselected"}) == [
        WORLD, SIDE, *NARRATIVES,
    ]
    many = [c["id"] for c in load_quality_cards() if c["layer"] == "narrative"][:20]
    assert validate_project_card_selection(WORLD, [SIDE, *many]) == (WORLD, (SIDE, *many))


@pytest.mark.parametrize("primary,secondary", [
    ("girls_love_gl", []),
    (WORLD, [SIDE, "urban_superpower_ability"]),
    (WORLD, [WORLD]),
])
def test_new_project_settings_reject_wrong_main_or_multiple_world_sides(primary, secondary):
    with pytest.raises(WorkflowError):
        validate_project_card_selection(primary, secondary)


@pytest.mark.parametrize("changes", [
    {"focus_card_id": "girls_love_gl"},
    {"supporting_card_id": "girls_love_gl"},
    {"supporting_card_id": WORLD},
    {"narrative_card_ids": [WORLD]},
    {"narrative_card_ids": ["unknown-card"]},
])
def test_new_stage_rejects_category_errors_and_missing_cards(changes):
    spec, _, _ = setup()
    spec = spec.model_copy(update={
        "card_selection_policy": "separate-v1", "focus_card_id": WORLD,
        "narrative_card_ids": NARRATIVES, **changes,
    })
    with pytest.raises(WorkflowError):
        card_selection.selected_ids(spec, {})


def test_new_schema_defaults_and_duplicate_rejection_keep_frozen_old_defaults():
    old, _, _ = setup()
    data = old.model_dump(mode="json")
    data.pop("card_selection_policy")
    data.pop("narrative_card_ids")
    assert NovelRunSpec.model_validate(data).card_selection_policy == "separate-v1"
    assert FrozenGenerationSpec.model_validate(data).card_selection_policy == "legacy-v1"
    for ids in [["girls_love_gl", "girls_love_gl"], [" "], ["x" * 81]]:
        with pytest.raises(ValidationError):
            NovelRunSpec.model_validate({**data, "narrative_card_ids": ids})


def test_old_render_contracts_and_mixed_selections_remain_unchanged():
    spec, snapshot, plan = setup()
    assert card_selection.contract_for(spec) == guidance.contract_for(spec)
    for action in ["plan", "write:1", "memory:1", "checker", "reader"]:
        assert card_selection.render_for(spec, snapshot, action, plan, "正文") == (
            guidance.render_for(spec, snapshot, action, plan, "正文")
        )
    assert validate_genre_card_selection("girls_love_gl", [WORLD, SIDE]) == (
        "girls_love_gl", (WORLD, SIDE),
    )


def test_all_selected_narratives_reach_chief_but_writer_and_feedback_do_not_get_cards():
    old, snapshot, plan = setup()
    spec = old.model_copy(update={
        "card_selection_policy": "separate-v1", "focus_card_id": WORLD,
        "supporting_card_id": SIDE, "narrative_card_ids": NARRATIVES,
    })
    snapshot["cards"] = [
        {"id": value, "name": value, "text": "独有整卡标记:" + value, "sha256": value}
        for value in [WORLD, SIDE, *NARRATIVES]
    ]
    _, chief = card_selection.render_for(spec, snapshot, "plan")
    payload = json.loads(chief)
    assert payload["world_cards"]["primary"]["id"] == WORLD
    assert payload["world_cards"]["secondary"]["id"] == SIDE
    assert [c["id"] for c in payload["narrative_cards"]] == NARRATIVES
    assert "background_cards" not in payload and "genre_direction" not in payload
    assert card_selection.contract_for(spec) != guidance.contract_for(old)
    for action in ["write:1", "checker", "reader"]:
        _, raw = card_selection.render_for(spec, snapshot, action, plan, "正文")
        assert "独有整卡标记" not in raw
