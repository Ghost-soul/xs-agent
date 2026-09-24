from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from novel_writer.api.dependencies import Session
from novel_writer.services.reference_styles import (
    ReferenceStyleService,
    SampleCategory,
    SampleReview,
)

router = APIRouter(prefix="/api/projects/{project_id}/reference-style", tags=["reference-style"])


class RegisterCorpusRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=1000)
    local_analysis_allowed: Literal[True]
    provider_excerpt_allowed: bool = False
    confirmed: Literal[True]


class UpdateSampleRequest(SampleReview):
    confirmed: Literal[True]


class CreateProfileRequest(BaseModel):
    manifest_id: UUID
    confirmed: Literal[True]


class UpdateProfileRequest(BaseModel):
    positive_contract: list[dict[str, Any]] = Field(min_length=1, max_length=12)
    negative_transfer_rules: list[str] = Field(min_length=1, max_length=12)
    confirmed: Literal[True]


class ActivateProfileRequest(BaseModel):
    blind_test: dict[str, Any]
    confirmed: Literal[True]


class SetGlobalReferenceStyleRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    confirmed: Literal[True]


@router.get("")
async def reference_style_workspace(
    project_id: UUID,
    session: Session,
    category: SampleCategory | None = None,
) -> dict[str, Any]:
    return await ReferenceStyleService(session).manifest(project_id, sample_category=category)


@router.post("/corpora")
async def register_reference_corpus(
    project_id: UUID, payload: RegisterCorpusRequest, session: Session
) -> dict[str, Any]:
    return await ReferenceStyleService(session).register(
        project_id,
        payload.source_path,
        local_analysis_allowed=payload.local_analysis_allowed,
        provider_excerpt_allowed=payload.provider_excerpt_allowed,
    )


@router.put("/samples/{sample_id}")
async def review_reference_sample(
    project_id: UUID, sample_id: UUID, payload: UpdateSampleRequest, session: Session
) -> dict[str, Any]:
    review = SampleReview.model_validate(payload.model_dump(exclude={"confirmed"}))
    return await ReferenceStyleService(session).review_sample(project_id, sample_id, review)


@router.post("/profiles")
async def create_reference_profile(
    project_id: UUID, payload: CreateProfileRequest, session: Session
) -> dict[str, Any]:
    return await ReferenceStyleService(session).create_draft(project_id, payload.manifest_id)


@router.put("/profiles/{profile_id}")
async def update_reference_profile(
    project_id: UUID, profile_id: UUID, payload: UpdateProfileRequest, session: Session
) -> dict[str, Any]:
    return await ReferenceStyleService(session).update_draft(
        project_id,
        profile_id,
        positive_contract=payload.positive_contract,
        negative_transfer_rules=payload.negative_transfer_rules,
    )


@router.post("/profiles/{profile_id}/activate")
async def activate_reference_profile(
    project_id: UUID, profile_id: UUID, payload: ActivateProfileRequest, session: Session
) -> dict[str, Any]:
    return await ReferenceStyleService(session).activate(
        project_id, profile_id, blind_test=payload.blind_test, confirmed=payload.confirmed
    )




@router.put("/profiles/{profile_id}/global-default")
async def set_global_reference_style_default(
    project_id: UUID,
    profile_id: UUID,
    payload: SetGlobalReferenceStyleRequest,
    session: Session,
) -> dict[str, Any]:
    return await ReferenceStyleService(session).set_global_default(
        project_id,
        profile_id,
        confirmed=payload.confirmed,
        reason=payload.reason,
    )
