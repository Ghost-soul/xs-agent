from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from novel_writer.domain.character import Character
from novel_writer.domain.state import StateDelta, StoryState
from novel_writer.generation.content import digest, json_text, paragraphs
from novel_writer.generation.diagnostics import revalidation_blocker
from novel_writer.generation.novel import parser_for
from novel_writer.generation.reports import memory_result
from novel_writer.generation.schemas import (
    LONGFORM_PLAN_PARSER_REVISION,
    LONGFORM_REVISION,
    NOVEL_REVISION,
    REVISION,
)
from tests.unit.test_novel_run_rebuild import BODY, memory


def change(collection, values, **extra):
    return {
        "collection": collection,
        "values": values,
        "observation": "正文中的变化",
        "paragraph_ids": [paragraphs(BODY)[0]["id"]],
        **extra,
    }


def promise(**extra):
    return change(
        "reader_promises",
        {"kind": "relationship", "summary": "是否同行"},
        promise_action="established",
        **extra,
    )


def scene(**values):
    return change("scenes", {"summary": "渡口", "ordinal": 1, **values})


def test_all_legal_position_and_fact_references_are_retained():
    body = "\n\n".join(f"第 {i} 段中的实际动作。" for i in range(40))
    ids = [p["id"] for p in paragraphs(body)]
    raw = memory(body, position_paragraph_ids=ids[:30], unresolved=["动机仍未知"])
    raw["changes"][0]["paragraph_ids"] = ids[:27]
    result = memory_result(json_text(raw), body, StoryState(), 1)
    assert result["status"] == "complete"
    assert [p["id"] for p in result["position_evidence"]] == ids[:30]
    assert result["changes"][0]["paragraph_ids"] == ids[:27]
    assert [p["id"] for p in result["changes"][0]["evidence"]] == ids[:27]
    assert result["unresolved"] == raw["unresolved"] and result["requires_author_confirmation"]


@pytest.mark.parametrize("bad", [[], ["foreign:1"], ["same", "same"]])
@pytest.mark.parametrize("core", [True, False])
def test_invalid_evidence_is_not_dropped_or_deduplicated(bad, core):
    ids = [paragraphs(BODY)[0]["id"] if i == "same" else i for i in bad]
    raw = memory()
    if core:
        raw["position_paragraph_ids"] = ids
        with pytest.raises(ValueError):
            memory_result(json_text(raw), BODY, StoryState(), 1)
    else:
        raw["changes"][0]["paragraph_ids"] = ids
        result = memory_result(json_text(raw), BODY, StoryState(), 1)
        assert result["status"] == "partial" and not result["factual_changes"]
        assert result["diagnostics"][0]["raw"] == raw["changes"][0]


def test_promise_before_scene_and_transitive_reference_preserve_original_indices():
    chapter = {"id": str(uuid4()), "ordinal": 1}
    items = [
        promise(),
        change("foreshadowings", {"content": "同行信物", "related_reader_promises": ["$change:0"]}),
        scene(),
        change(
            "disclosures",
            {"fact_key": "pass", "statement": "烧证", "first_revealed_in_scene_id": "$change:2"},
        ),
    ]
    result = memory_result(json_text(memory(changes=items)), BODY, StoryState(), 1, chapter)
    assert result["status"] == "complete", result["diagnostics"]
    ids = [str(uuid5(NAMESPACE_URL, f"{digest(BODY)}:{i}")) for i in range(4)]
    assert [c["object_id"] for c in result["changes"]] == ids
    assert [c["paragraph_ids"] for c in result["changes"]] == [c["paragraph_ids"] for c in items]
    state = StateDelta.model_validate({"base_version": 1, **result["factual_changes"]}).apply(
        StoryState()
    )
    assert str(state.foreshadowings[0].related_reader_promises[0]) == ids[0]
    assert str(state.disclosures[0].first_revealed_in_scene_id) == ids[2]
    proof = state.reader_promises[0].history[0].evidence[0]
    assert str(proof.chapter_id) == chapter["id"]
    assert BODY[proof.start : proof.end] == proof.quote


@pytest.mark.parametrize("scenes", [[], [scene(ordinal=0)], [scene(chapter_id=str(uuid4()))]])
def test_missing_failed_or_foreign_scene_does_not_invent_chapter_evidence(scenes):
    items = [promise(), *scenes]
    result = memory_result(
        json_text(memory(changes=items)), BODY, StoryState(), 1, {"id": str(uuid4()), "ordinal": 1}
    )
    assert result["status"] == "partial" and not result["changes"]
    assert [d["index"] for d in result["diagnostics"]] == list(range(len(items)))
    assert [d["raw"] for d in result["diagnostics"]] == items


def test_failed_update_cannot_be_referenced_even_when_old_object_exists():
    c = Character(name="林青")
    items = [
        change("characters", {"invented_field": "bad"}, object_id=str(c.id)),
        change("events", {"summary": "同行", "participants": ["$change:0"]}),
    ]
    result = memory_result(json_text(memory(changes=items)), BODY, StoryState(characters=(c,)), 1)
    assert not result["changes"] and len(result["diagnostics"]) == 2
    assert "未通过校验" in result["diagnostics"][1]["error"]


def test_forward_and_cyclic_references_remain_diagnostics():
    items = [
        change("events", {"summary": "甲", "happens_before": ["$change:1"]}),
        change("events", {"summary": "乙", "happens_before": ["$change:0"]}),
    ]
    result = memory_result(json_text(memory(changes=items)), BODY, StoryState(), 1)
    assert result["status"] == "partial" and not result["changes"]
    assert len(result["diagnostics"]) == 2


@pytest.mark.parametrize("revision", [LONGFORM_REVISION, NOVEL_REVISION, REVISION])
def test_parser_upgrade_is_scoped_to_memory(revision):
    old = parser_for(revision)
    for action in ("plan", "write", "checker", "reader", "editor", "amend"):
        expected = (
            LONGFORM_PLAN_PARSER_REVISION
            if revision == LONGFORM_REVISION and action == "plan"
            else old
        )
        assert parser_for(revision, action) == expected
    for action in ("memory", "memory:1", "memory_edit", "memory_amend"):
        assert parser_for(revision, action) == "memory-evidence-v4" != old


def test_new_memory_parser_allows_one_revalidation_and_keeps_author_edit_guard():
    call = SimpleNamespace(
        id=uuid4(),
        action="memory:1",
        status="local_failure",
        response={
            "text": "saved",
            "terminal": {"terminal_event_seen": True, "finish_reason": "stop"},
        },
    )
    batch = SimpleNamespace(
        revision=LONGFORM_REVISION,
        status="needs_attention",
        state={"compiled": [f"{call.id}:{parser_for(LONGFORM_REVISION)}"]},
    )
    assert revalidation_blocker(batch, call, None) is None
    batch.state["plan_author_note_id"] = "author-edit"
    assert "作者已修订" in revalidation_blocker(batch, call, None)
    del batch.state["plan_author_note_id"]
    batch.state["compiled"].append(f"{call.id}:{parser_for(batch.revision, call.action)}")
    assert "当前解析版本已处理" in revalidation_blocker(batch, call, None)
    call.status = "completed"
    assert revalidation_blocker(batch, call, None) is not None
