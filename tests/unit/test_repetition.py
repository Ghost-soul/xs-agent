from copy import deepcopy

from novel_writer.generation.repetition import observe
from novel_writer.generation.writer_observations import current_units
from novel_writer.generation.writer_observations import observe as observe_call
from tests.unit.test_output_reliability import call

PARAGRAPH = (
    "他终于签下了协议，双方依约交出各自保管的凭证，"
    "在场见证人收好副本，约定次日出发前共同复核。"
)


def test_counts_subsequent_occurrences_only_and_keeps_source_unchanged():
    body = "\n\n".join([PARAGRAPH, "新的一天，他们已经离开会场。", PARAGRAPH, PARAGRAPH])
    report = observe(body)
    assert report["repeated_characters"] == len(PARAGRAPH) * 2
    assert report["repeated_paragraphs"] == 2 and report["groups"] == 1
    assert report["examples"] == [{"first_paragraph": 1, "repeat_paragraph": 3,
        "occurrences": 3, "paragraph_characters": len(PARAGRAPH)}]
    assert body.count(PARAGRAPH) == 3


def test_unicode_whitespace_end_markers_and_short_refrains():
    p = "𠮷" + PARAGRAPH
    body = p + "\r\n\r\n  " + p + "\t<|eos|>\n\n好。\n\n好。"
    report = observe(body)
    assert report["repeated_characters"] == len(p)
    assert report["characters"] == 2 * len(p) + 4
    assert report["repeated_paragraphs"] == 1
    assert observe("\n <|eos|>\n")["characters"] == 0
    assert observe("")["percent"] == 0


def test_near_matches_and_embedded_end_marker_are_not_silently_normalized():
    other = PARAGRAPH.replace("次日", "当日")
    body = PARAGRAPH + "\n\n" + other + "<|eos|>是正文提到的标记。"
    assert observe(body)["repeated_characters"] == 0
    assert observe(body)["characters"] == len(body.replace("\n", ""))


def test_read_only_call_observation_never_changes_completed_status_or_current_source_binding():
    record = call(PARAGRAPH + "\n\n" + PARAGRAPH, "write:1")
    record.status = "completed"
    record.request = {}
    before = deepcopy(record.response)
    assert observe_call(record)["repetition"]["repeated_characters"] == len(PARAGRAPH)
    assert record.status == "completed" and record.response == before
    assert current_units("作者已另写正文", [], [record]) == []


def test_many_repeats_use_bounded_examples_and_include_cross_unit_repeats():
    paragraphs = [f"{i}：" + PARAGRAPH for i in range(10)]
    body = "\n\n".join(paragraphs + paragraphs)
    report = observe(body)
    assert report["groups"] == 10 and len(report["examples"]) == 3
    assert report["percent"] == 50.0
