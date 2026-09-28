import json
from copy import deepcopy

import pytest

from novel_writer.generation import chief_json
from novel_writer.generation.chief_json import ChiefJSONError, parse_chief_json
from novel_writer.generation.content import digest, json_text
from novel_writer.generation.craft_models import CraftPlan
from novel_writer.generation.craft_plans import parse_stage
from novel_writer.generation.plan_format import PlanIncompleteError
from tests.unit.test_stage_craft import craft_fixture

SCHEMA = CraftPlan.model_json_schema()


def misplaced_braces(plan):
    """Neutral reproduction: extra scene closures and a swapped final scene/list closure."""
    base = {k: v for k, v in plan.items() if k not in {"scenes", "macro_progression"}}
    scenes = []
    for scene in plan["scenes"]:
        scene = dict(scene)
        development = scene.pop("development", {"onstage_process": "寻找线索"})
        scenes.append(json.dumps({**scene, "development": development}, ensure_ascii=False))
    assert len(scenes) >= 3
    scenes[0] += "}"
    scenes[1] += "}"
    scenes[-1] = scenes[-1][:-1]
    return (
        json.dumps(base, ensure_ascii=False)[:-1] + ',"scenes":[' + ",".join(scenes)
        + ']},"macro_progression":' + json.dumps(plan.get("macro_progression", "")) + "}"
    )


def test_realistic_nested_plan_repairs_only_punctuation_and_keeps_all_values():
    spec, snapshot, original = craft_fixture()
    original["scenes"] = [deepcopy(original["scenes"][0]) for _ in range(3)]
    for scene in original["scenes"]:
        scene["development"] = {"onstage_process": '对白："不要改 }, ] 和逗号，"；路径 C:\\notes'}
    original["macro_progression"] = "下一阶段仍保留悬念"
    raw = misplaced_braces(original)
    recovered = parse_chief_json(raw, SCHEMA)
    assert recovered.value == original
    assert len(recovered.receipt["edits"]) == 4
    assert recovered.receipt["source_sha256"] == digest(raw)
    rebuilt = raw
    for edit in recovered.receipt["edits"]:
        position, symbol = edit["position"], edit["symbol"]
        if edit["operation"] == "remove":
            assert rebuilt[position] == symbol
            rebuilt = rebuilt[:position] + rebuilt[position + 1:]
        else:
            rebuilt = rebuilt[:position] + symbol + rebuilt[position:]
    assert digest(rebuilt) == recovered.receipt["normalized_sha256"]
    assert json.loads(rebuilt) == original
    assert len(parse_stage(json_text(recovered.value), spec, snapshot).scenes) == 3


@pytest.mark.parametrize("raw", [
    '{"chapter_goal":"寻找线索",}',
    '{"chapter_goal":"寻找线索" "bridge":"抵达渡口"}',
    '{"chapter_goal" "寻找线索"}',
    '{"story_questions":["下一步？",]}',
    '{"story_questions":["甲？" "乙？"]}',
])
def test_missing_separators_and_trailing_commas_keep_explicit_values(raw):
    parsed = parse_chief_json(raw, SCHEMA)
    assert parsed.receipt and len(parsed.receipt["edits"]) == 1


@pytest.mark.parametrize("wrapper", [
    "```json\n{}\n```", "```JSON\r\n{}\r\n```", "```\n{}\n```", "\ufeff{}",
])
def test_single_json_fence_or_bom_is_lossless(wrapper):
    body = '{"chapter_goal":"原始 \\u4e2d 文与 [符号]"}'
    parsed = parse_chief_json(wrapper.format(body), SCHEMA)
    assert parsed.value == json.loads(body)
    assert parsed.receipt


def test_valid_response_needs_no_conversion_receipt():
    body = '{"chapter_goal":"保持原文"}'
    parsed = parse_chief_json(body, SCHEMA)
    assert parsed.receipt is None
    assert parsed.value == json.loads(body)


@pytest.mark.parametrize("raw", [
    '{"chapter_goal":"前", "chapter_goal":"后"}',
    '{"chapter_goal":"前", "\\u0063hapter_goal":"后"}',
    '{"scenes":[{"development":{"onstage_process":"前","onstage_process":"后"}}]}',
])
def test_duplicate_members_are_never_overwritten(raw):
    with pytest.raises(ChiefJSONError, match="重复字段"):
        parse_chief_json(raw, SCHEMA)


@pytest.mark.parametrize("raw", [
    '{"chapter_goal":"未完', '{"chapter_goal":"已写完文字"',
    '{"scenes":[', '{"chapter_goal":', '{"chapter_goal":"未完<|eos|>',
])
def test_unfinished_output_is_not_completed_even_with_a_successful_transport(raw):
    with pytest.raises(PlanIncompleteError):
        parse_chief_json(raw, SCHEMA)


@pytest.mark.parametrize("raw", [
    '{"chapter_goal":}', '{chapter_goal:"未加引号"}',
    '{"chapter_goal":"他说"你好""}', "{'chapter_goal':'单引号'}",
    '{"chapter_goal":NaN}', '{"chapter_goal":Infinity}',
    '{"chapter_goal":"甲"}\n{"chapter_goal":"乙"}',
    '以下为计划：{"chapter_goal":"甲"}',
    '{"story_questions":[1}2]}',
])
def test_repair_never_invents_values_discards_text_or_joins_numeric_tokens(raw):
    with pytest.raises((ValueError, ChiefJSONError)):
        parse_chief_json(raw, SCHEMA)


def test_ambiguous_container_ownership_is_rejected_instead_of_picking_shortest_path():
    with pytest.raises(ChiefJSONError, match="多种"):
        parse_chief_json('{"a":{"b":1},"c":2}}', {"type": "object"})


def test_frozen_structure_disambiguates_field_ownership():
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "a": {"type": "object", "properties": {"b": {"type": "integer"}},
                  "additionalProperties": False},
            "c": {"type": "integer"},
        },
    }
    assert parse_chief_json('{"a":{"b":1},"c":2}}', schema).value == {"a": {"b": 1}, "c": 2}


def test_missing_frozen_schema_does_not_repair_against_an_active_template():
    with pytest.raises(ChiefJSONError, match="没有绑定"):
        parse_chief_json('{"chapter_goal":"甲",}')


def test_bounded_search_rejects_excessive_damage_without_dropping_fields(monkeypatch):
    monkeypatch.setattr(chief_json, "MAX_EDITS", 1)
    with pytest.raises(ChiefJSONError, match="有限符号"):
        parse_chief_json('{"chapter_goal" "甲","bridge" "乙"}', SCHEMA)
    monkeypatch.setattr(chief_json, "MAX_WORK", 1)
    with pytest.raises(ChiefJSONError, match="候选过多"):
        parse_chief_json('{"chapter_goal":"甲",}', SCHEMA)


def test_many_possible_closures_stop_before_materializing_all_candidates(monkeypatch):
    monkeypatch.setattr(chief_json, "MAX_STATES", 3)
    with pytest.raises(ChiefJSONError, match="候选过多"):
        parse_chief_json('{"x":[{}, {}, {}, {}]}}', {"type": "object"})


def test_structure_repair_does_not_waive_the_original_cast_validation():
    spec, snapshot, original = craft_fixture()
    original["scenes"] = [deepcopy(original["scenes"][0]) for _ in range(3)]
    for scene in original["scenes"]:
        scene["development"] = {"onstage_process": "完整行动"}
    original["scenes"][0]["character_ids"].append("unapproved-character")
    parsed = parse_chief_json(misplaced_braces(original), SCHEMA)
    with pytest.raises(ValueError):
        parse_stage(json_text(parsed.value), spec, snapshot)
