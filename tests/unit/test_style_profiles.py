from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_writer.services.errors import WorkflowError
from novel_writer.services.style_profiles import (
    StyleAsset,
    StyleProfileService,
    load_quality_cards,
    validate_genre_card_selection,
)


def test_genre_cards_define_reader_contract_checks_and_failures() -> None:
    cards = load_quality_cards()
    card_ids = {card["id"] for card in cards}
    assert "oriental_fantasy_xianxia" in card_ids
    assert {
        "horror_thriller_supernatural",
        "dark_intrigue_human_nature",
        "ensemble_tragedy_drama",
        "adult_extreme_mature",
    }.issubset(card_ids)
    assert len(cards) >= 10
    for card in cards:
        assert card["match_terms"]
        assert card["reader_contract"]
        assert card["quality_checks"]
        assert card["failure_modes"]
        assert card["source_file"].endswith(".md")


def test_public_card_catalog_does_not_expose_the_full_random_point_pool() -> None:
    service = StyleProfileService(AsyncMock())

    catalog = service.catalog()

    assert catalog
    assert all("random_points" not in card for card in catalog)


def test_style_asset_normalizes_duplicate_rules() -> None:
    asset = StyleAsset(
        name="动作先于解释",
        purpose="减少静态说明",
        strengthen=["先写动作", "先写动作", "  写出后果  "],
        avoid=["无功能日常"],
    )

    assert asset.strengthen == ["先写动作", "写出后果"]


def test_style_asset_rejects_oversized_rule() -> None:
    with pytest.raises(ValueError, match="160字"):
        StyleAsset(
            name="过长",
            purpose="验证边界",
            strengthen=["长" * 161],
        )


def test_author_genre_card_selection_rejects_duplicates_and_reads_larger_pools() -> None:
    card_ids = tuple(card["id"] for card in load_quality_cards()[:14])

    with pytest.raises(WorkflowError, match="不能重复"):
        validate_genre_card_selection(card_ids[0], (card_ids[0],))
    assert validate_genre_card_selection(card_ids[0], card_ids[1:14]) == (
        card_ids[0], card_ids[1:14],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "automatic", "unselected", "specified"])
async def test_profile_never_adds_genre_cards_from_title_or_story(mode: str | None) -> None:
    primary = "traditional_wuxia_jianghu"
    session = AsyncMock()
    session.get.side_effect = [
        SimpleNamespace(title="仙侠宗门修士", current_version_id=uuid4()),
        (
            SimpleNamespace(
                selection_mode=mode, genre_id=primary, secondary_genre_ids=[], assets=[]
            )
            if mode is not None
            else None
        ),
        SimpleNamespace(
            state={"world": "修士吸收灵气，修炼功法突破境界。系统面板发布任务并发放奖励。"}
        ),
    ]

    profile = await StyleProfileService(session).get(uuid4())

    assert [card["id"] for card in profile["matched_cards"]] == (
        [primary] if mode == "specified" else []
    )
    assert profile["selection_mode"] == ("specified" if mode == "specified" else "unselected")
    assert profile["matched_mechanisms"] == []
