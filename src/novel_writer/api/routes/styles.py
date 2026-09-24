from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from novel_writer.api.dependencies import Session
from novel_writer.services.style_profiles import StyleProfileService

router = APIRouter(prefix="/api", tags=["style-profiles"])


class SaveStyleProfileRequest(BaseModel):
    genre_id: str = ""
    selection_mode: Literal["automatic", "unselected", "specified"] = "unselected"
    genre_card_id: str | None = None
    secondary_genre_card_ids: tuple[str, ...] = ()
    assets: list[dict[str, Any]] = Field(max_length=12)
    confirmed: Literal[True]




@router.get("/genre-quality-cards")
async def genre_quality_cards(session: Session) -> list[dict[str, Any]]:
    return StyleProfileService(session).catalog()


@router.get("/projects/{project_id}/style-profile")
async def style_profile(project_id: UUID, session: Session) -> dict[str, Any]:
    return await StyleProfileService(session).get(project_id)


@router.put("/projects/{project_id}/style-profile")
async def save_style_profile(
    project_id: UUID, payload: SaveStyleProfileRequest, session: Session
) -> dict[str, Any]:
    return await StyleProfileService(session).save(
        project_id,
        payload.assets,
        payload.genre_id,
        selection_mode=payload.selection_mode,
        genre_card_id=payload.genre_card_id,
        secondary_genre_card_ids=payload.secondary_genre_card_ids,
    )
