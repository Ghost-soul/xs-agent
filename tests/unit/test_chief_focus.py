import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from novel_writer.generation import chief_context, key_context
from novel_writer.generation.content import fingerprint, json_text
from novel_writer.generation.request_preparation import InputCapacityError, prepare_request
from novel_writer.knowledge.text import source, state_sources
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_role_context import configured


def fixture():
    spec, snapshot, plan = configured()
    return spec.model_copy(update={"context_policy": chief_context.POLICY}), snapshot, plan


def test_all_seven_frozen_contracts_and_seventy_requests_remain_exact():
    cases = json.loads(Path("tests/fixtures/generation-pre-chief-focus.json").read_text())
    for case in cases:
        spec, snapshot, plan = configured()
        spec = spec.model_copy(update={"context_policy": case["policy"]})
        assert chief_context.contract_for(spec) == case["contract"]
        for action, sha in case["renders"].items():
            actual = chief_context.render_for(spec, snapshot, action, plan, "source paragraph")
            assert fingerprint(actual) == sha


@pytest.mark.parametrize(
    "action", ["write:1", "write:2", "memory:1", "checker", "rewrite", "amend", "reader", "title"]
)
def test_other_roles_keep_v3_requests_and_saved_selection(action):
    spec, snapshot, plan = fixture()
    old_reports = {}
    new_reports = {}
    expected = key_context.render_for(
        chief_context.previous_spec(spec), snapshot, action, plan, "正文", reports=old_reports
    )
    actual = chief_context.render_for(spec, snapshot, action, plan, "正文", reports=new_reports)
    assert actual == expected and new_reports == old_reports


def test_chief_keeps_cards_task_schema_and_authority_boundaries():
    spec, snapshot, plan = fixture()
    _, before = key_context.render_for(chief_context.previous_spec(spec), snapshot, "plan", plan)
    _, after = chief_context.render_for(spec, snapshot, "plan", plan)
    old, new = json.loads(before), json.loads(after)
    for field in (
        "story_task",
        "narrative_design",
        "world_cards",
        "cast_scope",
        "reference_boundary",
        "future_proposal_not_fact",
        "unit_limit",
        "output_schema",
    ):
        assert new[field] == old[field]


def test_candidate_projection_preserves_all_decision_facts_and_voice_with_no_truncation():
    spec, snapshot, plan = fixture()
    person = {
        "id": str(uuid4()),
        "name": "远方候选",
        "library_status": "active",
        "tier": "A",
        "description": "完整身份；此前受伤，现在仍不能使用左手。",
        "current_state": "过去的细节" * 1000 + "只有旧印归还才可离开，否则必须留下。",
        "speech_style": "说话慢，句尾直截了当",
        "personality": "不轻易相信承诺",
        "decision_style": "先确认退路",
        "forbidden_behaviors": ["不得泄密"],
        "portrayal_profile": {
            "independent_goal": "找到失踪者",
            "unique_competence": "识别旧印",
            "value_boundary": "不出卖同伴",
            "conflict_method": "先提出交换",
            "stress_response": "遭逼迫时沉默",
            "attention_bias": ["优先描写袖口" * 100],
            "voice_examples": [{"quote": "先把印拿来。", "source": "author"}],
            "anti_examples": ["不要滔滔不绝"],
        },
    }
    snapshot["context"]["characters"].append(person)
    snapshot["cast_selection"]["characters"].append(deepcopy(person))
    original = deepcopy(snapshot)
    reports = {"_key_material_target": 0}
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan, reports=reports)
    actual = next(
        c for c in json.loads(raw)["formal_reference"]["characters"] if c["id"] == person["id"]
    )
    for key in (
        "id",
        "name",
        "description",
        "current_state",
        "speech_style",
        "personality",
        "decision_style",
        "forbidden_behaviors",
    ):
        assert actual[key] == person[key]
    assert actual["portrayal_profile"] == {
        k: v for k, v in person["portrayal_profile"].items() if k != "attention_bias"
    }
    assert "library_status" not in actual and "tier" not in actual
    assert snapshot == original
    assert reports["key_context_selection"]["required_above_target"]


def test_focal_expression_and_small_optional_candidate_expression_are_retained():
    spec, snapshot, plan = fixture()
    for person in snapshot["context"]["characters"]:
        person["portrayal_profile"] = {"attention_bias": ["留意别人停顿"]}
    spec = spec.model_copy(update={"character_ids": [snapshot["context"]["characters"][0]["id"]]})
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan)
    assert all(
        c["portrayal_profile"]["attention_bias"] == ["留意别人停顿"]
        for c in json.loads(raw)["formal_reference"]["characters"]
    )


def test_style_rules_keep_text_but_internal_provenance_stays_out_of_prompt():
    spec, snapshot, plan = fixture()
    reference = {
        "profile_id": "INTERNAL_PROFILE",
        "version": 5,
        "contains_reference_text": False,
        "positive_contract": [
            {
                "dimension": "rhythm",
                "rule": "保留人物停顿和自主回应",
                "evidence_sample_ids": ["INTERNAL_SAMPLE"] * 40,
            }
        ],
        "negative_transfer_rules": ["不要照搬人物与情节"],
    }
    snapshot["context"]["reference_style"] = deepcopy(reference)
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan)
    style = json.loads(raw)["style_guidance"]["reference_style"]
    assert style == {
        "positive_contract": [{"dimension": "rhythm", "rule": "保留人物停顿和自主回应"}],
        "negative_transfer_rules": reference["negative_transfer_rules"],
    }
    assert "INTERNAL_" not in raw
    assert snapshot["context"]["reference_style"] == reference


def test_latest_summary_and_historical_condition_survive_oversized_required_dossiers():
    spec, snapshot, plan = fixture()
    spec = spec.model_copy(update={"direction": "林青走向渡口"})
    paragraph = "林青在渡口想起往事。" + "很长的描述" * 180 + "只有出示旧印才可离开，否则无效。"
    ending = "江月还在等她回答。"
    snapshot["context"]["recent_chapters"] = [{"revision_id": "latest", "body": ending}]
    summary = {
        "revision_id": "latest",
        "outcome": "问题尚未回答",
        "unresolved": ["等待回应"],
        "position": {"current_location": "渡口"},
        "coverage": "complete",
    }
    snapshot["context"]["formal_summaries"] = [summary]
    snapshot["knowledge_sources"] = [
        *state_sources(snapshot["context"]),
        source("chapter", "old", paragraph, ordinal=1),
    ]
    snapshot["context"]["world_rules"] = [{"id": "required", "statement": "必要限制" * 4000}]
    before = deepcopy(snapshot)
    reports = {"_key_material_target": 0}
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan, reports=reports)
    data = json.loads(raw)
    assert data["knowledge_context"]["recent_summaries"] == [summary]
    assert data["knowledge_context"]["hits"][0]["text"] == paragraph
    assert data["knowledge_context"]["previous_ending"]["text"] == ending
    assert data["formal_reference"]["world_rules"] == snapshot["context"]["world_rules"]
    assert reports["key_context_selection"]["protected_continuity"] == {
        "recent_summaries": 1,
        "history_paragraphs": 1,
    }
    assert snapshot == before


def test_retrieved_history_does_not_overlap_ending_or_duplicate_other_hits():
    spec, snapshot, plan = fixture()
    spec = spec.model_copy(update={"direction": "林青走向渡口"})
    text = "林青在渡口交出旧印。\n\n林青在渡口等候回应。"
    snapshot["context"]["recent_chapters"] = [{"revision_id": "same", "body": text}]
    snapshot["knowledge_sources"] = [source("chapter", "same", text, ordinal=1)]
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan)
    history = json.loads(raw)["knowledge_context"]
    assert len(history["hits"]) == 1
    assert history["hits"][0]["text"] == "林青在渡口交出旧印。"
    assert history["previous_ending"]["text"] == "林青在渡口等候回应。"


def test_no_available_history_is_reported_as_empty_without_inventing_context():
    spec, snapshot, plan = fixture()
    snapshot["context"].update(recent_chapters=[], formal_summaries=[])
    snapshot["knowledge_sources"] = []
    reports = {}
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan, reports=reports)
    history = json.loads(raw)["knowledge_context"]
    assert not history["hits"] and not history["recent_summaries"]
    assert "previous_ending" not in history
    assert reports["key_context_selection"]["protected_continuity"] == {
        "recent_summaries": 0,
        "history_paragraphs": 0,
    }


def test_protected_long_ending_still_obeys_final_capacity_without_truncation():
    spec, snapshot, _ = fixture()
    capable = profile()
    spec = spec.model_copy(update={"chief_model": capable.models[0].id, "input_limit": 12000})
    snapshot["profile"] = capable.model_dump(mode="json")
    snapshot["counting"] = {"chief": {"method": "utf8-byte-upper-bound"}}
    ending = "完整末段，保留条件。" * 2000
    snapshot["context"]["recent_chapters"] = [{"revision_id": "latest", "body": ending}]
    reports = {}
    with pytest.raises(InputCapacityError) as failure:
        prepare_request(spec, snapshot, "plan", None, None, None, reports, {})
    unsent = json.loads(failure.value.request.user_prompt)
    assert unsent["knowledge_context"]["previous_ending"]["text"] == ending
    assert reports["key_context_selection"]["soft_target"] == 0


def test_single_unit_output_schema_and_review_evidence_are_preserved():
    spec, snapshot, plan = fixture()
    spec = spec.model_copy(update={"stage_mode": "single-unit-v1"})
    _, raw = chief_context.render_for(spec, snapshot, "plan", plan)
    data = json.loads(raw)
    assert "unit_limit" not in data and "scene_count" in data
    assert data["output_schema"]["properties"]["scenes"]["minItems"] == 2
    assert data["output_schema"]["properties"]["scenes"]["maxItems"] == 4
    _, raw = chief_context.render_for(
        spec, snapshot, "review", plan, "已写正文", reports={"completed_units": 1}
    )
    data = json.loads(raw)
    _, old = key_context.render_for(chief_context.previous_spec(spec), snapshot, "review", plan)
    assert data["output_schema"] == json.loads(old)["output_schema"]
    assert data["current_plan"] == plan and data["completed_units"] == 1
    assert "已写正文" in json_text(data["written_candidate"])
