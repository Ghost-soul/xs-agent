"""Finite draft scheduling without compiling model output into domain facts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from novel_writer.db.models import (
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
)
from novel_writer.generation.content import (
    checked_body,
    digest,
    fingerprint,
    json_text,
    parse_object,
)
from novel_writer.generation.craft_models import CraftSpec, execution_spec
from novel_writer.generation.format_trial import KEY, checked
from novel_writer.generation.novel import role_for
from novel_writer.services.errors import WorkflowError

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


def scheduling_plan(raw: str, spec: CraftSpec) -> dict[str, Any]:
    """Best-effort extraction, not validation; never reject a malformed plan."""
    try:
        data = parse_object(raw)
    except (ValueError, RecursionError):
        data = {}
    scenes = data.get("scenes")
    available = scenes if isinstance(scenes, list) and scenes else []
    count = min(len(available), spec.unit_limit) if available else spec.stage_scale.preferred_units
    projected = []
    for index in range(count):
        # If Chief exceeds the budget, group all its material into the finite
        # authorized calls. The original text remains whole and visible.
        group = available[index * len(available) // count : (index + 1) * len(available) // count]
        source = group[0] if len(group) == 1 and isinstance(group[0], dict) else {}
        ids = source.get("character_ids")
        projected.append(
            {
                "event": source.get("event")
                if isinstance(source.get("event"), str)
                else (
                    json_text(group)
                    if group
                    else f"依据 Chief 原文和已写进度完成第 {index + 1} 个事件单元"
                ),
                "choice_and_response": source.get("choice_and_response")
                if isinstance(source.get("choice_and_response"), str)
                else "依据原文设计展开人物行动与回应",
                "consequence": source.get("consequence")
                if isinstance(source.get("consequence"), str)
                else "完成当前事件的实际后果，再交接下一单元",
                "character_ids": [v for v in ids if isinstance(v, str)]
                if isinstance(ids, list)
                else [],
                "size_weight": 1,
                "unvalidated_chief_material": json_text(group),
            }
        )
    return {
        "chapter_goal": data.get("chapter_goal")
        if isinstance(data.get("chapter_goal"), str)
        else spec.direction,
        "bridge": "以正式起点、已写正文和 Chief 原文为准",
        "major_turn": "参考 Chief 原文设计",
        "scenes": projected,
        "questions": [],
        "question_scopes": {},
        "raw_response": raw,
        "trial_schedule": {
            "source": "program-schedule-not-validated-plan",
            "extracted_units": len(available) or None,
            "scheduled_units": count,
            "authorized_limit": spec.unit_limit,
            "fallback": not available,
            "grouped": len(available) > count,
        },
    }


async def note_for(
    service: GenerationService,
    batch: GenerationBatchRecord,
    unit: dict[str, Any],
) -> GenerationArtifactRecord:
    item = await service.session.get(GenerationArtifactRecord, UUID(unit["note_id"]))
    if (
        not item
        or item.batch_id != batch.id
        or item.kind != "trial_note"
        or item.sha256 != fingerprint(item.payload)
        or item.sha256 != unit["note_sha256"]
        or item.payload.get("unit_body_sha256") != unit["body_sha256"]
    ):
        raise WorkflowError("试验笔记的来源或正文绑定已改变")
    return item


async def prepared_reports(
    service: GenerationService,
    batch: GenerationBatchRecord,
    action: str,
) -> dict[str, Any]:
    from novel_writer.generation.longform import units_for

    checked(batch.snapshot)
    items = await units_for(service, batch)
    candidate = await service.artifact(batch, "candidate")
    notes = []
    for unit in items:
        if unit.get("note_id"):
            note = await note_for(service, batch, unit)
            notes.append(
                {
                    "unit": unit["ordinal"],
                    "text": note.payload["text"],
                    "sha256": note.sha256,
                    "validated": False,
                }
            )
    reports: dict[str, Any] = {
        KEY: batch.snapshot[KEY],
        "unit_chain_sha256": fingerprint(items),
        "completed_units": len(notes),
        "trial_notes": notes[-3:],
    }
    if items and candidate:
        last = items[-1]
        reports["trial_previous_prose"] = {
            "source": "trial-candidate-unvalidated",
            "unit": last["ordinal"],
            "body_sha256": last["body_sha256"],
            "coverage": "full-last-unit",
            "recent_prose": {
                "text": candidate.payload["body"][last["start"] : last["end"]],
                "start": last["start"],
                "end": last["end"],
                "complete": True,
            },
        }
    if action.startswith("memory:"):
        ordinal = int(action.split(":")[1])
        if len(items) != ordinal or not candidate or not items[-1].get("complete"):
            raise WorkflowError("试验笔记调度与已写单元不匹配")
        unit = items[-1]
        reports["input_body"] = candidate.payload["body"][unit["start"] : unit["end"]]
        reports["unit_chapter"] = {
            "id": unit["chapter_id"],
            "ordinal": batch.snapshot["candidate_chapter"]["ordinal"] + ordinal - 1,
        }
    if action == "title" and candidate:
        reports["chapter_bodies"] = [
            {"id": u["chapter_id"], "body": candidate.payload["body"][u["start"] : u["end"]]}
            for u in items
        ]
    return reports


async def compile_response(
    service: GenerationService,
    batch: GenerationBatchRecord,
    call: GenerationCallRecord,
    raw: str,
    complete: bool,
) -> None:
    from novel_writer.generation.longform import freeze_segments, units_for

    checked(batch.snapshot)
    if call.request.get(KEY) != batch.snapshot[KEY]:
        raise WorkflowError("调用的试验合同与阶段不匹配")
    spec = execution_spec(batch.spec, call.request)
    if not isinstance(spec, CraftSpec):
        raise WorkflowError("试验需要新版阶段合同")
    candidate = await service.artifact(batch, "candidate")
    plan = await service.artifact(batch, "plan")
    items = await units_for(service, batch)
    if (
        call.request.get("candidate_sha256") != (candidate.sha256 if candidate else None)
        or call.request.get("plan_sha256") != (plan.sha256 if plan else None)
        or call.request.get("unit_chain_sha256") != fingerprint(items)
    ):
        raise WorkflowError("试验响应来源或已写单元链已改变")
    action = call.action
    role = role_for(action)
    slots = call.request["action_slots"]
    following: str | None = None
    if role == "writer" and raw.strip():
        ordinal = int(action.split(":")[1])
        if ordinal != len(items) + 1:
            raise WorkflowError("试验写作顺序与检查点不匹配")
        old = candidate.payload["body"] if candidate else ""
        prefix = old + ("\n\n" if old else "")
        body = checked_body(prefix + checked_body(raw))
        candidate = await service.append(
            batch,
            "candidate",
            {
                "body": body,
                "complete": complete,
                "source_call_id": str(call.id),
                "trial_unvalidated": True,
            },
        )
        items.append(
            {
                "ordinal": ordinal,
                "start": len(prefix),
                "end": len(body),
                "body_sha256": digest(raw),
                "chapter_id": str(uuid5(batch.id, f"trial:{ordinal}:{digest(raw)}")),
                "source_call_id": str(call.id),
                "complete": complete,
            }
        )
        await service.append(batch, "units", {"items": items})
    if not complete:
        raise WorkflowError("供应商响应不完整，原响应和已写内容已保存，不自动续写")
    if not raw.strip():
        raise WorkflowError("供应商没有返回可见内容，原响应已保存")
    if action == "plan":
        await service.append(batch, "plan", scheduling_plan(raw, spec))
        following = "write:1"
    elif role == "writer":
        following = f"memory:{len(items)}"
    elif role == "memory":
        if (
            not candidate
            or not items
            or action != f"memory:{len(items)}"
            or items[-1].get("note_id")
        ):
            raise WorkflowError("试验笔记不能重复或越过当前单元")
        unit = items[-1]
        note = await service.append(
            batch,
            "trial_note",
            {
                "text": raw,
                "validated": False,
                "source_call_id": str(call.id),
                "unit": unit["ordinal"],
                "unit_body_sha256": unit["body_sha256"],
                "candidate_sha256": candidate.sha256,
            },
        )
        items[-1] = {**unit, "note_id": str(note.id), "note_sha256": note.sha256}
        await service.append(batch, "units", {"items": items})
        await freeze_segments(service, batch, spec)
        assert plan
        if len(items) < len(plan.payload["scenes"]):
            following = f"write:{len(items) + 1}"
        else:
            batch.state = {**batch.state, "units_finished": True}
            following = next((s for s in slots if s in {"checker", "title"}), None)
    elif action in {"checker", "title"}:
        assert candidate
        await service.append(
            batch,
            f"trial_{action}",
            {
                "text": raw,
                "validated": False,
                "source_call_id": str(call.id),
                "candidate_sha256": candidate.sha256,
            },
        )
        following = "title" if action == "checker" and "title" in slots else None
    else:
        raise WorkflowError("此动作不属于有限试验流程")
    if following is not None and following not in slots:
        raise WorkflowError("下一试验动作超出已授权槽位")
    batch.next_action = following
    batch.status = "queued" if following else "needs_attention"
    if action == "plan" and spec.pause_after_plan:
        batch.status = "awaiting_plan"
