# ruff: noqa: F401 F811
"""Regression checks for real incident shapes, using only the fake provider."""

import pytest

from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_format_trial import create_trial, formal_rows
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft


@pytest.mark.parametrize("trial", [False, True])
@pytest.mark.parametrize("refused_action", ["plan", "write:1", "memory:1", "write:2"])
def test_refusal_preserved_but_never_becomes_plan_prose_or_memory(
    craft, monkeypatch, trial, refused_action,
):
    client, control = craft
    original = GenerationRuntime.dispatch
    refusal = "**I must decline to generate this content.** The request cannot be fulfilled."

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        result = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == refused_action:
            return result.model_copy(update={"text": refusal})
        return result

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    base, draft = (create_trial if trial else create_craft)(client)
    before = formal_rows(client, base.split("/")[3])
    failed = start(client, base, draft)
    assert control["calls"][-1] == refused_action
    assert failed["status"] == "needs_attention" and failed["next_action"] is None
    call = failed["calls"][-1]
    assert call["status"] == "local_failure" and call["error_code"] == "provider_refusal"
    assert not call["can_revalidate"]
    saved = read(client, f"{base}/{failed['id']}/calls/{call['id']}")
    assert saved["response"]["text"] == refusal and saved["response"]["sha256"]
    for artifact in failed["artifacts"]:
        if artifact["kind"] in {"candidate", "plan", "trial_note", "memory", "handoff"}:
            assert refusal not in str(artifact["payload"])
    if refused_action in {"memory:1", "write:2"}:
        assert current(failed, "candidate")["payload"]["body"]
    assert formal_rows(client, base.split("/")[3]) == before
    expected_count = {"plan": 1, "write:1": 2, "memory:1": 3, "write:2": 4}[refused_action]
    assert len(control["calls"]) == expected_count
