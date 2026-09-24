# ruff: noqa: F811
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_longform import longform  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)


def test_gl_default_without_relationship_or_viewpoint_flows_through_existing_slots(longform):
    client, control = longform
    base, initial = create(client, workflow="novel-run-v1", stage_mode="longform-v1", unit_limit=2)
    payload = {
        k: v
        for k, v in initial["spec"].items()
        if k not in {"relationship_scope", "relationship_character_ids", "viewpoint"}
    }
    payload.update(
        feedback_policy="logic-v1",
        character_selection="chief-auto-v1",
        character_ids=[],
        pause_after_plan=False,
        author_boundaries="本阶段不表白，保留双方自主回应",
    )
    response = post(client, base, payload)
    assert response.status_code == 200, response.text
    draft = response.json()
    assert control["calls"] == []
    assert draft["spec"]["relationship_scope"] == "genre-led"
    assert read(client, base + "/setup")["configuration_revision"] == "author-intent-v1"
    batch = start(client, base, draft)
    assert batch["status"] == "needs_attention", batch["state"]
    for action, request in control["requests"].items():
        if action == "plan" or action.startswith(("write:", "chief:")):
            assert "relationship_scope" not in request
            assert request["author_boundaries"] == payload["author_boundaries"]
            assert request["creative_policy"] == "author-intent-v1"
        if action.startswith("reader"):
            assert "author_boundaries" not in request and "creative_policy" not in request
    assert len(control["calls"]) <= draft["snapshot"]["maximum_calls"]
    assert read(client, base.replace("/generation-batches", "/chapters")) == []
    assert read(client, f"{base}/{initial['id']}")["spec"]["relationship_scope"] == "explore"
