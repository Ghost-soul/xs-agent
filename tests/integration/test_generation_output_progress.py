# ruff: noqa: F811
from copy import deepcopy

from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ProviderResponseError, ProviderTerminalMetadata, TokenUsage
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_longform import longform  # noqa: F401
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)


def test_failed_plan_has_clear_diagnostics_and_new_preview_needs_new_authorization(
    longform, monkeypatch
):
    client, control = longform
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    store.save(profile)
    original_dispatch = GenerationRuntime.dispatch
    requests = []

    async def capped(self, call_id, request, profile, spec, counting, api_key):
        requests.append(request.max_output_tokens)
        if len(requests) == 1:
            raise ProviderResponseError(
                "output limit",
                "immutable reasoning-only transport",
                code="token_limit_exceeded",
                extracted_content="",
                terminal=ProviderTerminalMetadata(
                    protocol="fixture", terminal_event_seen=True, finish_reason="length"
                ),
                usage=TokenUsage(
                    input_tokens=1000, output_tokens=6000, reasoning_tokens=6000, content_tokens=0
                ),
            )
        return await original_dispatch(self, call_id, request, profile, spec, counting, api_key)

    monkeypatch.setattr(GenerationRuntime, "dispatch", capped)
    base, original = create(
        client,
        workflow="novel-run-v1",
        stage_mode="longform-v1",
        unit_limit=2,
        chief_output_limit=6000,
    )
    failed = start(client, base, original)
    assert failed["status"] == "needs_attention" and failed["next_action"] is None
    assert failed["calls"][0]["diagnostic"]["visible_characters"] == 0
    advice = failed["plan_retry_preview"]
    assert advice["chief_output_limit"] == 100000
    old_response = deepcopy(read(client, f"{base}/{failed['id']}/calls/{failed['calls'][0]['id']}"))
    payload = {
        **failed["spec"],
            "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
            
        "chief_output_limit": advice["chief_output_limit"],
        "auxiliary_output_limit": advice["auxiliary_output_limit"],
        "writer_output_limit": advice["writer_output_limit"],
        "input_limit": advice["input_limit"],
    }
    response = post(client, base, payload)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["status"] == "draft" and preview["calls"] == [] and requests == [6000]
    assert preview["preview_sha256"] != failed["preview_sha256"]
    assert preview["spec"]["max_cost_cny"] == failed["spec"]["max_cost_cny"]
    assert all(m["output_limit"] == 100000 for m in preview["snapshot"]["action_models"].values())
    done = start(client, base, preview)
    assert requests[1] == 100000 and len(done["calls"]) > 1
    assert done["state"].get("candidate_id") and done["plan_retry_preview"] is None
    assert read(client, f"{base}/{failed['id']}/calls/{failed['calls'][0]['id']}") == old_response
    unchanged = read(client, f"{base}/{failed['id']}")
    assert unchanged["snapshot"] == failed["snapshot"] and unchanged["spec"] == failed["spec"]


def test_all_100000_limits_create_only_preview_and_keep_model_capability_checks(generation):
    client, control = generation
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    assert profile
    profile.models[0].context_window = 300000
    profile.models[0].max_output_tokens = 100000
    store.save(profile)
    base, preview = create(
        client,
        workflow="novel-run-v1",
        stage_mode="longform-v1",
        unit_limit=2,
        input_limit=100000,
        chief_output_limit=100000,
        auxiliary_output_limit=100000,
        writer_output_limit=100000,
        roles={
            r: {"model": "fixture-model", "output_limit": 100000}
            for r in ("memory", "checker", "reader", "editor")
        },
    )
    assert preview["status"] == "draft" and preview["calls"] == [] and control["calls"] == []
    assert all(v["output_limit"] == 100000 for v in preview["snapshot"]["action_models"].values())
    rejected = post(client, base, {**preview["spec"],
        "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
         "writer_output_limit": 100001})
    assert rejected.status_code == 422
    assert len(read(client, base)) == 1 and control["calls"] == []
    profile.models[0].max_output_tokens = 48000
    store.save(profile)
    blocked = post(client, base, {**preview["spec"], "feedback_policy": "logic-v1"})
    assert blocked.status_code == 400, blocked.text
    assert "超过模型能力" in blocked.json()["detail"]
    assert len(read(client, base)) == 1 and control["calls"] == []
