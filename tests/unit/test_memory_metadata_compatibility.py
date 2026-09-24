from copy import deepcopy

import pytest

from novel_writer.domain.state import StoryState
from novel_writer.generation.content import json_text, paragraphs
from novel_writer.generation.reports import (
    memory_result,
    normalize_memory_format,
    normalize_memory_metadata,
)
from tests.unit.test_novel_run_rebuild import BODY, memory


@pytest.mark.parametrize("version", [1, 78, 110, "110", None])
@pytest.mark.parametrize("evidence", [None, []])
def test_redundant_metadata_is_retained_separately_and_never_changes_facts(version, evidence):
    original = memory()
    raw = {**original, "base_version": version, "evidence": evidence}
    before = deepcopy(raw)
    expected = memory_result(json_text(original), BODY, StoryState(), 78)
    result = memory_result(json_text(raw), BODY, StoryState(), 78)
    notes = result.pop("format_notes")
    assert result == expected
    assert notes[0]["raw"] == version and notes[0]["bound_base_version"] == 78
    assert notes[1]["raw"] == evidence
    normalized, _ = normalize_memory_metadata(raw, 78)
    assert normalized == original and raw == before


@pytest.mark.parametrize(
    "extras", [{"evidence": ["unverified"]}, {"evidence": {}}, {"other_facts": []}]
)
def test_nonempty_evidence_and_unknown_report_fields_are_not_silently_discarded(extras):
    with pytest.raises(ValueError, match="Extra inputs"):
        memory_result(
            json_text({**memory(), "base_version": 110, **extras}), BODY, StoryState(), 78
        )


@pytest.mark.parametrize("field", ["position", "position_paragraph_ids"])
def test_metadata_cannot_replace_required_position_or_evidence(field):
    raw = {**memory(), "base_version": 110, "evidence": []}
    raw.pop(field)
    with pytest.raises(ValueError):
        memory_result(json_text(raw), BODY, StoryState(), 78)


def test_extra_metadata_does_not_relax_fact_fields_or_foreign_evidence():
    raw = {**memory(), "base_version": 110, "evidence": []}
    raw["position_paragraph_ids"] = ["foreign:1"]
    with pytest.raises(ValueError, match="证据"):
        memory_result(json_text(raw), BODY, StoryState(), 78)
    raw = {**memory(), "base_version": 110, "evidence": []}
    raw["changes"][0]["values"]["invented_field"] = "not allowed"
    result = memory_result(json_text(raw), BODY, StoryState(), 78)
    assert result["status"] == "partial" and result["requires_author_confirmation"]
    assert not result["factual_changes"] and "未知字段" in result["diagnostics"][0]["error"]


@pytest.mark.parametrize("numeric", [str, int])
def test_numeric_evidence_preserves_the_same_facts_and_all_source_text(numeric):
    original = memory()
    raw = deepcopy(original)
    raw["position_paragraph_ids"] = [numeric(i.rsplit(":", 1)[1])
                                     for i in raw["position_paragraph_ids"]]
    for change in raw["changes"]:
        change["paragraph_ids"] = [numeric(i.rsplit(":", 1)[1]) for i in change["paragraph_ids"]]
    before = deepcopy(raw)
    result = memory_result(json_text(raw), BODY, StoryState(), 78)
    notes = result.pop("format_notes")
    assert result == memory_result(json_text(original), BODY, StoryState(), 78)
    assert {n["field"] for n in notes} == {"position_paragraph_ids", "changes[0].paragraph_ids"}
    for span in result["position_evidence"]:
        assert BODY[span["start"]:span["end"]] == span["text"]
    assert raw == before


@pytest.mark.parametrize("note", [None, "", "提取时的附加说明", [], ["备注一", "备注二"]])
def test_plain_annotations_are_kept_outside_evidence_and_facts(note):
    raw = memory()
    raw["position_paragraph_ids_note"] = note
    raw["position"]["extraction_note"] = note
    raw["changes"][0]["comment"] = note
    result = memory_result(json_text(raw), BODY, StoryState(), 78)
    notes = result.pop("format_notes")
    assert result == memory_result(json_text(memory()), BODY, StoryState(), 78)
    assert [n["raw"] for n in notes] == [note] * 3
    assert not result["requires_author_confirmation"]


@pytest.mark.parametrize("bad", ["foreign:1", "0", "999", "-1", "1-2", True, 1.0, None])
@pytest.mark.parametrize("core", [True, False])
def test_invalid_references_are_not_guessed_or_dropped(bad, core):
    raw = memory()
    if core:
        raw["position_paragraph_ids"] = ["1", bad]
        with pytest.raises(ValueError):
            memory_result(json_text(raw), BODY, StoryState(), 78)
    else:
        raw["changes"][0]["paragraph_ids"] = ["1", bad]
        result = memory_result(json_text(raw), BODY, StoryState(), 78)
        assert result["status"] == "partial" and not result["factual_changes"]


def test_mixed_ids_whitespace_and_actual_numbering_do_not_shift_evidence():
    body = "第一段。\n   \n最后一段。"
    ids = [p["id"] for p in paragraphs(body)]
    raw = memory(body, position_paragraph_ids=[" 1 ", f" {ids[-1]} "])
    result = memory_result(json_text(raw), body, StoryState(), 78)
    assert [s["id"] for s in result["position_evidence"]] == ids
    # A whitespace-only line has no paragraph; ordinal 2 must not mean the second kept row.
    raw["position_paragraph_ids"] = ["2"]
    with pytest.raises(ValueError, match="证据"):
        memory_result(json_text(raw), body, StoryState(), 78)
    raw["position_paragraph_ids"] = ["1", ids[0]]
    with pytest.raises(ValueError, match="重复"):
        memory_result(json_text(raw), body, StoryState(), 78)


def test_annotations_do_not_hide_unknown_facts_or_replace_missing_core_fields():
    raw = memory()
    raw["position"]["invented_fact"] = "不可忽略"
    with pytest.raises(ValueError, match="未知字段"):
        memory_result(json_text(raw), BODY, StoryState(), 78)
    raw = memory()
    raw.pop("position_paragraph_ids")
    raw["position_paragraph_ids_note"] = ["1"]
    with pytest.raises(ValueError):
        memory_result(json_text(raw), BODY, StoryState(), 78)
    raw = memory()
    raw["notes"] = {"other_facts": "structured facts are not plain annotations"}
    normalized, notes = normalize_memory_format(raw, BODY)
    assert normalized == raw and not notes
