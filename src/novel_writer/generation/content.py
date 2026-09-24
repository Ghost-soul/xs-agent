from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from novel_writer.generation.schemas import (
    ChapterReview,
    NovelStoryPlan,
    ParagraphCategory,
    StoryPlan,
)
from novel_writer.services.errors import WorkflowError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return digest(json_text(value))


def parse_object(value: str) -> dict[str, Any]:
    value = value.strip()
    if value.startswith("```json\n") and value.endswith("\n```"):
        value = value[8:-4]
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("需要完整 JSON 对象；不会猜测或补造字段")
    return result


def parse_plan(value: str, allowed_characters: list[str]) -> StoryPlan:
    plan = StoryPlan.model_validate(parse_object(value))
    if not {c for scene in plan.scenes for c in scene.character_ids} <= set(allowed_characters):
        raise ValueError("Chief 使用了本次人物范围外的承载者")
    return plan


def parse_novel_plan(value: str, allowed_characters: list[str]) -> NovelStoryPlan:
    plan = NovelStoryPlan.model_validate(parse_object(value))
    if not {c for scene in plan.scenes for c in scene.character_ids} <= set(allowed_characters):
        raise ValueError("Chief 使用了本次人物范围外的承载者")
    return plan


def paragraphs(body: str) -> list[dict[str, Any]]:
    prefix = digest(body)[:16]
    return [
        {"id": f"{prefix}:{i}", "text": m.group(), "start": m.start(), "end": m.end()}
        for i, m in enumerate(re.finditer(r"[^\r\n]+", body), 1)
        if m.group().strip()
    ]


def review_result(raw: str, body: str) -> dict[str, Any]:
    original = parse_object(raw)
    warnings: list[str] = []
    clean = {k: v for k, v in original.items() if k in ChapterReview.model_fields}
    if set(clean) != set(original):
        warnings.append("额外字段保留在原响应，不解释为核心结论")
    for field, expected in (
        ("classifications", list),
        ("continuity", dict),
        ("revision_advice", str),
    ):
        if (
            field in clean
            and not isinstance(clean[field], expected)
            and not (field == "continuity" and clean[field] is None)
        ):
            clean.pop(field)
            warnings.append(f"辅助字段 {field} 无效，保留未知")
    review = ChapterReview.model_validate(clean)
    entries = paragraphs(body)
    known = {p["id"]: p for p in entries}
    findings = [f for f in review.findings if set(f.paragraph_ids) <= known.keys()]
    conflicts = [f for f in review.continuity_conflicts if set(f.paragraph_ids) <= known.keys()]
    invalid_core = len(findings) != len(review.findings) or len(conflicts) != len(
        review.continuity_conflicts
    )
    if invalid_core:
        warnings.append("核心观察存在失配引用，结果待判断；其他内容保留")
    categories: dict[str, str] = {}
    collisions: set[str] = set()
    for item in review.classifications:
        try:
            category = ParagraphCategory.model_validate(item)
            if not set(category.paragraph_ids) <= known.keys():
                raise ValueError("跨候选引用")
        except (ValueError, ValidationError):
            warnings.append("一项篇幅分类无效，保留未知")
            continue
        for identifier in category.paragraph_ids:
            if identifier in categories and categories[identifier] != category.category:
                collisions.add(identifier)
            categories[identifier] = category.category
    for identifier in collisions:
        categories[identifier] = "unknown"
    lengths = {p["id"]: len(re.sub(r"\s", "", p["text"])) for p in entries}
    total = sum(lengths.values())
    focus = sum(n for key, n in lengths.items() if categories.get(key) == "focus")
    unknown = sum(n for key, n in lengths.items() if categories.get(key, "unknown") == "unknown")
    ratio: dict[str, Any] | None = (
        None
        if not categories or not total
        else {
            "lower": focus / total,
            "upper": (focus + unknown) / total,
            "source": "model_classification",
            "semantic_correctness_verified": False,
        }
    )
    outcome = review.outcome
    if invalid_core or (outcome in {"realized", "partial"} and not findings):
        outcome = "unknown"
    needs_attention = (
        outcome != "realized"
        or bool(conflicts)
        or ratio is None
        or bool(ratio is not None and ratio["lower"] < 0.7)
    )
    return {
        **review.model_dump(mode="json"),
        "outcome": outcome,
        "findings": [f.model_dump() for f in findings],
        "continuity_conflicts": [f.model_dump() for f in conflicts],
        "ratio": ratio,
        "warnings": warnings,
        "needs_attention": needs_attention,
    }


def checked_body(value: str) -> str:
    if not value.strip():
        raise WorkflowError("供应商没有返回可见正文")
    if len(value) > 40000:
        raise WorkflowError("正文超过本次资源上限，原响应已保留")
    return value
