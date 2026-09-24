# ruff: noqa: F811
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.schemas import FrozenGenerationSpec
from novel_writer.generation.world_context import POLICY, contract_for
from novel_writer.providers.base import ModelRequest
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle  # noqa: F401
from tests.integration.test_generation_feedback import feedback_run  # noqa: F401
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_genre_generation import generation, read, start  # noqa: F401


def test_world_budget_is_applied_to_actual_plan_writer_memory_and_checker_requests(feedback_run):
    client, control = feedback_run
    base, draft = stage_create(
        client,
        context_policy=POLICY,
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
        stored = read(client, f"{base}/{batch['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(stored["model_request"])
        assert stored["input_tokens"] == input_tokens(request_preview(request), stored["counting"])
        selection = control["requests"][call["action"]]["world_context_selection"]
        assert selection["policy"] == POLICY
        assert selection["material_count"] <= selection["limit"]
