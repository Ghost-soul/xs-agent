# ruff: noqa: F811
import pytest

from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.creation import contract_for
from novel_writer.generation.schemas import FrozenGenerationSpec
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


def test_creative_requests_match_preview_bindings_and_use_all_completed_unit_handoffs(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(
        client,
        feedback_policy="advisory-v1",
        writing_policy="creative-v1",
        automation_policy="stage-auto-v1",
    )
    assert not draft["snapshot"]["blockers"]
    assert draft["snapshot"]["prompt_contract_sha256"] == contract_for(
        FrozenGenerationSpec.model_validate(draft["spec"])
    )
    batch = start(client, base, draft)
    assert control["calls"] == [
        "plan",
        "write:1",
        "memory:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
        "checker",
    ], batch["state"]
    assert all(call["status"] == "completed" for call in batch["calls"])
    for call in batch["calls"]:
        saved = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        actual = ModelRequest.model_validate(saved["model_request"])
        assert saved["input_tokens"] == input_tokens(request_preview(actual), saved["counting"])
        assert saved["feedback_options"]["writing_policy"] == "creative-v1"
    for n in (1, 2, 3):
        payload = control["requests"][f"write:{n}"]
        assert payload["effective_plan"] == current(batch, "plan")["payload"]
        assert payload["cards"] == draft["snapshot"]["cards"]
        assert payload["unit_position"]["last_unit"] == (n == 3)
        if n > 1:
            assert f"第{n - 1}次选择" in payload["already_written"]
            assert payload["candidate_handoff"]["position"]["recent_major_event"] == (
                f"完成memory:{n - 1}"
            )


@pytest.mark.parametrize("old_policy", ["legacy-v1", "creative-v1", "background-v1"])
def test_new_rewrite_binds_its_own_creative_policy_and_does_not_upgrade_source(
    feedback_run, old_policy
):
    client, control = feedback_run
    profile = client.app.state.provider_profile_store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    client.app.state.provider_profile_store.save(profile)
    base, draft = stage_create(
        client,
        feedback_policy="advisory-v1",
        writing_policy=old_policy,
        automation_policy="stage-auto-v1",
    )
    batch = start(client, base, draft)
    original = batch["spec"]
    route = f"{base}/{batch['id']}"
    preview = post(
        client,
        route + "/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "rewrite",
            "instruction": "保留人物声音，加强选择与回应",
            "max_cost_cny": "10",
            "enable_checker": False,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["writing_policy"] == "guided-v1"
    assert preview.json()["slots"] == ["rewrite", "memory_amend"]
    assert len(control["calls"]) == len(batch["calls"])
    response = post(
        client,
        route + "/amendment-authorize",
        {
            "preview_sha256": preview.json()["preview_sha256"],
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    done = settle(client, base, batch["id"])
    assert done["spec"] == original
    assert all(call["status"] == "completed" for call in done["calls"]), done["state"]
    rewrite = next(c for c in done["calls"] if c["action"] == "rewrite")
    rewrite = read(client, f"{route}/calls/{rewrite['id']}")
    assert rewrite["request"]["feedback_options"]["writing_policy"] == "guided-v1"
    assert "保留人物声音，加强选择与回应" in rewrite["request"]["model_request"]["user_prompt"]
    assert "unit_position" not in control["requests"]["rewrite"]
