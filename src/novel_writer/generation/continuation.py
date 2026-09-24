"""One author action resolves questions and starts remaining, existing slots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation.schemas import ContinueStageRequest, PlanEdit
from novel_writer.services.errors import ConflictError

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService

DELEGATION = (
    "作者将此项作为普通剧情选择委托 Chief／Writer 在既定题材与有效计划内自主处理。"
    "这不是新增正式事实，不改变人物身份、世界规则或明确作者边界；遇到真实资料缺口仍须说明并暂停。"
)
CONTINUATION_NOTE = (
    "作者确认以下答复／创作委托，并继续原预算内的剩余步骤。"
    "剩余普通剧情选择由 Chief／Writer 在题材与有效计划内处理，悬念写入场面或未来提案，"
    "不再作为 questions 交回作者；真实正式事实缺口或明确作者边界冲突仍须暂停说明。"
)


async def continue_stage(
    service: GenerationService,
    project_id: UUID,
    batch_id: UUID,
    request: ContinueStageRequest,
    key: str,
) -> dict[str, Any]:
    batch = await service.batch(project_id, batch_id, lock=True)
    await service.assert_current(batch)
    if (
        batch.spec.get("stage_mode") != "longform-v1"
        or batch.status not in {"awaiting_plan", "paused"}
        or not batch.authorized
        or batch.preview_sha256 != request.preview_sha256
        or batch.state.get("checkpoint_author_required")
    ):
        raise ConflictError("当前不是可继续的已授权阶段；需先处理实际阻塞或修订方案")
    calls = list(
        await service.session.scalars(
            select(GenerationCallRecord).where(
                GenerationCallRecord.batch_id == batch_id,
            )
        )
    )
    from novel_writer.generation.memory_recovery import resolved_failures

    resolved = await resolved_failures(service, batch, calls)
    if any(c.status != "completed" and str(c.id) not in resolved for c in calls):
        raise ConflictError("仍有未完成或失败调用，不能通过答复入口重发")
    plan = await service.artifact(batch, "plan")
    questions = await service.artifact(batch, "questions")
    if (
        not plan
        or plan.sha256 != request.expected_plan_sha256
        or (questions.sha256 if questions else None) != request.expected_questions_sha256
    ):
        raise ConflictError("方案或待答问题已改变，请刷新后确认")
    pending = (
        {q["question"]: q for q in questions.payload["items"] if q["status"] == "pending"}
        if questions
        else {}
    )
    delegated = set(request.delegated_questions)
    answered = set(request.question_answers)
    if (
        len(delegated) != len(request.delegated_questions)
        or delegated & answered
        or (delegated | answered) - pending.keys()
    ):
        raise ConflictError("答复与委托必须对应当前未决问题，且不能重复")
    if any(pending[q].get("reason") for q in delegated):
        raise ConflictError("已明确标记的事实缺口或作者边界冲突需要实际答复，不能委托猜测")
    if any(not a.strip() or len(a) > 2000 for a in request.question_answers.values()):
        raise ConflictError("每项答复须有明确内容，最多2000字")
    if any(
        q["scope"] == "current_unit" and name not in delegated | answered
        for name, q in pending.items()
    ):
        raise ConflictError("当前问题仍未答复或明确委托，不启动后续步骤")
    answers = {**request.question_answers, **{q: DELEGATION for q in delegated}}
    if pending or answers:
        await service.edit_plan(
            batch,
            PlanEdit(
                plan=plan.payload,
                expected_plan_sha256=plan.sha256,
                author_note=request.author_note or CONTINUATION_NOTE,
                question_answers=answers,
            ),
        )
    elif batch.next_action is None:
        raise ConflictError("当前没有可执行的下一步，请核对方案")
    await service.append(
        batch,
        "author_continuation",
        {
            "plan_sha256": request.expected_plan_sha256,
            "questions_sha256": request.expected_questions_sha256,
            "delegated_questions": request.delegated_questions,
            "question_answers": request.question_answers,
            "author_note": request.author_note,
            "preview_sha256": request.preview_sha256,
            "scope": "existing-unconsumed-slots-until-review",
        },
    )
    await service.authorize(project_id, batch_id, request.preview_sha256, key + ":authorize")
    return await service.detail(batch)
