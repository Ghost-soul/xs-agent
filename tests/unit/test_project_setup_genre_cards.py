import pytest
from pydantic import ValidationError

from novel_writer.api.routes.workflow import ProjectSetupPayload


def test_project_setup_transports_one_side_genre_and_multiple_narratives() -> None:
    payload = ProjectSetupPayload(
        title="多题材小说",
        genre_selection_mode="specified",
        genre_card_id="oriental_fantasy_xianxia",
        secondary_genre_card_ids=(
            "traditional_wuxia_jianghu",
            "fair_play_mystery",
            "girls_love_gl",
        ),
    )

    assert payload.genre_card_id == "oriental_fantasy_xianxia"
    assert len(payload.secondary_genre_card_ids) == 3


def test_project_setup_transports_many_narrative_defaults_for_service_validation() -> None:
    twelve = tuple(f"secondary-{index}" for index in range(12))

    payload = ProjectSetupPayload(
        title="卡池小说",
        genre_selection_mode="specified",
        genre_card_id="primary",
        secondary_genre_card_ids=twelve,
    )

    assert payload.secondary_genre_card_ids == twelve
    larger = ProjectSetupPayload(
        title="多叙事卡", genre_selection_mode="specified", genre_card_id="primary",
        secondary_genre_card_ids=(*twelve, "secondary-12"),
    )
    assert len(larger.secondary_genre_card_ids) == 13


def test_project_setup_rejects_duplicate_or_automatic_secondary_genre_cards() -> None:
    with pytest.raises(ValidationError, match="不能重复"):
        ProjectSetupPayload(
            title="重复题材",
            genre_selection_mode="specified",
            genre_card_id="oriental_fantasy_xianxia",
            secondary_genre_card_ids=("oriental_fantasy_xianxia",),
        )
    with pytest.raises(ValidationError, match="未选择题材卡时不能携带副题材卡"):
        ProjectSetupPayload(
            title="自动题材",
            secondary_genre_card_ids=("traditional_wuxia_jianghu",),
        )


@pytest.mark.parametrize(
    "values", [{}, {"genre_selection_mode": "automatic"}, {"genre_selection_mode": "unselected"}]
)
def test_setup_without_manual_cards_stays_unselected(values: dict[str, str]) -> None:
    payload = ProjectSetupPayload.model_validate({"title": "修士修炼宗门仙侠", **values})

    assert payload.genre_selection_mode == "unselected"
    assert payload.genre_card_id is None
    assert payload.secondary_genre_card_ids == ()
