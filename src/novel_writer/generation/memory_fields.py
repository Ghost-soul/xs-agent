"""Compact writable domain schemas, derived from the unchanged evidence compiler types."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any, get_args

from novel_writer.domain.state import StateDelta
from novel_writer.generation.reports import FactChange, MemoryReport

# These values are assigned by reports.compile_change / lifecycle.compile_lifecycle.
GENERATED = {
    "scenes": {"chapter_id", "chapter_ordinal"},
    "world_lore": {"source"},
    "reader_promises": {
        "history",
        "established_chapter",
        "last_updated_chapter",
        "status",
    },
    "foreshadowings": {
        "history",
        "lifecycle_events",
        "fulfilled_evidence",
        "introduced_chapter",
        "last_advanced_chapter",
        "fulfilled_chapter",
    },
}


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                {name: compact(schema) for name, schema in v.items()}
                if k in {"properties", "$defs"}
                else deepcopy(v)
                if k in {"default", "const", "enum", "examples"}
                else compact(v)
            )
            for k, v in value.items()
            if k not in {"title", "description"}
        }
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


@lru_cache(maxsize=1)
def _fields() -> dict[str, Any]:
    schema = StateDelta.model_json_schema()
    definitions = schema["$defs"]
    values = {}
    for collection in get_args(FactChange.model_fields["collection"].annotation):
        ref = schema["properties"]["add_" + collection]["items"]["$ref"]
        model = deepcopy(definitions[ref.rsplit("/", 1)[1]])
        excluded = {"id", *GENERATED.get(collection, set())}
        model["properties"] = {k: v for k, v in model["properties"].items() if k not in excluded}
        model["required"] = [k for k in model.get("required", []) if k not in excluded]
        values[collection] = compact(model)
    needed: set[str] = set()

    def references(value: Any) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref:
                name = ref.rsplit("/", 1)[1]
                if name not in needed:
                    needed.add(name)
                    references(definitions[name])
            for item in value.values():
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(values)
    return {
        "collections": values,
        "$defs": {name: compact(definitions[name]) for name in sorted(needed)},
        "rules": (
            "collections 对应 changes[].collection，字段写入 values。required 仅约束新建对象；"
            "更新沿用 object_id，只提交本次改变的字段。"
            "id、章节归属、设定来源与生命周期历史由系统填充。"
            "values 内可用 $change:序号 引用前面新对象，不能前向引用。"
            "场景只新建，不改旧章；承诺须提供 promise_action，已兑现承诺不能重建。"
            "已兑现或废弃伏笔不能重开；来源、段落证据和实体依赖仍按领域模型校验。"
        ),
    }


def fields() -> dict[str, Any]:
    return deepcopy(_fields())


def output_schema() -> dict[str, Any]:
    schema: dict[str, Any] = compact(MemoryReport.model_json_schema())
    change = compact(FactChange.model_json_schema())
    schema.setdefault("$defs", {}).update(change.pop("$defs", {}))
    schema["properties"]["changes"]["items"] = change
    return schema
