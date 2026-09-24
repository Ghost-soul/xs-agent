"""Versioned narrative units without system-assigned length targets."""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from novel_writer.generation import focused_context, plan_capacity
from novel_writer.generation.content import digest, fingerprint, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.errors import WorkflowError


def enabled(spec: GenerationSpec) -> bool:
    return spec.length_policy == "unit-v1"


def contract_for(spec: GenerationSpec) -> str:
    base = plan_capacity.contract_for(spec)
    return (
        fingerprint({"base": base, "unit_scope": inspect.getsource(sys.modules[__name__])})
        if enabled(spec)
        else base
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else plan_capacity.amendment_contract(spec)


def compatibility_spec(spec: GenerationSpec) -> GenerationSpec:
    return spec.model_copy(update={"chapter_count": 1, "target_characters": 4000})


def fit_context(spec, snapshot, profile, latest_chapter_id):
    # This existing material-selection heuristic is local only. Final preview and
    # dispatch counts always use the target-free render_for below.
    return focused_context.fit_context(
        compatibility_spec(spec) if enabled(spec) else spec,
        snapshot,
        profile,
        latest_chapter_id,
    )


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    if not enabled(spec):
        return plan_capacity.render_for(spec, snapshot, action, plan, body, author_note, reports)
    # Legacy renderers remain byte-stable. Their temporary arithmetic values never
    # enter the new request, saved scope, or chapter boundaries.
    render_spec = compatibility_spec(spec)
    system, raw = plan_capacity.render_for(
        render_spec, snapshot, action, plan, body, author_note, reports
    )
    payload = parse_object(raw)
    for key in ("estimated_chapters", "total_target_characters", "unit_target_characters"):
        payload.pop(key, None)
    role = role_for(action)
    if role in {"chief", "writer"}:
        system = system.replace("篇幅目标是节奏参考，容量上限不是写满目标。", "").replace(
            "篇幅目标是节奏参考，token 上限是容量而非写满目标。", ""
        )
        if role == "chief":
            system += (
                "\n按事件的因果联系设计叙事单元，不设字数或预计章节数。每个单元围绕一个连贯的"
                "事件过程，明确人物行动、选择、回应及本次产生的后果；单元长短由事件展开决定。"
                "关键变化在当前单元内发生，保留可供下一单元接续的状态；不为凑单元数拆分事件。"
            )
            if "scene_count" in payload:
                payload["scene_count"] = "本次为一个完整叙事单元，可包含二至四个连贯场面"
                system = system.replace("单章二至四个场面", "单单元二至四个场面").replace(
                    "单章安排二至四个场面", "单单元安排二至四个场面"
                )
        else:
            system += (
                "\n本次按叙事单元完成创作，不设字数目标或最低篇幅。将 current_task 中的事件、"
                "人物选择、回应与直接后果写到本单元的自然结束处，再交付正文。重要变化在现场展开，"
                "避免用提纲或概述替代关键过程；自然完成后即可结束，不凑字数、不抢写后续单元。"
                "模型输出容量仅是资源上限，不是创作目标。"
            )
            if action == "rewrite":
                system += "\n本次为作者独立授权的整稿改写，完整处理授权原稿及修改要求。"
    return system, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def segment_units(
    body: str, units: list[dict[str, Any]], namespace: str, *, complete: bool = True
) -> dict[str, Any]:
    """Keep full unit boundaries and every separator, regardless of length."""
    segments: list[dict[str, Any]] = []
    cursor = 0
    # Author-edited or independently rebuilt manuscripts have no trustworthy old
    # boundaries. Preserve the entire current manuscript as one chapter draft.
    if not units and complete:
        units = [{"start": 0, "end": len(body), "body_sha256": digest(body), "complete": True}]
    for unit in units:
        start, end = unit["start"], unit["end"]
        if (
            not cursor <= start < end <= len(body)
            or body[cursor:start].strip()
            or digest(body[start:end]) != unit["body_sha256"]
        ):
            raise WorkflowError("叙事单元与当前正文来源失配，不能生成章节草稿")
        if not unit.get("complete"):
            break
        segments.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{namespace}:{digest(body)}:{cursor}:{end}")),
                "number": len(segments) + 1,
                "start": cursor,
                "end": end,
                "body_sha256": digest(body[cursor:end]),
            }
        )
        cursor = end
    return {
        "body_sha256": digest(body),
        "segments": segments,
        "tail": {"start": cursor, "end": len(body)} if cursor < len(body) else None,
        "tolerance": None,
        "lossless": True,
        "length_policy": "unit-v1",
    }
