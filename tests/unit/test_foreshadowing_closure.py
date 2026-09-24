from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_writer.domain.models import (
    Foreshadowing,
    StoryState,
)
from novel_writer.services.causal_continuity import (
    CausalHandoffManifest,
    build_causal_pulse_report,
    validate_causal_handoff_manifest,
)
from novel_writer.services.foreshadowing import (
    ForeshadowingAttentionService,
    ForeshadowingSelectionInput,
)


def test_attention_selector_is_stable_and_excludes_closed_items() -> None:
    due = Foreshadowing(
        content="旧印章会在城门关闭后发热",
        importance="high",
        introduced_chapter=1,
        last_advanced_chapter=1,
        reminder_after_chapters=2,
        payoff_readiness=85,
    )
    fresh = Foreshadowing(
        content="守门人记得一张陌生面孔",
        importance="low",
        introduced_chapter=7,
        last_advanced_chapter=7,
    )
    closed = Foreshadowing(
        content="已经兑现的旧承诺",
        introduced_chapter=1,
        last_advanced_chapter=3,
        status="fulfilled",
        fulfilled_chapter=3,
    )
    state = StoryState(foreshadowings=(fresh, closed, due))
    selection = ForeshadowingSelectionInput(current_chapter=9)

    first = ForeshadowingAttentionService().select(state, selection, limit=8)
    second = ForeshadowingAttentionService().select(state, selection, limit=8)

    assert first.model_dump_json() == second.model_dump_json()
    assert [item.foreshadowing_id for item in first.items] == [due.id, fresh.id]
    assert first.items[0].suggested_action == "consider_payoff"
    assert first.open_total == 2
    assert first.backlog_attention_total == 1


def test_causal_pulse_is_advisory_and_uses_stable_state() -> None:
    ready = Foreshadowing(
        content="等待兑现",
        introduced_chapter=1,
        last_advanced_chapter=2,
        status="ready_for_payoff",
    )
    report = build_causal_pulse_report(
        StoryState(foreshadowings=(ready,)),
        (),
        current_chapter=10,
    )

    payoff = next(item for item in report.observations if item.kind == "payoff_debt_age")
    assert payoff.state == "observed"
    assert payoff.advisory_only is True
    assert "幕" not in report.model_dump_json()
    assert "多巴胺" not in report.model_dump_json()


def test_saved_causal_handoff_remains_readable_and_bound_to_formal_body() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "retired-causal-handoff.json"
    saved = json.loads(fixture.read_text(encoding="utf-8"))
    manifest = CausalHandoffManifest.model_validate(saved["manifest"])
    segments = saved["segments"]
    binding = {
        "version_id": manifest.source_version_id,
        "version_number": manifest.source_version_number,
    }
    validate_causal_handoff_manifest(manifest, segments=segments, **binding)
    assert manifest.handoffs[0].open_consequence == "必须寻找另一条路"
    stale = [{**segments[0], "body": segments[0]["body"] + "改"}]
    with pytest.raises(ValueError, match="body binding"):
        validate_causal_handoff_manifest(manifest, segments=stale, **binding)
