# ruff: noqa: F401 F811
"""Template lifecycle with database receipts and an entirely local provider substitute."""

import json
from uuid import UUID, uuid4

import pytest

from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation.budget import input_tokens, request_preview
from novel_writer.generation.content import paragraphs
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.generation.template_catalog import default_text
from novel_writer.providers.base import ModelRequest
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform, stage_create
from tests.integration.test_generation_memory_recovery import authorize as authorize_memory
from tests.integration.test_genre_generation import create, generation, post, read, start
from tests.integration.test_novel_run_rebuild import current, novel_generation
from tests.integration.test_role_context import OPTIONS as OLD_OPTIONS
from tests.integration.test_step_recovery import authorize, recovery

OPTIONS = {**OLD_OPTIONS, "context_policy": "chief-focus-v4", "narrative_policy": "plot-led-v3"}


def save(client, variant, marker, expected=None):
    template = default_text(variant).model_copy(update={"system_text": marker})
    response = client.put(
        "/api/prompt-templates/" + variant,
        headers=headers(),
        json={
            "expected_revision": expected or read(client, "/api/prompt-templates")["revision"],
            "template": template.model_dump(),
            "note": marker,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def adapt_fixture_provider(client, monkeypatch):
    """The existing fake reads JSON; adapt only its local input, never saved requests."""
    previous = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        async with self.database.session() as session:
            call = await session.get(GenerationCallRecord, call_id)
            source = call.request.get("prompt_template_source")
        if source is not None:
            request = request.model_copy(
                update={"user_prompt": json.dumps(source, ensure_ascii=False)}
            )
        return await previous(self, call_id, request, profile, spec, counting, api_key)

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)


def test_template_api_validation_history_conflict_and_preview_never_dispatches(automated):
    client, control = automated
    base, draft = stage_create(client, **OPTIONS)
    project_id = base.split("/")[3]
    before = read(client, f"{base}/{draft['id']}")
    assert client.get("/api/prompt-templates").status_code in {401, 403}
    initial = read(client, "/api/prompt-templates")
    assert len(initial["entries"]) == 7
    assert all(e["variant"] != "reader" for e in initial["entries"])
    bad = client.put(
        "/api/prompt-templates/memory",
        headers=headers(),
        json={
            "expected_revision": initial["revision"],
            "template": {"system_text": "x", "task_template": "无证据"},
        },
    )
    assert bad.status_code == 400
    first = save(client, "chief", "CUSTOM_CHIEF")
    stale = client.put(
        "/api/prompt-templates/chief",
        headers=headers(),
        json={
            "expected_revision": initial["revision"],
            "template": None,
        },
    )
    assert stale.status_code == 409
    version = read(client, f"/api/prompt-templates/versions/{first['revision']}/chief")
    assert version["text"]["system_text"] == "CUSTOM_CHIEF"
    preview = post(
        client,
        "/api/prompt-templates/preview",
        {
            "project_id": project_id,
            "batch_id": draft["id"],
            "variant": "chief",
            "template": version["text"],
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["system_prompt"].startswith("CUSTOM_CHIEF")
    assert preview.json()["input_count"] > 0 and not preview.json()["blockers"]
    assert "output_schema" in preview.json()["engine_contract"]
    assert control["calls"] == []
    assert read(client, f"{base}/{draft['id']}") == before
    unavailable = post(
        client,
        "/api/prompt-templates/preview",
        {
            "project_id": project_id,
            "batch_id": draft["id"],
            "variant": "writer",
        },
    )
    assert unavailable.status_code == 400
    wrong_project = post(
        client,
        "/api/prompt-templates/preview",
        {
            "project_id": str(uuid4()),
            "batch_id": draft["id"],
            "variant": "chief",
        },
    )
    assert wrong_project.status_code == 404
    reset = client.put(
        "/api/prompt-templates/chief",
        headers=headers(),
        json={
            "expected_revision": first["revision"],
            "template": None,
        },
    )
    assert reset.status_code == 200
    assert not reset.json()["entries"][0]["customized"]
    assert read(client, f"/api/prompt-templates/versions/{first['revision']}/chief") == version


def test_new_defaults_freeze_before_authorization_and_actual_requests_are_counted(
    automated, monkeypatch
):
    client, control = automated
    adapt_fixture_provider(client, monkeypatch)
    for variant in ("chief", "writer", "memory", "checker"):
        save(client, variant, "FROZEN_" + variant)
    base, draft = stage_create(client, unit_limit=2, **OPTIONS)
    revision = draft["snapshot"]["prompt_templates"]["revision"]
    save(client, "writer", "LATER_WRITER")
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2", "checker"]
    for call in done["calls"]:
        saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        request = ModelRequest.model_validate(saved["model_request"])
        assert request.system_prompt.startswith("FROZEN_")
        assert saved["prompt_template_revision"] == revision
        assert saved["input_tokens"] == input_tokens(request_preview(request), saved["counting"])
        assert saved["prompt_template_source"]
        if call["action"] == "plan":
            assert saved["input_tokens"] == draft["snapshot"]["plan_input_tokens"]
    assert done["snapshot"] == draft["snapshot"]
    preview = post(
        client,
        "/api/prompt-templates/preview",
        {
            "project_id": base.split("/")[3],
            "batch_id": done["id"],
            "variant": "writer",
            "template": default_text("writer")
            .model_copy(update={"system_text": "LOCAL_PREVIEW"})
            .model_dump(),
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_action"] == "write:2"
    assert preview.json()["system_prompt"].startswith("LOCAL_PREVIEW")
    assert len(control["calls"]) == 6


def test_existing_preview_never_adopts_later_default(automated):
    client, control = automated
    base, draft = stage_create(client, unit_limit=1, **OPTIONS)
    assert "prompt_templates" not in draft["snapshot"]
    save(client, "chief", "UNRELATED_NEW_DEFAULT")
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    saved = read(client, f"{base}/{done['id']}/calls/{done['calls'][0]['id']}")
    assert "UNRELATED_NEW_DEFAULT" not in str(saved["request"])


def test_failed_writer_replay_keeps_exact_template_after_defaults_change(recovery, monkeypatch):
    client, control = recovery
    adapt_fixture_provider(client, monkeypatch)
    save(client, "writer", "WRITER_AT_FREEZE")
    control["retry_failure_action"] = "write:1"
    base, draft = stage_create(client, unit_limit=1, **OPTIONS)
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    saved = read(client, target + f"/calls/{before['calls'][-1]['id']}")["request"]
    save(client, "writer", "CHANGED_DURING_PAUSE")
    preview = read(client, target + "/step-recovery-preview")
    del control["retry_failure_action"]
    control["pause_action"] = "write:1"
    result = authorize(client, target, preview)
    assert result.status_code == 200, result.text
    done = settle(client, base, before["id"])
    retry = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert retry["model_request"] == saved["model_request"]
    assert control["calls"].count("plan") == 1


def test_memory_output_recovery_keeps_frozen_template_and_evidence(automated, monkeypatch):
    client, control = automated
    adapt_fixture_provider(client, monkeypatch)
    save(client, "memory", "ORIGINAL_MEMORY")
    control["incomplete_action"] = "memory:1"
    base, draft = stage_create(client, unit_limit=1, auxiliary_output_limit=6000, **OPTIONS)
    before = start(client, base, draft)
    assert before["memory_recovery_available"], before["state"]
    target = f"{base}/{before['id']}"
    first = read(client, target + f"/calls/{before['calls'][-1]['id']}")["request"]
    save(client, "memory", "LATER_MEMORY")
    preview = read(client, target + "/memory-recovery-preview?output_limit=24000&all_roles=false")
    assert not preview["blockers"]
    del control["incomplete_action"]
    control["pause_action"] = "memory:1"
    result = authorize_memory(client, target, preview)
    assert result.status_code == 200, result.text
    done = settle(client, base, before["id"])
    retry = read(client, target + f"/calls/{done['calls'][-1]['id']}")["request"]
    assert retry["model_request"]["system_prompt"] == first["model_request"]["system_prompt"]
    assert retry["model_request"]["user_prompt"] == first["model_request"]["user_prompt"]
    assert current(done, "candidate") == current(before, "candidate")


@pytest.mark.parametrize("mode", ["rewrite", "verify"])
def test_independent_revision_freezes_own_defaults(automated, monkeypatch, mode):
    client, control = automated
    adapt_fixture_provider(client, monkeypatch)
    save(client, "memory", "STAGE_MEMORY")
    base, draft = stage_create(client, unit_limit=1, **OPTIONS)
    before = start(client, base, draft)
    target = f"{base}/{before['id']}"
    save(client, "memory", "AMENDMENT_MEMORY")
    save(client, "rewrite", "INDEPENDENT_REWRITE")
    preview = post(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": current(before, "candidate")["sha256"],
            "mode": mode,
            "instruction": "保留事实，调整语气",
            "max_cost_cny": "1",
            "output_limit": 12000,
            "enable_checker": True,
        },
    )
    assert preview.status_code == 200, preview.text
    saved_revision = preview.json()["prompt_templates"]["revision"]
    save(client, "memory", "SAVED_AFTER_AMENDMENT_PREVIEW")
    result = post(
        client,
        target + "/amendment-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert result.status_code == 200, result.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    call = next(c for c in done["calls"] if c["action"] == "memory_amend")
    saved = read(client, target + f"/calls/{call['id']}")["request"]
    assert saved["model_request"]["system_prompt"].startswith("AMENDMENT_MEMORY")
    assert saved["prompt_template_revision"] == saved_revision
    assert done["snapshot"] == before["snapshot"]


def test_single_unit_local_editor_preserves_protected_paragraphs(novel_generation, monkeypatch):
    client, control = novel_generation
    adapt_fixture_provider(client, monkeypatch)
    for variant in ("chief", "writer", "editor", "memory"):
        save(client, variant, "SINGLE_" + variant)
    base, draft = create(client, workflow="novel-run-v1", **OPTIONS)
    before = start(client, base, draft)
    assert all(c["status"] == "completed" for c in before["calls"]), before["state"]
    target = f"{base}/{before['id']}"
    candidate = current(before, "candidate")
    entries = paragraphs(candidate["payload"]["body"])
    preview = post(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": candidate["sha256"],
            "mode": "local",
            "instruction": "仅调整第一段的语气",
            "paragraph_ids": [entries[0]["id"]],
            "protected_paragraph_ids": [entries[-1]["id"]],
            "max_cost_cny": "1",
            "output_limit": 12000,
            "enable_checker": True,
        },
    )
    assert preview.status_code == 200, preview.text
    response = post(
        client,
        target + "/amendment-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    call = next(c for c in done["calls"] if c["action"] == "amend")
    saved = read(client, target + f"/calls/{call['id']}")["request"]
    assert saved["model_request"]["system_prompt"].startswith("SINGLE_editor")
    assert saved["prompt_template_source"]["scope"]["protected_paragraph_ids"] == [
        entries[-1]["id"]
    ]
    assert entries[-1]["text"] in current(done, "candidate")["payload"]["body"]


def test_reset_default_before_amendment_does_not_inherit_original_template(automated, monkeypatch):
    client, control = automated
    adapt_fixture_provider(client, monkeypatch)
    first = save(client, "memory", "OLD_STAGE_MEMORY")
    base, draft = stage_create(client, unit_limit=1, **OPTIONS)
    before = start(client, base, draft)
    reset = client.put(
        "/api/prompt-templates/memory",
        headers=headers(),
        json={
            "expected_revision": first["revision"],
            "template": None,
        },
    )
    assert reset.status_code == 200
    target = f"{base}/{before['id']}"
    preview = post(
        client,
        target + "/amendment-preview",
        {
            "candidate_sha256": current(before, "candidate")["sha256"],
            "mode": "verify",
            "instruction": "重新核对事实",
            "output_limit": 12000,
            "max_cost_cny": "1",
            "enable_checker": True,
        },
    )
    assert preview.status_code == 200, preview.text
    assert "prompt_templates" not in preview.json()
    response = post(
        client,
        target + "/amendment-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert response.status_code == 200, response.text
    done = settle(client, base, before["id"])
    call = next(c for c in done["calls"] if c["action"] == "memory_amend")
    saved = read(client, target + f"/calls/{call['id']}")["request"]
    assert saved["model_request"]["system_prompt"] == default_text("memory").system_text
    assert "prompt_template_revision" not in saved
