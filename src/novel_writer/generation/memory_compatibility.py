"""Lossless, evidence-bound normalization of a few known report representations."""

from copy import deepcopy
from typing import Any

from novel_writer.generation.content import fingerprint

PARSER_REVISION = "memory-evidence-v5"


def normalize(
    original: dict[str, Any], reference_boundary: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = deepcopy(original)
    notes = []
    if "reference_boundary" in value:
        echoed = value["reference_boundary"]
        if reference_boundary is None or echoed != reference_boundary:
            raise ValueError("Memory 回传的来源说明与本次冻结输入不一致，不能忽略")
        value.pop("reference_boundary")
        notes.append({
            "field": "reference_boundary", "raw": echoed,
            "source_sha256": fingerprint(reference_boundary),
            "explanation": "与冻结输入完全一致的来源回显另存；不作为事实或证据",
        })
    position = value.get("position")
    if isinstance(position, dict) and "unresolved" in position:
        nested = position["unresolved"]
        top = value.get("unresolved", [])
        if not all(
            isinstance(items, list) and all(isinstance(item, str) for item in items)
            for items in (nested, top)
        ):
            raise ValueError("Memory 待定事项错层且内容不是文本列表，不能猜测转换")
        merged = list(dict.fromkeys([*top, *nested]))
        value["unresolved"] = merged
        position.pop("unresolved")
        notes.append({
            "field": "position.unresolved", "raw": nested, "normalized": merged,
            "explanation": "待定事项移入顶层并与原列表合并，不转换为现场事实",
        })
    return value, notes
