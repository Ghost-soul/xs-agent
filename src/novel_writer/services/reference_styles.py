"""Local-only reference corpus parsing and abstract style profile extraction.

The source novel is never copied into database records.  Manifests and samples
retain hashes and decoded-character offsets; excerpts are read from the source
on demand after the file fingerprint is revalidated.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    GlobalReferenceStyleDefaultRecord,
    ProjectRecord,
    ReferenceCorpusManifestRecord,
    ReferenceStyleProfileRecord,
    ReferenceStyleSampleRecord,
)
from novel_writer.services.errors import NotFoundError, WorkflowError

PARSER_VERSION = "reference-corpus-v1"
REFERENCE_NAMESPACE = UUID("b7e15c4c-fdc8-4cd7-9406-7e12421c33a1")
SampleCategory = Literal[
    "daily_dialogue",
    "conflict_dialogue",
    "action",
    "psychology_information",
    "opening",
    "ending",
    "full_chapter",
]
SampleDecision = Literal["candidate", "approved", "excluded", "false_positive"]
SAMPLE_QUOTAS: dict[str, int] = {
    "daily_dialogue": 10,
    "conflict_dialogue": 10,
    "action": 10,
    "psychology_information": 10,
    "opening": 10,
    "ending": 10,
    "full_chapter": 7,
}
STYLE_DIMENSIONS = {
    "rhythm",
    "dialogue",
    "humor",
    "narrator_stance",
    "information_release",
    "ending",
}

_HEADING = re.compile(
    r"(?m)^[ \t\u3000]*"
    r"(第[0-9０-９〇零一二两三四五六七八九十百千万]+[章节卷回部集][^\r\n]{0,70})"
    r"[ \t\u3000]*\r?$"
)
_HTML = re.compile(r"(?is)<(?:br\s*/?|/?p|/?div|/?span)[^>]*>|<[^>]+>")
_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_BLANKS = re.compile(r"\n[ \t\u3000]*\n(?:[ \t\u3000]*\n)+")
_DIALOGUE = re.compile(r"[“「『\"]([^”」』\"]+)[”」』\"]")
_SENTENCE = re.compile(r"[^。！？!?…]+[。！？!?…]*")
_ANNOUNCEMENT = re.compile(
    r"作者公告|感谢(?:各位)?读者|求(?:点击|收藏|推荐|月票)|暂停更新|停更|评分|本书群|章节感言"
)
_TRUNCATED = re.compile(r"(?:\.{2,}|未完待续|待续)\s*$")
_CONFLICT = re.compile(r"怒|吼|骂|滚|闭嘴|混蛋|威胁|杀|不许|休想|质问|争|反驳|冷笑|喝道|厉声")
_ACTION = re.compile(r"冲|扑|砍|斩|刺|劈|踢|拳|闪|跃|撞|抓|挡|退|追|逃|爆|挥|射|砸|翻身|拔出|鲜血")
_PSYCHOLOGY = re.compile(
    r"心想|心中|暗想|意识到|明白|觉得|感到|记起|想起|犹豫|恐惧|担心|疑惑|念头|脑海"
)
_EXPLANATION = re.compile(r"也就是说|换句话说|事实上|显然|原因是|这意味着|所谓|简单来说|总而言之")
_EMOTION = re.compile(r"愤怒|悲伤|高兴|喜悦|恐惧|紧张|震惊|失望|尴尬|羞愧|绝望")
_BODY = re.compile(r"皱眉|咬牙|握紧|心头一|心中一紧|脸色|瞳孔|呼吸|冷汗|颤抖|僵住|一怔")
_ASIDE = re.compile(r"当然|不过|可惜|说起来|撇开|显然|要知道|事实上|总之|反正")
_SIMILE = re.compile(r"像|如同|仿佛|好似|犹如|宛如|恰似|好像")
_FIRST_PERSON = re.compile(r"我|我们|咱|本人")
_THIRD_PERSON = re.compile(r"他|她|他们|她们|其|对方")


class ParsedChapter(BaseModel):
    ordinal: int
    title: str
    start_offset: int
    end_offset: int
    raw_chars: int
    cleaned_chars: int
    cleaned_sha256: str
    exclusion_reasons: list[str] = Field(default_factory=list)


class CandidateSample(BaseModel):
    id: UUID
    category: SampleCategory
    chapter_ordinal: int
    chapter_title: str
    start_offset: int
    end_offset: int
    raw_sha256: str
    cleaned_sha256: str
    score: float


class SampleReview(BaseModel):
    decision: SampleDecision
    approved_dimensions: list[str] = Field(default_factory=list)
    prohibited_transfer: list[str] = Field(default_factory=list)
    author_note: str = Field(default="", max_length=500)

    @field_validator("approved_dimensions")
    @classmethod
    def dimensions_are_known(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        unknown = set(cleaned) - STYLE_DIMENSIONS
        if unknown:
            raise ValueError(f"未知认可维度：{', '.join(sorted(unknown))}")
        return cleaned


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest().upper()


def detect_encoding(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return encoding, content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise WorkflowError("参考语料编码无法识别；支持 UTF-8、GB18030/GBK 和 UTF-16")


def clean_reference_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML.sub("\n", text.replace("&nbsp;", " "))
    text = _INVISIBLE.sub("", text)
    lines = [line.strip(" \t\u3000") for line in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def parse_chapters(text: str) -> list[ParsedChapter]:
    matches = list(_HEADING.finditer(text))
    seen_hashes: set[str] = set()
    chapters: list[ParsedChapter] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[start:end]
        cleaned = clean_reference_text(raw)
        digest = sha256_bytes(cleaned.encode("utf-8"))
        reasons: list[str] = []
        if len(cleaned) < 1500:
            reasons.append("abnormally_short")
        if len(cleaned) > 15000:
            reasons.append("abnormally_long")
        if _TRUNCATED.search(cleaned[-160:]):
            reasons.append("truncated_ending")
        if _ANNOUNCEMENT.search(cleaned[:800] + "\n" + cleaned[-800:]):
            reasons.append("author_announcement")
        if "<br" in raw.casefold() or re.search(r"</?(?:p|div|span)\b", raw, re.I):
            reasons.append("web_residue_cleaned")
        if digest in seen_hashes:
            reasons.append("duplicate_body")
        seen_hashes.add(digest)
        chapters.append(
            ParsedChapter(
                ordinal=index + 1,
                title=match.group(1).strip(),
                start_offset=start,
                end_offset=end,
                raw_chars=len(raw),
                cleaned_chars=len(cleaned),
                cleaned_sha256=digest,
                exclusion_reasons=reasons,
            )
        )
    if not chapters:
        raise WorkflowError("没有识别到“第…章/节/卷”等中文章节标题")
    return chapters


def _paragraph_spans(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    raw = text[start:end]
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"(?s)(?<!\S).*?(?=\n\s*\n|\Z)", raw):
        value = match.group(0).strip()
        if not value:
            continue
        left = match.start() + len(match.group(0)) - len(match.group(0).lstrip())
        right = match.end() - (len(match.group(0)) - len(match.group(0).rstrip()))
        spans.append((start + left, start + right, clean_reference_text(value)))
    return spans


def _span_candidate(
    source_sha: str,
    category: SampleCategory,
    chapter: ParsedChapter,
    start: int,
    end: int,
    text: str,
    score: float,
) -> CandidateSample:
    raw_sha = sha256_bytes(text.encode("utf-8"))
    cleaned = clean_reference_text(text)
    identity = f"{source_sha}:{category}:{start}:{end}:{raw_sha}"
    return CandidateSample(
        id=uuid5(REFERENCE_NAMESPACE, identity),
        category=category,
        chapter_ordinal=chapter.ordinal,
        chapter_title=chapter.title,
        start_offset=start,
        end_offset=end,
        raw_sha256=raw_sha,
        cleaned_sha256=sha256_bytes(cleaned.encode("utf-8")),
        score=round(score, 4),
    )


def generate_candidates(
    text: str, source_sha: str, chapters: list[ParsedChapter]
) -> list[CandidateSample]:
    pools: dict[str, list[CandidateSample]] = {key: [] for key in SAMPLE_QUOTAS}
    eligible = [
        chapter
        for chapter in chapters
        if not set(chapter.exclusion_reasons) - {"web_residue_cleaned"}
    ]
    for chapter in eligible:
        paragraphs = _paragraph_spans(text, chapter.start_offset, chapter.end_offset)
        if len(paragraphs) < 3:
            continue
        full_text = text[chapter.start_offset : chapter.end_offset]
        if 3000 <= len(clean_reference_text(full_text)) <= 9000:
            pools["full_chapter"].append(
                _span_candidate(
                    source_sha,
                    "full_chapter",
                    chapter,
                    chapter.start_offset,
                    chapter.end_offset,
                    full_text,
                    1 / (1 + abs(chapter.cleaned_chars - 4500) / 4500),
                )
            )
        for category, selected in (("opening", paragraphs[:5]), ("ending", paragraphs[-5:])):
            chosen = _fit_paragraphs(
                selected, minimum=240, maximum=1100, from_end=category == "ending"
            )
            if chosen:
                start, end = chosen[0][0], chosen[-1][1]
                sample_category = cast(SampleCategory, category)
                pools[category].append(
                    _span_candidate(
                        source_sha,
                        sample_category,
                        chapter,
                        start,
                        end,
                        text[start:end],
                        1
                        + (
                            0.2
                            if category == "ending"
                            and (_CONFLICT.search(chosen[-1][2]) or _DIALOGUE.search(chosen[-1][2]))
                            else 0
                        ),
                    )
                )
        for offset in range(0, len(paragraphs), 3):
            window = _fit_paragraphs(paragraphs[offset : offset + 8], minimum=350, maximum=1300)
            if not window:
                continue
            start, end = window[0][0], window[-1][1]
            excerpt = clean_reference_text(text[start:end])
            dialogue_chars = sum(len(item) for item in _DIALOGUE.findall(excerpt))
            dialogue_ratio = dialogue_chars / max(len(excerpt), 1)
            conflict = len(_CONFLICT.findall(excerpt))
            action = len(_ACTION.findall(excerpt))
            psychology = len(_PSYCHOLOGY.findall(excerpt))
            if dialogue_ratio >= 0.28:
                dialogue_category: SampleCategory = (
                    "conflict_dialogue" if conflict >= 2 else "daily_dialogue"
                )
                pools[dialogue_category].append(
                    _span_candidate(
                        source_sha,
                        dialogue_category,
                        chapter,
                        start,
                        end,
                        text[start:end],
                        dialogue_ratio + min(conflict, 8) / 20,
                    )
                )
            if action >= 4:
                pools["action"].append(
                    _span_candidate(
                        source_sha,
                        "action",
                        chapter,
                        start,
                        end,
                        text[start:end],
                        action / max(len(excerpt) / 300, 1),
                    )
                )
            if psychology >= 2:
                pools["psychology_information"].append(
                    _span_candidate(
                        source_sha,
                        "psychology_information",
                        chapter,
                        start,
                        end,
                        text[start:end],
                        psychology / max(len(excerpt) / 400, 1),
                    )
                )
    selected_candidates: list[CandidateSample] = []
    for category, quota in SAMPLE_QUOTAS.items():
        ranked = sorted(
            pools[category], key=lambda item: (-item.score, item.chapter_ordinal, item.start_offset)
        )
        used_chapters: set[int] = set()
        diverse: list[CandidateSample] = []
        for item in ranked:
            if item.chapter_ordinal not in used_chapters:
                used_chapters.add(item.chapter_ordinal)
                diverse.append(item)
        selected_candidates.extend(
            (diverse + [item for item in ranked if item not in diverse])[:quota]
        )
    return selected_candidates


def _fit_paragraphs(
    paragraphs: list[tuple[int, int, str]], *, minimum: int, maximum: int, from_end: bool = False
) -> list[tuple[int, int, str]]:
    ordered = list(reversed(paragraphs)) if from_end else paragraphs
    selected: list[tuple[int, int, str]] = []
    count = 0
    for item in ordered:
        if selected and count + len(item[2]) > maximum:
            break
        selected.append(item)
        count += len(item[2])
        if count >= minimum:
            break
    if from_end:
        selected.reverse()
    return selected if count >= minimum else []


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {
            key: 0.0
            for key in ("p10", "p25", "median", "p75", "p90", "mean", "ci95_low", "ci95_high")
        }
    ordered = sorted(values)

    def pct(q: float) -> float:
        index = (len(ordered) - 1) * q
        lower, upper = math.floor(index), math.ceil(index)
        return (
            ordered[lower]
            if lower == upper
            else ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
        )

    average = mean(ordered)
    variance = sum((item - average) ** 2 for item in ordered) / max(len(ordered) - 1, 1)
    margin = 1.96 * math.sqrt(variance / len(ordered))
    return {
        "p10": round(pct(0.1), 3),
        "p25": round(pct(0.25), 3),
        "median": round(median(ordered), 3),
        "p75": round(pct(0.75), 3),
        "p90": round(pct(0.9), 3),
        "mean": round(average, 3),
        "ci95_low": round(max(0, average - margin), 3),
        "ci95_high": round(average + margin, 3),
    }


def extract_structural_ranges(texts: list[str]) -> dict[str, Any]:
    sentence_lengths: list[float] = []
    paragraph_lengths: list[float] = []
    utterance_lengths: list[float] = []
    dialogue_ratios: list[float] = []
    adjacent_variations: list[float] = []
    equal_paragraph_ratios: list[float] = []
    dialogue_turns: list[float] = []
    marker_density: dict[str, list[float]] = {
        key: []
        for key in (
            "explanation",
            "emotion_naming",
            "body_reaction",
            "narrator_aside",
            "simile",
        )
    }
    opening_types: Counter[str] = Counter()
    ending_types: Counter[str] = Counter()
    paragraph_ending_types: Counter[str] = Counter()
    mode_transitions: Counter[str] = Counter()
    perspective_counts: Counter[str] = Counter()
    for text in texts:
        cleaned = clean_reference_text(text)
        sentences = [
            item.group(0).strip() for item in _SENTENCE.finditer(cleaned) if item.group(0).strip()
        ]
        lengths = [len(item) for item in sentences]
        sentence_lengths.extend(lengths)
        adjacent_variations.extend(
            abs(right - left) for left, right in zip(lengths, lengths[1:], strict=False)
        )
        paragraphs = [item.strip() for item in cleaned.split("\n") if item.strip()]
        plengths = [len(item) for item in paragraphs]
        paragraph_lengths.extend(plengths)
        equal = sum(
            1 for left, right in zip(plengths, plengths[1:], strict=False) if abs(left - right) <= 5
        )
        equal_paragraph_ratios.append(equal / max(len(plengths) - 1, 1))
        utterances = _DIALOGUE.findall(cleaned)
        utterance_lengths.extend(len(item) for item in utterances)
        dialogue_chars = sum(len(item) for item in utterances)
        dialogue_ratios.append(dialogue_chars / max(len(cleaned), 1))
        longest_turns = 0
        current = 0
        for paragraph in paragraphs:
            current = current + 1 if _DIALOGUE.search(paragraph) else 0
            longest_turns = max(longest_turns, current)
        dialogue_turns.append(longest_turns)
        scale = max(len(cleaned) / 1000, 0.1)
        for key, pattern in (
            ("explanation", _EXPLANATION),
            ("emotion_naming", _EMOTION),
            ("body_reaction", _BODY),
            ("narrator_aside", _ASIDE),
            ("simile", _SIMILE),
        ):
            marker_density[key].append(len(pattern.findall(cleaned)) / scale)
        modes = [_paragraph_mode(item) for item in paragraphs]
        mode_transitions.update(
            f"{left}->{right}"
            for left, right in zip(modes, modes[1:], strict=False)
            if left != right
        )
        paragraph_ending_types.update(_endpoint_type(item, opening=False) for item in paragraphs)
        perspective_counts[
            "first_person"
            if len(_FIRST_PERSON.findall(cleaned)) > len(_THIRD_PERSON.findall(cleaned))
            else "third_person"
        ] += 1
        first = paragraphs[0] if paragraphs else ""
        last = paragraphs[-1] if paragraphs else ""
        opening_types[_endpoint_type(first, opening=True)] += 1
        ending_types[_endpoint_type(last, opening=False)] += 1
    return {
        "contract_version": "reference-structural-ranges-v1",
        "sample_count": len(texts),
        "total_characters": sum(len(clean_reference_text(item)) for item in texts),
        "sentence_length": _percentiles(sentence_lengths),
        "adjacent_sentence_variation": _percentiles(adjacent_variations),
        "paragraph_length": _percentiles(paragraph_lengths),
        "consecutive_similar_paragraph_ratio": _percentiles(equal_paragraph_ratios),
        "dialogue_character_ratio": _percentiles(dialogue_ratios),
        "utterance_length": _percentiles(utterance_lengths),
        "continuous_dialogue_turns": _percentiles(dialogue_turns),
        "marker_density_per_kchar": {
            key: _percentiles(value) for key, value in marker_density.items()
        },
        "opening_type_distribution": _counter_ratio(opening_types),
        "ending_type_distribution": _counter_ratio(ending_types),
        "paragraph_ending_type_distribution": _counter_ratio(paragraph_ending_types),
        "paragraph_mode_transition_distribution": _counter_ratio(mode_transitions),
        "perspective_distribution": _counter_ratio(perspective_counts),
        "interpretation": "区间仅作建议；样本量与置信区间必须随报告展示，不得转成每章硬配额。",
    }


def _endpoint_type(text: str, *, opening: bool) -> str:
    if _DIALOGUE.search(text):
        return "dialogue"
    if _ACTION.search(text):
        return "action"
    if _CONFLICT.search(text):
        return "danger_or_conflict"
    if _PSYCHOLOGY.search(text):
        return "psychology"
    if opening:
        return "setting_or_information"
    if re.search(r"决定|选择|答应|拒绝|必须|不能", text):
        return "choice"
    return "fact_or_closure"


def _counter_ratio(counter: Counter[str]) -> dict[str, float]:
    total = sum(counter.values())
    return {key: round(value / max(total, 1), 3) for key, value in sorted(counter.items())}


def _paragraph_mode(text: str) -> str:
    if _DIALOGUE.search(text):
        return "dialogue"
    if _ACTION.search(text):
        return "action"
    if _PSYCHOLOGY.search(text):
        return "psychology"
    return "narration"


DEFAULT_POSITIVE_CONTRACT = [
    {
        "dimension": "narrator_stance",
        "rule": "第三人称叙述可以带轻微口语判断和人物贴近感，但不替人物宣布情绪。",
    },
    {
        "dimension": "rhythm",
        "rule": "长短句自然混排；短句承担动作、反应或落点，不连续排成整齐口号。",
    },
    {
        "dimension": "dialogue",
        "rule": "对白通过争辩、调侃、误会和利益差异改变场面，避免轮流递送设定。",
    },
    {"dimension": "humor", "rule": "幽默来自人物关系、身份反差和现实后果，不由旁白解释笑点。"},
    {
        "dimension": "information_release",
        "rule": "场景优先从行为或对白进入，背景解释随冲突和人物需要分批释放。",
    },
    {
        "dimension": "ending",
        "rule": "段尾和章末落在具体对白、动作、关系变化或新信息上，不强补总结与反问。",
    },
]
DEFAULT_NEGATIVE_RULES = [
    "不得复制参考作品人物、关系、情节、专有名词或连续原句。",
    "不得继承参考作品的价值判断、性描写尺度、固定口癖、错字和病句。",
    "不得让参考档案覆盖正式世界规则、既成事实、人物稳定声音、知识边界或作者当前方向。",
    "不得把统计区间变成机械配额，也不得靠堆叠高频词制造表面模仿。",
]


class ReferenceStyleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(
        self,
        project_id: UUID,
        source_path: str,
        *,
        local_analysis_allowed: bool,
        provider_excerpt_allowed: bool,
    ) -> dict[str, Any]:
        if not local_analysis_allowed:
            raise WorkflowError("必须明确允许本地分析；这不会授权任何 Provider 外发")
        await self._project(project_id)
        path = Path(source_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise WorkflowError("参考语料路径不是文件")
        content = path.read_bytes()
        source_sha = sha256_bytes(content)
        encoding, text = detect_encoding(content)
        chapters = parse_chapters(text)
        candidates = generate_candidates(text, source_sha, chapters)
        existing = await self.session.scalar(
            select(ReferenceCorpusManifestRecord).where(
                ReferenceCorpusManifestRecord.project_id == project_id,
                ReferenceCorpusManifestRecord.source_sha256 == source_sha,
            )
        )
        report = self._report(chapters, candidates)
        if existing is None:
            existing = ReferenceCorpusManifestRecord(
                project_id=project_id,
                source_path=str(path),
                source_name=path.name,
                source_sha256=source_sha,
                byte_size=len(content),
                source_mtime=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                encoding=encoding,
                parser_version=PARSER_VERSION,
                local_analysis_allowed=True,
                provider_excerpt_allowed=provider_excerpt_allowed,
                chapters=[item.model_dump(mode="json") for item in chapters],
                parse_report=report,
            )
            self.session.add(existing)
            await self.session.flush()
            self.session.add_all(
                [
                    ReferenceStyleSampleRecord(
                        id=item.id,
                        manifest_id=existing.id,
                        category=item.category,
                        chapter_ordinal=item.chapter_ordinal,
                        chapter_title=item.chapter_title,
                        start_offset=item.start_offset,
                        end_offset=item.end_offset,
                        raw_sha256=item.raw_sha256,
                        cleaned_sha256=item.cleaned_sha256,
                        status="candidate",
                        approved_dimensions=[],
                        prohibited_transfer=[],
                        author_note="",
                    )
                    for item in candidates
                ]
            )
        else:
            existing.source_path = str(path)
            existing.provider_excerpt_allowed = provider_excerpt_allowed
            existing.parse_report = report
        await self.session.flush()
        return await self.manifest(project_id, existing.id)

    async def manifest(
        self,
        project_id: UUID,
        manifest_id: UUID | None = None,
        *,
        sample_category: SampleCategory | None = None,
    ) -> dict[str, Any]:
        await self._project(project_id)
        query = select(ReferenceCorpusManifestRecord).where(
            ReferenceCorpusManifestRecord.project_id == project_id
        )
        if manifest_id:
            query = query.where(ReferenceCorpusManifestRecord.id == manifest_id)
        else:
            query = query.order_by(ReferenceCorpusManifestRecord.created_at.desc())
        record = await self.session.scalar(query)
        if record is None:
            return {"project_id": str(project_id), "manifest": None, "samples": [], "profiles": []}
        source_status = self._source_status(record)
        sample_query = select(ReferenceStyleSampleRecord).where(
            ReferenceStyleSampleRecord.manifest_id == record.id
        )
        if sample_category is not None:
            sample_query = sample_query.where(
                ReferenceStyleSampleRecord.category == sample_category
            )
        samples = (
            await self.session.scalars(
                sample_query.order_by(
                    ReferenceStyleSampleRecord.category, ReferenceStyleSampleRecord.chapter_ordinal
                )
            )
        ).all()
        approved_counts = {
            category: count
            for category, count in (
                await self.session.execute(
                    select(
                        ReferenceStyleSampleRecord.category,
                        func.count(ReferenceStyleSampleRecord.id),
                    )
                    .where(
                        ReferenceStyleSampleRecord.manifest_id == record.id,
                        ReferenceStyleSampleRecord.status == "approved",
                    )
                    .group_by(ReferenceStyleSampleRecord.category)
                )
            ).all()
        }
        profiles = (
            await self.session.scalars(
                select(ReferenceStyleProfileRecord)
                .where(ReferenceStyleProfileRecord.project_id == project_id)
                .order_by(ReferenceStyleProfileRecord.version.desc())
            )
        ).all()
        return {
            "project_id": str(project_id),
            "manifest": self._manifest_dict(record, source_status),
            "samples": [self._sample_dict(item, record, include_text=True) for item in samples],
            "profiles": [self._profile_dict(item) for item in profiles],
            "approved_counts": approved_counts,
            "sample_quotas": SAMPLE_QUOTAS,
            "style_dimensions": sorted(STYLE_DIMENSIONS),
        }

    async def review_sample(
        self, project_id: UUID, sample_id: UUID, review: SampleReview
    ) -> dict[str, Any]:
        sample = await self.session.get(ReferenceStyleSampleRecord, sample_id)
        if sample is None:
            raise NotFoundError("reference style sample not found")
        manifest = await self.session.get(ReferenceCorpusManifestRecord, sample.manifest_id)
        if manifest is None or manifest.project_id != project_id:
            raise NotFoundError("reference style sample not found")
        if review.decision == "approved" and not review.approved_dimensions:
            raise WorkflowError("认可样本时至少勾选一个希望学习的维度")
        sample.status = review.decision
        sample.approved_dimensions = review.approved_dimensions
        sample.prohibited_transfer = review.prohibited_transfer
        sample.author_note = review.author_note
        await self.session.flush()
        return self._sample_dict(sample, manifest, include_text=True)

    async def create_draft(self, project_id: UUID, manifest_id: UUID) -> dict[str, Any]:
        manifest = await self.session.get(ReferenceCorpusManifestRecord, manifest_id)
        if manifest is None or manifest.project_id != project_id:
            raise NotFoundError("reference corpus manifest not found")
        self._verify_source(manifest)
        approved = (
            await self.session.scalars(
                select(ReferenceStyleSampleRecord).where(
                    ReferenceStyleSampleRecord.manifest_id == manifest_id,
                    ReferenceStyleSampleRecord.status == "approved",
                )
            )
        ).all()
        coverage = Counter(item.category for item in approved)
        missing = [key for key in SAMPLE_QUOTAS if coverage[key] == 0]
        if missing:
            raise WorkflowError(
                "每类至少认可一个样本后才能生成档案草稿；尚缺：" + "、".join(missing)
            )
        texts = [self._read_sample(item, manifest) for item in approved]
        version = (
            await self.session.scalar(
                select(func.max(ReferenceStyleProfileRecord.version)).where(
                    ReferenceStyleProfileRecord.project_id == project_id
                )
            )
        ) or 0
        approved_by_dimension = {
            dimension: [str(item.id) for item in approved if dimension in item.approved_dimensions]
            for dimension in STYLE_DIMENSIONS
        }
        positive_contract = [
            {
                **item,
                "evidence_sample_ids": approved_by_dimension[item["dimension"]][:8],
            }
            for item in DEFAULT_POSITIVE_CONTRACT
            if approved_by_dimension[item["dimension"]]
        ]
        if not positive_contract:
            raise WorkflowError("认可样本尚未提供可用于正向合同的维度证据")
        profile = ReferenceStyleProfileRecord(
            project_id=project_id,
            manifest_id=manifest_id,
            version=version + 1,
            status="draft",
            structural_ranges=extract_structural_ranges(texts),
            positive_contract=positive_contract,
            negative_transfer_rules=DEFAULT_NEGATIVE_RULES,
            provenance={
                "source_sha256": manifest.source_sha256,
                "parser_version": manifest.parser_version,
                "sample_ids": [str(item.id) for item in approved],
                "sample_count": len(approved),
                "coverage": dict(sorted(coverage.items())),
            },
            blind_test={},
        )
        self.session.add(profile)
        await self.session.flush()
        return self._profile_dict(profile)

    async def update_draft(
        self,
        project_id: UUID,
        profile_id: UUID,
        *,
        positive_contract: list[dict[str, Any]],
        negative_transfer_rules: list[str],
    ) -> dict[str, Any]:
        profile = await self._profile(project_id, profile_id)
        if profile.status != "draft":
            raise WorkflowError("只有草稿档案可以编辑；已激活版本不可原地覆盖")
        profile.positive_contract = _validate_contract(
            positive_contract,
            allowed_sample_ids=set(profile.provenance.get("sample_ids", [])),
        )
        profile.negative_transfer_rules = _validate_rules(negative_transfer_rules)
        manifest = await self.session.get(ReferenceCorpusManifestRecord, profile.manifest_id)
        if manifest is None:
            raise NotFoundError("reference corpus manifest not found")
        self._assert_no_source_overlap(
            manifest,
            [item["rule"] for item in profile.positive_contract] + profile.negative_transfer_rules,
        )
        await self.session.flush()
        return self._profile_dict(profile)

    async def activate(
        self, project_id: UUID, profile_id: UUID, *, blind_test: dict[str, Any], confirmed: bool
    ) -> dict[str, Any]:
        if not confirmed:
            raise WorkflowError("激活参考文风档案需要作者明确确认")
        profile = await self._profile(project_id, profile_id)
        if profile.status != "draft":
            raise WorkflowError("只能激活草稿档案")
        required = (
            "more_natural",
            "closer_rhythm",
            "dialogue_interaction",
            "no_content_transfer",
            "keeps_project_voice",
        )
        if not all(blind_test.get(key) is True for key in required):
            raise WorkflowError("必须完成盲测并确认自然度、节奏、对白、无内容迁移和项目声音五项")
        active = (
            await self.session.scalars(
                select(ReferenceStyleProfileRecord).where(
                    ReferenceStyleProfileRecord.project_id == project_id,
                    ReferenceStyleProfileRecord.status == "active",
                )
            )
        ).all()
        for item in active:
            item.status = "superseded"
        profile.status = "active"
        profile.blind_test = blind_test
        profile.activated_at = datetime.now(UTC)
        await self.session.flush()
        return self._profile_dict(profile)


    async def set_global_default(
        self,
        project_id: UUID,
        profile_id: UUID,
        *,
        confirmed: bool,
        reason: str,
    ) -> dict[str, Any]:
        if not confirmed:
            raise WorkflowError("设置全局参考文风需要作者明确确认")
        profile = await self._profile(project_id, profile_id)
        if profile.status != "active":
            raise WorkflowError("只有已完成盲测并激活的参考文风才能设为全局默认")
        record = await self.session.get(GlobalReferenceStyleDefaultRecord, "writer")
        if record is None:
            record = GlobalReferenceStyleDefaultRecord(
                key="writer",
                profile_id=profile.id,
                author_reason=reason.strip(),
            )
        else:
            record.profile_id = profile.id
            record.author_reason = reason.strip()
        self.session.add(record)
        await self.session.flush()
        return {
            "profile_id": str(profile.id),
            "profile_version": profile.version,
            "source_project_id": str(profile.project_id),
            "selection_scope": "global_default",
            "contains_reference_text": False,
            "author_reason": record.author_reason,
        }

    async def _project(self, project_id: UUID) -> ProjectRecord:
        project = await self.session.get(ProjectRecord, project_id)
        if project is None:
            raise NotFoundError("project not found")
        return project

    async def _profile(self, project_id: UUID, profile_id: UUID) -> ReferenceStyleProfileRecord:
        profile = await self.session.get(ReferenceStyleProfileRecord, profile_id)
        if profile is None or profile.project_id != project_id:
            raise NotFoundError("reference style profile not found")
        return profile

    def _source_status(self, record: ReferenceCorpusManifestRecord) -> str:
        try:
            self._verify_source(record)
        except WorkflowError:
            return "changed_or_missing"
        return "verified"

    def _verify_source(self, record: ReferenceCorpusManifestRecord) -> bytes:
        path = Path(record.source_path)
        if not path.is_file():
            raise WorkflowError("参考语料源文件已移动或不存在；旧档案仍可追溯，但不能静默读取")
        content = path.read_bytes()
        if sha256_bytes(content) != record.source_sha256:
            raise WorkflowError("参考语料指纹已变化，请重新登记为新语料版本")
        return content

    def _assert_no_source_overlap(
        self, manifest: ReferenceCorpusManifestRecord, rules: list[str]
    ) -> None:
        content = self._verify_source(manifest)
        _, source = detect_encoding(content)
        compact_source = re.sub(r"\s+", "", source)
        for rule in rules:
            compact_rule = re.sub(r"\s+", "", rule)
            for index in range(max(0, len(compact_rule) - 23)):
                fragment = compact_rule[index : index + 24]
                if len(fragment) == 24 and fragment in compact_source:
                    raise WorkflowError("文风合同含有与参考原文连续重合的长片段；请改写为抽象规则")

    def _read_sample(
        self, sample: ReferenceStyleSampleRecord, manifest: ReferenceCorpusManifestRecord
    ) -> str:
        content = self._verify_source(manifest)
        _, text = detect_encoding(content)
        raw = text[sample.start_offset : sample.end_offset]
        if sha256_bytes(raw.encode("utf-8")) != sample.raw_sha256:
            raise WorkflowError("样本偏移或哈希不匹配，拒绝读取")
        cleaned = clean_reference_text(raw)
        if sha256_bytes(cleaned.encode("utf-8")) != sample.cleaned_sha256:
            raise WorkflowError("样本清洗哈希不匹配，拒绝读取")
        return cleaned

    def _sample_dict(
        self,
        sample: ReferenceStyleSampleRecord,
        manifest: ReferenceCorpusManifestRecord,
        *,
        include_text: bool,
    ) -> dict[str, Any]:
        result = {
            "sample_id": str(sample.id),
            "category": sample.category,
            "chapter_ordinal": sample.chapter_ordinal,
            "chapter_title": sample.chapter_title,
            "start_offset": sample.start_offset,
            "end_offset": sample.end_offset,
            "raw_sha256": sample.raw_sha256,
            "cleaned_sha256": sample.cleaned_sha256,
            "decision": sample.status,
            "approved_dimensions": sample.approved_dimensions,
            "prohibited_transfer": sample.prohibited_transfer,
            "author_note": sample.author_note,
        }
        if include_text:
            try:
                result["text"] = self._read_sample(sample, manifest)
                result["source_status"] = "verified"
            except WorkflowError:
                result["text"] = None
                result["source_status"] = "changed_or_missing"
        return result

    def _manifest_dict(
        self, record: ReferenceCorpusManifestRecord, source_status: str
    ) -> dict[str, Any]:
        return {
            "manifest_id": str(record.id),
            "source_name": record.source_name,
            "source_path": record.source_path,
            "source_sha256": record.source_sha256,
            "byte_size": record.byte_size,
            "source_mtime": record.source_mtime.isoformat(),
            "encoding": record.encoding,
            "parser_version": record.parser_version,
            "local_analysis_allowed": record.local_analysis_allowed,
            "provider_excerpt_allowed": record.provider_excerpt_allowed,
            "source_status": source_status,
            "parse_report": record.parse_report,
            "chapters": record.chapters,
        }

    def _profile_dict(self, profile: ReferenceStyleProfileRecord) -> dict[str, Any]:
        return {
            "profile_id": str(profile.id),
            "manifest_id": str(profile.manifest_id),
            "version": profile.version,
            "status": profile.status,
            "structural_ranges": profile.structural_ranges,
            "positive_contract": profile.positive_contract,
            "negative_transfer_rules": profile.negative_transfer_rules,
            "provenance": profile.provenance,
            "blind_test": profile.blind_test,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "activated_at": profile.activated_at.isoformat() if profile.activated_at else None,
        }

    def _report(
        self, chapters: list[ParsedChapter], candidates: list[CandidateSample]
    ) -> dict[str, Any]:
        reasons = Counter(reason for chapter in chapters for reason in chapter.exclusion_reasons)
        retained = sum(
            not (set(chapter.exclusion_reasons) - {"web_residue_cleaned"}) for chapter in chapters
        )
        counts = Counter(item.category for item in candidates)
        return {
            "total_chapters": len(chapters),
            "retained_chapters": retained,
            "excluded_chapters": len(chapters) - retained,
            "exclusion_reasons": dict(sorted(reasons.items())),
            "candidate_counts": dict(sorted(counts.items())),
            "candidate_quotas": SAMPLE_QUOTAS,
            "deterministic": True,
            "provider_calls": 0,
        }


def _validate_contract(
    items: list[dict[str, Any]], *, allowed_sample_ids: set[str]
) -> list[dict[str, Any]]:
    if not 1 <= len(items) <= 12:
        raise WorkflowError("正向文风合同需要 1–12 条")
    result: list[dict[str, Any]] = []
    for item in items:
        dimension, rule = item.get("dimension", "").strip(), item.get("rule", "").strip()
        if dimension not in STYLE_DIMENSIONS or not rule or len(rule) > 240:
            raise WorkflowError("文风合同维度无效，或规则为空/超过240字")
        evidence = list(dict.fromkeys(str(value) for value in item.get("evidence_sample_ids", [])))
        if not evidence or not set(evidence).issubset(allowed_sample_ids):
            raise WorkflowError("每条正向文风合同必须回指至少一个本版本认可样本")
        result.append(
            {
                "dimension": dimension,
                "rule": rule,
                "evidence_sample_ids": evidence[:8],
            }
        )
    return result


def _validate_rules(items: list[str]) -> list[str]:
    result = list(dict.fromkeys(item.strip() for item in items if item.strip()))
    if not 1 <= len(result) <= 12 or any(len(item) > 240 for item in result):
        raise WorkflowError("禁止继承规则需要 1–12 条，单条不超过240字")
    return result
