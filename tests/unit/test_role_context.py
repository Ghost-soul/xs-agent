import json
from copy import deepcopy
from pathlib import Path
from typing import get_args

import pytest

from novel_writer.domain.state import StateDelta, StoryState
from novel_writer.generation import (
    knowledge_context,
    memory_fields,
    role_context,
    role_materials,
    role_queries,
)
from novel_writer.generation.content import fingerprint, json_text, paragraphs
from novel_writer.generation.reports import FactChange, memory_result
from novel_writer.knowledge.text import source, state_sources
from novel_writer.services.errors import WorkflowError
from tests.unit.test_narrative_prompts import fixture
from tests.unit.test_world_context import setup


def configured():
    spec, snap, plan = fixture(
        context_policy=role_context.POLICY,
        length_policy="unit-v1",
        plan_policy="bounded-v1",
        input_limit=200000,
    )
    snap["counting"] = {}
    return spec, snap, plan


def render(action, body="林青走向渡口。", reports=None):
    spec, snap, plan = configured()
    system, raw = role_context.render_for(spec, snap, action, plan, body, reports=reports)
    return system, json.loads(raw)


def test_fifty_historical_requests_and_contracts_remain_exact():
    for case in json.loads(Path("tests/fixtures/generation-pre-role-rag.json").read_text()):
        spec, snap, plan = fixture(
            context_policy=case["policy"],
            length_policy="unit-v1",
            plan_policy="bounded-v1",
            input_limit=200000,
        )
        snap["counting"] = {}
        assert role_context.contract_for(spec) == case["contract"]
        for action, sha in case["renders"].items():
            assert (
                fingerprint(role_context.render_for(spec, snap, action, plan, "source paragraph"))
                == sha
            )


@pytest.mark.parametrize(
    "action", ["memory:1", "memory:5", "checker", "memory_amend", "checker_amend"]
)
def test_evidence_queries_cover_actual_body_and_never_future_plan(action):
    spec, snap, plan = configured()
    plan["scenes"] = [{"event": f"PLANNED_{i}"} for i in range(5)]
    body = "ACTUAL_START " + "x" * 500 + " ACTUAL_MIDDLE " + "x" * 500 + " ACTUAL_END"
    queries = role_queries.queries(spec, snap, action, plan, body, {})
    assert "PLANNED" not in str(queries)
    assert "ACTUAL_START" in queries[0] and "ACTUAL_END" in str(queries)
    assert len(queries) <= 8 and max(map(len, queries)) <= 300


def test_writer_query_uses_current_unit_and_actual_ending():
    spec, snap, plan = configured()
    plan["scenes"] = [{"event": f"PLANNED_{i}"} for i in range(1, 6)]
    queries = role_queries.queries(spec, snap, "write:5", plan, "ACTUAL_END", {})
    assert "PLANNED_5" in str(queries) and "ACTUAL_END" in str(queries)
    assert "PLANNED_1" not in str(queries)


def test_chief_card_and_author_constraints_and_rewrite_instruction_drive_search():
    spec, snap, plan = configured()
    spec = spec.model_copy(
        update={"direction": "AUTHOR", "author_boundaries": "BOUNDARY", "viewpoint": "VIEWPOINT"}
    )
    snap["cards"][-1]["text"] = "SELECTED_NARRATIVE"
    assert "BOUNDARY" in str(role_queries.queries(spec, snap, "plan", None, None, {}))
    assert "SELECTED_NARRATIVE" in str(role_queries.queries(spec, snap, "plan", None, None, {}))
    queries = role_queries.queries(
        spec,
        snap,
        "rewrite",
        plan,
        "ORIGINAL_BODY",
        {"edit_scope": {"instruction": "AUTHOR_CHANGE"}},
    )
    assert queries[0] == "AUTHOR_CHANGE" and "ORIGINAL_BODY" in queries


def test_checker_world_and_people_share_the_formal_opening_even_after_handoff():
    spec, snap, plan = setup()
    spec = spec.model_copy(update={"context_policy": role_context.POLICY})
    working = deepcopy(snap["context"])
    working["world_lore"][0]["details"] = ["CANDIDATE_END_CONDITION"]
    working["world_rules"][0]["statement"] = "CANDIDATE_END_RULE"
    working["characters"][0]["current_state"] = "CANDIDATE_END_CHARACTER"
    reports = {"working_context": working, "role_working_state": working, "completed_units": 2}
    for action in ["checker", "checker_amend", "memory_amend", "rewrite"]:
        _, raw = role_context.render_for(
            spec, snap, action, plan, "回潮契约仍然有效。", reports=reports
        )
        assert "CANDIDATE_END" not in raw
        assert "只有持有旧印才能渡河" in raw


@pytest.mark.parametrize("action", ["write:2", "memory:2"])
def test_structured_retrieval_uses_current_complete_objects_without_old_state(action):
    spec, snap, plan = configured()
    snap["context"]["characters"][0]["current_state"] = "FORMAL_OLD_STATE"
    snap["knowledge_sources"] = state_sources(snap["context"])
    working = deepcopy(snap["context"])
    working["characters"][0]["current_state"] = "CANDIDATE_CURRENT_STATE"
    reports = {"role_working_state": working, "working_context": working}
    _, raw = role_context.render_for(
        spec, snap, action, plan, "林青在渡口改变了选择。", reports=reports
    )
    packet = json.loads(raw)
    assert "CANDIDATE_CURRENT_STATE" in raw and "FORMAL_OLD_STATE" not in raw
    assert all(h["kind"] == "chapter" for h in packet["knowledge_context"]["hits"])
    assert "尚未正式采用" in packet["reference_boundary"]["opening"]


def test_deleted_candidate_entity_cannot_be_rehydrated_from_formal_retrieval():
    spec, snap, plan = configured()
    snap["context"]["characters"][0]["current_state"] = "REMOVED_MARKER"
    snap["knowledge_sources"] = state_sources(snap["context"])
    working = {"characters": [], "relationships": [], "narrative_position": {}}
    _, raw = role_context.render_for(
        spec, snap, "memory:2", plan, "林青被提及。", reports={"role_working_state": working}
    )
    assert "REMOVED_MARKER" not in raw


def test_writer_growth_is_bounded_but_rewrite_keeps_the_entire_original():
    spec, snap, plan = configured()
    sizes = []
    for length in [1000, 100000]:
        body = "x" * length + "\n\n她刚答应保守秘密，还在等对方回应。"
        units = [
            {
                "unit": 1,
                "start": 0,
                "end": length,
                "outcome": "已约定守密",
                "position": {},
                "unresolved": [],
            },
            {
                "unit": 2,
                "start": length + 2,
                "end": len(body),
                "outcome": "等待回应",
                "position": {"in_progress": "正在对话"},
                "unresolved": [],
            },
        ]
        _, raw = role_context.render_for(
            spec, snap, "write:2", plan, body, reports={"role_units": units}
        )
        packet = json.loads(raw)
        assert "already_written" not in packet and "effective_plan" not in packet
        assert packet["continuity"]["recent_prose"]["text"] == "她刚答应保守秘密，还在等对方回应。"
        assert "已约定守密" in raw and "正在对话" in raw
        sizes.append(len(raw))
        _, rewritten = role_context.render_for(
            spec, snap, "rewrite", plan, body, reports={"edit_scope": {"instruction": "保留事件"}}
        )
        assert json.loads(rewritten)["original_draft"] == body
    # More matching past excerpts may be selected, but the hundredfold source growth
    # must not become a hundredfold request; recent prose and obligations stay intact.
    assert sizes[1] < 10000 and sizes[1] - sizes[0] < 2000


def test_memory_full_evidence_and_all_writable_categories_survive_compaction():
    system, packet = render("memory:1", "开场事实。\n\n结尾的实际变化。")
    assert [p["text"] for p in packet["candidate"]] == ["开场事实。", "结尾的实际变化。"]
    schema = packet["writable_fields"]
    assert set(schema["collections"]) == set(
        get_args(FactChange.model_fields["collection"].annotation)
    )
    assert "chapter_id" not in schema["collections"]["scenes"]["properties"]
    assert "history" not in schema["collections"]["reader_promises"]["properties"]
    assert "character_id" in schema["collections"]["beliefs"]["required"]
    assert "enum" in schema["collections"]["reader_promises"]["properties"]["kind"]
    assert len(json_text(schema).encode()) < 12000
    assert "domain_fields" not in packet and "change_schema" not in packet
    assert "本次唯一新增证据" in system
    schema["collections"].clear()
    assert memory_fields.fields()["collections"]


def test_compact_schema_preserves_every_writable_property_and_literal_default():
    full = StateDelta.model_json_schema()
    for collection, schema in memory_fields.fields()["collections"].items():
        ref = full["properties"]["add_" + collection]["items"]["$ref"]
        original = full["$defs"][ref.rsplit("/", 1)[1]]
        excluded = {"id", *memory_fields.GENERATED.get(collection, set())}
        assert set(schema["properties"]) == set(original["properties"]) - excluded
    assert "description" in memory_fields.fields()["collections"]["characters"]["properties"]
    literal = {"title": "actual title", "description": "actual content"}
    assert memory_fields.compact({"default": literal}) == {"default": literal}


def test_single_unit_chief_distinguishes_scenes_from_authorized_units():
    spec, snap, _ = configured()
    spec = spec.model_copy(update={"stage_mode": "single-unit-v1", "unit_limit": 1})
    _, raw = role_context.render_for(spec, snap, "plan")
    packet = json.loads(raw)
    scenes = packet["output_schema"]["properties"]["scenes"]
    assert (scenes["minItems"], scenes["maxItems"]) == (2, 4)
    assert "unit_limit" not in packet and "一个叙事单元" in packet["scene_count"]


def test_compact_memory_contract_still_compiles_actual_source_bound_changes():
    body = "她到达渡口。\n\n此刻她仍在渡口等人。"
    ids = [p["id"] for p in paragraphs(body)]
    report = {
        "position": {"current_location": "渡口", "recent_major_event": "抵达后等待"},
        "position_paragraph_ids": ids[-1:],
        "outcome": "她已抵达渡口",
        "unresolved": [],
        "changes": [
            {
                "collection": "places",
                "values": {"name": "渡口"},
                "paragraph_ids": ids[:1],
                "observation": "抵达渡口",
            }
        ],
    }
    compiled = memory_result(json_text(report), body, StoryState(), 1)
    assert compiled["status"] == "complete" and compiled["changes"][0]["value"]["name"] == "渡口"
    report["changes"][0]["paragraph_ids"] = ["foreign:1"]
    assert memory_result(json_text(report), body, StoryState(), 1)["diagnostics"]


def test_recent_summaries_keep_outcomes_not_full_old_state_payloads():
    spec, snap, plan = configured()
    snap["context"]["formal_summaries"] = [
        {
            "revision_id": str(i),
            "outcome": f"已发生结果{i}",
            "position": {},
            "coverage": "complete",
            "factual_changes": {"add_world_lore": [{"details": "OLD_HUGE" * 10000}]},
        }
        for i in range(3)
    ]
    snap["context"]["recent_chapters"] = [{"revision_id": "last", "body": "上一章未完交流。"}]
    _, raw = role_context.render_for(spec, snap, "write:1", plan)
    history = json.loads(raw)["knowledge_context"]
    assert len(history["recent_summaries"]) == 3 and "OLD_HUGE" not in raw
    assert history["previous_ending"]["text"] == "上一章未完交流。"
    assert len(json_text(history).encode()) <= role_materials.HISTORY_LIMITS["writer"]


def test_local_editor_scope_neighbors_and_no_rag_expansion():
    spec, snap, plan = configured()
    body = "前段保护。\n\n只修改这一段。\n\n后段保护。"
    ids = [p["id"] for p in paragraphs(body)]
    scope = {
        "paragraph_ids": ids[1:2],
        "protected_paragraph_ids": [ids[0], ids[2]],
        "instruction": "表达清楚",
        "candidate_sha256": "private-audit",
    }
    _, raw = role_context.render_for(spec, snap, "amend", plan, body, reports={"edit_scope": scope})
    packet = json.loads(raw)
    assert [p["id"] for p in packet["authorized_paragraphs"]] == ids[1:2]
    assert [p["id"] for p in packet["read_only_neighbors"]] == [ids[0], ids[2]]
    assert "knowledge_context" not in packet and "private-audit" not in raw
    assert role_queries.queries(spec, snap, "amend", plan, body, {"edit_scope": scope}) == []


@pytest.mark.parametrize("action", ["plan", "write:1", "memory:1", "checker"])
def test_input_order_audit_separation_and_snapshot_immutability(action):
    spec, snap, plan = configured()
    before = deepcopy(snap)
    _, raw = role_context.render_for(spec, snap, action, plan, "林青走向渡口。")
    packet = json.loads(raw)
    assert next(iter(packet)) == ("story_task" if action in {"plan", "write:1"} else "candidate")
    assert "context_selection" not in packet and "receipt_sha256" not in raw
    assert snap == before
    if action == "plan":
        assert packet["narrative_design"]["selected_cards"] == [
            c for c in snap["cards"] if c["id"] in spec.narrative_card_ids
        ]
        assert packet["output_schema"]["properties"]["scenes"]["maxItems"] == spec.unit_limit


def test_receipt_tampering_is_rejected_before_hydration():
    spec, snap, plan = configured()
    snap["knowledge_project_id"] = "book-a"
    snap["knowledge_sources"] = [source("chapter", "rev", "林青走向渡口。")]
    receipt = role_context.retrieval_for(spec, snap, "write:1", plan, None, {})
    receipt["hits"][0]["text"] = "tampered"
    receipt["sha256"] = fingerprint({k: v for k, v in receipt.items() if k != "sha256"})
    with pytest.raises(WorkflowError, match="片段"):
        role_context.render_for(
            spec, snap, "write:1", plan, reports={"knowledge_retrieval": receipt}
        )


@pytest.mark.parametrize("action", ["reader", "reader_amend", "title"])
def test_historical_reader_and_title_do_not_receive_rag(action):
    spec, snap, plan = configured()
    assert role_context.render_for(
        spec, snap, action, plan, "正文"
    ) == knowledge_context.render_for(role_context.previous_spec(spec), snap, action, plan, "正文")
