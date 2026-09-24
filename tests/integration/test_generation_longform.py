import asyncio
import json
from uuid import UUID, uuid4

import pytest

from novel_writer.generation.content import json_text
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelResponse, ProviderTerminalMetadata, TokenUsage
from tests.integration.legacy_generation_fixture import saved_amendment_fixture
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_genre_generation import (  # noqa: F401
    create,
    generation,
    post,
    read,
    start,
)
from tests.integration.test_novel_run_rebuild import current
from tests.unit.test_genre_generation import plan


@pytest.fixture
def longform(generation, monkeypatch):  # noqa: F811
    client, control = generation

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        from novel_writer.db.models import GenerationCallRecord

        async with self.database.session() as session:
            call = await session.get(GenerationCallRecord, call_id)
            action = call.action
        control["calls"].append(action)
        # A rewrite also carries the author's instruction after the JSON context.
        prompt, _ = json.JSONDecoder().raw_decode(request.user_prompt)
        control.setdefault("requests", {})[action] = prompt
        if action == "plan":
            value = plan(
                spec.character_ids
                or [c["id"] for c in prompt["formal_reference"]["characters"]][:2]
            )
            template = value["scenes"][0]
            value["scenes"] = [
                {
                    **template,
                    "focus_percent": 100 // spec.unit_limit + int(i < 100 % spec.unit_limit),
                    "transition_percent": 0,
                    "other_percent": 0,
                }
                for i in range(spec.unit_limit)
            ]
            value["future_proposal"] = "下阶段可探索分别后的选择，尚未发生"
            control["plan"] = value
        elif action.startswith("write:") or action == "rewrite":
            n = int(action.split(":")[1]) if ":" in action else 1
            size = control.get("unit_size", 2100)
            value = "\n\n".join(
                (f"第{n}次选择。" + "她同意了邀请。" * 1000)[: size // 3] for _ in range(3)
            )
        elif action.startswith("chief:"):
            value = {
                "observation": "自主回应改变后续行动",
                "decision": control.get("decision", "continue"),
                "genre_progress": control.get("genre_progress", "changed"),
                "paragraph_ids": [prompt["written_candidate"][0]["id"]],
                "plan": control["plan"],
            }
        else:
            ids = [p["id"] for p in prompt.get("candidate", [])]
            if action.startswith("memory"):
                value = {
                    "position": {"current_location": "渡口", "recent_major_event": f"完成{action}"},
                    "position_paragraph_ids": ids[-1:],
                    "outcome": "邀请改变后续选择",
                    "unresolved": [],
                    "changes": [
                        {
                            "collection": "events",
                            "values": {
                                "summary": f"邀请{action}",
                                "participants": spec.character_ids,
                            },
                            "observation": "相邀",
                            "paragraph_ids": ids[:1],
                        }
                    ],
                }
                if action.startswith("memory:"):
                    value.update(
                        stage_complete=False,
                        correction_needed=control.get("risk", False),
                        continuity_blocked=control.get("blocked", False),
                        progress_reason="关系继续变化",
                    )
            elif action.startswith("checker"):
                value = {"conclusion": "clear", "explanation": "未发现冲突", "issues": []}
            elif action == "title":
                value = {
                    "chapters": [{"id": c["id"], "titles": ["同行"]} for c in prompt["chapters"]]
                }
            else:
                value = {
                    "experience": "自主选择",
                    "perceived_relationship": "特殊在意",
                    "findings": [],
                    "problems": [],
                    "limits": "冷读有限正文",
                }
        if action.startswith("memory") and control.get("lifecycle"):
            value["changes"] += [
                {
                    "collection": "scenes",
                    "values": {"summary": "相邀", "ordinal": 1, "event_ids": ["$change:0"]},
                    "paragraph_ids": [ids[0]],
                    "observation": "邀请场景完成",
                },
                {
                    "collection": "scenes",
                    "values": {"summary": "回应", "ordinal": 2, "event_ids": []},
                    "paragraph_ids": [ids[-1]],
                    "observation": "回应场景完成",
                },
                {
                    "collection": "reader_promises",
                    "promise_action": "established",
                    "values": {"kind": "relationship", "summary": "回应带来下一次期待"},
                    "paragraph_ids": [ids[0], ids[-1]],
                    "observation": "跨章的相邀与回应",
                },
            ]
        if action.startswith("memory") and control.get("late_dependency"):
            value["changes"][0]["paragraph_ids"] = ids[-1:]
            value["changes"].append(
                {
                    "collection": "scenes",
                    "values": {"summary": "邀请带来回应", "ordinal": 1, "event_ids": ["$change:0"]},
                    "paragraph_ids": ids[:1],
                    "observation": "依赖后续完成的事件",
                }
            )
        if control.get("fail_action") == action:
            value = "broken complete report"
        if control.get("pause_action") == action:
            async with self.database.session() as session, session.begin():
                from novel_writer.db.models import GenerationBatchRecord

                batch = await session.get(GenerationBatchRecord, call.batch_id)
                batch.pause_requested = True
        return ModelResponse(
            text=value if isinstance(value, str) else json_text(value),
            usage=TokenUsage(input_tokens=200, output_tokens=100),
            terminal=ProviderTerminalMetadata(
                protocol="fixture",
                terminal_event_seen=True,
                finish_reason="length" if control.get("incomplete_action") == action else "stop",
            ),
        )

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def stage_create(client, **extra):
    return create(
        client,
        workflow="novel-run-v1",
        stage_mode="longform-v1",
        unit_limit=extra.pop("unit_limit", 3),
        chapter_count=extra.pop("chapter_count", 3),
        target_characters=2000,
        **extra,
    )


def test_units_checkpoints_cold_reads_adoption_and_continuation(longform):
    client, control = longform
    base, draft = stage_create(client)
    batch = start(client, base, draft)
    assert control["calls"] == [
        "plan",
        "write:1",
        "memory:1",
        "reader_early",
        "chief:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
        "checker",
        "reader",
    ], batch["state"]
    assert len(current(batch, "units")["payload"]["items"]) == 3
    assert (
        control["requests"]["write:2"]["formal_reference"]["narrative_position"][
            "recent_major_event"
        ]
        == "完成memory:1"
    )
    for action in ("reader_early", "reader"):
        assert set(control["requests"][action]) == {
            "candidate",
            "public_preceding",
            "output_schema",
            "finding_schema",
        }
    assert len(control["requests"]["reader"]["candidate"]) > len(
        control["requests"]["reader_early"]["candidate"]
    )
    suggestions = read(client, f"{base}/{batch['id']}/stage-chapters")
    assert len(suggestions["chapters"]) == 3 and suggestions["tail"] is None
    data = {
        "chapters": [
            {
                "chapter_id": c["id"],
                "title": "同行",
                "facts_confirmed": True,
                "narrative_position": c["position"]
                or {"current_location": "渡口", "recent_major_event": "相邀"},
                "factual_changes": c["factual_changes"],
            }
            for c in suggestions["chapters"]
        ]
    }
    check = post(client, f"{base}/{batch['id']}/stage-adoption-preview", data)
    assert check.status_code == 200, check.text
    adopted = post(
        client,
        f"{base}/{batch['id']}/stage-adopt",
        {
            **data,
            "preview_sha256": check.json()["preview_sha256"],
            "confirmed": True,
            "accept_genre_deviation": True,
        },
    )
    assert adopted.status_code == 200, adopted.text
    next_stage = post(
        client,
        base,
        {
            **draft["spec"],
                "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
                
            "base_version_id": adopted.json()["version_id"],
            "previous_stage_id": batch["id"],
        },
    )
    assert next_stage.status_code == 200, next_stage.text
    assert next_stage.json()["snapshot"]["previous_proposal"]["is_fact"] is False
    assert next_stage.json()["snapshot"]["run"]["stage_index"] == 2


def test_first_unit_precedes_first_chapter_and_checkpoint_slots_are_finite(longform):
    client, control = longform
    control["unit_size"] = 700
    base, draft = stage_create(client, unit_limit=4, chapter_count=1)
    batch = start(client, base, draft)
    assert control["calls"][:4] == ["plan", "write:1", "memory:1", "chief:1"], batch["state"]
    assert "reader_early" in control["calls"] and "chief:2" in control["calls"], batch["state"]
    assert control["calls"][-1] == "reader"


def test_fact_risk_stops_and_author_edit_requires_fresh_reports(longform):
    client, control = longform
    control["blocked"] = True
    base, draft = stage_create(client)
    batch = start(client, base, draft)
    assert control["calls"] == ["plan", "write:1", "memory:1"], batch["state"]
    assert batch["next_action"] is None
    candidate = current(batch, "candidate")
    changed = client.put(
        f"{base}/{batch['id']}/candidate",
        headers=headers(str(uuid4())),
        json={
            "body": candidate["payload"]["body"] + "\n她离开。",
            "expected_body_sha256": current(batch, "segments")["payload"]["body_sha256"],
        },
    )
    assert changed.status_code == 200, changed.text
    assert (
        not {"memory_id", "units_id", "early_review_id", "review_id"}
        & changed.json()["state"].keys()
    )


def approval_data(suggestions, count=None):
    return {
        "chapters": [
            {
                "chapter_id": c["id"],
                "facts_confirmed": True,
                "narrative_position": c["position"]
                or {"current_location": "渡口", "recent_major_event": "本章相邀"},
                "factual_changes": c["factual_changes"],
            }
            for c in suggestions["chapters"][:count]
        ]
    }


def test_cross_chapter_evidence_prefix_adoption_and_future_proposal_guard(longform):
    client, control = longform
    control.update(unit_size=6300, lifecycle=True)
    base, draft = stage_create(client, unit_limit=1)
    batch = start(client, base, draft)
    assert control["calls"] == ["plan", "write:1", "memory:1", "checker", "reader"], batch["state"]
    suggestions = read(client, f"{base}/{batch['id']}/stage-chapters")
    chapters = suggestions["chapters"]
    assert len(chapters) == 3
    assert not chapters[0]["position"] and not chapters[1]["position"]
    assert "add_reader_promises" not in chapters[0]["factual_changes"]
    assert "add_reader_promises" not in chapters[1]["factual_changes"]
    assert "add_reader_promises" in chapters[2]["factual_changes"], {
        "memory": current(batch, "memory")["payload"]["diagnostics"],
        "chapter": chapters[2]["diagnostics"],
    }
    promise = chapters[2]["factual_changes"]["add_reader_promises"][0]
    assert promise["established_chapter"] == 3
    proof = promise["history"][0]["evidence"]
    assert {p["chapter_id"] for p in proof} == {chapters[0]["id"], chapters[2]["id"]}
    data = approval_data(suggestions, 2)
    bad = approval_data(suggestions, 1)
    bad["chapters"][0]["factual_changes"] = chapters[2]["factual_changes"]
    response = post(client, f"{base}/{batch['id']}/stage-adoption-preview", bad)
    assert response.status_code == 400, response.text
    preview = post(client, f"{base}/{batch['id']}/stage-adoption-preview", data)
    assert preview.status_code == 200, preview.text
    adopted = post(
        client,
        f"{base}/{batch['id']}/stage-adopt",
        {
            **data,
            "confirmed": True,
            "accept_genre_deviation": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert adopted.status_code == 200, adopted.text
    backup = read(client, base.replace("/generation-batches", "/backup"))
    assert not backup["formal_version"]["state"]["reader_promises"]
    assert len(read(client, base.replace("/generation-batches", "/chapters"))) == 2
    next_stage = post(
        client,
        base,
        {
            **draft["spec"],
                "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
                
            "base_version_id": adopted.json()["version_id"],
            "previous_stage_id": batch["id"],
        },
    )
    assert next_stage.status_code == 400, next_stage.text


def test_genre_missing_pauses_for_explanation_not_keyword_retry(longform):
    client, control = longform
    control["genre_progress"] = "missing"
    base, draft = stage_create(client)
    batch = start(client, base, draft)
    assert control["calls"][-1] == "chief:1"
    assert batch["status"] == "awaiting_plan" and batch["next_action"] is None
    assert current(batch, "chief_comparison")["payload"]["genre_progress"] == "missing"
    edited = client.put(
        f"{base}/{batch['id']}/plan",
        headers=headers(str(uuid4())),
        json={
            "plan": current(batch, "plan")["payload"],
            "expected_plan_sha256": current(batch, "plan")["sha256"],
            "author_note": "接受当前慢推进，后续按原计划展开",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["next_action"] == "write:2"


def test_two_chief_slots_exhausted_pauses_without_extra_call(longform):
    client, control = longform
    control["risk"] = True
    base, draft = stage_create(client, unit_limit=5)
    batch = start(client, base, draft)
    assert control["calls"].count("chief:1") == 1 and control["calls"].count("chief:2") == 1
    assert control["calls"][-1] == "memory:3", batch["state"]
    assert batch["next_action"] is None and "槽位已用完" in batch["state"]["message"]


@pytest.mark.parametrize(
    "failure,incomplete", [("memory:1", False), ("memory:1", True), ("write:2", True)]
)
def test_failure_keeps_prose_and_never_continues_dependent_units(longform, failure, incomplete):
    client, control = longform
    control["incomplete_action" if incomplete else "fail_action"] = failure
    base, draft = stage_create(client)
    batch = start(client, base, draft)
    assert batch["status"] == "needs_attention", batch["state"]
    assert "write:3" not in control["calls"]
    assert current(batch, "candidate")["payload"]["body"]
    assert control["calls"][-1] == (failure if incomplete else "reader")


def test_pause_resume_and_separate_amendment_slots(longform):
    client, control = longform
    control["pause_action"] = "memory:1"
    base, draft = stage_create(client, unit_limit=2, chapter_count=1)
    batch = start(client, base, draft)
    assert batch["status"] == "paused"
    control.pop("pause_action")
    batch = start(client, base, batch)
    assert control["calls"].count("write:1") == 1
    preview = saved_amendment_fixture(
        client,
        f"{base}/{batch['id']}/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "feedback_policy": "legacy-v1",
            "instruction": "重新核验正文",
            "output_limit": 24000,
            "max_cost_cny": "1",
        },
    )
    assert preview.status_code == 200, preview.text
    authorized = post(
        client,
        f"{base}/{batch['id']}/amendment-authorize",
        {"preview_sha256": preview.json()["preview_sha256"], "confirmed": True},
    )
    assert authorized.status_code == 200, authorized.text

    async def finish():
        await asyncio.wait_for(client.app.state.generation.tasks[UUID(batch["id"])], 10)

    client.portal.call(finish)
    updated = read(client, f"{base}/{batch['id']}")
    assert control["calls"][-3:] == ["memory_amend", "checker_amend", "reader_amend"], updated[
        "state"
    ]
    assert (
        current(updated, "memory")["payload"]["candidate_sha256"]
        == current(updated, "candidate")["sha256"]
    )


def test_two_units_form_one_chapter_without_duplicate_scene_positions(longform):
    client, control = longform
    control.update(unit_size=1200, lifecycle=True)
    base, draft = stage_create(client, unit_limit=2, chapter_count=1)
    batch = start(client, base, draft)
    assert control["calls"][-1] == "reader", batch["state"]
    suggestions = read(client, f"{base}/{batch['id']}/stage-chapters")
    assert len(suggestions["chapters"]) == 1 and suggestions["tail"] is None
    scenes = suggestions["chapters"][0]["factual_changes"]["add_scenes"]
    assert [s["ordinal"] for s in scenes] == [1, 2, 3, 4]
    response = post(
        client, f"{base}/{batch['id']}/stage-adoption-preview", approval_data(suggestions)
    )
    assert response.status_code == 200, response.text


def test_failed_chief_is_not_reused_after_author_plan_revision(longform):
    client, control = longform
    control["fail_action"] = "chief:1"
    base, draft = stage_create(client)
    batch = start(client, base, draft)
    assert control["calls"][-1] == "chief:1"
    edited = client.put(
        f"{base}/{batch['id']}/plan",
        headers=headers(str(uuid4())),
        json={
            "plan": current(batch, "plan")["payload"],
            "expected_plan_sha256": current(batch, "plan")["sha256"],
            "author_note": "已核对首章的题材变化，按现有未写方案继续",
        },
    )
    assert edited.status_code == 200, edited.text
    control.pop("fail_action")
    final = start(client, base, edited.json())
    assert control["calls"].count("chief:1") == 1
    assert control["calls"][-1] == "reader", final["state"]


@pytest.mark.parametrize("writing_policy", ["legacy-v1", "creative-v1"])
def test_automatic_cast_is_selected_once_for_longform_and_never_sent_to_reader(
    longform, writing_policy
):
    client, control = longform
    base, draft = stage_create(
        client,
        unit_limit=2,
        character_selection="chief-auto-v1",
        character_ids=[],
        writing_policy=writing_policy,
    )
    batch = start(client, base, draft)
    assert control["calls"][-1] == "reader", batch["state"]
    chosen = {
        identifier
        for s in current(batch, "plan")["payload"]["scenes"]
        for identifier in s["character_ids"]
    }
    assert len(chosen) == 2
    assert {
        c["id"] for c in control["requests"]["write:1"]["formal_reference"]["characters"]
    } == chosen
    assert "characters" not in control["requests"]["reader"]
    planned = control["requests"]["plan"]["formal_reference"]["characters"]
    if writing_policy == "legacy-v1":
        assert all(
            set(c["portrayal_profile"])
            == {"independent_goal", "value_boundary", "unique_competence"}
            for c in planned
        )
    else:
        assert planned == draft["snapshot"]["context"]["characters"]
        assert all("speech_style" in c for c in planned)


def test_new_object_dependency_delays_fact_until_its_event_is_complete(longform):
    client, control = longform
    control.update(unit_size=6300, late_dependency=True)
    base, draft = stage_create(client, unit_limit=1)
    batch = start(client, base, draft)
    chapters = read(client, f"{base}/{batch['id']}/stage-chapters")["chapters"]
    assert len(chapters) == 3
    assert not chapters[0]["factual_changes"] and not chapters[1]["factual_changes"]
    assert len(chapters[2]["factual_changes"]["add_events"]) == 1
    assert len(chapters[2]["factual_changes"]["add_scenes"]) == 1
