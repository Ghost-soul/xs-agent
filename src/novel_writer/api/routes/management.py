from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from novel_writer.api.dependencies import IdempotencyKey, Session
from novel_writer.domain.models import (
    Character,
    CharacterRelationship,
    Foreshadowing,
    NarrativePhase,
    NarrativePosition,
    OpenQuestion,
    PlotHistoryDecision,
    PlotThread,
    ScheduledDevelopment,
    StoryForce,
    StoryFoundation,
    WorldLoreEntry,
    WorldRule,
)
from novel_writer.services.blueprint_management import BlueprintManagementService
from novel_writer.services.blueprint_sections import BlueprintSectionName
from novel_writer.services.formal_title_renames import FormalTitleRenameService

router = APIRouter(prefix="/api", tags=["manuscript-management"])

class UpdateStoryBlueprintRequest(BaseModel):
    base_version: int = Field(ge=1)
    characters: list[Character] | None = None
    relationships: list[CharacterRelationship] | None = None
    story_forces: list[StoryForce] = Field(default_factory=list)
    scheduled_developments: list[ScheduledDevelopment] = Field(default_factory=list)
    plot_threads: list[PlotThread] = Field(default_factory=list)
    story_foundation: StoryFoundation = Field(default_factory=StoryFoundation)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    foreshadowings: list[Foreshadowing] | None = None
    narrative_phases: list[NarrativePhase] = Field(default_factory=list)
    plot_history: list[PlotHistoryDecision] = Field(default_factory=list)
    narrative_position: NarrativePosition = Field(default_factory=NarrativePosition)
    world_lore: list[WorldLoreEntry] = Field(default_factory=list)
    world_rules: list[WorldRule] = Field(default_factory=list)
    reason: str = Field(default="", max_length=1000)
    confirmed: Literal[True]


class _BlueprintSectionData(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharactersBlueprintSectionData(_BlueprintSectionData):
    section: Literal["characters"]
    characters: list[Character]


class RelationshipsBlueprintSectionData(_BlueprintSectionData):
    section: Literal["relationships"]
    relationships: list[CharacterRelationship]


class WorldBlueprintSectionData(_BlueprintSectionData):
    section: Literal["world"]
    world_lore: list[WorldLoreEntry]
    world_rules: list[WorldRule]
    story_forces: list[StoryForce]
    scheduled_developments: list[ScheduledDevelopment]


class OutlineBlueprintSectionData(_BlueprintSectionData):
    section: Literal["outline"]
    story_foundation: StoryFoundation
    open_questions: list[OpenQuestion]
    narrative_phases: list[NarrativePhase]
    story_forces: list[StoryForce]
    scheduled_developments: list[ScheduledDevelopment]


class PlotThreadsBlueprintSectionData(_BlueprintSectionData):
    section: Literal["plot_threads"]
    plot_threads: list[PlotThread]


class ForeshadowingsBlueprintSectionData(_BlueprintSectionData):
    section: Literal["foreshadowings"]
    foreshadowings: list[Foreshadowing]


class NarrativePositionBlueprintSectionData(_BlueprintSectionData):
    section: Literal["narrative_position"]
    narrative_position: NarrativePosition


class HistoryBlueprintSectionData(_BlueprintSectionData):
    section: Literal["history"]
    plot_history: list[PlotHistoryDecision]


BlueprintSectionData = Annotated[
    CharactersBlueprintSectionData
    | RelationshipsBlueprintSectionData
    | WorldBlueprintSectionData
    | OutlineBlueprintSectionData
    | PlotThreadsBlueprintSectionData
    | ForeshadowingsBlueprintSectionData
    | NarrativePositionBlueprintSectionData
    | HistoryBlueprintSectionData,
    Field(discriminator="section"),
]


class BlueprintCharacterOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    tier: Literal["A", "B", "C"]


class BlueprintSectionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_options: list[BlueprintCharacterOption] = Field(default_factory=list)


class BlueprintDependentSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formal_dependents_synchronized: bool
    archived_session_count: int
    archived_session_ids: list[UUID]
    cancelled_run_count: int
    cancelled_run_ids: list[UUID]
    audit_preserved: bool


class BlueprintSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    state_version: int = Field(ge=1)
    state_version_id: UUID
    section: BlueprintSectionName
    data: BlueprintSectionData
    context: BlueprintSectionContext
    dependent_sync: BlueprintDependentSync | None = None


class UpdateBlueprintSectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state_version: int = Field(ge=1)
    data: BlueprintSectionData
    reason: str = Field(default="", max_length=1000)
    confirmed: Literal[True]


@router.get("/projects/{project_id}/story-blueprint")
async def story_blueprint(project_id: UUID, session: Session) -> dict[str, Any]:
    return await BlueprintManagementService(session).blueprint(project_id)


@router.get(
    "/projects/{project_id}/story-blueprint/sections/{section}",
    response_model=BlueprintSectionResponse,
)
async def story_blueprint_section(
    project_id: UUID,
    section: BlueprintSectionName,
    session: Session,
) -> dict[str, Any]:
    return await BlueprintManagementService(session).blueprint_section(project_id, section)


@router.put(
    "/projects/{project_id}/story-blueprint/sections/{section}",
    status_code=201,
    response_model=BlueprintSectionResponse,
)
async def update_story_blueprint_section(
    project_id: UUID,
    section: BlueprintSectionName,
    payload: UpdateBlueprintSectionRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await BlueprintManagementService(session).update_blueprint_section(
        project_id,
        section,
        payload.expected_state_version,
        payload.data.model_dump(mode="json"),
        payload.reason,
        payload.confirmed,
        idempotency_key,
    )


@router.put("/projects/{project_id}/story-blueprint", status_code=201)
async def update_story_blueprint(
    project_id: UUID,
    payload: UpdateStoryBlueprintRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    return await BlueprintManagementService(session).update_blueprint(
        project_id,
        payload.base_version,
        (
            [item.model_dump(mode="json") for item in payload.characters]
            if payload.characters is not None
            else None
        ),
        (
            [item.model_dump(mode="json") for item in payload.relationships]
            if payload.relationships is not None
            else None
        ),
        [item.model_dump(mode="json") for item in payload.story_forces],
        [item.model_dump(mode="json") for item in payload.scheduled_developments],
        [item.model_dump(mode="json") for item in payload.plot_threads],
        payload.story_foundation.model_dump(mode="json"),
        [item.model_dump(mode="json") for item in payload.open_questions],
        (
            [item.model_dump(mode="json") for item in payload.foreshadowings]
            if payload.foreshadowings is not None
            else None
        ),
        [item.model_dump(mode="json") for item in payload.narrative_phases],
        [item.model_dump(mode="json") for item in payload.plot_history],
        payload.narrative_position.model_dump(mode="json"),
        [item.model_dump(mode="json") for item in payload.world_lore],
        [item.model_dump(mode="json") for item in payload.world_rules],
        payload.reason,
        payload.confirmed,
        idempotency_key,
    )


class FormalChapterTitleEdit(BaseModel):
    chapter_id: UUID
    title: str = Field(min_length=1, max_length=200)


class UpdateFormalChapterTitlesRequest(BaseModel):
    base_version: int = Field(ge=1)
    titles: list[FormalChapterTitleEdit] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=1000)
    confirmed: Literal[True]


@router.put("/projects/{project_id}/formal-chapter-titles", status_code=201)
async def update_formal_chapter_titles(
    project_id: UUID,
    payload: UpdateFormalChapterTitlesRequest,
    idempotency_key: IdempotencyKey,
    session: Session,
) -> dict[str, Any]:
    """Version an author-edited complete title set without changing prose or StoryState."""

    return await FormalTitleRenameService(session).apply_manual(
        project_id,
        payload.base_version,
        {str(item.chapter_id): item.title for item in payload.titles},
        payload.reason,
        idempotency_key,
    )

