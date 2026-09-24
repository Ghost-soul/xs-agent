from copy import deepcopy
from uuid import uuid4

import pytest

from novel_writer.db.models import GenerationArtifactRecord
from novel_writer.generation.content import digest, fingerprint
from novel_writer.generation.stage_adoption import earlier_position_reference


def record(kind, payload):
    return GenerationArtifactRecord(
        id=uuid4(), kind=kind, payload=payload, sha256=fingerprint(payload)
    )


def history():
    candidate = record("candidate", {"body": "甲乙丙丁", "complete": True})
    handoff = record(
        "handoff",
        {
            "candidate_sha256": candidate.sha256,
            "unit_body_sha256": digest("甲乙丙丁"),
            "parent_chain_sha256": fingerprint([]),
            "position": {"current_location": "渡口", "recent_major_event": "交出船票"},
        },
    )
    units = record(
        "units",
        {
            "items": [
                {
                    "start": 0,
                    "end": 4,
                    "body_sha256": digest("甲乙丙丁"),
                    "memory_id": str(handoff.id),
                    "memory_sha256": handoff.sha256,
                }
            ]
        },
    )
    return [units, handoff, candidate]


def test_unchanged_prefix_handoff_is_a_reference_with_explicit_remaining_prose():
    records = history()
    before = [deepcopy(r.payload) for r in records]
    result = earlier_position_reference(records, "甲乙丙丁新现场", 0, 7)
    assert result["position"]["current_location"] == "渡口"
    assert result["source"]["kind"] == "earlier-handoff"
    assert result["source"]["end"] == 4
    assert result["source"]["remaining_characters"] == 3
    assert [r.payload for r in records] == before


@pytest.mark.parametrize(
    "body,start,end",
    [
        ("甲乙丙丁新现场", 0, 3),  # Later handoffs never fill an earlier chapter.
        ("甲乙丙丁新现场", 4, 7),  # A previous chapter is not this chapter's scene.
        ("改乙丙丁新现场", 0, 7),  # Changed prefix invalidates old continuity.
    ],
)
def test_future_outside_chapter_or_changed_source_cannot_supply_a_position(body, start, end):
    assert earlier_position_reference(history(), body, start, end) is None


@pytest.mark.parametrize("index", [0, 1, 2])
def test_corrupt_historical_bindings_are_ignored(index):
    records = history()
    records[index].sha256 = "0" * 64
    assert earlier_position_reference(records, "甲乙丙丁新现场", 0, 7) is None


def test_matching_unit_text_cannot_hide_a_changed_earlier_prefix():
    candidate = record("candidate", {"body": "旧前文甲乙丙丁", "complete": True})
    handoff = record(
        "handoff",
        {
            "candidate_sha256": candidate.sha256,
            "unit_body_sha256": digest("甲乙丙丁"),
            "parent_chain_sha256": fingerprint([]),
            "position": {"current_location": "渡口", "recent_major_event": "交出船票"},
        },
    )
    units = record(
        "units",
        {
            "items": [
                {
                    "start": 3,
                    "end": 7,
                    "body_sha256": digest("甲乙丙丁"),
                    "memory_id": str(handoff.id),
                    "memory_sha256": handoff.sha256,
                }
            ]
        },
    )
    assert (
        earlier_position_reference([units, handoff, candidate], "新前文甲乙丙丁后文", 0, 9) is None
    )
