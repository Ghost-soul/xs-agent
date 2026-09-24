import json

import pytest

from novel_writer.generation import automation, context_budget
from novel_writer.generation.schemas import GenerationSpec
from tests.unit.test_generation_casting import automatic, roster, snapshot
from tests.unit.test_genre_generation import plan


def test_legacy_contract_and_prompts_do_not_upgrade():
    spec = automatic(stage_mode="longform-v1", unit_limit=2, context_policy="bounded-v1")
    snap = snapshot(spec, roster())
    assert spec.automation_policy == "legacy-v1"
    assert automation.contract_for(spec) == context_budget.contract_for(spec)
    assert automation.render_for(spec, snap, "plan") == context_budget.render_for(
        spec, snap, "plan"
    )


def test_automated_questions_separate_plot_choices_and_true_author_blockers():
    spec = automatic(stage_mode="longform-v1", unit_limit=2, automation_policy="stage-auto-v1")
    snap = snapshot(spec, roster())
    value = {
        **plan([c["id"] for c in snap["cast_selection"]["characters"][:2]]),
        "story_questions": ["谁先开口表达在意？"],
        "author_question_reasons": {},
    }
    parsed = automation.parse_stage(json.dumps(value), spec, snap)
    assert parsed.questions == []
    system, user = automation.render_for(spec, snap, "plan")
    assert "普通人物选择" in system and "author_question_reasons" in user
    value["questions"] = ["正式档案互相矛盾，请明确人物身份"]
    with pytest.raises(ValueError, match="作者问题必须"):
        automation.parse_stage(json.dumps(value), spec, snap)
    value["author_question_reasons"] = {
        value["questions"][0]: {
            "kind": "missing_canonical_fact",
            "source": "formal_reference.characters",
            "why_blocked": "两个正式身份互相矛盾，不能选择其中一个冒充事实",
        }
    }
    assert automation.parse_stage(json.dumps(value), spec, snap).questions
    assert GenerationSpec.model_validate(spec.model_dump()).automation_policy == "stage-auto-v1"
