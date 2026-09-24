import json

import pytest

from novel_writer.generation.automation import contract_for as legacy_contract
from novel_writer.generation.content import paragraphs
from novel_writer.generation.feedback import (
    checker_feedback,
    contract_for,
    reader_feedback,
)
from novel_writer.generation.novel import slots_for
from novel_writer.generation.schemas import NovelRunSpec
from tests.unit.test_genre_generation import spec


def config(**changes):
    return spec(
        workflow="novel-run-v1",
        stage_mode="longform-v1",
        unit_limit=3,
        feedback_policy="advisory-v1",
        **changes,
    )


def test_new_api_default_disables_reader_and_keeps_legacy_contract():
    payload = config().model_dump()
    payload.pop("feedback_policy")
    assert NovelRunSpec.model_validate(payload).feedback_policy == "logic-v1"
    assert not NovelRunSpec.model_validate(payload).enable_reader
    old = spec(workflow="novel-run-v1")
    assert contract_for(old) == legacy_contract(old)
    assert contract_for(config()) != legacy_contract(config())


@pytest.mark.parametrize(
    "checker,reader,ending",
    [
        (True, False, ["checker"]),
        (False, True, ["reader"]),
        (False, False, []),
        (True, True, ["checker", "reader"]),
    ],
)
def test_optional_slots_do_not_interrupt_creative_units(checker, reader, ending):
    chosen = slots_for(config(enable_checker=checker, enable_reader=reader))
    assert chosen == [
        "plan",
        "write:1",
        "memory:1",
        "write:2",
        "memory:2",
        "write:3",
        "memory:3",
        *ending,
    ]


def test_uncertainty_and_bad_citations_remain_advice():
    body = "她推开门。"
    result = checker_feedback(
        json.dumps(
            {
                "conclusion": "unknown",
                "explanation": "前文未提供",
                "issues": [
                    {
                        "observation": "可能矛盾",
                        "paragraph_ids": ["missing"],
                        "severity": "blocking",
                        "local_edit": True,
                    }
                ],
            }
        ),
        body,
        config(),
    )
    assert result["blocking"] is False
    assert result["issues"][0]["paragraph_ids"] == []
    assert result["issues"][0]["local_edit"] is False
    assert result["diagnostics"]
    assert checker_feedback("开门前角色尚在楼下，建议核对。", body, config())["raw_feedback"]


def test_only_valid_explicit_local_advice_can_feed_opted_in_editor():
    body = "她推开门。"
    result = checker_feedback(
        json.dumps(
            {
                "issues": [
                    {
                        "observation": "重复用词",
                        "paragraph_ids": [paragraphs(body)[0]["id"]],
                        "severity": "warning",
                        "local_edit": True,
                    }
                ]
            }
        ),
        body,
        config(),
    )
    assert result["issues"][0]["local_edit"] is True
    assert not result["blocking"]


def test_reader_accepts_prose_without_scores_relationship_or_citation_fields():
    result = reader_feedback("我喜欢这里的留白，也想知道她为何离开。", "她离开。", [], config())
    assert result["experience"].startswith("我喜欢")
    assert not result["needs_attention"]
    assert result["reading_scope"]["candidate"]["end"] == 4


@pytest.mark.parametrize(
    "function,args",
    [(checker_feedback, ("", "正文", config())), (reader_feedback, ("", "正文", [], config()))],
)
def test_empty_feedback_is_not_reported_as_a_completed_read(function, args):
    with pytest.raises(ValueError, match="未收到"):
        function(*args)
