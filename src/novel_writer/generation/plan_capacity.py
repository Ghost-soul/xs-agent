"""Bounded plans, with versioned prompts and local recovery of saved designs."""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any

from novel_writer.generation import guidance, narrative_prompts
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import BackgroundPlan, GenerationSpec, StagePlan


def supported(spec: GenerationSpec) -> bool:
    return (
        spec.workflow == "novel-run-v1"
        and spec.stage_mode == "longform-v1"
        and spec.writing_policy in {"background-v1", "guided-v1"}
    )


def enabled(spec: GenerationSpec) -> bool:
    return supported(spec) and spec.plan_policy == "bounded-v1"


def plan_size(plan: dict[str, Any], spec: GenerationSpec) -> int:
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= spec.unit_limit:
        raise ValueError(f"Chief 须设计 1 至 {spec.unit_limit} 个单元，不能为空或超出授权上限")
    return len(scenes)


def parse_stage(
    raw: str, spec: GenerationSpec, snapshot: dict[str, Any]
) -> StagePlan | BackgroundPlan:
    # The new local compiler can also recover saved background/guided plans.
    # Only cardinality changes; schema, character and author-boundary checks remain.
    if supported(spec):
        size = plan_size(parse_object(raw), spec)
        spec = spec.model_copy(update={"unit_limit": size})
    return guidance.parse_stage(raw, spec, snapshot)


def contract_for(spec: GenerationSpec) -> str:
    base = narrative_prompts.contract_for(spec)
    return (
        fingerprint({"base": base, "plan_capacity": inspect.getsource(sys.modules[__name__])})
        if enabled(spec)
        else base
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else narrative_prompts.amendment_contract(spec)


def bound_schema(schema: dict[str, Any], minimum: int, maximum: int) -> None:
    scenes = schema.get("properties", {}).get("scenes")
    if scenes:
        scenes.update(minItems=minimum, maxItems=maximum)
    for definition in schema.get("$defs", {}).values():
        bound_schema(definition, minimum, maximum)


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    count = None
    if supported(spec) and action.startswith("write:") and plan:
        count = plan_size(plan, spec)
        if int(action.split(":")[1]) > count:
            raise ValueError("当前单元超出有效计划")
    system, raw = narrative_prompts.render_for(
        spec, snapshot, action, plan, body, author_note, reports
    )
    if not supported(spec):
        return system, raw
    # Shorter historical plans were previously invalid. Their local recovery is
    # recorded by the new compiler revision; already valid requests keep their bytes.
    recovered = plan is not None and len(plan.get("scenes", [])) < spec.unit_limit
    if not enabled(spec) and not recovered:
        return system, raw
    role = role_for(action)
    if role != "chief" and count is None:
        return system, raw
    payload = parse_object(raw)
    if role == "chief":
        for original in ("长篇 scenes 等于 unit_limit", "长篇 scenes 数量等于 unit_limit"):
            system = system.replace(
                original, "长篇 scenes 数量为 1 至 unit_limit，unit_limit 只是授权上限"
            )
        system += "\n按事件需要安排单元，不为凑足上限拆分或重复事件；检查点保留全部已写单元。"
        bound_schema(
            payload["output_schema"],
            max(1, (reports or {}).get("completed_units", 0)),
            spec.unit_limit,
        )
    elif count is not None:
        ordinal = int(action.split(":")[1])
        payload["unit_position"] = {
            "current": ordinal,
            "total": count,
            "last_unit": ordinal == count,
        }
        payload["unit_target_characters"] = round(
            spec.chapter_count * spec.target_characters / count
        )
    return system, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
