from uuid import uuid4

import pytest
from pydantic import ValidationError

from novel_writer.api.app import create_app
from novel_writer.core.config import Settings
from novel_writer.domain.models import StoryState
from novel_writer.services.blueprint_sections import (
    blueprint_section_response,
    rebuild_story_state_for_blueprint_section,
)
from novel_writer.services.errors import WorkflowError


def _state() -> StoryState:
    character_id = uuid4()
    return StoryState.model_validate(
        {
            "characters": [{"id": character_id, "name": "林遥"}],
            "world_lore": [
                {
                    "category": "geography",
                    "name": "雨港",
                    "summary": "终年落雨的港城",
                }
            ],
            "world_rules": [{"statement": "潮印必须支付记忆"}],
            "story_forces": [
                {"layer": "world", "kind": "势力", "name": "潮印商会"},
                {"layer": "outline", "kind": "主题", "name": "记忆与身份"},
            ],
            "scheduled_developments": [
                {"layer": "world", "name": "潮汐倒灌", "outcome": "旧港封闭"},
                {"layer": "outline", "name": "账册公开", "outcome": "联盟破裂"},
            ],
            "plot_threads": [{"name": "追查潮印账册"}],
            "plot_history": [
                {"plan": "直接公开账册", "status": "cancelled", "reason": "证据不足"}
            ],
        }
    )


def test_section_projection_excludes_unrelated_blueprint_payload() -> None:
    state = _state()
    response = blueprint_section_response(uuid4(), 4, uuid4(), state, "world")

    assert set(response["data"]) == {
        "section",
        "world_lore",
        "world_rules",
        "story_forces",
        "scheduled_developments",
    }
    assert response["data"]["section"] == "world"
    assert [item["layer"] for item in response["data"]["story_forces"]] == ["world"]
    assert "characters" not in response["data"]
    assert "plot_threads" not in response["data"]


def test_relationship_projection_returns_only_compact_character_context() -> None:
    state = _state()
    response = blueprint_section_response(uuid4(), 4, uuid4(), state, "relationships")

    assert response["data"] == {"section": "relationships", "relationships": []}
    assert response["context"]["character_options"] == [
        {"id": str(state.characters[0].id), "name": "林遥", "tier": "A"}
    ]


def test_world_section_rebuild_preserves_every_other_story_state_field() -> None:
    state = _state()
    updated = rebuild_story_state_for_blueprint_section(
        state,
        "world",
        {
            "section": "world",
            "world_lore": [],
            "world_rules": [{"statement": "潮印只在雨中生效"}],
            "story_forces": [],
            "scheduled_developments": [],
        },
    )

    assert updated.characters == state.characters
    assert updated.plot_threads == state.plot_threads
    assert updated.plot_history == state.plot_history
    assert [item.layer for item in updated.story_forces] == ["outline"]
    assert [item.layer for item in updated.scheduled_developments] == ["outline"]
    assert [item.statement for item in updated.world_rules] == ["潮印只在雨中生效"]


def test_section_rebuild_rejects_cross_section_items_and_mismatched_discriminator() -> None:
    state = _state()
    with pytest.raises(WorkflowError, match="outside the world"):
        rebuild_story_state_for_blueprint_section(
            state,
            "world",
            {
                "section": "world",
                "world_lore": [],
                "world_rules": [],
                "story_forces": [{"layer": "outline", "kind": "主题", "name": "越界"}],
                "scheduled_developments": [],
            },
        )
    with pytest.raises(WorkflowError, match="does not match"):
        rebuild_story_state_for_blueprint_section(
            state,
            "history",
            {"section": "plot_threads", "plot_history": []},
        )


def test_section_rebuild_validates_references_against_the_complete_state() -> None:
    state = _state()
    with pytest.raises(ValidationError, match="relationship target references an unknown id"):
        rebuild_story_state_for_blueprint_section(
            state,
            "relationships",
            {
                "section": "relationships",
                "relationships": [
                    {
                        "source_character_id": str(state.characters[0].id),
                        "target_character_id": str(uuid4()),
                        "relation_type": "ally",
                    }
                ],
            },
        )


def test_blueprint_section_openapi_contract_is_closed_and_version_bound() -> None:
    schema = create_app(Settings(local_token="test-token", _env_file=None)).openapi()
    path = schema["paths"][
        "/api/projects/{project_id}/story-blueprint/sections/{section}"
    ]

    request_ref = path["put"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
    assert "expected_state_version" in request_schema["required"]
    assert request_schema["additionalProperties"] is False
    assert path["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BlueprintSectionResponse"
    }
