from scripts.performance_gate import FIXTURE_SPECS, _chapter_body, _p95, _story_state


def test_longform_performance_fixtures_are_deterministic_and_match_the_plan() -> None:
    medium, large = FIXTURE_SPECS

    assert (medium.chapters, medium.target_characters) == (200, 1_000_000)
    assert (large.chapters, large.target_characters) == (500, 2_500_000)
    assert _chapter_body(large, 17, "当前") == _chapter_body(large, 17, "当前")
    assert len(_chapter_body(large, 17, "当前")) == 5_000
    state = _story_state(large, "当前")
    assert len(state.characters) == 120
    assert len(state.foreshadowings) == 300
    assert len(state.plot_threads) == 300
    assert state.characters[-1].aliases == ("代号119", "旧称119")


def test_performance_p95_uses_the_nearest_rank() -> None:
    assert _p95([float(value) for value in range(1, 21)]) == 19.0
