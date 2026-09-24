

from novel_writer.domain.models import (
    Character,
    CharacterMindState,
    Foreshadowing,
    MindBelief,
    StoryState,
)


def test_mind_state_and_foreshadowing_are_versioned_story_state() -> None:
    state = StoryState(
        characters=(
            Character(
                name="林夜",
                mind_state=CharacterMindState(
                    beliefs=(MindBelief(content="力量能够解决问题", confidence=80),)
                ),
            ),
        ),
        foreshadowings=(
            Foreshadowing(
                content="父亲留下的戒指",
                importance="high",
                introduced_chapter=2,
                last_advanced_chapter=3,
                reminder_after_chapters=5,
            ),
        ),
    )

    restored = StoryState.model_validate(state.model_dump(mode="json"))
    assert restored.characters[0].mind_state.beliefs[0].confidence == 80
    assert restored.foreshadowings[0].status == "active"
