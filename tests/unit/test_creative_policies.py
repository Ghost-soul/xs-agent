from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from novel_writer.services.creative_policies import CreativePolicy, CreativePolicyService


def test_creative_policy_defaults_match_author_workspace_recommendations() -> None:
    policy = CreativePolicy()

    assert policy.target_chapters == 1
    assert policy.scene_target_chars == 4000
    assert policy.chapter_min_chars == 4000
    assert policy.chapter_target_chars == 5000
    assert policy.chapter_max_chars == 6000
    assert policy.target_min_chars == 20000
    assert policy.target_max_chars == 30000
    assert policy.minimum_follow_read_signal == "strong"
    assert policy.stop_on_blocking_risk is True
    assert policy.target_cost_cny == Decimal("3")
    assert policy.soft_budget_cny == Decimal("5")
    assert policy.hard_safety_ceiling_cny == Decimal("20")


def test_creative_policy_response_keeps_recommendations_and_all_safety_boundaries() -> None:
    response = CreativePolicyService._response(
        uuid4(), CreativePolicy(scene_target_chars=1800), saved=True
    )

    assert response["scene_target_chars"] == 1800
    assert response["recommended_policy"]["scene_target_chars"] == 4000
    assert response["recommended_policy"]["chapter_min_chars"] == 4000
    assert response["recommended_policy"]["chapter_max_chars"] == 6000
    assert "provider_profile_consistency" in response["immutable_safety_boundaries"]


def test_creative_policy_accepts_author_selected_scene_and_quality_thresholds() -> None:
    policy = CreativePolicy(
        target_chapters=3,
        scene_target_chars=1800,
        chapter_target_chars=5200,
        target_min_chars=12000,
        target_max_chars=18000,
        minimum_follow_read_signal="weak",
        stop_on_blocking_risk=False,
        target_cost_cny=Decimal("6"),
        soft_budget_cny=Decimal("8"),
        hard_safety_ceiling_cny=Decimal("12"),
    )

    assert policy.scene_target_chars == 1800
    assert policy.chapter_target_chars == 5200
    assert policy.minimum_follow_read_signal == "weak"
    assert policy.stop_on_blocking_risk is False


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"target_min_chars": 30000, "target_max_chars": 20000}, "target_max_chars"),
        (
            {
                "chapter_min_chars": 3000,
                "chapter_target_chars": 5500,
                "chapter_max_chars": 5000,
            },
            "chapter_target_chars",
        ),
        ({"target_cost_cny": 6, "soft_budget_cny": 5}, "target_cost_cny"),
        ({"soft_budget_cny": 21, "hard_safety_ceiling_cny": 20}, "soft_budget_cny"),
    ],
)
def test_creative_policy_rejects_inconsistent_ranges(
    updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CreativePolicy(**updates)
