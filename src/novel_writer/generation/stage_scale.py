"""Reference scale, independent of billing, acceptance, and transport completeness."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from novel_writer.generation.content import digest
from novel_writer.generation.craft_models import CraftSpec, StageScale, enabled
from novel_writer.generation.schemas import GenerationSpec


def characters(body: str) -> int:
    return sum(not char.isspace() for char in body)


def acceptable_units(spec: CraftSpec) -> tuple[int, int]:
    preferred = spec.stage_scale.preferred_units
    return max(1, preferred - 1), spec.unit_limit


def ranges(scale: StageScale, scenes: list[dict[str, Any]]) -> list[dict[str, int]]:
    weights = [Decimal(str(scene.get("size_weight", 1))) for scene in scenes]
    if not weights:
        return []
    total = sum(weights)

    def allocate(amount: int) -> list[int]:
        # Cumulative boundaries preserve both totals exactly, including remainders.
        boundaries = [0]
        running = Decimal(0)
        for weight in weights:
            running += weight
            boundaries.append(int(amount * running / total))
        return [b - a for a, b in zip(boundaries, boundaries[1:], strict=False)]

    return [
        {"min_characters": low, "max_characters": high}
        for low, high in zip(
            allocate(scale.min_characters), allocate(scale.max_characters), strict=False
        )
    ]


def status(
    spec: GenerationSpec, body: str, units: list[dict[str, Any]], *, complete: bool = True
) -> dict[str, Any]:
    effective = body
    if not complete:
        incomplete = next((u for u in units if u.get("complete") is False), None)
        effective = body[: incomplete["start"]] if incomplete else ""
    actual = characters(effective)
    scale = spec.stage_scale if isinstance(spec, CraftSpec) else None
    requested = bool(enabled(spec) and scale and scale.scale_mode == "stage-range")
    low, high = (scale.min_characters, scale.max_characters) if requested and scale else (0, 0)
    return {
        "body_sha256": digest(body),
        "characters": actual,
        "unfinished_characters": characters(body) - actual,
        "counting": "non-whitespace-unicode-including-punctuation-excluding-title",
        "target": scale.model_dump(mode="json") if requested and scale else None,
        "status": "not_requested"
        if not requested
        else ("below" if actual < low else "above" if actual > high else "within"),
        "deficit": max(0, low - actual),
        "excess": max(0, actual - high) if requested else 0,
        "units": [
            {"ordinal": u["ordinal"], "characters": characters(body[u["start"] : u["end"]])}
            for u in units
            if u.get("complete") is not False
            if 0 <= u["start"] < u["end"] <= len(body)
            and digest(body[u["start"] : u["end"]]) == u["body_sha256"]
        ],
    }
