"""Bound world material per request without changing saved facts or legacy renderers."""

from __future__ import annotations

import inspect
import re
import sys
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from novel_writer.generation import focused_context, unit_scope
from novel_writer.generation.budget import TOKENIZER_ROOT, _loaded_tokenizer, counting_config
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.context import terms
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

POLICY = "world-bounded-v1"
ROLE_LIMITS = {"chief": 16000, "writer": 12000, "memory": 10000, "checker": 16000, "editor": 8000}
NOTE = (
    "世界资料按当前任务和容量选取，未选条目保留在本地；省略不表示不存在或允许推翻正式设定。"
    "完整资料中的规则与适用条件保持原文，概览只供定位，不足以支持新设定或推断人物已知秘密。"
    "历史世界变更仅展示本次相关条目，保留原时间、动作和当时值，不能视为完整世界变更清单。"
)
WORLD_KEYS = {"world_rules", "world_lore", "world_lore_overview", "world_context"}
LORE_CHANGES = {"add_world_lore", "update_world_lore", "remove_world_lore"}
CORE = {"world_structure", "power_system", "core_secret"}


def enabled(spec: GenerationSpec) -> bool:
    return spec.workflow == "novel-run-v1" and spec.context_policy == POLICY


def previous_spec(spec: GenerationSpec) -> GenerationSpec:
    return spec.model_copy(update={"context_policy": "focused-v1"}) if enabled(spec) else spec


def contract_for(spec: GenerationSpec) -> str:
    base = unit_scope.contract_for(previous_spec(spec))
    return (
        fingerprint({"base": base, "world_context": inspect.getsource(sys.modules[__name__])})
        if enabled(spec)
        else base
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else unit_scope.amendment_contract(spec)


def material_target(spec: GenerationSpec) -> int:
    return focused_context.material_target(previous_spec(spec))


def fit_context(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    profile: ProviderProfile,
    latest_chapter_id: str | None,
) -> None:
    legacy_fit: Callable[[GenerationSpec, dict[str, Any], ProviderProfile, str | None], None] = (
        unit_scope.fit_context
    )
    legacy_fit(previous_spec(spec), snapshot, profile, latest_chapter_id)


def _counter(config: dict[str, Any]) -> Callable[[Any], int]:
    if config["method"] == "utf8-byte-upper-bound":
        return lambda value: len(json_text(value).encode("utf-8"))
    current = counting_config(config["id"], config["model"])
    if current != config:
        raise WorkflowError("分词配置已改变，需要创建新的预览")
    tokenizer = _loaded_tokenizer(
        str((TOKENIZER_ROOT / f"{config['id']}.json").resolve()), current["sha256"]
    )
    return lambda value: len(tokenizer.encode(json_text(value), add_special_tokens=False).ids)


def _mentions(entry: dict[str, Any], query: str) -> bool:
    for value in (entry.get("id"), entry.get("name")):
        if not isinstance(value, str) or len(value) < 2:
            continue
        if re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            if re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", query, re.I):
                return True
        elif value.casefold() in query.casefold():
            return True
    return False


def _expand_world_links(value: Any, source: dict[str, Any], reference_key: str) -> Any:
    if isinstance(value, dict):
        path = value.get("same_as")
        if isinstance(path, str) and re.match(
            re.escape(reference_key) + r"\.(world_lore|world_rules|world_lore_overview)\[", path
        ):
            resolved: Any = source
            for component in re.findall(r"[^.\[\]]+", path):
                resolved = (
                    resolved[int(component)] if isinstance(resolved, list) else resolved[component]
                )
            return deepcopy(resolved)
        return {k: _expand_world_links(v, source, reference_key) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_world_links(item, source, reference_key) for item in value]
    return value


def world_material(payload: dict[str, Any]) -> dict[str, Any]:
    """Count every transmitted world field, including historical values and handoff changes."""
    result: dict[str, Any] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("collection"), str) and value["collection"] in {
                "world_lore", "world_rules"
            }:
                result[path] = value
                return
            for key, item in value.items():
                if key in {"output_schema", "context_selection", "world_context_selection"}:
                    continue
                location = f"{path}.{key}"
                if key in WORLD_KEYS | LORE_CHANGES or key.endswith("_world_rules"):
                    result[location] = item
                else:
                    visit(item, location)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")

    visit(payload, "input")
    return result


def _project(
    base: dict[str, Any],
    reference_key: str,
    full: list[dict[str, Any]],
    overview: list[dict[str, Any]],
    protected_history: set[str],
) -> dict[str, Any]:
    selected = {entry["id"] for entry in full} | protected_history

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "output_schema":
                    result[key] = item
                elif key in LORE_CHANGES - {"remove_world_lore"} and isinstance(item, list):
                    # Never replace a historical update with the current entity's value.
                    result[key] = [
                        entry
                        for entry in item
                        if (entry.get("id") if isinstance(entry, dict) else entry) in selected
                    ]
                else:
                    result[key] = visit(item)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    payload: dict[str, Any] = visit(base)
    reference = payload[reference_key]
    reference["world_lore"] = full
    reference["world_lore_overview"] = overview
    audit = payload.get("context_selection", {})
    audit["world_lore_full"] = [entry["id"] for entry in full]
    audit["world_lore_overview"] = [entry["id"] for entry in overview]
    focused_context.share_duplicates(payload, reference_key)
    return payload


def _dependencies(entries: list[dict[str, Any]], selected: set[str]) -> set[str]:
    selected = set(selected)
    while True:
        references = json_text([entry for entry in entries if entry["id"] in selected])
        more = {entry["id"] for entry in entries if _mentions(entry, references)}
        if more <= selected:
            return selected
        selected.update(more)


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system, raw = unit_scope.render_for(
        previous_spec(spec), snapshot, action, plan, body, author_note, reports
    )
    if not enabled(spec) or role_for(action) == "reader" or action == "title":
        return system, raw
    payload = parse_object(raw)
    reference_key = next(
        (
            key
            for key in (
                "formal_reference",
                "formal_start",
                "candidate_working_reference",
                "formal_facts",
            )
            if isinstance(payload.get(key), dict)
        ),
        None,
    )
    if reference_key is None:
        return system, raw  # Local Editor receives only its already bounded authorized passages.
    role = role_for(action)
    reference = payload[reference_key]
    source = {**snapshot["context"], **(reports or {}).get("working_context", {})}
    lore = deepcopy(source.get("world_lore", []))
    base = _expand_world_links(payload, payload, reference_key)
    base[reference_key]["world_lore"] = []
    base[reference_key].pop("world_lore_overview", None)
    task = payload.get("plot_execution", {}).get("current_task", payload.get("current_task"))
    active = set(task.get("character_ids", [])) if isinstance(task, dict) else set()
    # Full current-unit evidence for Memory/Checker; Writer recalls the recent prose and handoff.
    evidence = (
        body if role in {"memory", "checker"} or action == "rewrite" else (body or "")[-12000:]
    )
    query = json_text(
        {
            "direction": spec.direction,
            "boundaries": spec.author_boundaries,
            "task": task,
            "note": author_note,
            "body": evidence,
            "position": reference.get("narrative_position"),
            "handoff": payload.get("candidate_handoff"),
            "rules": reference.get("world_rules", []),
            "characters": [
                {k: c.get(k) for k in ("id", "name", "location_id", "current_state")}
                for c in reference.get("characters", [])
                if not active or c.get("id") in active
            ],
        }
    )
    history = [
        entry
        for path, values in world_material(base).items()
        if path.endswith((".add_world_lore", ".update_world_lore")) and isinstance(values, list)
        for entry in values
        if isinstance(entry, dict) and entry.get("id")
    ]
    graph = [*lore, *history]
    required = {entry["id"] for entry in graph if _mentions(entry, query)}
    # References in selected facts form a closure; unrelated high-risk entries are not global rules.
    required = _dependencies(graph, required)
    protected_history = {entry["id"] for entry in history} & required
    full = [entry for entry in lore if entry["id"] in required]
    overview: list[dict[str, Any]] = []
    counting = snapshot.get("counting", {})
    config = counting.get(action) or counting.get("writer" if role == "writer" else "chief")
    config = config or {"method": "utf8-byte-upper-bound"}
    count = _counter(config)
    limit = min(ROLE_LIMITS[role], spec.input_limit // 4)
    result = _project(base, reference_key, full, overview, protected_history)
    required_count = count(world_material(result))
    if required_count > limit:
        raise WorkflowError(
            "当前任务必需的世界规则、设定依赖和事实接力超过世界资料预算"
            f"（{required_count} / {limit}，"
            f"计数方式 {config['method']}）。请缩小当前事件范围或整理重复的正式规则；"
            "未截断设定、未发送模型请求。"
        )
    query_terms = terms(query)
    tokens = {
        entry["id"]: terms(entry.get("name", "") + entry.get("summary", "")) for entry in lore
    }
    frequencies = Counter(token for values in tokens.values() for token in values)
    ranked = sorted(
        (entry for entry in lore if entry["id"] not in required),
        key=lambda entry: (
            -sum(1 / frequencies[t] for t in tokens[entry["id"]] & query_terms),
            -int(entry.get("category") in CORE),
            -int(entry.get("risk_level") == "high"),
            str(entry["id"]),
        ),
    )
    # Fixed candidate and overview counts prevent a full-library index from growing in prompts.
    for entry in ranked[:16]:
        if entry["id"] in {item["id"] for item in full}:
            continue
        detailed = (
            bool(tokens[entry["id"]] & query_terms)
            and len({item["id"] for item in full} - required) < 8
        )
        if not detailed and len(overview) >= 8:
            continue
        closure = (
            _dependencies(graph, {item["id"] for item in full} | {entry["id"]})
            if detailed else set()
        )
        candidate_full = [item for item in lore if item["id"] in closure] if detailed else full
        compact = {k: entry[k] for k in ("id", "category", "name", "summary") if k in entry}
        candidate_overview = (
            [item for item in overview if item["id"] not in closure]
            if detailed
            else [*overview, compact]
        )
        candidate_history = protected_history | ({item["id"] for item in history} & closure)
        candidate = _project(
            base,
            reference_key,
            candidate_full,
            candidate_overview,
            candidate_history if detailed else protected_history,
        )
        if len(candidate_overview) <= 8 and count(world_material(candidate)) <= limit:
            full, overview, result = candidate_full, candidate_overview, candidate
            if detailed:
                protected_history = candidate_history
        elif detailed and len(overview) < 8:
            candidate = _project(base, reference_key, full, [*overview, compact], protected_history)
            if count(world_material(candidate)) <= limit:
                overview, result = [*overview, compact], candidate
    result["world_context_selection"] = {
        "policy": POLICY,
        "role": role,
        "limit": limit,
        "counting_method": config["method"],
        "material_count": count(world_material(result)),
        "required_count": required_count,
        "source_count": len(lore),
        "full_count": len(full),
        "overview_count": len(overview),
        "omitted_count": len(lore) - len(full) - len(overview),
        "source_sha256": fingerprint(
            {"world_lore": lore, "world_rules": reference.get("world_rules", [])}
        ),
        "omission_is_absence": False,
    }
    return system + "\n" + NOTE, json_text(result)
