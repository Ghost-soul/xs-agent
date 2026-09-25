from pathlib import Path

from novel_writer.services.style_profiles import (
    load_quality_cards,
    validate_genre_card_selection,
)


def test_grouped_library_retains_subject_ids_and_exposes_content_cards():
    cards = load_quality_cards()
    assert len(cards) == 107
    grouped = {
        layer: [c for c in cards if c["layer"] == layer]
        for layer in ("genre", "narrative")
    }
    assert [len(grouped[k]) for k in grouped] == [15, 92]
    rewritten_ids = {
        "lovecraftian",
        "political_career_power_struggle",
        "workplace_business_war",
        "farming_infrastructure",
        "girls_love_gl",
        "son_in_law",
        "hidden_identity",
        "invincible_flow",
        "adult_extreme_mature",
    }
    content_cards = [c for c in cards if c["content_version"] in {"4.0", "4.1", "5.0", "5.1"}]
    assert len(content_cards) == 88
    assert len({c["name"] for c in content_cards}) == 88
    assert all(c["layer"] == "narrative" for c in content_cards)
    assert rewritten_ids <= {c["id"] for c in content_cards}
    assert {c["id"] for c in grouped["narrative"] if c not in content_cards} == {
        "ensemble_tragedy_drama",
        "male_wish_fulfillment_romance",
        "dark_intrigue_human_nature",
        "consensual_yuri_erotica",
    }
    removed_ids = {
        "detective_mystery_investigation",
        "literary_tragedy_melancholy",
        "military_warfare_modern",
        "spiritual_resurgence",
        "checkin_rewards",
        "child_family_story",
        "esports_career",
        "infinite_world",
        "interspecies_romance",
        "level_progression",
        "life_simulator",
        "livestream_story",
        "multiverse_journey",
        "no_romance",
        "power_couple",
        "pregnancy_departure",
        "pure_love_danmei_bl",
        "quick_transmigration",
        "rebirth",
        "system_flow",
        "transmigration",
        "transmigration_book",
        "variety_show",
        "zombie_survival",
    }
    assert not removed_ids & {c["id"] for c in cards}
    assert "livestream_reality" not in {c["id"] for c in cards}
    assert "no_cp_female_protagonist_career" not in {c["id"] for c in cards}
    assert {
        "time_loop", "wish_fulfillment",
        "infrastructure_building", "academic_ace",
    } <= {
        c["id"] for c in content_cards
    }
    for card in cards:
        expected_version = (
            "1.0" if card["id"] == "consensual_yuri_erotica"
            else "5.1" if card["id"] in {
                "adult_extreme_mature", "forbidden_romance", "forced_noncon"
            }
            else "5.0" if card["id"] in {
                "cnc_play", "harem_possession", "extreme_revenge_violence",
                "dominant_power_sex", "hypnosis_control", "humiliation_abuse_romance",
                "extreme_survival_outburst", "girls_love_gl", "cuckold_ntr",
                "dark_slave_gangbang",
            }
            else "4.1" if card["id"] in {
                "universal_class_awakening", "horror_thriller_supernatural", "strong_female_lead"
            }
            else "4.0" if card in content_cards
            else "3.0"
        )
        assert card["content_version"] == expected_version
        assert card["layer"] in grouped
        folder = "genres" if card["layer"] == "genre" else "narrative"
        assert card["source_file"].split("/")[0] == folder
        assert card["writing_guidance"] and card["combination_guidance"]
        assert Path("configs/genre-quality-cards", card["source_file"]).is_file()
    # Reclassified historical selections remain valid, including a narrative main card.
    assert validate_genre_card_selection(
        "ensemble_tragedy_drama", ["girls_love_gl", "hidden_identity"]
    ) == (
        "ensemble_tragedy_drama",
        ("girls_love_gl", "hidden_identity"),
    )


def test_loader_reads_known_groups_and_ignores_archived_duplicates(tmp_path):
    card = next(c for c in load_quality_cards() if c["id"] == "girls_love_gl")
    source = Path("configs/genre-quality-cards", card["source_file"]).read_text(encoding="utf-8")
    (tmp_path / "narrative").mkdir()
    (tmp_path / "narrative/gl.md").write_text(source, encoding="utf-8")
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive/duplicate.md").write_text(source, encoding="utf-8")
    cards = load_quality_cards(tmp_path)
    assert len(cards) == 1 and cards[0]["source_file"] == "narrative/gl.md"
