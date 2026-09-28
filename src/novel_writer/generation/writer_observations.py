"""Read-only observations from each saved request; never gate or rewrite fiction."""

import json
from typing import Any

from novel_writer.db.models import GenerationCallRecord
from novel_writer.generation.content import digest
from novel_writer.generation.repetition import observe as repetition_observation
from novel_writer.generation.stage_scale import characters


def observe(call: GenerationCallRecord) -> dict[str, Any] | None:
    if call.action != "write" and not call.action.startswith("write:"):
        return None
    raw = call.response or {}
    body = raw.get("text")
    if not isinstance(body, str):
        return None
    request = call.request
    source = request.get("prompt_template_source")
    if not isinstance(source, dict):
        try:
            source = json.loads(request.get("model_request", {}).get("user_prompt", ""))
        except (ValueError, TypeError):
            source = {}
    scale = request.get("writer_scale")
    if not isinstance(scale, dict):
        scale = source.get("stage_scale", {}) if isinstance(source, dict) else {}
    if not isinstance(scale, dict):
        scale = {}
    target = (
        scale.get("current_unit_reference") if scale.get("scale_mode") == "stage-range" else None
    )
    if not (
        isinstance(target, dict)
        and type(target.get("min_characters")) is int
        and type(target.get("max_characters")) is int
        and 0 < target["min_characters"] <= target["max_characters"]
    ):
        target = None
    marker = "<|eos|>" if body.rstrip().endswith("<|eos|>") else None
    prose = body.rstrip()[:-len(marker)] if marker else body
    actual = characters(prose)
    low, high = (target["min_characters"], target["max_characters"]) if target else (0, 0)
    return {
        "call_id": str(call.id),
        "body_sha256": digest(body),
        "characters": actual,
        "saved_characters": characters(body),
        "target": target,
        "status": "not_requested" if target is None else (
            "below" if actual < low else "above" if actual > high else "within"
        ),
        "percent_of_minimum": round(actual / low * 100, 1) if low else None,
        "terminal_marker": marker,
        "reasoning_effort": request.get("model_request", {}).get("reasoning_effort"),
        "reasoning_tokens": (raw.get("usage") or {}).get("reasoning_tokens"),
        "call_status": call.status,
        "repetition": repetition_observation(body),
    }


def current_units(
    body: str, units: list[dict[str, Any]], calls: list[GenerationCallRecord],
) -> list[dict[str, Any]]:
    observations = {str(c.id): observe(c) for c in calls}
    result = []
    for unit in units:
        item = observations.get(unit.get("source_call_id", ""))
        start, end = unit.get("start", -1), unit.get("end", -1)
        if (
            item is not None
            and 0 <= start < end <= len(body)
            and digest(body[start:end]) == unit.get("body_sha256") == item["body_sha256"]
        ):
            result.append({
                **item, "ordinal": unit["ordinal"], "complete": unit.get("complete", True),
            })
    return result
