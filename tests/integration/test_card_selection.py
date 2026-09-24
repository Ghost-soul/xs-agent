# ruff: noqa: F811
import json

import pytest

from novel_writer.generation.card_selection import contract_for, render_for
from novel_writer.generation.content import digest
from novel_writer.generation.schemas import FrozenGenerationSpec
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)

WORLD = "western_fantasy_dnd"
SIDE = "traditional_wuxia_jianghu"
NARRATIVES = ["girls_love_gl", "farming_infrastructure", "wish_fulfillment"]


def test_actual_chief_request_receives_all_narratives_and_writer_uses_the_plan(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(
        client, writing_policy="guided-v1", feedback_policy="logic-v1",
        automation_policy="stage-auto-v1", card_selection_policy="separate-v1",
        focus_card_id=WORLD, supporting_card_id=SIDE, narrative_card_ids=NARRATIVES,
        unit_limit=3,
    )
    batch = start(client, base, draft)
    assert batch["state"]["units_finished"], batch["state"]
    assert all(c["status"] == "completed" for c in batch["calls"]), batch["state"]
    chief = control["requests"]["plan"]
    assert chief["world_cards"]["primary"]["id"] == WORLD
    assert chief["world_cards"]["secondary"]["id"] == SIDE
    assert [c["id"] for c in chief["narrative_cards"]] == NARRATIVES
    for n in (1, 2, 3):
        writer = control["requests"][f"write:{n}"]
        assert not {"cards", "background_cards", "world_cards", "narrative_cards"} & writer.keys()
        assert writer["effective_plan"]
    assert "reader" not in control["calls"]


def test_preview_freezes_only_selected_worlds_and_stage_narratives(generation):
    client, control = generation
    base, old = create(client, workflow="novel-run-v1", writing_policy="guided-v1")
    setup = read(client, base + "/setup")
    assert len(setup["available_cards"]) == 106
    response = post(client, base, {
        **old["spec"],
            "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
             "card_selection_policy": "separate-v1",
        "focus_card_id": WORLD, "supporting_card_id": SIDE,
        "narrative_card_ids": NARRATIVES,
    })
    assert response.status_code == 200, response.text
    batch = response.json()
    assert not batch["snapshot"]["blockers"]
    cards = batch["snapshot"]["cards"]
    assert [card["id"] for card in cards] == [WORLD, SIDE, *NARRATIVES]
    assert all(digest(card["text"]) == card["sha256"] for card in cards)
    spec = FrozenGenerationSpec.model_validate(batch["spec"])
    _, raw = render_for(spec, batch["snapshot"], "plan")
    payload = json.loads(raw)
    assert [card["id"] for card in payload["narrative_cards"]] == NARRATIVES
    assert batch["snapshot"]["prompt_contract_sha256"] == contract_for(spec)
    second = post(client, base, {**batch["spec"],
        "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
         "narrative_card_ids": []})
    assert second.status_code == 200, second.text
    assert [card["id"] for card in second.json()["snapshot"]["cards"]] == [WORLD, SIDE]
    assert read(client, f"{base}/{old['id']}")["snapshot"] == old["snapshot"]
    assert not control["calls"]


@pytest.mark.parametrize("changes", [
    {"focus_card_id": "girls_love_gl"},
    {"supporting_card_id": "girls_love_gl"},
    {"narrative_card_ids": [WORLD]},
    {"narrative_card_ids": ["girls_love_gl", "girls_love_gl"]},
])
def test_stage_api_enforces_world_and_narrative_categories(generation, changes):
    client, control = generation
    base, old = create(client, workflow="novel-run-v1", writing_policy="guided-v1")
    response = post(client, base, {
        **old["spec"],
            "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
             "card_selection_policy": "separate-v1",
        "focus_card_id": WORLD, "narrative_card_ids": NARRATIVES, **changes,
    })
    assert response.status_code in {400, 422}, response.text
    assert not control["calls"]


def test_project_settings_allow_many_narratives_but_only_one_secondary_genre(generation):
    client, control = generation
    base, _ = create(client)
    path = base.replace("/generation-batches", "/style-profile")
    narrative_ids = [c["id"] for c in read(client, "/api/genre-quality-cards")
                     if c["layer"] == "narrative"][:20]
    payload = {"confirmed": True, "selection_mode": "specified", "assets": [],
               "genre_card_id": WORLD, "secondary_genre_card_ids": [SIDE, *narrative_ids]}
    response = client.put(path, headers=headers(), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["secondary_genre_card_ids"] == [SIDE, *narrative_ids]
    bad = client.put(path, headers=headers(), json={
        **payload, "secondary_genre_card_ids": [SIDE, "urban_superpower_ability", *narrative_ids],
    })
    assert bad.status_code == 400, bad.text
    assert read(client, path)["secondary_genre_card_ids"] == payload["secondary_genre_card_ids"]
    assert not control["calls"]


def test_large_narrative_selection_is_counted_and_blocked_without_truncating_cards(generation):
    client, control = generation
    base, old = create(client, workflow="novel-run-v1", writing_policy="guided-v1")
    narratives = [c["id"] for c in read(client, "/api/genre-quality-cards")
                  if c["layer"] == "narrative"]
    response = post(client, base, {
        **old["spec"],
            "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
             "card_selection_policy": "separate-v1",
        "focus_card_id": WORLD, "narrative_card_ids": narratives,
    })
    assert response.status_code == 200, response.text
    batch = response.json()
    assert len(batch["snapshot"]["cards"]) == 1 + len(narratives)
    assert batch["snapshot"]["blockers"]
    assert "超过" in str(batch["snapshot"]["blockers"])
    assert not control["calls"] and not batch["calls"]
