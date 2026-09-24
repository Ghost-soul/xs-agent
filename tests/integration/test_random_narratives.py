# ruff: noqa: F401, F811
from unittest.mock import Mock

import pytest

from novel_writer.generation.content import digest
from novel_writer.generation.service import SystemRandom
from novel_writer.services.style_profiles import StyleProfileService
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated
from tests.integration.test_generation_feedback import feedback_run
from tests.integration.test_generation_longform import longform, stage_create
from tests.integration.test_genre_generation import create, generation, post, read, start


def draft(client):
    return create(
        client, workflow="novel-run-v1", writing_policy="guided-v1", feedback_policy="logic-v1",
        card_selection_policy="separate-v1", focus_card_id="western_fantasy_dnd",
        supporting_card_id="traditional_wuxia_jianghu", narrative_card_ids=["girls_love_gl"],
    )


def test_random_preview_freezes_two_distinct_active_narratives_and_preserves_worlds(generation):
    client, control = generation
    base, original = draft(client)
    response = post(client, base + "/random-preview", original["spec"])
    assert response.status_code == 200, response.text
    preview = response.json()
    ids = preview["spec"]["narrative_card_ids"]
    pool = {c["id"] for c in read(client, base + "/setup")["available_cards"]
            if c["layer"] == "narrative"}
    assert len(ids) == len(set(ids)) == 2 and set(ids) <= pool
    assert preview["snapshot"]["narrative_selection_policy"] == "random-two-v1"
    cards = preview["snapshot"]["cards"]
    assert [c["id"] for c in cards] == [
        original["spec"]["focus_card_id"], original["spec"]["supporting_card_id"], *ids,
    ]
    assert all(digest(c["text"]) == c["sha256"] for c in cards)
    assert read(client, f"{base}/{original['id']}") == original
    assert not control["calls"] and not preview["calls"]


def test_network_retry_reuses_draw_and_new_preview_draws_again(generation, monkeypatch):
    client, control = generation
    base, original = draft(client)
    draws = [["girls_love_gl", "farming_infrastructure"], ["wish_fulfillment", "girls_love_gl"]]
    sample = Mock(side_effect=draws)
    monkeypatch.setattr(SystemRandom, "sample", sample)
    route = base + "/random-preview"
    first = post(client, route, original["spec"], "same-draw")
    assert first.status_code == 200, first.text
    second = post(client, route, original["spec"], "same-draw")
    assert second.status_code == 200 and second.json() == first.json()
    assert sample.call_count == 1
    # Changing selection mode with the same key must not silently reuse a different request.
    conflict = post(client, base, original["spec"], "same-draw")
    assert conflict.status_code == 409, conflict.text
    third = post(client, route, original["spec"], "next-draw")
    assert third.status_code == 200, third.text
    assert third.json()["spec"]["narrative_card_ids"] == draws[1]
    assert first.json()["spec"]["narrative_card_ids"] == draws[0]
    assert third.json()["id"] != first.json()["id"]
    assert sample.call_count == 2 and not control["calls"]


@pytest.mark.parametrize("pool", [[], [{"id": "girls_love_gl", "layer": "narrative"}], [
    {"id": "girls_love_gl", "layer": "narrative"},
    {"id": "girls_love_gl", "layer": "narrative"},
    {"id": "western_fantasy_dnd", "layer": "genre"},
]])
def test_insufficient_distinct_narratives_creates_no_preview_or_call(generation, monkeypatch, pool):
    client, control = generation
    base, original = draft(client)
    previous = read(client, base)
    monkeypatch.setattr(StyleProfileService, "catalog", lambda self: pool)
    response = post(client, base + "/random-preview", original["spec"])
    assert response.status_code == 400 and "不足两张" in response.text
    assert read(client, base) == previous and not control["calls"]


def test_authorized_stage_uses_frozen_pair_without_drawing_again(feedback_run, monkeypatch):
    client, control = feedback_run
    base, original = stage_create(
        client, writing_policy="guided-v1", feedback_policy="logic-v1",
        automation_policy="stage-auto-v1", card_selection_policy="separate-v1",
        focus_card_id="western_fantasy_dnd", narrative_card_ids=[], unit_limit=3,
    )
    pair = ["girls_love_gl", "farming_infrastructure"]
    sample = Mock(return_value=pair)
    monkeypatch.setattr(SystemRandom, "sample", sample)
    response = post(client, base + "/random-preview", original["spec"])
    assert response.status_code == 200, response.text
    preview = response.json()
    assert not control["calls"]
    sample.side_effect = AssertionError("Existing stages must not draw again")
    done = start(client, base, preview)
    assert done["state"]["units_finished"], done["state"]
    assert all(c["status"] == "completed" for c in done["calls"])
    assert done["spec"] == preview["spec"] and done["snapshot"] == preview["snapshot"]
    assert [c["id"] for c in control["requests"]["plan"]["narrative_cards"]] == pair
    assert sample.call_count == 1
