# ruff: noqa: F811
from novel_writer.generation.content import digest
from novel_writer.generation.guidance import render_for
from novel_writer.generation.schemas import FrozenGenerationSpec
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_genre_generation import create, generation, post, read  # noqa: F401


def test_reclassified_selection_freezes_only_chosen_sources_without_calls(generation):
    client, control = generation
    base, old = create(client, workflow="novel-run-v1", writing_policy="guided-v1")
    catalog = read(client, "/api/genre-quality-cards")
    assert len(catalog) == 106
    assert {c["layer"] for c in catalog} == {"genre", "narrative"}
    path = base.replace("/generation-batches", "/style-profile")
    result = client.put(
        path,
        headers=headers(),
        json={
            "confirmed": True,
            "selection_mode": "specified",
            "genre_card_id": "western_fantasy_dnd",
            "secondary_genre_card_ids": [
                "girls_love_gl", "horror_thriller_supernatural", "hidden_identity",
            ],
            "assets": [],
        },
    )
    assert result.status_code == 200, result.text
    selected = result.json()["matched_cards"]
    assert [c["layer"] for c in selected] == ["genre", "narrative", "narrative", "narrative"]
    assert all(c["writing_guidance"] for c in selected)
    new = post(
        client,
        base,
        {
            **old["spec"],
                "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
                
            "focus_card_id": "hidden_identity",
            "supporting_card_id": "horror_thriller_supernatural",
        },
    )
    assert new.status_code == 200, new.text
    batch = new.json()
    assert not batch["snapshot"]["blockers"]
    cards = batch["snapshot"]["cards"]
    assert [c["id"] for c in cards] == [
        "western_fantasy_dnd",
        "hidden_identity",
        "horror_thriller_supernatural",
    ]
    assert [c["layer"] for c in cards] == ["genre", "narrative", "narrative"]
    assert [c["content_version"] for c in cards] == ["3.0", "4.0", "4.1"]
    assert all(digest(c["text"]) == c["sha256"] for c in cards)
    assert all("id: rebirth" not in c["text"] for c in cards)
    assert all("id: time_loop" not in c["text"] for c in cards)
    _, prompt = render_for(
        FrozenGenerationSpec.model_validate(batch["spec"]), batch["snapshot"], "plan"
    )
    assert "layer: mechanism" not in prompt and "layer: narrative" in prompt
    assert read(client, f"{base}/{old['id']}")["snapshot"] == old["snapshot"]
    assert not control["calls"] and not batch["calls"]
