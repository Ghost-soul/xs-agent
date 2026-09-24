# ruff: noqa: F811
from decimal import Decimal
from uuid import UUID

import pytest

from novel_writer.db.models import GenerationArtifactRecord, GenerationCallRecord
from novel_writer.generation.content import fingerprint
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.legacy_generation_fixture import saved_amendment_fixture
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import settle
from tests.integration.test_generation_longform import longform, stage_create  # noqa: F401
from tests.integration.test_generation_memory_recovery import authorize
from tests.integration.test_genre_generation import generation, post, read, start  # noqa: F401
from tests.integration.test_novel_run_rebuild import current


@pytest.fixture
def capable(longform):
    client, control = longform
    store = client.app.state.provider_profile_store
    profile = store.get("fixture")
    profile.models[0].max_output_tokens = 100000
    profile.models[0].context_window = 300000
    profile.models[0].input_price_cny_per_million = Decimal(1)
    profile.models[0].output_price_cny_per_million = Decimal(9)
    store.save(profile)
    return client, control


def amendment(client, target, batch, **changes):
    return saved_amendment_fixture(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "feedback_policy": "legacy-v1",
            "instruction": "核验当前完整稿",
            "max_cost_cny": "5",
            **changes,
        },
    )


def approve_amendment(client, target, sha):
    response = post(
        client,
        target + "/amendment-authorize",
        {
            "preview_sha256": sha,
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text


def test_new_amendment_defaults_bind_preview_cost_and_dispatched_limits(capable):
    client, control = capable
    base, draft = stage_create(client, max_cost_cny="20")
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    count = len(control["calls"])
    blocked = amendment(client, target, before, max_cost_cny="1")
    assert blocked.status_code == 400, blocked.text
    response = amendment(client, target, before)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["input_limit"] == 200000
    assert preview["output_limit"] == 100000
    assert Decimal(preview["maximum_cost_cny"]) == Decimal("3.3")
    assert len(control["calls"]) == count
    approve_amendment(client, target, preview["preview_sha256"])
    done = settle(client, base, before["id"])
    assert control["calls"][count:] == ["memory_amend", "checker_amend", "reader_amend"]
    assert current(done, "review")
    assert done["spec"] == before["spec"] and done["snapshot"] == before["snapshot"]
    for call in done["calls"]:
        if call["action"].endswith("_amend"):
            request = read(client, target + f"/calls/{call['id']}")["request"]
            assert request["effective_input_limit"] == 200000
            assert request["model_request"]["max_output_tokens"] == 100000
            assert request["amendment_authorized_sha256"] == preview["preview_sha256"]


def test_default_amendment_refuses_model_that_cannot_supply_100k(longform):
    client, control = longform
    base, draft = stage_create(client)
    batch = start(client, base, draft)
    count = len(control["calls"])
    response = amendment(client, f"{base}/{batch['id']}", batch)
    assert response.status_code == 400 and "模型能力" in response.text
    assert len(control["calls"]) == count


@pytest.fixture
def legacy_amendment(capable):
    client, control = capable
    control["incomplete_action"] = "memory:2"
    base, draft = stage_create(client, max_cost_cny="20")
    batch = start(client, base, draft)
    target = f"{base}/{batch['id']}"
    response = amendment(client, target, batch, output_limit=6000)
    assert response.status_code == 200, response.text

    # Reconstruct a pre-fix authorization in the dedicated test database only.
    async def legacy_payload():
        async with client.app.state.database.session() as session, session.begin():
            from sqlalchemy import select

            artifact = await session.scalar(
                select(GenerationArtifactRecord).where(
                    GenerationArtifactRecord.batch_id == UUID(batch["id"]),
                    GenerationArtifactRecord.kind == "amendment",
                )
            )
            payload = dict(artifact.payload)
            payload.pop("input_limit")
            payload.pop("output_limit")
            payload["request"] = {
                k: v
                for k, v in payload["request"].items()
                if k not in {"input_limit", "output_limit"}
            }
            artifact.payload = payload
            artifact.sha256 = fingerprint(payload)
            return artifact.sha256

    sha = client.portal.call(legacy_payload)
    control["incomplete_action"] = "memory_amend"
    approve_amendment(client, target, sha)
    failed = settle(client, base, batch["id"])
    assert len([c for c in failed["calls"] if c["status"] == "local_failure"]) == 2
    assert failed["memory_recovery_available"]
    last = next(c for c in failed["calls"] if c["action"] == "memory_amend")
    assert last["diagnostic"]["output_limit"] == 6000
    return client, control, base, failed


def test_legacy_amendment_recovery_uses_only_its_remaining_scope_and_preserves_history(
    legacy_amendment,
):
    client, control, base, before = legacy_amendment
    target = f"{base}/{before['id']}"
    calls = {c["id"]: read(client, target + f"/calls/{c['id']}") for c in before["calls"]}
    count = len(control["calls"])
    preview = read(client, target + "/memory-recovery-preview")
    assert read(client, target) == before
    assert preview["scope"] == "amendment"
    assert preview["replacement_slot"] == 300
    assert preview["remaining_slots"] == ["memory_amend", "checker_amend", "reader_amend"]
    assert preview["input_limit"] == 200000
    assert preview["output_limit"] == 100000
    assert preview["previous_output_limit"] == 6000
    assert Decimal(preview["max_cost_cny"]) == 5
    assert Decimal(preview["remaining_cost_upper_cny"]) == Decimal("3.3")
    failed_call = next(c for c in before["calls"] if c["id"] == preview["failed_call_id"])
    assert Decimal(preview["spent_cost_cny"]) == Decimal(failed_call["actual_cost_cny"])
    assert not preview["blockers"]
    assert authorize(client, target, {**preview, "max_cost_cny": "6"}).status_code == 409
    del control["incomplete_action"]
    response = authorize(client, target, preview)
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert control["calls"][count:] == ["memory_amend", "checker_amend", "reader_amend"]
    assert done["next_action"] is None and current(done, "review")
    assert current(done, "candidate") == current(before, "candidate")
    assert current(done, "plan") == current(before, "plan")
    for field in ("spec", "snapshot", "preview_sha256"):
        assert done[field] == before[field]
    for call in done["calls"]:
        detail = read(client, target + f"/calls/{call['id']}")
        if call["id"] in calls:
            assert detail == calls[call["id"]]
        else:
            assert detail["request"]["effective_input_limit"] == 200000
            assert detail["request"]["model_request"]["max_output_tokens"] == 100000
    assert authorize(client, target, preview).status_code == 409
    assert not done["memory_recovery_available"]


@pytest.mark.parametrize("failure", ["length", "parse", "unknown"])
def test_amendment_replacement_failure_pauses_without_loop(legacy_amendment, monkeypatch, failure):
    client, control, base, batch = legacy_amendment
    target = f"{base}/{batch['id']}"
    count = len(batch["calls"])
    preview = read(client, target + "/memory-recovery-preview")
    if failure == "parse":
        del control["incomplete_action"]
        control["fail_action"] = "memory_amend"
    if failure == "unknown":

        async def timeout(*args):
            raise TimeoutError("fixture")

        monkeypatch.setattr(GenerationRuntime, "dispatch", timeout)
    assert authorize(client, target, preview).status_code == 200
    done = settle(client, base, batch["id"])
    assert len(done["calls"]) == count + 1
    assert not done["memory_recovery_available"] and done["next_action"] is None
    assert "reader_amend" not in control["calls"]
    assert authorize(client, target, preview).status_code == 409


@pytest.mark.parametrize("when", ["before", "after"])
def test_amendment_recovery_pause_resumes_authorized_slots_only(
    legacy_amendment, monkeypatch, when
):
    client, control, base, batch = legacy_amendment
    target = f"{base}/{batch['id']}"
    count = len(control["calls"])
    runtime = client.app.state.generation
    original_start = runtime.start
    if when == "before":
        monkeypatch.setattr(runtime, "start", lambda *_: None)
    else:
        control["pause_action"] = "memory_amend"
    del control["incomplete_action"]
    preview = read(client, target + "/memory-recovery-preview")
    assert authorize(client, target, preview).status_code == 200
    if when == "before":
        assert post(client, target + "/pause", {"confirmed": True}).status_code == 200
        client.portal.call(runtime.run, UUID(batch["id"]))
    paused = settle(client, base, batch["id"])
    assert paused["status"] == "paused"
    assert paused["next_action"] == ("memory_amend" if when == "before" else "checker_amend")
    monkeypatch.setattr(runtime, "start", original_start)
    control.pop("pause_action", None)
    done = start(client, base, paused)
    assert control["calls"][count:] == ["memory_amend", "checker_amend", "reader_amend"]
    assert current(done, "review")


@pytest.mark.parametrize("change", ["source", "unknown", "response"])
def test_amendment_recovery_rejects_stale_sources_or_unknown_calls(legacy_amendment, change):
    client, _, base, batch = legacy_amendment
    target = f"{base}/{batch['id']}"
    failed = next(c for c in batch["calls"] if c["action"] == "memory_amend")

    async def mutate():
        async with client.app.state.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, UUID(failed["id"]))
            if change == "source":
                call.request = {**call.request, "unit_chain_sha256": "changed"}
            elif change == "unknown":
                call.status = "outcome_uncertain"
            else:
                call.response = {**call.response, "sha256": "changed"}

    client.portal.call(mutate)
    assert client.get(target + "/memory-recovery-preview", headers=headers()).status_code == 409
