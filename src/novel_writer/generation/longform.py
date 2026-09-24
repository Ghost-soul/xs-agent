"""Finite Stage/Unit orchestration inside the shared generation executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from sqlalchemy import select

from novel_writer.db.models import (
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
)
from novel_writer.domain.state import StateDelta, StoryState
from novel_writer.generation.content import (
    checked_body,
    digest,
    fingerprint,
    json_text,
    paragraphs,
    parse_object,
)
from novel_writer.generation.feedback import execution_spec
from novel_writer.generation.guidance import parse_comparison
from novel_writer.generation.logic import advisory, checker_feedback, reader_feedback
from novel_writer.generation.novel import invalidate_candidate, next_action, role_for, slots_for
from novel_writer.generation.plan_capacity import parse_stage, plan_size
from novel_writer.generation.reports import (
    apply_edit,
    evidence,
    memory_result,
)
from novel_writer.generation.schemas import FrozenGenerationSpec, PlanEdit
from novel_writer.generation.segmentation import segment_body
from novel_writer.generation.unit_scope import enabled as unit_based
from novel_writer.generation.unit_scope import segment_units
from novel_writer.services.errors import ConflictError, WorkflowError

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


async def units_for(
    service: GenerationService, batch: GenerationBatchRecord
) -> list[dict[str, Any]]:
    artifact = await service.artifact(batch, "units")
    items = artifact.payload["items"] if artifact else []
    candidate = await service.artifact(batch, "candidate")
    if items:
        if not candidate or items[-1]["end"] != len(candidate.payload["body"]):
            raise WorkflowError("单元链与候选长度不匹配")
        for ordinal, item in enumerate(items, 1):
            start = 0 if ordinal == 1 else items[ordinal - 2]["end"] + 2
            if (
                item["ordinal"] != ordinal
                or item["start"] != start
                or item["body_sha256"]
                != digest(candidate.payload["body"][item["start"] : item["end"]])
            ):
                raise WorkflowError("单元顺序、范围或正文SHA损坏")
    return items


async def handoff_for(
    service: GenerationService, batch: GenerationBatchRecord, item: dict[str, Any]
) -> dict[str, Any]:
    artifact = await service.session.get(GenerationArtifactRecord, UUID(item["memory_id"]))
    if (
        not artifact
        or artifact.batch_id != batch.id
        or artifact.kind != "handoff"
        or artifact.sha256 != fingerprint(artifact.payload)
        or artifact.sha256 != item["memory_sha256"]
    ):
        raise WorkflowError("单元接力来源或SHA损坏")
    if artifact.payload["unit_body_sha256"] != item["body_sha256"]:
        raise WorkflowError("接力报告不属于本单元正文")
    return artifact.payload


async def prepared_reports(
    service: GenerationService, batch: GenerationBatchRecord, action: str
) -> dict[str, Any]:
    items = await units_for(service, batch)
    candidate = await service.artifact(batch, "candidate")
    complete = [u for u in items if u.get("memory_id")]
    reports: dict[str, Any] = {
        "completed_units": len(complete),
        "unit_chain_sha256": fingerprint(items),
    }
    for kind, key in (
        ("memory", "memory"),
        ("checker", "checker"),
        ("early_review", "early_review"),
    ):
        report = await service.artifact(batch, kind)
        if report:
            if (
                key != "early_review"
                and candidate
                and report.payload.get("candidate_sha256") != candidate.sha256
            ):
                raise WorkflowError("阶段报告与正文绑定不一致")
            reports[key] = report.payload
    if complete:
        handoff = await handoff_for(service, batch, complete[-1])
        state = handoff["working_state"]
        plan = await service.artifact(batch, "plan")
        ids = (
            {i for scene in plan.payload["scenes"] for i in scene["character_ids"]}
            if plan
            else set()
        )
        reports["working_context"] = {
            "characters": [c for c in state["characters"] if c["id"] in ids],
            "relationships": [
                r
                for r in state["relationships"]
                if {r["source_character_id"], r["target_character_id"]} & ids
            ],
            "beliefs": [b for b in state["beliefs"] if b["character_id"] in ids],
            "narrative_position": state["narrative_position"],
            "recent_events": state["events"][-24:],
            "candidate_state_is_formal": False,
        }
        changed_ids: set[str] = set()
        for unit in complete:
            prior = await handoff_for(service, batch, unit)
            changed_ids.update(c["object_id"] for c in prior["changes"])
        related = batch.snapshot["context"].get("related_state", {})
        reports["working_context"]["related_state"] = {
            key: [
                value
                for value in values
                if value["id"] in changed_ids
                or value["id"] in {v["id"] for v in related.get(key, [])}
            ]
            for key, values in state.items()
            if isinstance(values, list) and all(isinstance(v, dict) and "id" in v for v in values)
        }
    if action.startswith("memory:"):
        ordinal = int(action.split(":")[1])
        if len(items) != ordinal or not candidate:
            raise WorkflowError("单元接力次序错误")
        item = items[-1]
        reports["input_body"] = candidate.payload["body"][item["start"] : item["end"]]
        reports["unit_chapter"] = {
            "id": item["chapter_id"],
            "ordinal": batch.snapshot["candidate_chapter"]["ordinal"] + ordinal - 1,
        }
    elif role_for(action) == "memory" and candidate:
        # A whole-stage rebuild starts from the formal base, never old candidate facts.
        reports.pop("working_context", None)
        reports["unit_chapter"] = {
            "id": str(uuid5(batch.id, f"rebuilt:{digest(candidate.payload['body'])}")),
            "ordinal": batch.snapshot["candidate_chapter"]["ordinal"],
        }
    if action == "reader_early":
        manifest = await service.artifact(batch, "segments")
        if not candidate or not manifest or not manifest.payload["segments"]:
            raise WorkflowError("早读缺少完整首章")
        end = manifest.payload["segments"][0]["end"]
        reports["input_body"] = candidate.payload["body"][:end]
    if action == "title":
        manifest = await service.artifact(batch, "segments")
        reports["chapter_bodies"] = (
            [
                {"id": s["id"], "body": candidate.payload["body"][s["start"] : s["end"]]}
                for s in manifest.payload["segments"]
            ]
            if manifest and candidate
            else []
        )
    return reports


async def freeze_segments(
    service: GenerationService, batch: GenerationBatchRecord, spec=None
) -> None:
    candidate = await service.artifact(batch, "candidate")
    if not candidate:
        return
    spec = spec or FrozenGenerationSpec.model_validate(batch.spec)
    if unit_based(spec):
        manifest = segment_units(
            candidate.payload["body"],
            await units_for(service, batch),
            str(batch.id),
            complete=candidate.payload.get("complete", True),
        )
    else:
        manifest = segment_body(candidate.payload["body"], spec.target_characters, 3, str(batch.id))
    manifest["candidate_sha256"] = candidate.sha256
    await service.append(batch, "segments", manifest)


def protect_written(prior: dict[str, Any], replacement: dict[str, Any], completed: int) -> None:
    for field in ("chapter_goal", "major_turn", "genre_causal_role"):
        if prior.get(field) != replacement.get(field):
            raise ValueError("阶段原始题材目标不可在纠偏中替换")
    if prior["scenes"][:completed] != replacement["scenes"][:completed]:
        raise ValueError("只能替换未写单元，已写设计不可修改")
    allowed = {i for s in prior["scenes"] for i in s["character_ids"]}
    if not {i for s in replacement["scenes"] for i in s["character_ids"]} <= allowed:
        raise ValueError("检查点不能扩大最初阶段的承载人物范围")


async def store_questions(
    service: GenerationService, batch: GenerationBatchRecord, plan: dict[str, Any]
) -> bool:
    previous = await service.artifact(batch, "questions")
    items = {q["question"]: dict(q) for q in previous.payload["items"]} if previous else {}
    for q in plan["questions"]:
        items.setdefault(
            q,
            {
                "question": q,
                "status": "pending",
                "scope": plan["question_scopes"].get(q, "current_unit"),
                "source_plan_sha256": fingerprint(plan),
            },
        )
        if (
            items[q]["status"] == "pending"
            and plan["question_scopes"].get(q, "current_unit") == "current_unit"
        ):
            items[q]["scope"] = "current_unit"
        if q in plan.get("author_question_reasons", {}):
            items[q]["reason"] = plan["author_question_reasons"][q]
    await service.append(batch, "questions", {"items": list(items.values())})
    return any(q["status"] == "pending" and q["scope"] == "current_unit" for q in items.values())


async def choose_next(service: GenerationService, batch: GenerationBatchRecord) -> None:
    spec = FrozenGenerationSpec.model_validate(batch.spec)
    items = await units_for(service, batch)
    done = len([u for u in items if u.get("memory_id")])
    plan = await service.artifact(batch, "plan")
    if not plan:
        raise WorkflowError("缺少有效计划，不能继续单元")
    count = plan_size(plan.payload, spec)
    if len(items) > count:
        raise WorkflowError("已写单元超出有效计划，不能继续")
    if done < count:
        batch.state = {k: v for k, v in batch.state.items() if k != "units_finished"}
    if done < len(items):
        following: str | None = f"memory:{len(items)}"
    elif done == 0:
        following = "write:1"
    else:
        memory = await handoff_for(service, batch, items[-1])
        if memory.get("continuity_blocked"):
            raise WorkflowError("本单元报告事实接力冲突，已暂停；请核对后独立修订")
        if done == count or (not advisory(spec) and memory.get("stage_complete")):
            batch.state = {**batch.state, "units_finished": True}
            following = next(
                (a for a in slots_for(spec) if a in {"checker", "reader", "title"}), None
            )
        else:
            manifest = await service.artifact(batch, "segments")
            early = await service.artifact(batch, "early_review")
            compared = batch.state.get("compared_units", [])
            reasons = []
            if not advisory(spec) and done == 1 and done not in compared:
                reasons.append("first-unit")
            if not advisory(spec) and manifest and manifest.payload["segments"] and not early:
                following = "reader_early"
                batch.next_action, batch.status = following, "queued"
                return
            if early and early.sha256 != batch.state.get("compared_early_sha256"):
                reasons.append("first-chapter")
            if done not in compared and (
                (not advisory(spec) and memory.get("correction_needed"))
                or spec.milestone_unit == done
            ):
                reasons.append("risk-or-milestone")
            if reasons:
                consumed = list(
                    await service.session.scalars(
                        select(GenerationCallRecord.action).where(
                            GenerationCallRecord.batch_id == batch.id,
                            GenerationCallRecord.action.in_(("chief:1", "chief:2")),
                        )
                    )
                )
                used = max((int(a.split(":")[1]) for a in consumed), default=0)
                if used >= 2:
                    raise WorkflowError("需要纠偏但两次预留 Chief 槽位已用完；不增加调用或自动续写")
                batch.state = {**batch.state, "checkpoint_reasons": reasons}
                following = f"chief:{used + 1}"
            else:
                following = f"write:{done + 1}"
    batch.next_action, batch.status = following, "queued" if following else "needs_attention"


async def aggregate_memory(service: GenerationService, batch: GenerationBatchRecord) -> None:
    candidate = await service.artifact(batch, "candidate")
    assert candidate
    items = await units_for(service, batch)
    changes: list[dict[str, Any]] = []
    diagnostics, unresolved = [], []
    latest: dict[str, Any] = {}
    known = {(p["start"], p["end"]): p for p in paragraphs(candidate.payload["body"])}
    values: dict[str, dict[str, Any]] = {}
    for item in items:
        if not item.get("memory_id"):
            continue
        latest = await handoff_for(service, batch, item)
        diagnostics.extend(latest["diagnostics"])
        unresolved.extend(latest["unresolved"])
        for change in latest["changes"]:
            spans = [
                known[(p["start"] + item["start"], p["end"] + item["start"])]
                for p in change["evidence"]
            ]
            changes.append(
                {
                    **change,
                    "evidence": spans,
                    "paragraph_ids": [p["id"] for p in spans],
                    "unit": item["ordinal"],
                }
            )
            values.setdefault("add_" + change["collection"], {})[change["object_id"]] = change[
                "value"
            ]
    await service.append(
        batch,
        "memory",
        {
            "candidate_sha256": candidate.sha256,
            "body_sha256": digest(candidate.payload["body"]),
            "position": latest["position"],
            "outcome": latest["outcome"],
            "changes": changes,
            "factual_changes": {k: list(v.values()) for k, v in values.items()},
            "diagnostics": diagnostics,
            "unresolved": unresolved,
            "status": "partial" if diagnostics else "complete",
            "requires_author_confirmation": True,
        },
    )


async def compile_longform(
    service: GenerationService,
    batch: GenerationBatchRecord,
    call: GenerationCallRecord,
    raw: str,
    complete: bool,
) -> None:
    spec = execution_spec(batch.spec, call.request)
    candidate = await service.artifact(batch, "candidate")
    plan = await service.artifact(batch, "plan")
    items = await units_for(service, batch)
    if call.request.get("candidate_sha256") != (
        candidate.sha256 if candidate else None
    ) or call.request.get("plan_sha256") != (plan.sha256 if plan else None):
        raise WorkflowError("响应的正文或有效计划来源已变化")
    if call.request.get("unit_chain_sha256") != fingerprint(items):
        raise WorkflowError("响应单元接力链失配")
    action, role = call.action, role_for(call.action)
    following: str | None
    item: dict[str, Any]
    if role == "writer":
        if raw.lstrip().startswith(("{", "```json")):
            try:
                issue = parse_object(raw)
            except ValueError:
                issue = {}
            if "generation_blocked" in issue:
                await service.append(batch, "writer_issue", issue)
                raise WorkflowError("Writer 报告设计与边界冲突，未继续")
        if raw:
            old_body = candidate.payload["body"] if candidate and action != "rewrite" else ""
            separator = "\n\n" if old_body else ""
            body = checked_body(old_body + separator + checked_body(raw))
            # Keep early reading as a historical scoped observation, never as the final read.
            early_id = batch.state.get("early_review_id")
            batch.state = invalidate_candidate(batch.state)
            if early_id and action != "rewrite":
                batch.state = {**batch.state, "early_review_id": early_id}
            candidate = await service.append(
                batch,
                "candidate",
                {"body": body, "complete": complete, "source_call_id": str(call.id)},
            )
            ordinal = len(items) + 1 if action != "rewrite" else 1
            item = {
                "ordinal": ordinal,
                "start": len(old_body + separator),
                "end": len(body),
                "body_sha256": digest(raw),
                "chapter_id": str(uuid5(batch.id, f"unit:{ordinal}:{digest(raw)}")),
                "source_call_id": str(call.id),
                "complete": complete,
            }
            await service.append(
                batch, "units", {"items": [*items, item] if action != "rewrite" else [item]}
            )
    if not complete:
        raise WorkflowError("供应商响应不完整，原响应和已写内容已保存，不自动续写")
    if action == "plan" or action.startswith("chief:"):
        original = parse_object(raw)
        if action != "plan":
            parse_comparison(original, spec)
        plan_data = original if action == "plan" else original["plan"]
        new_plan = parse_stage(json_text(plan_data), spec, batch.snapshot).model_dump(mode="json")
        if action != "plan":
            assert plan and candidate
            comparison = parse_comparison(original, spec)
            done = len([u for u in items if u.get("memory_id")])
            protect_written(plan.payload, new_plan, done)
            spans = evidence(original["paragraph_ids"], candidate.payload["body"])
            early = await service.artifact(batch, "early_review")
            await service.append(
                batch,
                "chief_comparison",
                {
                    "assessment": comparison.observation,
                    "decision": comparison.decision,
                    "genre_progress": comparison.genre_progress,
                    "evidence": spans,
                    "candidate_sha256": candidate.sha256,
                    "parent_plan_sha256": plan.sha256,
                    "reasons": batch.state.get("checkpoint_reasons"),
                    "completed_units": done,
                    "early_review_sha256": early.sha256 if early else None,
                    "source_call_id": str(call.id),
                },
            )
            batch.state = {
                **batch.state,
                "chief_checkpoints": int(action.split(":")[1]),
                "compared_units": [*batch.state.get("compared_units", []), done],
                "compared_early_sha256": early.sha256 if early else None,
            }
            if (
                comparison.decision == "revise"
                and new_plan["scenes"][done:] == plan.payload["scenes"][done:]
            ):
                raise ValueError("Chief 声明纠偏但没有调整未写设计")
            insufficient = comparison.genre_progress in {"missing", "unknown"} or (
                early is not None and comparison.genre_progress != "changed"
            )
            if comparison.decision == "pause" or (
                not advisory(spec) and insufficient and comparison.decision != "revise"
            ):
                batch.status, batch.next_action = "awaiting_plan", None
                batch.state = {**batch.state, "checkpoint_author_required": True}
                await store_questions(service, batch, new_plan)
                return
        await service.append(batch, "plan", new_plan)
        blocking = await store_questions(service, batch, new_plan)
        if blocking or (action == "plan" and spec.pause_after_plan):
            batch.status, batch.next_action = "awaiting_plan", None if blocking else "write:1"
            return
        await choose_next(service, batch)
        return
    if role == "memory":
        assert candidate
        rebuilding = ":" not in action
        body = candidate.payload["body"]
        base = await service._version(batch.base_version_id)
        state = StoryState.model_validate(base.state)
        if rebuilding:
            item = {
                "ordinal": 1,
                "start": 0,
                "end": len(body),
                "body_sha256": digest(body),
                "chapter_id": str(uuid5(batch.id, f"rebuilt:{digest(body)}")),
                "complete": True,
            }
            items = [item]
            data = parse_object(raw)
            flags: dict[str, Any] = {"stage_complete": True}
        else:
            item = items[-1]
            if len(items) > 1:
                previous = await handoff_for(service, batch, items[-2])
                state = StoryState.model_validate(previous["working_state"])
            body = body[item["start"] : item["end"]]
            data = parse_object(raw)
            if advisory(spec):
                # Memory records continuity; creative decisions belong to Chief/Writer.
                data.update(stage_complete=False, correction_needed=False)
                data.setdefault("continuity_blocked", False)
                data.setdefault("progress_reason", "事实接力")
            if (
                not {"stage_complete", "correction_needed", "continuity_blocked", "progress_reason"}
                <= data.keys()
            ):
                raise ValueError("单元进展或接力风险字段缺失")
            flags = {
                k: data.pop(k)
                for k in (
                    "stage_complete",
                    "correction_needed",
                    "continuity_blocked",
                    "progress_reason",
                )
            }
            if any(
                type(flags[k]) is not bool
                for k in ("stage_complete", "correction_needed", "continuity_blocked")
            ) or not isinstance(flags["progress_reason"], str):
                raise ValueError("单元进展/接力风险字段无效")
        result = memory_result(
            json_text(data),
            body,
            state,
            base.number,
            {
                "id": item["chapter_id"],
                "ordinal": batch.snapshot["candidate_chapter"]["ordinal"] + item["ordinal"] - 1,
            },
        )
        delta = StateDelta.model_validate(
            {
                "base_version": base.number,
                **result["factual_changes"],
                "set_narrative_position": result["position"],
            }
        )
        handoff = await service.append(
            batch,
            "handoff",
            {
                **result,
                **flags,
                "working_state": delta.apply(state).model_dump(mode="json"),
                "candidate_sha256": candidate.sha256,
                "unit_body_sha256": digest(body),
                "parent_chain_sha256": fingerprint(items[:-1]),
            },
        )
        items = [
            *items[:-1],
            {**item, "memory_id": str(handoff.id), "memory_sha256": handoff.sha256},
        ]
        await service.append(batch, "units", {"items": items})
        await aggregate_memory(service, batch)
        await freeze_segments(service, batch, spec)
        if not rebuilding:
            await choose_next(service, batch)
            return
        following = (
            next_action(call.request["action_slots"], action)
            if advisory(spec)
            else "checker_amend"
            if action == "memory_amend"
            else "checker_edit"
        )
    elif role == "checker":
        assert candidate
        result = checker_feedback(raw, candidate.payload["body"], spec)
        await service.append(batch, "checker", {**result, "candidate_sha256": candidate.sha256})
        from novel_writer.generation.stage import edit_scope

        scope = await edit_scope(service, batch)
        following = (
            "editor"
            if action == "checker" and spec.enable_editor and scope["paragraph_ids"]
            else "reader_amend"
            if action == "checker_amend"
            else "reader"
        )
        if advisory(spec):
            following = next_action(
                call.request["action_slots"], action, editable=bool(scope["paragraph_ids"])
            )
    elif role == "reader":
        assert candidate
        body = call.request["input_body"] if action == "reader_early" else candidate.payload["body"]
        if not candidate.payload["body"].startswith(body):
            raise WorkflowError("早读范围不属于当前候选")
        result = reader_feedback(
            raw, body, batch.snapshot["context"].get("recent_chapters", []), spec
        )
        await service.append(
            batch,
            "early_review" if action == "reader_early" else "review",
            {**result, "candidate_sha256": candidate.sha256},
        )
        if action == "reader_early":
            await choose_next(service, batch)
            return
        following = "title" if spec.generate_title and action == "reader" else None
    elif action in {"editor", "amend"}:
        assert candidate
        scope = call.request["edit_scope"]
        result = apply_edit(
            raw, candidate.payload["body"], scope["paragraph_ids"], scope["protected_paragraph_ids"]
        )
        await service.append(
            batch, "edit_decisions", {**result, "candidate_sha256": candidate.sha256}
        )
        if result["changed"]:
            batch.state = invalidate_candidate(batch.state)
            batch.state = {k: v for k, v in batch.state.items() if k != "units_id"}
            await service.append(
                batch,
                "candidate",
                {"body": result["body"], "complete": True, "source_call_id": str(call.id)},
            )
        following = (
            "memory_amend"
            if action == "amend"
            else "memory_edit"
            if result["changed"]
            else "reader"
        )
        if advisory(spec):
            following = next_action(call.request["action_slots"], action, changed=result["changed"])
    elif action == "title":
        assert candidate
        try:
            titles = parse_object(raw).get("chapters", [])
            manifest = await service.artifact(batch, "segments")
            allowed = {s["id"] for s in manifest.payload["segments"]} if manifest else set()
            titles = [
                {
                    "id": t["id"],
                    "titles": [
                        x.strip()
                        for x in t["titles"]
                        if isinstance(x, str) and 0 < len(x.strip()) <= 80
                    ][:3],
                }
                for t in titles
                if isinstance(t, dict)
                and t.get("id") in allowed
                and isinstance(t.get("titles"), list)
            ]
        except (ValueError, TypeError):
            titles = []
        await service.append(
            batch, "title", {"chapters": titles, "candidate_sha256": candidate.sha256}
        )
        following = None
    else:
        following = "memory_amend" if action == "rewrite" else f"memory:{len(items) + 1}"
    batch.next_action, batch.status = following, "queued" if following else "needs_attention"
    if not following:
        await freeze_segments(service, batch, spec)


async def edit_stage_plan(
    service: GenerationService, batch: GenerationBatchRecord, edit: PlanEdit
) -> dict[str, Any]:
    from novel_writer.generation.questions import reconcile_questions

    if batch.status not in {"awaiting_plan", "paused", "needs_attention"}:
        raise ConflictError("请先暂停并等待在途请求完成")
    prior = await service.artifact(batch, "plan")
    if not prior or prior.sha256 != edit.expected_plan_sha256:
        raise ConflictError("计划来源已改变")
    items = await units_for(service, batch)
    if any(not u.get("memory_id") for u in items):
        raise ConflictError("先完成当前已写单元的接力，不能修改其来源")
    used = await service.session.scalar(
        select(GenerationCallRecord.id).where(
            GenerationCallRecord.batch_id == batch.id,
            GenerationCallRecord.action == f"write:{len(items) + 1}",
        )
    )
    if used:
        raise ConflictError("下一单元已经领取，不能改写调用来源")
    spec = FrozenGenerationSpec.model_validate(batch.spec)
    try:
        plan = parse_stage(json_text(edit.plan.model_dump(mode="json")), spec, batch.snapshot)
        # Before any prose, the author can direct the entire plan. Once writing
        # starts, preserve its original goal and every already-written unit.
        if items:
            protect_written(prior.payload, plan.model_dump(mode="json"), len(items))
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    previous = await service.artifact(batch, "questions")
    questions = reconcile_questions(
        previous.payload["items"] if previous else [],
        plan,
        edit.question_answers,
        edit.deferred_questions,
        edit.author_note,
    )
    await service.append(batch, "plan", plan.model_dump(mode="json"))
    await service.append(batch, "questions", {"items": questions})
    await service.append(
        batch,
        "plan_author_note",
        {"note": edit.author_note + "\n" + json_text(questions), "previous_sha256": prior.sha256},
    )
    if batch.status == "needs_attention" or batch.state.get("checkpoint_author_required"):
        early = await service.artifact(batch, "early_review")
        batch.state = {
            **batch.state,
            "compared_units": sorted(set(batch.state.get("compared_units", [])) | {len(items)}),
            "compared_early_sha256": early.sha256 if early else None,
        }
    batch.state = {
        **batch.state,
        "checkpoint_author_required": False,
        "message": "作者方案已保存为新版本；尚未调用模型，请核对后继续",
    }
    await choose_next(service, batch)
    batch.status = "awaiting_plan"
    return await service.detail(batch)
