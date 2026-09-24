import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from novel_writer.generation.budget import counting_config, input_tokens
from novel_writer.generation.content import json_text, paragraphs, parse_plan, review_result
from novel_writer.generation.prompts import WRITER_SYSTEM, render_task
from novel_writer.generation.schemas import GenerationSpec, StoryPlan


def spec(**changes):
    return GenerationSpec.model_validate(
        {
            "base_version_id": str(uuid4()),
            "focus_card_id": "girls_love_gl",
            "direction": "爱情影响选择",
            "character_ids": ["a", "b"],
            "viewpoint": "甲",
            "relationship_scope": "explore",
            "relationship_character_ids": ["a", "b"],
            "profile_id": "local",
            "chief_model": "test",
            "writer_model": "test",
            "max_cost_cny": "1",
            "input_limit": 100000,
            # Explicit legacy fixture limits; new-request defaults have separate coverage.
            "chief_output_limit": 6000,
            "writer_output_limit": 12000,
            "auxiliary_output_limit": None,
            **changes,
        }
    )


def plan(characters=None):
    return {
        "chapter_goal": "开始明确在意",
        "bridge": "结束手续后相见",
        "scenes": [
            {
                "event": "请求同行",
                "character_ids": characters or ["a", "b"],
                "focus_percent": 40,
                "transition_percent": 15,
                "other_percent": 0,
                "choice_and_response": "提出私人邀请，对方决定答应",
                "consequence": "改道同行",
            },
            {
                "event": "一起抵达",
                "character_ids": characters or ["a", "b"],
                "focus_percent": 35,
                "transition_percent": 0,
                "other_percent": 10,
                "choice_and_response": "她主动留下",
                "consequence": "约定下次见面",
            },
        ],
        "major_turn": "为她改变路线",
        "genre_causal_role": "没有吸引便不会同行",
        "opening_focus_percent": 10,
        "questions": [],
    }


def test_scope_and_planning_distribution():
    assert StoryPlan.model_validate(plan()).scenes[0].focus_percent == 40
    wrong = plan()
    wrong["scenes"][0]["focus_percent"] = 30
    with pytest.raises(ValidationError):
        StoryPlan.model_validate(wrong)
    with pytest.raises(ValueError, match="人物范围"):
        parse_plan(json_text(plan(["outside"])), ["a", "b"])
    with pytest.raises(ValidationError):
        spec(relationship_character_ids=["a"])
    with pytest.raises(ValidationError):
        spec(input_limit=200001)


def test_complete_same_cards_once_and_review_context_isolation():
    snapshot = {
        "cards": [{"id": "gl", "sha256": "card-source", "text": "唯一整卡正文"}],
        "context": {},
    }
    for action in ("plan", "write", "review"):
        system, user = render_task(spec(), snapshot, action, plan(), "现场正文")
        assert user.count("唯一整卡正文") == 1
        assert "爱情影响选择" in user
        if action == "review":
            assert "没有吸引便不会同行" not in user
    assert "作者阶段目标与主导题材 > Chief" in WRITER_SYSTEM


def report(body, **changes):
    ids = [p["id"] for p in paragraphs(body)]
    return json_text(
        {
            "outcome": "realized",
            "explanation": "行动受吸引影响",
            "findings": [{"observation": "改变选择", "paragraph_ids": [ids[0]]}],
            "classifications": [
                {"paragraph_ids": ids[:1], "category": "focus", "reason": "私人选择"}
            ],
            **changes,
        }
    )


def test_ratio_denominator_union_and_unknown_are_honest():
    body = "甲乙丙\n\n丁"
    value = review_result(report(body), body)
    assert value["ratio"]["lower"] == 0.75
    assert value["ratio"]["upper"] == 1
    ids = [p["id"] for p in paragraphs(body)]
    duplicate = [{"paragraph_ids": [ids[0], ids[0]], "category": "focus", "reason": "自主选择"}]
    value = review_result(report(body, classifications=duplicate), body)
    assert value["ratio"]["lower"] == 0.75
    duplicate.append({"paragraph_ids": [ids[0]], "category": "other", "reason": "意见冲突"})
    value = review_result(report(body, classifications=duplicate), body)
    assert value["ratio"]["lower"] == 0
    assert value["needs_attention"]


def test_optional_bad_categories_and_wrong_core_evidence_do_not_invent_success():
    body = "甲乙\n\n丙丁"
    value = review_result(report(body, classifications=[{"broken": True}]), body)
    assert value["outcome"] == "realized"
    assert value["ratio"] is None and value["needs_attention"]
    raw = json.loads(report(body))
    raw["findings"][0]["paragraph_ids"] = ["different-body:1"]
    value = review_result(json_text(raw), body)
    assert value["outcome"] == "unknown"
    assert value["needs_attention"]


@pytest.mark.parametrize(
    "focus,total,attention", [(59, 100, True), (69, 100, True), (70, 100, False)]
)
def test_ratio_boundaries(focus, total, attention):
    body = "甲" * focus + "\n\n" + "乙" * (total - focus)
    ids = [p["id"] for p in paragraphs(body)]
    categories = [
        {"paragraph_ids": [identifier], "category": category, "reason": "分类"}
        for identifier, category in zip(ids, ["focus", "transition"], strict=True)
    ]
    assert (
        review_result(report(body, classifications=categories), body)["needs_attention"]
        is attention
    )


def test_no_tokenizer_fallback_cannot_pretend_chinese_characters_are_tokens():
    config = counting_config(None, "unknown-gateway-model")
    assert input_tokens("百合", config) == 2054
    assert input_tokens("百合" * 20000, config) > 100000


def test_local_tokenizer_binding_disables_truncation_and_detects_tampering(tmp_path, monkeypatch):
    from tokenizers import Tokenizer, models, pre_tokenizers

    from novel_writer.generation.content import digest
    from novel_writer.services.errors import WorkflowError

    monkeypatch.setattr("novel_writer.generation.budget.TOKENIZER_ROOT", tmp_path)
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "word": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(3)
    tokenizer.save(str(tmp_path / "fixture.json"))
    manifest = {
        "models": ["fixture-model"],
        "source": "offline fixture, not a production tokenizer",
        "sha256": digest((tmp_path / "fixture.json").read_text(encoding="utf-8")),
    }
    (tmp_path / "fixture.manifest.json").write_text(json_text(manifest), encoding="utf-8")
    config = counting_config("fixture", "fixture-model")
    assert input_tokens("word " * 50, config) == 2098
    with pytest.raises(WorkflowError, match="模型或文件 SHA"):
        counting_config("fixture", "different-model")
    (tmp_path / "fixture.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(WorkflowError, match="模型或文件 SHA"):
        input_tokens("word", config)


def test_broken_optional_review_fields_preserve_core_observation():
    body = "她提出邀请。"
    result = review_result(
        report(body, classifications=None, continuity="bad", revision_advice=None), body
    )
    assert result["outcome"] == "realized"
    assert result["ratio"] is None
    assert len(result["warnings"]) == 3
