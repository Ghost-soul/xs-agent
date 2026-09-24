from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from novel_writer.domain.models import StoryState
from novel_writer.services.errors import WorkflowError

type BlueprintSectionName = Literal[
    "characters",
    "relationships",
    "world",
    "outline",
    "plot_threads",
    "foreshadowings",
    "narrative_position",
    "history",
]

BLUEPRINT_SECTION_NAMES: tuple[BlueprintSectionName, ...] = (
    "characters",
    "relationships",
    "world",
    "outline",
    "plot_threads",
    "foreshadowings",
    "narrative_position",
    "history",
)


def blueprint_section_response(
    project_id: UUID,
    state_version: int,
    state_version_id: UUID,
    state: StoryState,
    section: BlueprintSectionName,
) -> dict[str, Any]:
    """Project one editable Blueprint section without returning the full StoryState."""

    return {
        "project_id": str(project_id),
        "state_version": state_version,
        "state_version_id": str(state_version_id),
        "section": section,
        "data": _section_data(state, section),
        "context": {
            "character_options": [
                {"id": str(item.id), "name": item.name, "tier": item.tier}
                for item in state.characters
                if item.library_status == "active"
            ]
            if section == "relationships"
            else []
        },
    }


def rebuild_story_state_for_blueprint_section(
    current: StoryState,
    section: BlueprintSectionName,
    data: dict[str, Any],
) -> StoryState:
    """Replace one section and validate the reconstructed complete StoryState."""

    submitted_section = data.get("section")
    if submitted_section != section:
        raise WorkflowError("blueprint section payload does not match the requested section")

    state_data = current.model_dump(mode="json")
    if section == "characters":
        characters = _required_list(data, "characters")
        submitted_ids = {str(item.get("id")) for item in characters if item.get("id")}
        preserved_retired = [
            item.model_copy(update={"library_status": "retired"}).model_dump(mode="json")
            for item in current.characters
            if str(item.id) not in submitted_ids
        ]
        state_data["characters"] = [*characters, *preserved_retired]
    elif section == "relationships":
        state_data["relationships"] = _required_list(data, "relationships")
    elif section == "world":
        story_forces = _required_list(data, "story_forces")
        developments = _required_list(data, "scheduled_developments")
        _require_layer(story_forces, "world", "story_forces")
        _require_layer(developments, "world", "scheduled_developments")
        state_data.update(
            {
                "world_lore": _required_list(data, "world_lore"),
                "world_rules": _required_list(data, "world_rules"),
                "story_forces": [
                    *(
                        item.model_dump(mode="json")
                        for item in current.story_forces
                        if item.layer != "world"
                    ),
                    *story_forces,
                ],
                "scheduled_developments": [
                    *(
                        item.model_dump(mode="json")
                        for item in current.scheduled_developments
                        if item.layer != "world"
                    ),
                    *developments,
                ],
            }
        )
    elif section == "outline":
        story_forces = _required_list(data, "story_forces")
        developments = _required_list(data, "scheduled_developments")
        _require_layer(story_forces, "outline", "story_forces")
        _require_layer(developments, "outline", "scheduled_developments")
        state_data.update(
            {
                "story_foundation": _required_dict(data, "story_foundation"),
                "open_questions": _required_list(data, "open_questions"),
                "narrative_phases": _required_list(data, "narrative_phases"),
                "story_forces": [
                    *(
                        item.model_dump(mode="json")
                        for item in current.story_forces
                        if item.layer != "outline"
                    ),
                    *story_forces,
                ],
                "scheduled_developments": [
                    *(
                        item.model_dump(mode="json")
                        for item in current.scheduled_developments
                        if item.layer != "outline"
                    ),
                    *developments,
                ],
            }
        )
    elif section == "plot_threads":
        state_data["plot_threads"] = _required_list(data, "plot_threads")
    elif section == "foreshadowings":
        state_data["foreshadowings"] = _required_list(data, "foreshadowings")
    elif section == "narrative_position":
        state_data["narrative_position"] = _required_dict(data, "narrative_position")
    elif section == "history":
        state_data["plot_history"] = _required_list(data, "plot_history")
    else:  # pragma: no cover - the route and type alias form a closed allowlist.
        raise WorkflowError("unsupported blueprint section")
    return StoryState.model_validate(state_data)


def _section_data(state: StoryState, section: BlueprintSectionName) -> dict[str, Any]:
    if section == "characters":
        return {
            "section": section,
            "characters": [
                item.model_dump(mode="json")
                for item in state.characters
                if item.library_status == "active"
            ],
        }
    if section == "relationships":
        return {
            "section": section,
            "relationships": [item.model_dump(mode="json") for item in state.relationships],
        }
    if section == "world":
        return {
            "section": section,
            "world_lore": [item.model_dump(mode="json") for item in state.world_lore],
            "world_rules": [item.model_dump(mode="json") for item in state.world_rules],
            "story_forces": [
                item.model_dump(mode="json")
                for item in state.story_forces
                if item.layer == "world"
            ],
            "scheduled_developments": [
                item.model_dump(mode="json")
                for item in state.scheduled_developments
                if item.layer == "world"
            ],
        }
    if section == "outline":
        return {
            "section": section,
            "story_foundation": state.story_foundation.model_dump(mode="json"),
            "open_questions": [
                item.model_dump(mode="json") for item in state.open_questions
            ],
            "narrative_phases": [
                item.model_dump(mode="json") for item in state.narrative_phases
            ],
            "story_forces": [
                item.model_dump(mode="json")
                for item in state.story_forces
                if item.layer == "outline"
            ],
            "scheduled_developments": [
                item.model_dump(mode="json")
                for item in state.scheduled_developments
                if item.layer == "outline"
            ],
        }
    if section == "plot_threads":
        return {
            "section": section,
            "plot_threads": [item.model_dump(mode="json") for item in state.plot_threads],
        }
    if section == "foreshadowings":
        return {
            "section": section,
            "foreshadowings": [
                item.model_dump(mode="json") for item in state.foreshadowings
            ],
        }
    if section == "narrative_position":
        return {
            "section": section,
            "narrative_position": state.narrative_position.model_dump(mode="json"),
        }
    if section == "history":
        return {
            "section": section,
            "plot_history": [item.model_dump(mode="json") for item in state.plot_history],
        }
    raise WorkflowError("unsupported blueprint section")


def _required_list(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WorkflowError(f"blueprint section field {key} must be a list of objects")
    return value


def _required_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise WorkflowError(f"blueprint section field {key} must be an object")
    return value


def _require_layer(items: list[dict[str, Any]], layer: str, field: str) -> None:
    if any(item.get("layer") != layer for item in items):
        raise WorkflowError(f"{field} contains an item outside the {layer} blueprint section")
