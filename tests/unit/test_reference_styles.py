from __future__ import annotations

from collections import Counter

from novel_writer.services.reference_styles import (
    clean_reference_text,
    detect_encoding,
    extract_structural_ranges,
    generate_candidates,
    parse_chapters,
    sha256_bytes,
)


def _chapter(number: str, *, contaminated: bool = False) -> str:
    daily = [
        f"“你今天还要去吗？”他把第{number}只杯子推过去，像是什么都不在意。",
        "“当然要去，顺便买菜。”她笑了一下，“你记得带伞。”",
    ] * 10
    conflict = ["“闭嘴！”她冷笑一声，“你以为威胁一句，我就会退吗？”"] * 10
    action = ["他猛地冲出门，拔出短刀挡住来人，翻身避开一拳，又挥刀斩下。"] * 10
    psychology = [
        "他心中犹豫，忽然意识到那句话另有意思，也明白自己漏掉了什么。",
        "不过事情显然没有这么简单，桌下那只手已经握紧，呼吸也慢了下来。",
    ] * 10
    paragraphs = [paragraph + paragraph for paragraph in daily + conflict + action + psychology]
    if contaminated:
        paragraphs.append("作者公告：感谢读者收藏，暂停更新两天。")
    return f"第{number}节 测试章节\r\n\r\n" + "\r\n\r\n".join(paragraphs)


def test_gb18030_and_crlf_chapters_keep_exact_offsets() -> None:
    source = (_chapter("一") + "\r\n\r\n" + _chapter("二")).encode("gb18030")
    encoding, text = detect_encoding(source)

    chapters = parse_chapters(text)

    assert encoding == "gb18030"
    assert len(chapters) == 2
    assert text[chapters[0].start_offset : chapters[0].end_offset].lstrip().startswith("“")
    assert chapters[0].cleaned_sha256 == sha256_bytes(
        clean_reference_text(text[chapters[0].start_offset : chapters[0].end_offset]).encode(
            "utf-8"
        )
    )


def test_cleaning_marks_pollution_without_modifying_source() -> None:
    text = _chapter("一", contaminated=True) + "\r\n\r\n" + _chapter("二")
    before = text

    chapters = parse_chapters(text)

    assert text == before
    assert "author_announcement" in chapters[0].exclusion_reasons
    assert "author_announcement" not in chapters[1].exclusion_reasons


def test_layered_candidate_generation_is_deterministic_and_complete() -> None:
    text = "\r\n\r\n".join(_chapter(value) for value in "一二三四五六七八九十")
    source_sha = sha256_bytes(text.encode("utf-8"))
    chapters = parse_chapters(text)

    first = generate_candidates(text, source_sha, chapters)
    second = generate_candidates(text, source_sha, chapters)
    counts = Counter(item.category for item in first)

    assert first == second
    assert counts == {
        "daily_dialogue": 10,
        "conflict_dialogue": 10,
        "action": 10,
        "psychology_information": 10,
        "opening": 10,
        "ending": 10,
        "full_chapter": 7,
    }
    assert len({item.id for item in first}) == len(first)


def test_structural_ranges_report_samples_and_uncertainty() -> None:
    texts = [_chapter("一"), _chapter("二")]

    first = extract_structural_ranges(texts)
    second = extract_structural_ranges(texts)

    assert first == second
    assert first["sample_count"] == 2
    assert first["sentence_length"]["ci95_high"] >= first["sentence_length"]["ci95_low"]
    assert first["dialogue_character_ratio"]["median"] > 0
    assert "interpretation" in first
