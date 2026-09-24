import json
from uuid import UUID, uuid4

import pytest

from novel_writer.generation.content import digest, json_text
from novel_writer.generation.runtime import GenerationRuntime
from novel_writer.providers.base import ModelResponse, ProviderTerminalMetadata, TokenUsage
from tests.integration.legacy_generation_fixture import saved_amendment_fixture
from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_genre_generation import (
    BODY,
    create,
    generation,  # noqa: F401 fixture
    post,
    read,
    start,
)
from tests.unit.test_genre_generation import plan


@pytest.fixture
def novel_generation(generation, monkeypatch):  # noqa: F811
    client, control = generation

    async def dispatch(self, call_id, request, profile, spec, counting, api_key):
        async with self.database.session() as session:
            from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord

            call = await session.get(GenerationCallRecord, call_id)
            action = call.action
            record = await session.get(GenerationBatchRecord, call.batch_id)
            cast_ids = [
                c["id"] for c in record.snapshot.get("cast_selection", {}).get("characters", [])
            ]
            if control.get("corrupt_binding") == action:
                call.request = {**call.request, "candidate_sha256": "changed"}
                await session.commit()
        control["calls"].append(action)
        control.setdefault("requests", {})[action] = request.user_prompt
        if action == "plan":
            value = plan(control.get("plan_cast") or cast_ids[:2] or spec.character_ids)
            if control.get("question_scope"):
                value["questions"] = ["是否允许同行？"]
                value["question_scopes"] = {"是否允许同行？": control["question_scope"]}
        elif action in {"write", "rewrite"}:
            value = BODY
        else:
            prompt = json.loads(request.user_prompt)
            entries = prompt.get("candidate", [])
            ids = [p["id"] for p in entries]
            if action.startswith("memory"):
                value = {
                    "position": {"current_location": "渡口", "recent_major_event": "约定同行"},
                    "position_paragraph_ids": ids,
                    "outcome": "私人邀请改变下一次行动",
                    "changes": [
                        {
                            "collection": "events",
                            "values": {
                                "summary": "私人邀请促成同行",
                                "participants": spec.character_ids,
                            },
                            "observation": "邀请",
                            "paragraph_ids": ids[:1],
                        }
                    ],
                }
                if control["mode"] == "partial":
                    value["changes"].append({"unknown": "bad auxiliary entry"})
                if control["mode"] == "scene":
                    value["changes"].extend(
                        [
                            {
                                "collection": "scenes",
                                "values": {
                                    "summary": "渡口相邀",
                                    "ordinal": 1,
                                    "event_ids": ["$change:0"],
                                },
                                "observation": "相邀",
                                "paragraph_ids": ids,
                            },
                            {
                                "collection": "reader_promises",
                                "promise_action": "established",
                                "values": {"kind": "relationship", "summary": "是否更进一步"},
                                "observation": "邀请留下期待",
                                "paragraph_ids": ids,
                            },
                        ]
                    )
            elif action.startswith("checker"):
                issues = []
                if control["mode"] == "edit" and action == "checker":
                    issues = [
                        {
                            "severity": "warning",
                            "local_edit": True,
                            "observation": "可以更简洁",
                            "paragraph_ids": ids[1:],
                        }
                    ]
                value = {
                    "conclusion": "issues" if issues else "clear",
                    "explanation": "核对完成",
                    "issues": issues,
                }
            elif action in {"editor", "amend"}:
                value = {
                    "decisions": [
                        {
                            "paragraph_id": p["id"],
                            "disposition": "patched",
                            "replacement": p["text"] + "她点头。",
                            "reason": "局部调整",
                        }
                        for p in prompt["authorized_paragraphs"]
                    ]
                }
            elif action == "title":
                value = {"titles": ["同行"]}
            else:
                value = {
                    "experience": "特殊在意推动选择",
                    "perceived_relationship": "邀请与自主回应",
                    "findings": [{"observation": "私人邀请", "paragraph_ids": ids}],
                    "problems": [],
                    "limits": "只读提供的正文",
                }
                if control["mode"] == "broken_reader":
                    value = "bad report"
        if control.get("fail_action") == action:
            value = "bad report"
        text = value if isinstance(value, str) else json_text(value)
        incomplete = control.get("incomplete_action") == action
        return ModelResponse(
            text=text,
            usage=TokenUsage(input_tokens=200, output_tokens=100),
            raw_response="saved:" + text,
            terminal=ProviderTerminalMetadata(
                protocol="fixture",
                terminal_event_seen=not (incomplete and control.get("uncertain")),
                finish_reason="length" if incomplete else "stop",
            ),
        )

    monkeypatch.setattr(GenerationRuntime, "dispatch", dispatch)
    return client, control


def current(batch, kind):
    return next(a for a in batch["artifacts"] if a["id"] == batch["state"][f"{kind}_id"])


def test_five_calls_facts_adoption_and_next_stage_recall(novel_generation):
    client, control = novel_generation
    control["mode"] = "scene"
    base, draft = create(client, workflow="novel-run-v1")
    assert draft["snapshot"]["normal_calls"] == 5
    batch = start(client, base, draft)
    assert control["calls"] == ["plan", "write", "memory", "checker", "reader"], batch["state"]
    assert "chapter_goal" not in control["requests"]["reader"]
    memory = current(batch, "memory")["payload"]
    data = {
        "title": "同行",
        "narrative_position": memory["position"],
        "factual_changes": memory["factual_changes"],
        "facts_confirmed": True,
    }
    preview = post(client, f"{base}/{batch['id']}/adoption-preview", data)
    assert preview.status_code == 200, preview.text
    adopted = post(
        client,
        f"{base}/{batch['id']}/adopt",
        {
            **data,
            "confirmed": True,
            "accept_genre_deviation": True,
            "preview_sha256": preview.json()["preview_sha256"],
        },
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["chapter_id"] == draft["snapshot"]["candidate_chapter"]["id"]
    updated = read(client, f"{base}/{batch['id']}")
    assert current(updated, "formal_summary")["payload"]["body_sha256"] == digest(BODY)
    spec = {**draft["spec"],
        "feedback_policy": "logic-v1", "enable_reader": False, "milestone_unit": None,
         "base_version_id": adopted.json()["version_id"]}
    next_stage = post(client, base, spec)
    assert next_stage.status_code == 200, next_stage.text
    context = next_stage.json()["snapshot"]["context"]
    assert context["formal_summaries"] and context["critical_facts"]
    project_base = base.replace("/generation-batches", "")
    backup = read(client, project_base + "/backup")
    restored = post(client, "/api/data/backup/restore", {"archive": backup, "confirmed": True})
    assert restored.status_code == 201, restored.text
    restored_base = f"/api/projects/{restored.json()['project_id']}"
    restored_backup = read(client, restored_base + "/backup")
    restored_state = restored_backup["formal_version"]["state"]
    restored_chapters = read(client, restored_base + "/chapters")
    assert restored_state["scenes"][0]["chapter_id"] == restored_chapters[0]["chapter_id"]
    promise_proof = restored_state["reader_promises"][0]["history"][0]["evidence"][0]
    assert promise_proof["chapter_id"] == restored_chapters[0]["chapter_id"]
    archived = read(client, restored_base + "/generation-batches")
    assert all(item["status"] == "archived" for item in archived)


def test_partial_memory_keeps_valid_items_and_reader_failure_keeps_prose(novel_generation):
    client, control = novel_generation
    control["mode"] = "partial"
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    assert len(control["calls"]) == 5, batch["state"]
    assert current(batch, "memory")["payload"]["status"] == "partial"
    assert len(current(batch, "memory")["payload"]["changes"]) == 1


def test_editor_reserved_but_skipped_without_evidence_and_rebuilds_after_change(novel_generation):
    client, control = novel_generation
    base, draft = create(client, workflow="novel-run-v1", enable_editor=True, generate_title=True)
    batch = start(client, base, draft)
    assert len(control["calls"]) == 6 and "editor" not in control["calls"], batch["state"]
    assert current(batch, "title")["payload"]["titles"] == ["同行"]
    control["calls"] = []
    control["mode"] = "edit"
    base, draft = create(client, workflow="novel-run-v1", enable_editor=True)
    batch = start(client, base, draft)
    assert control["calls"] == [
        "plan",
        "write",
        "memory",
        "checker",
        "editor",
        "memory_edit",
        "checker_edit",
        "reader",
    ], batch["state"]
    candidate = current(batch, "candidate")
    assert candidate["payload"]["body"].endswith("她点头。")
    for kind in ("memory", "checker", "review", "segments"):
        assert current(batch, kind)["payload"]["candidate_sha256"] == candidate["sha256"]


def test_manual_edit_invalidates_reports_and_verify_requires_separate_authorization(
    novel_generation,
):
    client, control = novel_generation
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    changed = client.put(
        f"{base}/{batch['id']}/candidate",
        headers=headers(str(uuid4())),
        json={
            "body": BODY + "\n新事实。",
            "expected_body_sha256": digest(BODY),
        },
    )
    assert changed.status_code == 200, changed.text
    batch = changed.json()
    assert not {"memory_id", "checker_id", "review_id"} & batch["state"].keys()
    preview = saved_amendment_fixture(
        client,
        f"{base}/{batch['id']}/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "feedback_policy": "legacy-v1",
            "instruction": "核对手工新稿",
            "output_limit": 24000,
            "max_cost_cny": "1",
        },
    )
    assert preview.status_code == 200, preview.text
    assert len(control["calls"]) == 5
    path = f"{base}/{batch['id']}/amendment-authorize"
    result = post(
        client, path, {"preview_sha256": preview.json()["preview_sha256"], "confirmed": True}
    )
    assert result.status_code == 200, result.text

    async def finish():
        await client.app.state.generation.tasks[UUID(batch["id"])]

    client.portal.call(finish)
    batch = read(client, f"{base}/{batch['id']}")
    assert control["calls"][-3:] == ["memory_amend", "checker_amend", "reader_amend"], batch[
        "state"
    ]
    assert current(batch, "review")["payload"]["body_sha256"] == digest(BODY + "\n新事实。")
    result = post(
        client, path, {"preview_sha256": preview.json()["preview_sha256"], "confirmed": True}
    )
    assert result.status_code == 409


def test_cold_reader_parse_failure_never_discards_prose_or_retries(novel_generation):
    client, control = novel_generation
    control["mode"] = "broken_reader"
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    assert len(control["calls"]) == 5
    assert current(batch, "candidate")["payload"]["body"] == BODY
    assert "review_id" not in batch["state"]
    assert batch["calls"][-1]["status"] == "local_failure"
    call = read(client, f"{base}/{batch['id']}/calls/{batch['calls'][-1]['id']}")
    assert call["response"]["text"] == "bad report"


@pytest.mark.parametrize("action", ["memory", "checker", "editor", "memory_edit", "checker_edit"])
def test_failed_complete_report_only_skips_dependent_slots(novel_generation, action):
    client, control = novel_generation
    control.update(mode="edit", fail_action=action)
    base, draft = create(client, workflow="novel-run-v1", enable_editor=True)
    batch = start(client, base, draft)
    slots = ["plan", "write", "memory", "checker", "editor", "memory_edit", "checker_edit"]
    assert control["calls"] == [*slots[: slots.index(action) + 1], "reader"], batch["state"]
    failed = next(c for c in batch["calls"] if c["action"] == action)
    assert failed["status"] == "local_failure"
    diagnostic = current(batch, "dependency_skip")["payload"]
    assert diagnostic["failed_call_id"] == failed["id"]
    assert diagnostic["skipped_actions"] == slots[slots.index(action) + 1 :]
    candidate = current(batch, "candidate")
    assert current(batch, "review")["payload"]["candidate_sha256"] == candidate["sha256"]
    assert batch["status"] == "needs_attention" and batch["next_action"] is None
    assert "部分报告失败" in batch["state"]["message"]
    # No empty report is fabricated and local revalidation cannot resend Reader.
    kind = "memory" if action.startswith("memory") else "checker"
    if action != "editor":
        assert f"{kind}_id" not in batch["state"]
    count = len(control["calls"])
    post(client, f"{base}/{batch['id']}/calls/{failed['id']}/revalidate", {"confirmed": True})
    assert len(control["calls"]) == count


@pytest.mark.parametrize("failure", ["truncated", "uncertain", "binding"])
def test_incomplete_or_mismatched_report_never_continues_reader(novel_generation, failure):
    client, control = novel_generation
    if failure == "binding":
        control["corrupt_binding"] = "memory"
    else:
        control.update(incomplete_action="memory", uncertain=failure == "uncertain")
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    assert control["calls"] == ["plan", "write", "memory"], batch["state"]
    assert current(batch, "candidate")["payload"]["body"] == BODY
    assert "dependency_skip_id" not in batch["state"]
    assert "review_id" not in batch["state"] and batch["next_action"] is None


def test_failed_amendment_memory_uses_only_its_reserved_reader(novel_generation):
    client, control = novel_generation
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    control["fail_action"] = "memory_amend"
    preview = saved_amendment_fixture(
        client,
        f"{base}/{batch['id']}/amendment-preview",
        {
            "candidate_sha256": current(batch, "candidate")["sha256"],
            "mode": "verify",
            "feedback_policy": "legacy-v1",
            "instruction": "核对当前稿",
            "output_limit": 24000,
            "max_cost_cny": "1",
        },
    )
    assert preview.status_code == 200, preview.text
    result = post(
        client,
        f"{base}/{batch['id']}/amendment-authorize",
        {"preview_sha256": preview.json()["preview_sha256"], "confirmed": True},
    )
    assert result.status_code == 200, result.text

    async def finish():
        await client.app.state.generation.tasks[UUID(batch["id"])]

    client.portal.call(finish)
    batch = read(client, f"{base}/{batch['id']}")
    assert control["calls"][-2:] == ["memory_amend", "reader_amend"], batch["state"]
    assert "checker_amend" not in control["calls"]
    assert len(control["calls"]) == 7


def test_question_deletion_is_not_answer_and_later_question_does_not_block(novel_generation):
    client, control = novel_generation
    control["question_scope"] = "current_unit"
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    assert control["calls"] == ["plan"] and batch["next_action"] is None
    plan_artifact = current(batch, "plan")
    edited = {**plan_artifact["payload"], "questions": [], "question_scopes": {}}
    payload = {
        "plan": edited,
        "expected_plan_sha256": plan_artifact["sha256"],
        "author_note": "已修改方案",
    }
    path = f"{base}/{batch['id']}/plan"
    result = client.put(path, headers=headers(str(uuid4())), json=payload)
    assert result.status_code == 400, result.text
    result = client.put(
        path,
        headers=headers(str(uuid4())),
        json={
            **payload,
            "question_answers": {"是否允许同行？": "允许，但不确定最终关系"},
        },
    )
    assert result.status_code == 200, result.text
    finished = start(client, base, result.json())
    assert len(control["calls"]) == 5, finished["state"]
    assert current(finished, "questions")["payload"]["items"][0]["status"] == "answered"
    control["calls"] = []
    control["question_scope"] = "later"
    base, draft = create(client, workflow="novel-run-v1")
    batch = start(client, base, draft)
    assert len(control["calls"]) == 5, batch["state"]
    assert current(batch, "questions")["payload"]["items"][0]["status"] == "pending"
