from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from novel_writer.api.dependencies import IdempotencyKey, Session
from novel_writer.api.schemas.generation_creation import NewAmendmentRequest, NewGenerationRequest
from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.schemas import (
    AdoptRequest,
    AmendmentAuthorization,
    AuthorizeRequest,
    CandidateEdit,
    ContinueStageRequest,
    FrozenGenerationSpec,
    PlanEdit,
    ResolveUnknown,
    StageAdoptRequest,
)
from novel_writer.generation.service import GenerationService
from novel_writer.generation.token_limits import INPUT_TOKEN_LIMIT, TOKEN_LIMIT
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.style_profiles import StyleProfileService

router = APIRouter(prefix="/api/projects/{project_id}/generation-batches", tags=["generation"])


class GenerationDetail(BaseModel):
    id: str
    project_id: str
    status: str
    revision: str
    spec: FrozenGenerationSpec
    snapshot: dict[str, Any]
    preview_sha256: str
    next_action: str | None
    state: dict[str, Any]
    artifacts: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    passages: list[dict[str, Any]] = Field(default_factory=list)
    plan_retry_preview: dict[str, Any] | None = None
    input_recovery_available: bool = False
    memory_recovery_available: bool = False
    step_recovery_available: bool = False
    plan_edit_revision: str | None = None


class InputAuthorization(BaseModel):
    input_limit: int = Field(default=INPUT_TOKEN_LIMIT, ge=8000, le=INPUT_TOKEN_LIMIT)
    output_limit: int = Field(default=TOKEN_LIMIT, ge=4000, le=TOKEN_LIMIT)
    all_roles: bool = True
    max_cost_cny: Decimal | None = Field(default=None, ge=0, le=10000)
    preview_sha256: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


class MemoryRecoveryAuthorization(BaseModel):
    output_limit: int = Field(default=TOKEN_LIMIT, ge=2000, le=TOKEN_LIMIT)
    all_roles: bool = True
    max_cost_cny: Decimal | None = Field(default=None, ge=0, le=10000)
    preview_sha256: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


class AdoptionPreviewRequest(BaseModel):
    narrative_position: dict[str, Any]
    factual_changes: dict[str, Any] = Field(default_factory=dict)
    title: str = Field(default="", max_length=200)
    facts_confirmed: bool = False


class StepRecoveryAuthorization(BaseModel):
    max_cost_cny: Decimal = Field(ge=0, le=10000)
    preview_sha256: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]
    uncertain_confirmed: bool = False


class ConfirmCommand(BaseModel):
    confirmed: Literal[True]


def service(request: Request, session: Any) -> GenerationService:
    if request.method not in {"GET", "HEAD"} and (
        request.app.state.generation.draining or not request.app.state.generation.available
    ):
        raise WorkflowError(
            "生成执行器暂不可用，正在恢复数据库连接；已保存记录仍可读取，请稍后再试"
        )
    return GenerationService(session, request.app.state.provider_profile_store)


@router.get("")
async def batches(project_id: UUID, request: Request, session: Session) -> list[dict[str, Any]]:
    current = service(request, session)
    await current._project(project_id)
    items = await session.execute(
        select(
            GenerationBatchRecord.id,
            GenerationBatchRecord.status,
            GenerationBatchRecord.spec["direction"].astext.label("direction"),
            GenerationBatchRecord.created_at,
            GenerationBatchRecord.revision,
        )
        .where(
            GenerationBatchRecord.project_id == project_id,
        )
        .order_by(GenerationBatchRecord.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": str(b.id),
            "status": b.status,
            "direction": b.direction,
            "created_at": b.created_at.isoformat(),
            "revision": b.revision,
        }
        for b in items
    ]


@router.post("", response_model=GenerationDetail)
async def create_batch(
    project_id: UUID,
    payload: NewGenerationRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await service(request, session).create(project_id, payload, idempotency_key)


@router.post("/random-preview", response_model=GenerationDetail)
async def create_random_preview(
    project_id: UUID,
    payload: NewGenerationRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await service(request, session).create(
        project_id, payload, idempotency_key, random_narratives=True
    )


@router.get("/setup")
async def setup(project_id: UUID, request: Request, session: Session) -> dict[str, Any]:
    current = service(request, session)
    project = await current._project(project_id)
    if project.current_version_id is None:
        raise WorkflowError("作品没有正式起点")
    version = await current._version(project.current_version_id)
    style = await StyleProfileService(session).get(project_id)
    return {
        "configuration_revision": "author-intent-v1",
        "context_budget_revision": "world-bounded-v1",
        "output_budget_revision": "chief-output-v1",
        "automation_revision": "stage-auto-v1",
        "base_version_id": str(version.id),
        "version": version.number,
        "characters": [
            {"id": c["id"], "name": c["name"], "library_status": c.get("library_status", "active")}
            for c in version.state.get("characters", [])
        ],
        "narrative_position": version.state.get("narrative_position", {}),
        "relationships": version.state.get("relationships", []),
        "style": style,
        "available_cards": [
            {key: card[key] for key in ("id", "name", "layer")}
            for card in StyleProfileService(session).catalog()
        ],
    }


@router.get("/{batch_id}", response_model=GenerationDetail)
async def batch_detail(
    project_id: UUID, batch_id: UUID, request: Request, session: Session
) -> dict[str, Any]:
    current = service(request, session)
    return await current.detail(await current.batch(project_id, batch_id))


@router.post("/{batch_id}/authorize")
async def authorize(
    project_id: UUID,
    batch_id: UUID,
    payload: AuthorizeRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    result = await service(request, session).authorize(
        project_id, batch_id, payload.preview_sha256, idempotency_key
    )
    await session.commit()
    request.app.state.generation.start(batch_id)
    return result


@router.post("/{batch_id}/pause")
async def pause(
    project_id: UUID, batch_id: UUID, payload: ConfirmCommand, request: Request, session: Session
) -> dict[str, str]:
    current = service(request, session)
    batch = await current.batch(project_id, batch_id, lock=True)
    if batch.status in {"queued", "running"}:
        batch.pause_requested = True
        if batch.status == "queued":
            batch.status = "paused"
    return {"id": str(batch.id), "status": batch.status}


@router.post("/{batch_id}/continue-stage", response_model=GenerationDetail)
async def continue_stage_run(
    project_id: UUID,
    batch_id: UUID,
    payload: ContinueStageRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.continuation import continue_stage

    current = service(request, session)
    command = f"generation_continue_stage:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await continue_stage(current, project_id, batch_id, payload, idempotency_key)
    await current._save_idempotent(command, idempotency_key, result, data)
    await session.commit()
    request.app.state.generation.start(batch_id)
    return result


@router.put("/{batch_id}/candidate", response_model=GenerationDetail)
async def edit_candidate(
    project_id: UUID,
    batch_id: UUID,
    payload: CandidateEdit,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    current = service(request, session)
    command = f"generation_edit:{batch_id}"
    data = payload.model_dump()
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await current.edit_candidate(
        await current.batch(project_id, batch_id, lock=True),
        payload.body,
        payload.expected_body_sha256,
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    return result


@router.post("/{batch_id}/adoption-preview")
async def adoption_preview(
    project_id: UUID,
    batch_id: UUID,
    payload: AdoptionPreviewRequest,
    request: Request,
    session: Session,
) -> dict[str, Any]:
    current = service(request, session)
    return await current.adoption_preview(
        await current.batch(project_id, batch_id),
        payload.narrative_position,
        payload.title,
        payload.factual_changes,
        payload.facts_confirmed,
    )


@router.post("/{batch_id}/adopt")
async def adopt(
    project_id: UUID,
    batch_id: UUID,
    payload: AdoptRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await service(request, session).adopt(project_id, batch_id, payload, idempotency_key)


@router.get("/{batch_id}/calls/{call_id}")
async def call_detail(
    project_id: UUID, batch_id: UUID, call_id: UUID, request: Request, session: Session
) -> dict[str, Any]:
    await service(request, session).batch(project_id, batch_id)
    call = await session.get(GenerationCallRecord, call_id)
    if call is None or call.batch_id != batch_id:
        raise ConflictError("调用不属于此批次")
    return {
        "request": call.request,
        "response": call.response,
        "status": call.status,
        "request_sha256": call.request_sha256,
    }


@router.put("/{batch_id}/plan", response_model=GenerationDetail)
async def edit_plan(
    project_id: UUID,
    batch_id: UUID,
    payload: PlanEdit,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    current = service(request, session)
    command = f"generation_plan_edit:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await current.edit_plan(await current.batch(project_id, batch_id, lock=True), payload)
    await current._save_idempotent(command, idempotency_key, result, data)
    return result


@router.get("/{batch_id}/input-preview")
async def input_preview(
    project_id: UUID,
    batch_id: UUID,
    request: Request,
    session: Session,
    input_limit: int | None = Query(default=None, ge=8000, le=INPUT_TOKEN_LIMIT),
    output_limit: int = Query(default=TOKEN_LIMIT, ge=4000, le=TOKEN_LIMIT),
    all_roles: bool = True,
    max_cost_cny: Annotated[Decimal | None, Query(ge=0, le=10000)] = None,
) -> dict[str, Any]:
    from novel_writer.generation.input_recovery import preview_input

    current = service(request, session)
    return await preview_input(
        current,
        await current.batch(project_id, batch_id),
        input_limit,
        output_limit=output_limit,
        all_roles=all_roles,
        max_cost_cny=max_cost_cny,
    )


@router.post("/{batch_id}/input-authorize", response_model=GenerationDetail)
async def input_authorize(
    project_id: UUID,
    batch_id: UUID,
    payload: InputAuthorization,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.input_recovery import authorize_input

    current = service(request, session)
    command = f"generation_input_authorize:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await authorize_input(
        current,
        await current.batch(project_id, batch_id, lock=True),
        payload.input_limit,
        payload.preview_sha256,
        idempotency_key,
        output_limit=payload.output_limit,
        all_roles=payload.all_roles,
        max_cost_cny=payload.max_cost_cny,
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    await session.commit()
    request.app.state.generation.start(batch_id)
    return result


@router.get("/{batch_id}/memory-recovery-preview")
async def memory_recovery_preview(
    project_id: UUID,
    batch_id: UUID,
    request: Request,
    session: Session,
    output_limit: int | None = Query(default=None, ge=2000, le=100000),
    all_roles: bool = True,
    max_cost_cny: Annotated[Decimal | None, Query(ge=0, le=10000)] = None,
) -> dict[str, Any]:
    from novel_writer.generation.memory_recovery import preview_memory

    current = service(request, session)
    return await preview_memory(
        current,
        await current.batch(project_id, batch_id),
        output_limit,
        all_roles=all_roles,
        max_cost_cny=max_cost_cny,
    )


@router.post("/{batch_id}/memory-recovery-authorize", response_model=GenerationDetail)
async def memory_recovery_authorize(
    project_id: UUID,
    batch_id: UUID,
    payload: MemoryRecoveryAuthorization,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.memory_recovery import authorize_memory

    current = service(request, session)
    command = f"generation_memory_recovery:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await authorize_memory(
        current,
        await current.batch(project_id, batch_id, lock=True),
        payload.output_limit,
        payload.preview_sha256,
        idempotency_key,
        all_roles=payload.all_roles,
        max_cost_cny=payload.max_cost_cny,
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    await session.commit()
    request.app.state.generation.start(batch_id)
    return result


@router.get("/{batch_id}/step-recovery-preview")
async def step_recovery_preview(
    project_id: UUID,
    batch_id: UUID,
    request: Request,
    session: Session,
    max_cost_cny: Annotated[Decimal | None, Query(ge=0, le=10000)] = None,
) -> dict[str, Any]:
    from novel_writer.generation.step_recovery import preview_step

    current = service(request, session)
    return await preview_step(current, await current.batch(project_id, batch_id), max_cost_cny)


@router.post("/{batch_id}/step-recovery-authorize", response_model=GenerationDetail)
async def step_recovery_authorize(
    project_id: UUID,
    batch_id: UUID,
    payload: StepRecoveryAuthorization,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.step_recovery import authorize_step

    current = service(request, session)
    command = f"generation_step_recovery:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await authorize_step(
        current,
        await current.batch(project_id, batch_id, lock=True),
        payload.preview_sha256,
        idempotency_key,
        payload.max_cost_cny,
        payload.uncertain_confirmed,
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    await session.commit()
    request.app.state.generation.start(batch_id)
    return result


@router.post("/{batch_id}/resolve-unknown", response_model=GenerationDetail)
async def resolve_unknown(
    project_id: UUID,
    batch_id: UUID,
    payload: ResolveUnknown,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    current = service(request, session)
    command = f"generation_resolve:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await current.resolve_unknown(
        await current.batch(project_id, batch_id, lock=True), payload.note
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    return result


@router.post("/{batch_id}/calls/{call_id}/revalidate")
async def revalidate(
    project_id: UUID,
    batch_id: UUID,
    call_id: UUID,
    payload: ConfirmCommand,
    request: Request,
    session: Session,
) -> dict[str, str]:
    from novel_writer.generation.diagnostics import revalidation_blocker

    current = service(request, session)
    batch = await current.batch(project_id, batch_id, lock=True)
    call = await session.get(GenerationCallRecord, call_id)
    if (
        call is None
        or call.batch_id != batch_id
        or call.status not in {"response_saved", "local_failure"}
    ):
        raise ConflictError("仅完整已保存响应的本地失败可以重验")
    candidate = await current.artifact(batch, "candidate")
    if reason := revalidation_blocker(batch, call, candidate):
        raise ConflictError(reason)
    batch.pause_requested = True
    await session.commit()
    await request.app.state.generation.compile_response(batch_id, call_id)
    return {"status": "locally_revalidated", "provider_requests": "0"}


@router.post("/{batch_id}/amendment-preview")
async def amendment_preview(
    project_id: UUID,
    batch_id: UUID,
    payload: NewAmendmentRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.amendments import preview_amendment

    current = service(request, session)
    command = f"generation_amendment_preview:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await preview_amendment(
        current, await current.batch(project_id, batch_id, lock=True), payload
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    return result


@router.post("/{batch_id}/amendment-authorize")
async def amendment_authorize(
    project_id: UUID,
    batch_id: UUID,
    payload: AmendmentAuthorization,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.amendments import authorize_amendment

    current = service(request, session)
    command = f"generation_amendment_authorize:{batch_id}"
    data = payload.model_dump(mode="json")
    cached = await current._idempotent(command, idempotency_key, data)
    if cached is not None:
        return cached
    result = await authorize_amendment(
        current, await current.batch(project_id, batch_id, lock=True), payload.preview_sha256
    )
    await current._save_idempotent(command, idempotency_key, result, data)
    await session.commit()
    request.app.state.generation.start(batch_id)
    return result


@router.get("/{batch_id}/stage-chapters")
async def stage_chapters(
    project_id: UUID, batch_id: UUID, request: Request, session: Session
) -> dict[str, Any]:
    from novel_writer.generation.stage_adoption import chapter_suggestions

    current = service(request, session)
    return await chapter_suggestions(current, await current.batch(project_id, batch_id))


@router.post("/{batch_id}/stage-adoption-preview")
async def stage_adoption_preview(
    project_id: UUID, batch_id: UUID, payload: StageAdoptRequest, request: Request, session: Session
) -> dict[str, Any]:
    from novel_writer.generation.stage_adoption import preview

    current = service(request, session)
    return await preview(current, await current.batch(project_id, batch_id, lock=True), payload)


@router.post("/{batch_id}/stage-adopt")
async def stage_adopt(
    project_id: UUID,
    batch_id: UUID,
    payload: StageAdoptRequest,
    request: Request,
    session: Session,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    from novel_writer.generation.stage_adoption import adopt

    return await adopt(service(request, session), project_id, batch_id, payload, idempotency_key)
