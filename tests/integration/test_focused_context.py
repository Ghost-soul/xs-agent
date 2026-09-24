# ruff: noqa: F811
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.focused_context import contract_for
from novel_writer.generation.schemas import FrozenGenerationSpec
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, read, start  # noqa: F401


def test_actual_role_requests_use_focused_policy_and_200k_without_changing_frozen_sources(
    feedback_run,
):
    client, control = feedback_run
    base, draft = stage_create(
        client,
        context_policy="focused-v1",
        input_limit=200000,
        writing_policy="guided-v1",
        feedback_policy="logic-v1",
        automation_policy="stage-auto-v1",
    )
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
    assert batch["snapshot"] == draft["snapshot"]
    for call in batch["calls"]:
        saved = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        actual = ModelRequest.model_validate(saved["model_request"])
        assert saved["effective_input_limit"] == 200000
        assert saved["input_tokens"] == input_tokens(request_preview(actual), saved["counting"])
        value = control["requests"][call["action"]]
        assert value["context_selection"]["policy"] == "focused-v1"
    for n in (1, 2, 3):
        value = control["requests"][f"write:{n}"]
        assert "cards" not in value and "background_cards" not in value
        if n > 1:
            assert f"第{n - 1}次选择" in value["already_written"]
            assert (
                value["candidate_handoff"]["position"]["recent_major_event"]
                == f"完成memory:{n - 1}"
            )
