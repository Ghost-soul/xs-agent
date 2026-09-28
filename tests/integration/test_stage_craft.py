# ruff: noqa: F401 F811
"""Real PostgreSQL workflows, deterministic provider responses, no paid requests."""

import json
from copy import deepcopy

import pytest

from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import approval_data, longform, stage_create
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current

OPTIONS = dict(
    workflow="novel-run-v1",
    automation_policy="stage-auto-v1",
    feedback_policy="logic-v1",
    context_policy="chief-focus-v4",
    writing_policy="guided-v1",
    narrative_policy="plot-led-v3",
    length_policy="unit-v1",
    plan_policy="bounded-v1",
    card_selection_policy="separate-v1",
    enable_reader=False,
    enable_checker=False,
    focus_card_id="western_fantasy_dnd",
    narrative_card_ids=["girls_love_gl"],
)


def create_craft(client, **changes):
    options = {k: v for k, v in OPTIONS.items() if k != "workflow"}
    base, old = stage_create(client, unit_limit=5, **options)
    spec = {**old["spec"], "craft_policy": "stage-craft-v1", **changes}
    response = post(client, base, spec)
    assert response.status_code == 200, response.text
    return base, response.json()


@pytest.fixture
def craft(automated, monkeypatch):
    client, control = automated
    original = GenerationRuntime.dispatch

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        response = await original(self, call_id, request, profile, spec, counting, api_key)
        if control["calls"][-1] == "plan":
            plan = json.loads(response.text)
            count = control.get("plan_count", 5)
            plan["scenes"] = [deepcopy(plan["scenes"][0]) for _ in range(count)]
            for scene in plan["scenes"]:
                scene["development"] = {
                    "onstage_process": "尝试、对方回应、调整、承担后果",
                    "local_freedom": "自主安排现场办法",
                }
            response = response.model_copy(update={"text": json_text(plan)})
        return response

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


@pytest.mark.parametrize("count", [4, 5, 2, 6])
def test_scope_completion_and_short_body_are_not_transport_failures(craft, count):
    client, control = craft
    control.update(plan_count=count, unit_size=1602)
    base, draft = create_craft(client)
    done = start(client, base, draft)
    assert current(done, "plan")["payload"]["scenes"][0]["development"]["onstage_process"]
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert control["calls"].count("plan") == 1
    if count in (2, 6):
        assert done["status"] == "awaiting_plan"
        assert done["next_action"] is None
        assert control["calls"] == ["plan"]
        assert done["state"]["plan_scope_discrepancy"]["planned"] == count
        return
    assert len(current(done, "units")["payload"]["items"]) == count
    assert done["state"]["units_finished"]
    assert done["state"]["stage_scale_status"]["status"] == "below"
    assert done["state"]["stage_scale_status"]["characters"] == count * 1602
    assert done["state"]["stage_scale_status"]["deficit"] == 15000 - count * 1602
    assert len(control["calls"]) == 1 + count * 2
    for ordinal in range(2, count + 1):
        prose = control["requests"][f"write:{ordinal}"]["continuity"]["recent_prose"]
        assert prose["complete"]
        assert f"第{ordinal - 1}次选择" in prose["text"]


def test_accept_smaller_plan_repreviews_remaining_request_and_rejects_stale_confirmation(craft):
    client, control = craft
    control.update(plan_count=2, unit_size=600)
    base, draft = create_craft(client)
    paused = start(client, base, draft)
    plan = current(paused, "plan")
    route = f"{base}/{paused['id']}"
    response = client.put(
        route + "/plan",
        json={
            "plan": plan["payload"],
            "expected_plan_sha256": plan["sha256"],
            "author_note": "本阶段接受两个单元，充分展开现有事件。",
        },
        headers=headers("accept-two"),
    )
    assert response.status_code == 200, response.text
    adjusted = response.json()
    assert current(adjusted, "plan_adjustment")["payload"]["input_count"] > 0
    assert "plan_scope_discrepancy" not in adjusted["state"]
    denied = post(
        client,
        route + "/authorize",
        {"preview_sha256": adjusted["preview_sha256"], "confirmed": True},
    )
    assert denied.status_code == 409
    allowed = post(
        client,
        route + "/continue-stage",
        {
            "preview_sha256": adjusted["preview_sha256"],
            "expected_plan_sha256": current(adjusted, "plan")["sha256"],
            "expected_adjustment_sha256": current(adjusted, "plan_adjustment")["sha256"],
            "expected_questions_sha256": current(adjusted, "questions")["sha256"],
            "confirmed": True,
        },
    )
    assert allowed.status_code == 200, allowed.text
    done = settle(client, base, adjusted["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert len(control["calls"]) == 5


def test_split_then_merge_preserves_body_evidence_and_refuses_old_manifest(craft):
    client, control = craft
    control.update(unit_size=602)
    base, draft = create_craft(client)
    done = start(client, base, draft)
    route = f"{base}/{done['id']}"
    candidate = current(done, "candidate")
    manifest = current(done, "segments")
    params = {
        "candidate_sha256": candidate["sha256"],
        "manifest_sha256": manifest["sha256"],
        "paragraph_ends": [done["passages"][0]["id"]],
    }
    preview = post(client, route + "/chapter-arrangement-preview", params)
    assert preview.status_code == 200, preview.text
    assert preview.json()["chapters_requiring_position"] == [1]
    apply = post(
        client,
        route + "/chapter-arrangement",
        {**params, "preview_sha256": preview.json()["preview_sha256"]},
    )
    assert apply.status_code == 200, apply.text
    changed = apply.json()
    assert current(changed, "candidate") == candidate
    segments = current(changed, "segments")["payload"]["segments"]
    body = candidate["payload"]["body"]
    assert "".join(body[s["start"] : s["end"]] for s in segments) == body
    suggested = read(client, route + "/stage-chapters")
    assert suggested["chapters"][0]["position_status"] == "author-required"
    assert suggested["chapters"][0]["position"] is None
    assert post(client, route + "/chapter-arrangement-preview", params).status_code == 409
    merge = {
        **params,
        "manifest_sha256": current(changed, "segments")["sha256"],
        "paragraph_ends": [],
    }
    merge_preview = post(client, route + "/chapter-arrangement-preview", merge)
    assert merge_preview.status_code == 200, merge_preview.text
    merged = post(
        client,
        route + "/chapter-arrangement",
        {**merge, "preview_sha256": merge_preview.json()["preview_sha256"]},
    )
    assert merged.status_code == 200, merged.text
    assert len(current(merged.json(), "segments")["payload"]["segments"]) == 1
    assert current(merged.json(), "candidate") == candidate
    assert current(merged.json(), "units") == current(done, "units")
    assert len(control["calls"]) == 11


def test_written_prefix_and_note_only_edit_bind_fresh_remaining_authorization(craft):
    client, control = craft
    control.update(pause_action="memory:1", unit_size=600)
    base, draft = create_craft(client)
    paused = start(client, base, draft)
    assert paused["status"] == "paused"
    route = f"{base}/{paused['id']}"
    plan = current(paused, "plan")
    replacement = deepcopy(plan["payload"])
    replacement["scenes"][0]["event"] = "覆盖已写事件"
    edit = {"plan": replacement, "expected_plan_sha256": plan["sha256"], "author_note": "修改后续"}
    assert (
        client.put(route + "/plan", json=edit, headers=headers("written-prefix")).status_code == 400
    )
    replacement = deepcopy(plan["payload"])
    replacement["scenes"][1]["development"]["local_freedom"] = "在下一单元选择具体办法"
    edit["plan"] = replacement
    first = client.put(route + "/plan", json=edit, headers=headers("remaining-plan"))
    assert first.status_code == 200, first.text
    adjusted = first.json()
    stale = {
        "confirmed": True,
        "preview_sha256": adjusted["preview_sha256"],
        "expected_plan_sha256": current(adjusted, "plan")["sha256"],
        "expected_adjustment_sha256": current(adjusted, "plan_adjustment")["sha256"],
        "expected_questions_sha256": current(adjusted, "questions")["sha256"],
    }
    edit.update(
        expected_plan_sha256=stale["expected_plan_sha256"], author_note="只更新具体写作要求"
    )
    second = client.put(route + "/plan", json=edit, headers=headers("note-change"))
    assert second.status_code == 200, second.text
    adjusted = second.json()
    assert current(adjusted, "plan")["sha256"] == stale["expected_plan_sha256"]
    assert current(adjusted, "candidate") == current(paused, "candidate")
    assert post(client, route + "/continue-stage", stale).status_code == 409
    control.pop("pause_action")
    response = post(
        client,
        route + "/continue-stage",
        {**stale, "expected_adjustment_sha256": current(adjusted, "plan_adjustment")["sha256"]},
    )
    assert response.status_code == 200, response.text
    done = settle(client, base, paused["id"])
    assert done["state"]["units_finished"], done["state"]
    assert control["calls"].count("write:1") == 1 and len(control["calls"]) == 11


@pytest.mark.parametrize("pause_action, incomplete", [("write:1", False), ("write:2", True)])
def test_missing_relay_or_unknown_fragment_cannot_change_plan(craft, pause_action, incomplete):
    client, control = craft
    control.update(pause_action=pause_action)
    if incomplete:
        control["incomplete_action"] = pause_action
    base, draft = create_craft(client)
    paused = start(client, base, draft)
    plan = current(paused, "plan")
    response = client.put(
        f"{base}/{paused['id']}/plan",
        json={
            "plan": plan["payload"],
            "expected_plan_sha256": plan["sha256"],
            "author_note": "不能越过未完成接力",
        },
        headers=headers("missing-relay"),
    )
    assert response.status_code == 409, response.text
    unchanged = read(client, f"{base}/{paused['id']}")
    assert current(unchanged, "candidate") == current(paused, "candidate")
    assert current(unchanged, "plan") == plan
    if incomplete:
        scale = unchanged["state"]["stage_scale_status"]
        assert scale["unfinished_characters"] > 0
        assert scale["characters"] == 2100


def test_paragraph_split_prefix_adoption_never_includes_future_dependency(craft):
    client, control = craft
    control.update(unit_size=600, late_dependency=True)
    base, draft = create_craft(client)
    done = start(client, base, draft)
    route = f"{base}/{done['id']}"
    args = {
        "candidate_sha256": current(done, "candidate")["sha256"],
        "manifest_sha256": current(done, "segments")["sha256"],
        "paragraph_ends": [done["passages"][0]["id"]],
    }
    preview = post(client, route + "/chapter-arrangement-preview", args)
    changed = post(
        client,
        route + "/chapter-arrangement",
        {**args, "preview_sha256": preview.json()["preview_sha256"]},
    )
    assert changed.status_code == 200, changed.text
    suggestions = read(client, route + "/stage-chapters")
    assert suggestions["chapters"][0]["factual_changes"] == {}
    assert suggestions["chapters"][0]["position"] is None
    assert suggestions["chapters"][1]["factual_changes"]["add_events"]
    data = approval_data(suggestions, 1)
    approved = post(client, route + "/stage-adoption-preview", data)
    assert approved.status_code == 200, approved.text
    adopted = post(
        client,
        route + "/stage-adopt",
        {
            **data,
            "confirmed": True,
            "accept_genre_deviation": True,
            "preview_sha256": approved.json()["preview_sha256"],
        },
    )
    assert adopted.status_code == 200, adopted.text
    backup = read(client, base.replace("/generation-batches", "/backup"))
    assert not backup["formal_version"]["state"]["events"]
    assert not backup["formal_version"]["state"]["scenes"]
    assert len(control["calls"]) == 11


def test_new_rewrite_of_old_stage_has_own_contract_and_discards_old_boundaries(automated):
    client, control = automated
    base, draft = stage_create(
        client, unit_limit=2, **{k: v for k, v in OPTIONS.items() if k != "workflow"}
    )
    before = start(client, base, draft)
    route = f"{base}/{before['id']}"
    old_spec, old_snapshot = before["spec"], before["snapshot"]
    proposed = post(
        client,
        route + "/amendment-preview",
        {
            "candidate_sha256": current(before, "candidate")["sha256"],
            "mode": "rewrite",
            "instruction": "只调整语气，保留事实与事件范围。",
            "max_cost_cny": "1",
            "output_limit": 12000,
            "enable_checker": False,
        },
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["request"]["craft_policy"] == "stage-craft-v1"
    granted = post(
        client,
        route + "/amendment-authorize",
        {"confirmed": True, "preview_sha256": proposed.json()["preview_sha256"]},
    )
    assert granted.status_code == 200, granted.text
    done = settle(client, base, before["id"])
    assert all(c["status"] == "completed" for c in done["calls"]), done["state"]
    assert done["spec"] == old_spec and done["snapshot"] == old_snapshot
    assert "stage_scale" not in control["requests"]["rewrite"]
    assert "stage_scale" not in control["requests"]["memory_amend"]
    assert len(current(done, "segments")["payload"]["segments"]) == 1
    assert current(done, "segments")["sha256"] != current(before, "segments")["sha256"]
    assert control["calls"][-2:] == ["rewrite", "memory_amend"]


def test_complete_stage_above_target_is_saved_without_cutting_or_retry(craft):
    client, control = craft
    control.update(unit_size=4803)
    base, draft = create_craft(client)
    done = start(client, base, draft)
    scale = done["state"]["stage_scale_status"]
    assert done["state"]["units_finished"]
    assert scale["characters"] == 24015 and scale["status"] == "above"
    assert scale["excess"] == 4015
    assert len(control["calls"]) == 11
    assert current(done, "candidate")["payload"]["complete"]
