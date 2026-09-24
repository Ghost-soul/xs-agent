# ruff: noqa: F811
from uuid import uuid4

from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current, novel_generation  # noqa: F401
from tests.unit.test_genre_generation import plan


def test_auto_cast_reaches_author_review_in_five_existing_calls(novel_generation):
    client, control = novel_generation
    base, draft = create(
        client,
        workflow="novel-run-v1",
        character_selection="chief-auto-v1",
        character_ids=[],
        relationship_character_ids=[],
        pause_after_plan=False,
    )
    assert control["calls"] == []
    assert draft["snapshot"]["normal_calls"] == draft["snapshot"]["maximum_calls"] == 5
    selection = draft["snapshot"]["cast_selection"]
    assert len(selection["candidates"]) == 2 and selection["required_ids"] == []
    batch = start(client, base, draft)
    assert control["calls"] == ["plan", "write", "memory", "checker", "reader"], batch["state"]
    assert batch["status"] == "needs_attention"
    assert current(batch, "candidate")["payload"]["complete"] is True
    assert "candidate_pool" in control["requests"]["plan"]
    assert "selected_by_current_plan" in control["requests"]["write"]
    assert "cast_scope" not in control["requests"]["reader"]
    assert batch["spec"]["character_ids"] == []  # Frozen author input is not overwritten by Chief.
    assert read(client, base.replace("/generation-batches", "/chapters")) == []


def test_out_of_pool_chief_choice_saves_failure_and_author_can_correct_without_replanning(
    novel_generation,
):
    client, control = novel_generation
    control["plan_cast"] = [str(uuid4()), str(uuid4())]
    base, draft = create(
        client,
        workflow="novel-run-v1",
        character_selection="chief-auto-v1",
        character_ids=[],
        relationship_character_ids=[],
    )
    batch = start(client, base, draft)
    assert control["calls"] == ["plan"] and batch["next_action"] is None
    assert batch["calls"][0]["status"] == "local_failure"
    ids = [c["id"] for c in draft["snapshot"]["cast_selection"]["candidates"]]
    from tests.integration.support import headers

    corrected = client.put(
        f"{base}/{batch['id']}/plan",
        headers=headers(str(uuid4())),
        json={
            "plan": plan(ids),
            "expected_plan_sha256": None,
            "author_note": "只使用冻结候选中的两位正式人物",
        },
    )
    assert corrected.status_code == 200, corrected.text
    batch = start(client, base, corrected.json())
    assert control["calls"] == ["plan", "write", "memory", "checker", "reader"], batch["state"]


def test_new_field_does_not_change_manual_api_validation(novel_generation):
    client, _ = novel_generation
    base, draft = create(client, workflow="novel-run-v1")
    assert draft["spec"]["character_selection"] == "manual"
    invalid = post(client, base, {**draft["spec"],
        "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
         "character_ids": []})
    assert invalid.status_code == 422


def test_auto_cast_capacity_failure_cannot_authorize_or_call_provider(novel_generation):
    client, control = novel_generation
    base, draft = create(
        client,
        workflow="novel-run-v1",
        character_selection="chief-auto-v1",
        character_ids=[],
        relationship_character_ids=[],
    )
    small = post(client, base, {**draft["spec"],
        "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
         "input_limit": 8000})
    assert small.status_code == 200, small.text
    preview = small.json()
    assert preview["snapshot"]["blockers"]
    response = post(
        client,
        f"{base}/{preview['id']}/authorize",
        {
            "preview_sha256": preview["preview_sha256"],
            "confirmed": True,
        },
    )
    assert response.status_code == 409
    assert control["calls"] == []
