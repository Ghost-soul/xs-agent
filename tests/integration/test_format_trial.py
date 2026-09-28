# ruff: noqa: F401 F811
"""Unchecked formatted outputs stay in the draft lane; no real model requests."""

import pytest
from sqlalchemy import text

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    StateDeltaRecord,
    StateVersionRecord,
)
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft
from tests.integration.test_step_recovery import authorize


@pytest.fixture
def unchecked(craft, monkeypatch):
    client, control = craft
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        result = await original(self, call_id, request, profile, spec, counting, api_key)
        action = control["calls"][-1]
        control.setdefault("systems", {})[action] = request.system_prompt
        if action == "plan":
            result = result.model_copy(
                update={
                    "text": control.get(
                        "chief_text",
                        ('{"scenes":[{"event":"渡口的选择"},{"event":"完成渡河"}],"extra":true}'),
                    )
                }
            )
        elif action.startswith("memory") or action == "checker":
            # Non-JSON and missing domain fields must both be accepted.
            result = result.model_copy(
                update={"text": control.get("note_text", "未校验笔记：选择已改变。")}
            )
        return result

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def create_trial(client, **changes):
    base, draft = create_craft(client, **changes)
    response = post(client, base + "/trial-preview?random_narratives=false", draft["spec"])
    assert response.status_code == 200, response.text
    return base, response.json()


def formal_rows(client, project_id):
    async def capture():
        result = {}
        async with client.app.state.generation.database.session() as session:
            for model in (
                StateVersionRecord,
                StateDeltaRecord,
                ChapterRecord,
                ChapterRevisionRecord,
            ):
                name = model.__tablename__
                rows = await session.scalars(text(f'SELECT to_jsonb(t)::text FROM "{name}" t'))
                result[name] = sorted(rows.all())
        return result

    return client.portal.call(capture)


@pytest.mark.parametrize(
    "chief",
    [
        '{"scenes":[{"event":"渡口的选择"},{"event":"完成渡河"}],"extra":true}',
        '{"scenes":[{"event":"未闭合的设计',
        '{"scenes":[null, {"character_ids":123,"size_weight":false}]}',
    ],
)
def test_format_requested_but_malformed_results_do_not_block(unchecked, chief):
    client, control = unchecked
    control.update(chief_text=chief, unit_size=602)
    base, draft = create_trial(client, enable_checker=True)
    before = read(client, base + "/setup")
    before_rows = formal_rows(client, base.split("/")[3])
    done = start(client, base, draft)
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"][-1] == "checker"
    plan = current(done, "plan")["payload"]
    assert plan["raw_response"] == chief
    count = plan["trial_schedule"]["scheduled_units"]
    assert count == (5 if chief.endswith("设计") else 2)
    assert len(control["calls"]) == 2 + count * 2
    assert not {a["kind"] for a in done["artifacts"]} & {"memory", "handoff", "checker", "adoption"}
    notes = [a for a in done["artifacts"] if a["kind"] == "trial_note"]
    assert len(notes) == count and all(a["payload"]["validated"] is False for a in notes)
    assert "output_schema" in control["requests"]["plan"]
    assert "【结构化交付】" in control["systems"]["plan"]
    assert "output_schema" in control["requests"]["memory:1"]
    second = control["requests"]["write:2"]
    assert second["format_trial"]["chief_response"] == chief
    assert second["format_trial"]["continuity_notes"][0]["text"] == "未校验笔记：选择已改变。"
    assert second["continuity"]["source"] == "trial-candidate-unvalidated"
    assert "第1次选择" in second["continuity"]["recent_prose"]["text"]
    assert "role_working_state" not in second and "candidate_working_reference" not in second
    assert read(client, base + "/setup") == before
    assert formal_rows(client, base.split("/")[3]) == before_rows
    assert done["status"] == "needs_attention" and done["next_action"] is None
    route = f"{base}/{done['id']}"
    for endpoint in (
        "adoption-preview",
        "adopt",
        "stage-adoption-preview",
        "stage-adopt",
        "amendment-preview",
        "amendment-authorize",
        "continue-stage",
        "chapter-arrangement",
        "memory-recovery-authorize",
    ):
        rejected = post(client, route + "/" + endpoint, {})
        assert rejected.status_code == 409, (endpoint, rejected.text)
        assert "试验" in rejected.text or "跳过本地格式校验" in rejected.text
    assert read(client, base + "/setup") == before


def test_standard_mode_still_rejects_the_same_missing_fields(unchecked):
    client, control = unchecked
    base, draft = create_craft(client)
    result = start(client, base, draft)
    assert control["calls"] == ["plan"]
    assert result["calls"][0]["status"] == "local_failure"


def test_incomplete_writer_preserved_and_exact_recovery_does_not_duplicate(unchecked):
    client, control = unchecked
    control.update(incomplete_action="write:2", unit_size=602)
    base, draft = create_trial(client)
    failed = start(client, base, draft)
    target = f"{base}/{failed['id']}"
    assert failed["calls"][-1]["status"] == "local_failure"
    old_units = current(failed, "units")["payload"]["items"]
    old_body = current(failed, "candidate")["payload"]["body"]
    assert old_units[-1]["complete"] is False
    receipt = read(client, target + f"/calls/{failed['calls'][-1]['id']}")
    preview = read(client, target + "/step-recovery-preview")
    assert not preview["blockers"], preview
    del control["incomplete_action"]
    response = authorize(client, target, preview, uncertain=False)
    assert response.status_code == 200, response.text
    done = settle(client, base, failed["id"])
    assert done["next_action"] is None and done["state"]["units_finished"], done["state"]
    assert current(done, "candidate")["payload"]["body"] == old_body
    assert current(done, "units")["payload"]["items"][0] == old_units[0]
    assert control["calls"].count("write:2") == 2 and control["calls"].count("memory:1") == 1
    original_after = read(client, target + f"/calls/{failed['calls'][-1]['id']}")
    assert receipt["request"] == original_after["request"]
    assert receipt["response"] == original_after["response"]
    replay = next(
        c for c in done["calls"] if c.get("retry_of_call_id") == failed["calls"][-1]["id"]
    )
    recovered = read(client, target + f"/calls/{replay['id']}")
    assert recovered["request"]["model_request"] == receipt["request"]["model_request"]


@pytest.mark.parametrize("action", ["plan", "memory:1"])
def test_empty_response_is_not_a_format_error_and_stops(unchecked, action):
    client, control = unchecked
    control["chief_text" if action == "plan" else "note_text"] = "  "
    base, draft = create_trial(client)
    failed = start(client, base, draft)
    assert failed["calls"][-1]["action"] == action
    assert failed["calls"][-1]["status"] == "local_failure"
    assert "没有返回可见内容" in failed["state"]["message"]
    assert not failed["memory_recovery_available"]


def test_pause_and_resume_stays_in_trial_and_no_facts_are_compiled(unchecked):
    client, control = unchecked
    base, draft = create_trial(client, pause_after_plan=True)
    paused = start(client, base, draft)
    assert paused["status"] == "awaiting_plan" and paused["next_action"] == "write:1"
    done = start(client, base, paused)
    assert done["state"]["units_finished"] and control["calls"].count("plan") == 1
    assert not {a["kind"] for a in done["artifacts"]} & {"handoff", "memory"}


def test_custom_templates_and_preview_keep_the_actual_trial_sources(unchecked, monkeypatch):
    from tests.integration.test_prompt_templates import adapt_fixture_provider, save

    client, control = unchecked
    for role in ("chief", "writer", "memory", "checker"):
        save(client, role, f"AUTHOR_{role}_KEEP")
    adapt_fixture_provider(client, monkeypatch)
    base, draft = create_trial(client, enable_checker=True)
    chief_preview = post(
        client,
        "/api/prompt-templates/preview",
        {
            "project_id": base.split("/")[3],
            "batch_id": draft["id"],
            "variant": "chief",
        },
    )
    assert chief_preview.status_code == 200, chief_preview.text
    assert "【格式要求保留的试验】" in chief_preview.json()["system_prompt"]
    done = start(client, base, draft)
    assert done["state"]["units_finished"], done["state"]
    for action, marker in (
        ("plan", "chief"),
        ("write:2", "writer"),
        ("memory:1", "memory"),
        ("checker", "checker"),
    ):
        call = next(c for c in done["calls"] if c["action"] == action)
        saved = read(client, f"{base}/{done['id']}/calls/{call['id']}")["request"]
        assert saved["model_request"]["system_prompt"].startswith(f"AUTHOR_{marker}_KEEP")
        assert "【试验资料与来源边界】" in saved["model_request"]["user_prompt"]
        assert saved["format_trial_contract"] == draft["snapshot"]["format_trial_contract"]
        if marker == "writer":
            assert "未校验笔记：选择已改变。" in saved["model_request"]["user_prompt"]
            assert "第1次选择" in saved["model_request"]["user_prompt"]
            preview = post(
                client,
                "/api/prompt-templates/preview",
                {
                    "project_id": base.split("/")[3],
                    "batch_id": draft["id"],
                    "variant": "writer",
                },
            )
            assert preview.status_code == 200, preview.text
            assert (
                preview.json()["source_bindings"]["format_trial"] == saved["format_trial_contract"]
            )
            assert "未校验笔记：选择已改变。" in preview.json()["task_prompt"]


def test_trial_idempotency_does_not_reuse_normal_preview(unchecked):
    client, control = unchecked
    base, source = create_craft(client)
    standard = post(client, base, source["spec"], "same-format-request")
    assert standard.status_code == 200
    collision = post(client, base + "/trial-preview", source["spec"], "same-format-request")
    assert collision.status_code == 409
    first = post(client, base + "/trial-preview", source["spec"], "same-trial-request")
    second = post(client, base + "/trial-preview", source["spec"], "same-trial-request")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["snapshot"]["narrative_selection_policy"] == "random-two-v1"
    assert len(first.json()["spec"]["narrative_card_ids"]) == 2
    assert control["calls"] == []


def test_input_reauthorization_keeps_trial_notes_and_only_runs_remaining_slots(unchecked):
    client, control = unchecked
    control["pause_action"] = "memory:1"
    base, draft = create_trial(client)
    paused = start(client, base, draft)
    target = f"{base}/{paused['id']}"
    preview = read(client, target + "/input-preview?all_roles=false&output_limit=4000")
    assert not preview["blockers"], preview
    assert preview["action"] == "write:2"
    del control["pause_action"]
    response = post(
        client,
        target + "/input-authorize",
        {
            "confirmed": True,
            "preview_sha256": preview["preview_sha256"],
            "all_roles": False,
            "output_limit": 4000,
        },
    )
    assert response.status_code == 200, response.text
    done = settle(client, base, draft["id"])
    assert done["state"]["units_finished"], done["state"]
    assert control["calls"] == ["plan", "write:1", "memory:1", "write:2", "memory:2"]
    assert control["requests"]["write:2"]["format_trial"]["continuity_notes"]
    assert (
        current(done, "units")["payload"]["items"][0]
        == current(paused, "units")["payload"]["items"][0]
    )
