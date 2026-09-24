from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.domain.state import StoryState
from novel_writer.generation.content import checked_body, digest, parse_object
from novel_writer.generation.feedback import execution_spec
from novel_writer.generation.guidance import selected_plan
from novel_writer.generation.logic import checker_feedback, reader_feedback
from novel_writer.generation.novel import invalidate_candidate, next_action, role_for, slots_for
from novel_writer.generation.reports import (
    apply_edit,
    memory_result,
)
from novel_writer.generation.schemas import LONGFORM_REVISION, FrozenGenerationSpec
from novel_writer.generation.service import GenerationService
from novel_writer.services.errors import WorkflowError


class ReportCompilationError(ValueError):
    """A complete report failed locally, separately from source/binding failures."""


@contextmanager
def report_boundary() -> Iterator[None]:
    try:
        yield
    except ValueError as error:
        raise ReportCompilationError(str(error)) from error


async def compile_stage(
    service: GenerationService,
    batch: GenerationBatchRecord,
    call: GenerationCallRecord,
    raw: str,
    complete: bool,
) -> None:
    spec = execution_spec(batch.spec, call.request)
    action = call.action
    role = role_for(action)
    candidate = await service.artifact(batch, "candidate")
    if action not in {"plan", "write"} and (
        candidate is None or candidate.sha256 != call.request.get("candidate_sha256")
    ):
        raise WorkflowError("响应绑定的候选已经改变，不使用旧报告覆盖新稿")
    if action in {"write", "rewrite"}:
        if raw.lstrip().startswith(("{", "```json")):
            try:
                issue = parse_object(raw)
            except ValueError:
                issue = {}
            if "generation_blocked" in issue:
                await service.append(batch, "writer_issue", issue)
                raise WorkflowError("Writer 报告设计冲突，请查看问题；未生成正文")
        if raw:
            batch.state = invalidate_candidate(batch.state)
            candidate = await service.append(
                batch,
                "candidate",
                {
                    "body": checked_body(raw),
                    "complete": complete,
                    "source_call_id": str(call.id),
                    "parent_sha256": candidate.sha256 if candidate else None,
                },
            )
    if not complete:
        raise WorkflowError("供应商响应不完整，已保存内容；不自动重发或继续")
    slots = list(call.request["action_slots"])
    following = next_action(slots, action)
    if action == "plan":
        plan = selected_plan(raw, spec, batch.snapshot)
        await service.append(batch, "plan", plan.model_dump(mode="json"))
        await service.append(
            batch,
            "questions",
            {
                "items": [
                    {
                        "question": q,
                        "source_call_id": str(call.id),
                        "status": "pending",
                        "scope": plan.question_scopes.get(q, "current_unit"),
                    }
                    for q in plan.questions
                ]
            },
        )
        blocking = any(
            plan.question_scopes.get(q, "current_unit") == "current_unit" for q in plan.questions
        )
        if spec.pause_after_plan or blocking:
            batch.status = "awaiting_plan"
            batch.next_action = None if blocking else "write"
            return
    elif role == "memory":
        assert candidate is not None
        base = await service._version(batch.base_version_id)
        state = StoryState.model_validate(base.state)
        with report_boundary():
            result = memory_result(
                raw,
                candidate.payload["body"],
                state,
                base.number,
                batch.snapshot["candidate_chapter"],
            )
        result["candidate_sha256"] = candidate.sha256
        await service.append(batch, "memory", result)
    elif role == "checker":
        assert candidate is not None
        with report_boundary():
            result = checker_feedback(raw, candidate.payload["body"], spec)
        result["candidate_sha256"] = candidate.sha256
        await service.append(batch, "checker", result)
        if action == "checker":
            scope = await edit_scope(service, batch)
            following = next_action(slots, action, editable=bool(scope["paragraph_ids"]))
    elif role == "reader":
        assert candidate is not None
        result = reader_feedback(
            raw,
            candidate.payload["body"],
            batch.snapshot["context"].get("recent_chapters", []),
            spec,
        )
        result["candidate_sha256"] = candidate.sha256
        await service.append(batch, "review", result)
    elif action in {"editor", "amend"}:
        assert candidate is not None
        scope = call.request["edit_scope"]
        with report_boundary():
            result = apply_edit(
                raw,
                candidate.payload["body"],
                scope["paragraph_ids"],
                scope["protected_paragraph_ids"],
            )
        await service.append(
            batch,
            "edit_decisions",
            {**result, "source_call_id": str(call.id), "candidate_sha256": candidate.sha256},
        )
        if result["changed"]:
            batch.state = invalidate_candidate(batch.state)
            candidate = await service.append(
                batch,
                "candidate",
                {
                    "body": result["body"],
                    "complete": True,
                    "source_call_id": str(call.id),
                    "parent_sha256": candidate.sha256,
                },
            )
        if action == "editor":
            following = next_action(slots, action, changed=result["changed"])
    elif action == "title":
        assert candidate is not None
        try:
            titles = parse_object(raw).get("titles", [])
            if not isinstance(titles, list):
                titles = []
            titles = [t.strip() for t in titles if isinstance(t, str) and 0 < len(t.strip()) <= 80][
                :3
            ]
        except ValueError:
            titles = []
        await service.append(
            batch, "title", {"titles": titles, "candidate_sha256": candidate.sha256}
        )
    batch.next_action = following
    batch.status = "queued" if following else "needs_attention"
    if not following and candidate:
        # R2 one complete unit -> one chapter; mapping remains exact and lossless.
        await service.append(
            batch,
            "segments",
            {
                "candidate_sha256": candidate.sha256,
                "body_sha256": digest(candidate.payload["body"]),
                "segments": [{"start": 0, "end": len(candidate.payload["body"])}],
                "tail": None,
            },
        )


async def edit_scope(service: GenerationService, batch: GenerationBatchRecord) -> dict[str, Any]:
    memory = await service.artifact(batch, "memory")
    checker = await service.artifact(batch, "checker")
    protected = list(
        dict.fromkeys(
            e["id"]
            for change in (memory.payload.get("changes", []) if memory else [])
            for e in change["evidence"]
        )
    )
    allowed = list(
        dict.fromkeys(
            p
            for issue in (checker.payload.get("issues", []) if checker else [])
            if issue["local_edit"] and issue["severity"] == "warning"
            for p in issue["paragraph_ids"]
            if p not in protected
        )
    )[:3]
    return {
        "paragraph_ids": allowed,
        "protected_paragraph_ids": protected,
        "instruction": "仅处理 Checker 精确指出的局部问题，不能补写题材或改变事实",
        "issues": checker.payload.get("issues", []) if checker else [],
    }


async def active_slots(service: GenerationService, batch: GenerationBatchRecord) -> list[str]:
    amendment = await service.artifact(batch, "amendment")
    if batch.state.get("amendment_authorized_sha256"):
        if not amendment or amendment.sha256 != batch.state["amendment_authorized_sha256"]:
            raise WorkflowError("修订授权与预览来源失配")
        return list(amendment.payload["slots"])
    return slots_for(FrozenGenerationSpec.model_validate(batch.spec))


async def continue_independent_reader(
    service: GenerationService, batch: GenerationBatchRecord, call: GenerationCallRecord
) -> bool:
    """Keep only the already-authorized cold read after a local report failure."""
    candidate = await service.artifact(batch, "candidate")
    if (
        not candidate
        or candidate.payload.get("complete") is not True
        or candidate.sha256 != call.request.get("candidate_sha256")
    ):
        return False
    slots = await active_slots(service, batch)
    reader = "reader_amend" if batch.state.get("amendment_authorized_sha256") else "reader"
    if (
        not batch.authorized
        or slots != call.request.get("action_slots")
        or call.action not in slots
        or reader not in slots[slots.index(call.action) + 1 :]
    ):
        return False
    slot = slots.index(reader) + (
        (100 if batch.revision == LONGFORM_REVISION else 20) if reader == "reader_amend" else 0
    )
    used = await service.session.scalar(
        select(GenerationCallRecord.id).where(
            GenerationCallRecord.batch_id == batch.id, GenerationCallRecord.slot == slot
        )
    )
    if used is not None:
        return False
    await service.append(
        batch,
        "dependency_skip",
        {
            "candidate_sha256": candidate.sha256,
            "failed_call_id": str(call.id),
            "failed_action": call.action,
            "skipped_actions": slots[slots.index(call.action) + 1 : slots.index(reader)],
            "next_action": reader,
        },
    )
    batch.status = "queued"
    batch.next_action = reader
    return True
