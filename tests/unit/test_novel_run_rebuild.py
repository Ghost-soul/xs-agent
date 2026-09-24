import json
from uuid import uuid4

import pytest

from novel_writer.domain.character import Character
from novel_writer.domain.state import StoryState
from novel_writer.domain.world import StoryEvent
from novel_writer.generation.content import digest, json_text, paragraphs
from novel_writer.generation.context import enrich_context
from novel_writer.generation.novel import invalidate_candidate, next_action, slots_for
from novel_writer.generation.questions import reconcile_questions
from novel_writer.generation.reports import (
    apply_edit,
    checker_result,
    local_summary,
    memory_result,
    reader_result,
)
from novel_writer.generation.roles import render_for
from novel_writer.generation.schemas import NovelStoryPlan
from tests.unit.test_genre_generation import plan, spec

BODY = "林青将通行证烧掉了。\n\n江月主动握住她的手。"


def test_author_questions_survive_plan_deletion_and_later_question_is_scoped():
    previous = [{"question": "当前地点？", "status": "pending", "scope": "current_unit"}]
    revised = NovelStoryPlan.model_validate(plan())
    with pytest.raises(ValueError, match="删除问题"):
        reconcile_questions(previous, revised, {}, [], "删除问题")
    answered = reconcile_questions(previous, revised, {"当前地点？": "渡口"}, [], "明确现场")
    assert answered[0]["status"] == "answered"
    deferred = reconcile_questions(previous, revised, {}, ["当前地点？"], "改写为已知室内场景")
    assert deferred[0]["status"] == "deferred"
    assert (
        reconcile_questions([{**previous[0], "scope": "later"}], revised, {}, [], "暂不涉及")[0][
            "status"
        ]
        == "pending"
    )


def test_unknown_memory_patch_field_is_preserved_as_diagnostic():
    raw = memory()
    raw["changes"][0]["values"]["invented_field"] = "must not silently disappear"
    result = memory_result(json_text(raw), BODY, StoryState(), 1)
    assert result["status"] == "partial" and not result["factual_changes"]


def test_scene_disclosure_and_lifecycle_use_real_candidate_chapter_and_evidence():
    c = Character(name="林青")
    chapter = {"id": str(uuid4()), "ordinal": 7}
    ids = [p["id"] for p in paragraphs(BODY)]
    items = [
        {"collection": "events", "values": {"summary": "烧证", "participants": [str(c.id)]}},
        {
            "collection": "scenes",
            "values": {"summary": "渡口告别", "ordinal": 1, "event_ids": ["$change:0"]},
        },
        {
            "collection": "disclosures",
            "values": {
                "fact_key": "pass_destroyed",
                "statement": "通行证已销毁",
                "first_revealed_in_scene_id": "$change:1",
            },
        },
        {
            "collection": "reader_promises",
            "promise_action": "established",
            "values": {"kind": "relationship", "summary": "两人是否同行"},
        },
        {"collection": "foreshadowings", "values": {"content": "握手的特殊在意"}},
    ]
    raw = memory(
        changes=[{**item, "observation": "正文变化", "paragraph_ids": ids} for item in items]
    )
    result = memory_result(json_text(raw), BODY, StoryState(characters=(c,)), 1, chapter)
    assert result["status"] == "complete", result["diagnostics"]
    changes = result["factual_changes"]
    assert changes["add_scenes"][0]["chapter_id"] == chapter["id"]
    assert changes["add_scenes"][0]["event_ids"] == [changes["add_events"][0]["id"]]
    proof = changes["add_reader_promises"][0]["history"][0]["evidence"][0]
    assert BODY[proof["start"] : proof["end"]] == proof["quote"]
    assert proof["chapter_id"] == chapter["id"]
    raw["changes"][-1]["values"]["fulfilled_evidence"] = [{"quote": "伪造"}]
    invalid = memory_result(json_text(raw), BODY, StoryState(characters=(c,)), 1, chapter)
    assert invalid["status"] == "partial" and len(invalid["changes"]) == 4


def test_missing_auxiliary_collection_is_unknown_not_empty_success():
    raw = memory()
    del raw["changes"]
    result = memory_result(json_text(raw), BODY, StoryState(), 1)
    assert result["status"] == "partial" and result["requires_author_confirmation"]


def memory(body=BODY, **changes):
    return {
        "position": {"current_location": "渡口", "recent_major_event": "烧掉通行证"},
        "position_paragraph_ids": [paragraphs(body)[0]["id"]],
        "outcome": "通行证已销毁，关系发生变化",
        "changes": [
            {
                "collection": "events",
                "values": {"summary": "通行证已销毁"},
                "observation": "烧掉通行证",
                "paragraph_ids": [paragraphs(body)[0]["id"]],
            }
        ],
        **changes,
    }


def test_auxiliary_bad_component_keeps_valid_facts_and_diagnostics():
    raw = memory()
    raw["changes"].append({"collection": "relationships", "broken": "cannot infer pair"})
    result = memory_result(json_text(raw), BODY, StoryState(), 1)
    assert result["status"] == "partial"
    assert len(result["changes"]) == 1 and len(result["diagnostics"]) == 1
    assert result["factual_changes"]["add_events"][0]["summary"] == "通行证已销毁"
    assert "add_relationships" not in result["factual_changes"]


def test_missing_core_and_foreign_passage_do_not_create_empty_handoff():
    raw = memory()
    raw["position_paragraph_ids"] = ["foreign:1"]
    with pytest.raises(ValueError, match="证据"):
        memory_result(json_text(raw), BODY, StoryState(), 1)
    raw = memory(position={})
    with pytest.raises(ValueError, match="Memory"):
        memory_result(json_text(raw), BODY, StoryState(), 1)


def test_belief_is_not_upgraded_to_world_truth():
    c = Character(name="林青")
    change = {
        "collection": "beliefs",
        "values": {"character_id": str(c.id), "proposition": "江月喜欢自己", "is_true": True},
        "observation": "她的猜测",
        "paragraph_ids": [paragraphs(BODY)[1]["id"]],
    }
    result = memory_result(
        json_text(memory(changes=[change])), BODY, StoryState(characters=(c,)), 1
    )
    assert result["status"] == "partial" and not result["factual_changes"]


def test_replay_new_ids_are_deterministic_and_existing_id_must_exist():
    first = memory_result(json_text(memory()), BODY, StoryState(), 1)
    second = memory_result(json_text(memory()), BODY, StoryState(), 1)
    assert first["factual_changes"] == second["factual_changes"]
    raw = memory()
    raw["changes"][0]["object_id"] = str(uuid4())
    assert memory_result(json_text(raw), BODY, StoryState(), 1)["status"] == "partial"


def test_reader_isolated_from_private_state_cards_and_prior_reports():
    snapshot = {
        "cards": [{"id": "gl", "sha256": "x", "text": "SECRET_CARD"}],
        "context": {
            "characters": ["SECRET_IDENTITY"],
            "future_material_not_obligations": "SECRET_PLAN",
            "recent_chapters": [{"body": "公开前文", "revision_id": "revision", "sha256": "sha"}],
        },
    }
    _, reader = render_for(
        spec(workflow="novel-run-v1"),
        snapshot,
        "reader",
        plan(),
        BODY,
        "SECRET_AUTHOR",
        {"memory": "SECRET_MEMORY"},
    )
    assert "SECRET" not in reader and "公开前文" in reader
    assert "爱情影响选择" not in reader
    for role in ("plan", "write"):
        assert (
            render_for(spec(workflow="novel-run-v1"), snapshot, role, plan())[1].count(
                "SECRET_CARD"
            )
            == 1
        )


def test_reader_scope_and_evidence_are_bound_to_exact_candidate():
    raw = {
        "experience": "主动回应",
        "perceived_relationship": "有特殊在意但未明确关系",
        "findings": [{"observation": "握手", "paragraph_ids": [paragraphs(BODY)[1]["id"]]}],
        "problems": [],
        "limits": "只读本章",
    }
    result = reader_result(json_text(raw), BODY, [])
    assert result["reading_scope"]["candidate"]["sha256"] == digest(BODY)
    assert result["outcome"] == "unknown"  # cold reader cannot certify an unseen author goal
    stale = reader_result(json_text(raw), BODY + "新段落", [])
    assert stale["status"] == "partial" and not stale["findings"]


def test_limited_edit_preserves_outside_text_and_requires_explicit_retention():
    ids = [p["id"] for p in paragraphs(BODY)]
    decision = {
        "paragraph_id": ids[1],
        "disposition": "patched",
        "replacement": "江月握住她的手。",
        "reason": "删重复词",
    }
    result = apply_edit(json_text({"decisions": [decision]}), BODY, [ids[1]], [ids[0]])
    assert result["body"] == "林青将通行证烧掉了。\n\n江月握住她的手。"
    with pytest.raises(ValueError, match="保护"):
        apply_edit(json_text({"decisions": [decision]}), BODY, [ids[1]], [ids[1]])
    with pytest.raises(ValueError, match="明确处理"):
        apply_edit(json_text({"decisions": [decision]}), BODY, ids, [])


def test_conditional_slots_and_full_candidate_invalidation():
    actions = slots_for(spec(workflow="novel-run-v1", enable_editor=True))
    assert len(actions) == 8
    assert next_action(actions, "checker") == "reader"
    assert next_action(actions, "checker", editable=True) == "editor"
    assert next_action(actions, "editor", changed=False) == "reader"
    assert next_action(actions, "editor") == "memory_edit"
    state = {f"{s}_id": s for s in ["plan", "memory", "checker", "review", "segments", "title"]}
    assert invalidate_candidate(state) == {"plan_id": "plan"}


def test_critical_recall_retains_old_entity_event_and_does_not_require_global_profiles():
    c = Character(name="林青")
    old = StoryEvent(summary="已烧掉通行证", participants=(c.id,))
    state = StoryState(
        characters=(c,), events=(old, *(StoryEvent(summary="无关事件") for _ in range(30)))
    )
    context = {"recent_chapters": [], "recent_events": []}
    enrich_context(context, state.model_dump(mode="json"), [str(c.id)])
    assert context["critical_facts"][0]["summary"] == "已烧掉通行证"
    assert not context["preflight"]["global_profile_gate"]


def test_large_history_selects_old_task_fact_without_unbounded_prompt_growth():
    c = Character(name="林青")
    old = StoryEvent(summary="通行证已销毁", participants=(c.id,))
    state = StoryState(
        characters=(c,),
        events=(
            old,
            *(StoryEvent(summary=f"第{i}次例行值班", participants=(c.id,)) for i in range(200)),
        ),
    )
    context = {"recent_chapters": [], "recent_events": []}
    audit = enrich_context(
        context, state.model_dump(mode="json"), [str(c.id)], "这次需要通行证进入"
    )
    assert any(e["id"] == str(old.id) for e in context["critical_facts"])
    assert len(context["critical_facts"]) < len(state.events)
    assert audit["events"]["omitted_ids"]
    assert not context["selection_audit"]["events"]["complete"]


def test_summary_cannot_rebind_to_edited_body_and_checker_cannot_claim_clear_with_issues():
    result = memory_result(json_text(memory()), BODY, StoryState(), 1)
    assert local_summary(result, BODY)["method"].startswith("local")
    with pytest.raises(ValueError):
        local_summary(result, BODY + "变动")
    with pytest.raises(ValueError):
        checker_result(
            json.dumps(
                {
                    "conclusion": "clear",
                    "explanation": "没问题",
                    "issues": [
                        {
                            "severity": "blocking",
                            "observation": "错配",
                            "paragraph_ids": [paragraphs(BODY)[0]["id"]],
                        }
                    ],
                }
            ),
            BODY,
        )
