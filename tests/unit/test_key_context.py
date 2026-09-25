import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from novel_writer.generation import key_context, key_materials, key_queries, role_context
from novel_writer.generation.content import fingerprint, json_text, paragraphs
from novel_writer.generation.schemas import AmendmentRequest, FrozenGenerationSpec, NovelRunSpec
from novel_writer.knowledge.text import source, state_sources
from novel_writer.services.errors import WorkflowError
from tests.unit.test_role_context import configured


def fixture():
    spec, snapshot, plan = configured()
    return spec.model_copy(update={"context_policy": key_context.POLICY}), snapshot, plan


def render(action="plan", body="林青走向渡口。", reports=None):
    spec, snapshot, plan = fixture()
    system, raw = key_context.render_for(spec, snapshot, action, plan, body, reports=reports)
    return system, json.loads(raw)


def test_sixty_old_requests_and_six_contracts_are_unchanged():
    for case in json.loads(Path("tests/fixtures/generation-pre-key-context.json").read_text()):
        spec, snapshot, plan = configured()
        spec = spec.model_copy(update={"context_policy": case["policy"]})
        assert key_context.contract_for(spec) == case["contract"]
        for action, sha in case["renders"].items():
            assert (
                fingerprint(
                    key_context.render_for(spec, snapshot, action, plan, "source paragraph")
                )
                == sha
            )


def test_new_defaults_leave_frozen_spec_and_frozen_v2_policy_intact():
    spec, _, _ = fixture()
    raw = spec.model_dump(mode="json")
    raw.pop("context_policy")
    assert NovelRunSpec.model_validate(raw).context_policy == "chief-focus-v4"
    assert FrozenGenerationSpec.model_validate(raw).context_policy == "full-v1"
    assert AmendmentRequest.model_fields["context_policy"].default == "chief-focus-v4"
    assert role_context.POLICY == "role-rag-v2"


def test_chief_retains_candidate_voice_but_does_not_expand_all_candidate_relations():
    spec, snapshot, plan = fixture()
    extra = {
        "id": str(uuid4()),
        "name": "远方候选",
        "current_state": "左臂受伤，不能举弓",
        "speech_style": "完整声音",
        "forbidden_behaviors": ["不得泄露约定"],
        "portrayal_profile": {"voice_examples": [{"quote": "完整示例"}]},
        "mind_state": {"current_emotions": [{"cause": "无关的旧情绪" * 1000}]},
    }
    snapshot["context"]["characters"].append(extra)
    snapshot["cast_selection"]["characters"].append(deepcopy(extra))
    snapshot["context"].setdefault("relationships", []).append(
        {
            "id": str(uuid4()),
            "source_character_id": extra["id"],
            "target_character_id": str(uuid4()),
            "description": "遥远无关关系" * 1000,
        }
    )
    before = deepcopy(snapshot)
    reports = {}
    _, raw = key_context.render_for(spec, snapshot, "plan", plan, reports=reports)
    packet = json.loads(raw)
    person = next(c for c in packet["formal_reference"]["characters"] if c["id"] == extra["id"])
    assert person["speech_style"] == extra["speech_style"]
    assert person["portrayal_profile"] == extra["portrayal_profile"]
    assert person["current_state"] == extra["current_state"]
    assert person["forbidden_behaviors"] == extra["forbidden_behaviors"]
    assert "无关的旧情绪" not in raw and "遥远无关关系" not in raw
    assert "key_context_selection" not in raw and snapshot == before
    assert reports["key_context_selection"]["omitted_count"] >= 1


@pytest.mark.parametrize("action", ["plan", "write:1", "memory:1", "checker"])
def test_unrelated_world_and_non_candidate_people_do_not_grow_prompt_linearly(action):
    spec, snapshot, plan = fixture()
    snapshot["knowledge_sources"] = state_sources(snapshot["context"])
    _, before = key_context.render_for(spec, snapshot, action, plan, "林青走向渡口。")
    for i in range(150):
        snapshot["knowledge_sources"] += [
            source(
                "world_lore",
                f"unrelated-{i}",
                json_text(
                    {
                        "id": f"unrelated-{i}",
                        "name": f"异域{i}",
                        "summary": "遥远地域",
                        "details": ["没有被当前任务涉及的地理资料。" * 60],
                    }
                ),
            )
        ]
    reports = {}
    _, after = key_context.render_for(
        spec, snapshot, action, plan, "林青走向渡口。", reports=reports
    )
    assert "异域" not in after
    assert len(after) < len(before) + 500
    assert reports["key_context_selection"]["omitted_count"] >= 150


def test_required_unscoped_rule_exceeds_soft_target_without_truncation_or_local_rejection():
    spec, snapshot, plan = fixture()
    statement = "必须满足前提，否则无效。" * 600
    snapshot["context"]["world_rules"] = [{"id": str(uuid4()), "statement": statement}]
    reports = {"_key_material_target": 0}
    _, raw = key_context.render_for(spec, snapshot, "write:1", plan, reports=reports)
    assert json.loads(raw)["formal_reference"]["world_rules"][0]["statement"] == statement
    assert reports["key_context_selection"]["required_above_target"]


def test_all_manual_cards_participate_even_after_fourth_and_still_reach_chief():
    spec, snapshot, plan = fixture()
    cards = [
        {"id": f"card-{i}", "name": f"事件标签{i}", "text": f"独特事件机制{i}"} for i in range(13)
    ]
    snapshot["cards"] += cards
    spec = spec.model_copy(update={"narrative_card_ids": [c["id"] for c in cards]})
    query = key_queries.query_plan(spec, snapshot, "plan", plan, None, {})
    assert len(query["queries"]) <= 8 and max(map(len, query["queries"])) <= 300
    assert len(query["cards"]) == len(cards)
    for card, coverage in zip(cards, query["cards"], strict=True):
        assert card["text"] in json_text(query["lexical_queries"])
        assert coverage["lexical"] and coverage["semantic_characters"] > 0
    _, raw = key_context.render_for(spec, snapshot, "plan", plan)
    assert json.loads(raw)["narrative_design"]["selected_cards"] == cards


@pytest.mark.parametrize("action", ["memory:2", "checker", "memory_amend", "checker_amend"])
def test_evidence_queries_find_middle_alias_outside_windows_and_ignore_future_plan(action):
    spec, snapshot, plan = fixture()
    person = {
        "id": str(uuid4()),
        "name": "罕见人物",
        "aliases": ["旧师父"],
        "current_state": "负伤",
    }
    snapshot["knowledge_sources"] = [
        *state_sources(snapshot["context"]),
        source("characters", person["id"], json_text(person)),
    ]
    body = "原文" * 1700 + "旧师父将钥匙交给她。" + "内容" * 3000
    plan["scenes"] = [{"event": "未来尚未发生"}]
    query = key_queries.query_plan(spec, snapshot, action, plan, body, {})
    assert "罕见人物" in query["lexical_queries"]
    assert "未来尚未发生" not in json_text(query)
    _, raw = key_context.render_for(spec, snapshot, action, plan, body)
    assert "负伤" in raw
    assert [p["text"] for p in json.loads(raw)["candidate"]] == [body]


def test_history_hit_expands_whole_paragraph_and_preserves_late_condition():
    spec, snapshot, plan = fixture()
    original = "林青在渡口回想旧事。" + "连续描述" * 90 + "只有持有旧印才准过河，否则不得进入。"
    snapshot["knowledge_sources"] = [source("chapter", "old", original, ordinal=1)]
    _, raw = key_context.render_for(spec, snapshot, "write:1", plan)
    hits = json.loads(raw)["knowledge_context"]["hits"]
    assert len(hits) == 1 and hits[0]["text"] == original
    assert hits[0]["start"] == 0 and hits[0]["end"] == len(original)


def test_optional_world_hit_keeps_named_prerequisite_with_original_conditions():
    spec, snapshot, plan = fixture()
    lore = [
        {
            "id": "item-a",
            "name": "月令",
            "summary": "林青在渡口的通行办法",
            "details": ["只有无声桥完好才可通行，否则月令无效。"],
        },
        {
            "id": "item-b",
            "name": "无声桥",
            "summary": "必须先确认桥面完整",
            "details": ["雨后桥面可能损毁，不能凭月令跳过检查。"],
        },
    ]
    snapshot["context"]["world_lore"] = lore
    snapshot["knowledge_sources"] = state_sources(snapshot["context"])
    _, raw = key_context.render_for(spec, snapshot, "write:1", plan)
    selected = json.loads(raw)["formal_reference"]["world_lore"]
    assert {r["id"] for r in selected} == {"item-a", "item-b"}
    assert selected == lore


def test_temporal_dependency_and_related_partner_preserved_without_entire_social_graph():
    spec, snapshot, plan = fixture()
    first, second = snapshot["context"]["characters"][:2]
    earlier, later, constraint = [str(uuid4()) for _ in range(3)]
    snapshot["context"]["events"] = [
        {"id": earlier, "summary": "受伤后才能请假", "happens_before": [later]},
        {"id": later, "summary": "请假", "happens_before": []},
    ]
    snapshot["context"]["timeline_constraints"] = [
        {"id": constraint, "before_event_id": earlier, "after_event_id": later, "reason": "先后"}
    ]
    snapshot["context"]["relationships"] = [
        {
            "id": str(uuid4()),
            "source_character_id": first["id"],
            "target_character_id": second["id"],
            "description": f"约定取决于{earlier}",
        }
    ]
    _, raw = key_context.render_for(spec, snapshot, "memory:1", plan, first["name"] + "提及约定。")
    reference = json.loads(raw)["opening_reference"]
    assert {e["id"] for e in reference["events"]} == {earlier, later}
    assert reference["timeline_constraints"][0]["id"] == constraint


def test_working_deletion_not_resurrected_and_whole_review_uses_formal_start():
    spec, snapshot, plan = fixture()
    snapshot["context"]["world_rules"] = [{"id": "rule", "statement": "开场旧条件"}]
    snapshot["knowledge_sources"] = state_sources(snapshot["context"])
    working = deepcopy(snapshot["context"])
    working["world_rules"] = []
    working["characters"][0]["current_state"] = "当前已负伤"
    reports = {"role_working_state": working}
    _, unit = key_context.render_for(spec, snapshot, "write:2", plan, reports=reports)
    assert "开场旧条件" not in unit and "当前已负伤" in unit
    _, check = key_context.render_for(spec, snapshot, "checker", plan, "正文", reports=reports)
    assert "开场旧条件" in check and "当前已负伤" not in check


def test_rewrite_has_complete_original_and_no_unit_instructions_or_later_plan():
    original = "第一段。\n\n" + "完整改写稿" * 1500
    system, packet = render("rewrite", original, {"edit_scope": {"instruction": "保留全部事件"}})
    assert packet["original_draft"] == original
    assert packet["revision_instruction"] == "保留全部事件"
    assert (
        "plot_execution" not in packet
        and "stage_context" not in packet
        and "continuity" not in packet
    )
    assert "当前一个完整叙事单元" not in system


def test_editor_protects_neighbors_and_includes_only_relevant_expression_material():
    body = "前文只读。\n\n林青说道。\n\n后文只读。"
    entries = paragraphs(body)
    system, packet = render(
        "amend",
        body,
        {
            "edit_scope": {
                "instruction": "让语气符合林青",
                "paragraph_ids": [entries[1]["id"]],
                "protected_paragraph_ids": [entries[0]["id"], entries[2]["id"]],
            }
        },
    )
    assert packet["authorized_paragraphs"] == [{k: entries[1][k] for k in ("id", "text")}]
    assert len(packet["read_only_neighbors"]) == 2
    assert "林青完整声音档案" in json_text(packet["expression_reference"])
    assert "knowledge_context" not in packet and "unsafe_to_change" in system


def test_cold_reader_uses_only_contiguous_public_text_and_complete_read_scope():
    spec, snapshot, plan = fixture()
    snapshot["context"]["recent_chapters"] = [
        {"revision_id": "old", "body": "公开旧文。\n公开结尾。"}
    ]
    snapshot["context"]["world_rules"] = [{"id": "secret", "statement": "不能偷看的秘密"}]
    reports = {"memory": {"outcome": "不能偷看的报告"}, "checker": {"explanation": "核对结果"}}
    body = "完整冷读正文" * 800
    system, raw = key_context.render_for(spec, snapshot, "reader", plan, body, reports=reports)
    packet = json.loads(raw)
    assert [p["text"] for p in packet["candidate"]] == [body]
    assert "不能偷看" not in raw and "核对结果" not in raw
    assert "formal_reference" not in raw and "knowledge_context" not in raw
    assert packet["public_preceding"][0]["text"] == "公开旧文。\n公开结尾。"
    assert "数据库" not in system


def test_tampered_retrieval_cannot_be_hydrated():
    spec, snapshot, plan = fixture()
    snapshot["knowledge_sources"] = [source("chapter", "old", "林青走向渡口。")]
    receipt = key_context.retrieval_for(spec, snapshot, "write:1", plan, None, {})
    receipt["hits"][0]["text"] = "改写原文"
    receipt["sha256"] = fingerprint({k: v for k, v in receipt.items() if k != "sha256"})
    with pytest.raises(WorkflowError, match="片段"):
        key_context.render_for(
            spec, snapshot, "write:1", plan, reports={"knowledge_retrieval": receipt}
        )


def test_title_keeps_unchanged_independent_contract():
    spec, snapshot, plan = fixture()
    assert key_context.render_for(spec, snapshot, "title", plan, "正文") == role_context.render_for(
        key_context.previous_spec(spec), snapshot, "title", plan, "正文"
    )


def test_mandatory_continuity_keeps_complete_last_paragraph_over_soft_target():
    spec, snapshot, plan = fixture()
    text = "未完交流" * 2000
    reports = {
        "_key_material_target": 0,
        "role_units": [
            {
                "unit": 1,
                "start": 0,
                "end": len(text),
                "outcome": "仍在商议",
                "position": {},
                "unresolved": [],
            }
        ],
    }
    _, raw = key_context.render_for(spec, snapshot, "write:2", plan, text, reports=reports)
    packet = json.loads(raw)
    assert packet["continuity"]["recent_prose"]["text"] == text
    assert packet["continuity"]["recent_prose"]["complete"]
    assert reports["key_context_selection"]["required_count"] > key_materials.TARGETS["writer"]
