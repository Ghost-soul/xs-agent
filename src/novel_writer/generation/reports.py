"""Evidence-bound reports. Invalid optional components never become empty facts."""

from __future__ import annotations

from typing import Any, Literal, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from novel_writer.domain.state import StateDelta, StoryState
from novel_writer.domain.story import NarrativePosition
from novel_writer.generation.content import digest, paragraphs, parse_object
from novel_writer.generation.lifecycle import compile_lifecycle
from novel_writer.generation.schemas import EvidenceFinding, StrictModel


class FactChange(EvidenceFinding):
    collection: Literal[
        "characters",
        "places",
        "events",
        "relationships",
        "beliefs",
        "plot_threads",
        "open_questions",
        "foreshadowings",
        "reader_promises",
        "world_rules",
        "world_lore",
        "scenes",
        "timeline_constraints",
        "disclosures",
    ]
    object_id: UUID | None = None
    values: dict[str, Any]
    promise_action: Literal["established", "advanced", "delayed", "fulfilled"] | None = None


class MemoryReport(StrictModel):
    position: NarrativePosition
    position_paragraph_ids: list[str] = Field(min_length=1, max_length=24)
    outcome: str = Field(min_length=1, max_length=2000)
    changes: list[Any] = Field(default_factory=list, max_length=64)
    unresolved: list[str] = Field(default_factory=list, max_length=16)


class _LocalMemoryReport(MemoryReport):
    # Keep the published schemas above unchanged: saved prompts bind their hashes.
    # Local evidence validation bounds references by the actual candidate paragraphs.
    position_paragraph_ids: list[str] = Field(min_length=1)


class _LocalFactChange(FactChange):
    paragraph_ids: list[str] = Field(min_length=1)


class _PendingMemoryDependency(ValueError):
    """Only explicit local dependencies may wait for another submitted change."""


class CheckIssue(EvidenceFinding):
    severity: Literal["blocking", "warning", "unknown"]
    local_edit: bool = False


class CheckerReport(StrictModel):
    conclusion: Literal["clear", "issues", "unknown"]
    explanation: str = Field(min_length=1, max_length=2000)
    issues: list[CheckIssue] = Field(max_length=24)


class ReaderReport(StrictModel):
    experience: str = Field(min_length=1, max_length=3000)
    perceived_relationship: str = Field(min_length=1, max_length=1500)
    findings: list[Any] = Field(max_length=24)
    problems: list[Any] = Field(max_length=24)
    limits: str = Field(min_length=1, max_length=1200)


class EditDecision(StrictModel):
    paragraph_id: str
    disposition: Literal["patched", "retain", "unsafe_to_change"]
    replacement: str | None = Field(default=None, max_length=12000)
    reason: str = Field(min_length=1, max_length=1000)


class EditorReport(StrictModel):
    decisions: list[EditDecision] = Field(min_length=1, max_length=12)


def evidence(ids: list[str], body: str) -> list[dict[str, Any]]:
    known = {p["id"]: p for p in paragraphs(body)}
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= known.keys():
        raise ValueError("证据缺失、重复或不属于当前候选")
    return [known[i] for i in ids]


def normalize_memory_metadata(
    original: dict[str, Any], base: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Separate known redundant annotations from evidence-bearing report fields."""
    payload = dict(original)
    notes = []
    if "base_version" in payload:
        notes.append(
            {
                "field": "base_version",
                "raw": payload.pop("base_version"),
                "bound_base_version": base,
                "explanation": "模型附带的版本号只作说明；事实变更使用系统冻结的正式版本",
            }
        )
    if "evidence" in payload and payload["evidence"] in (None, []):
        notes.append(
            {
                "field": "evidence",
                "raw": payload.pop("evidence"),
                "explanation": "空的顶层证据占位不参与事实编译；段落证据仍逐项校验",
            }
        )
    return payload, notes


def normalize_memory_format(
    original: dict[str, Any], body: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve presentation variants against this exact candidate, retaining their source."""
    notes: list[dict[str, Any]] = []
    by_ordinal = {p["id"].rsplit(":", 1)[1]: p["id"] for p in paragraphs(body)}

    def annotations(value: dict[str, Any], fields: Any, path: str) -> dict[str, Any]:
        result = dict(value)
        for key, item in value.items():
            named_note = key in {"note", "notes", "comment", "comments"} or key.endswith(
                ("_note", "_notes")
            )
            plain_note = item is None or isinstance(item, str) or (
                isinstance(item, list) and all(isinstance(part, str) for part in item)
            )
            if key not in fields and named_note and plain_note:
                result.pop(key)
                notes.append({
                    "field": f"{path}{key}", "raw": item,
                    "explanation": "附加备注另存，不作为事实、当前现场或证据",
                })
        return result

    def references(value: dict[str, Any], field: str, path: str) -> None:
        raw = value.get(field)
        if not isinstance(raw, list):
            return
        normalized: list[Any] = []
        for identifier in raw:
            # A foreign full ID is never reassigned using its numeric suffix.
            # Booleans, floats, ranges and missing ordinals remain invalid.
            key = str(identifier) if type(identifier) is int else identifier
            if isinstance(key, str):
                key = key.strip()
                key = by_ordinal.get(key, key)
            normalized.append(key)
        if normalized != raw:
            value[field] = normalized
            notes.append({
                "field": f"{path}{field}", "raw": raw, "normalized": normalized,
                "body_sha256": digest(body),
                "explanation": "按本次正文的段落编号补全引用并去除外围空白；仍校验全部证据",
            })

    payload = annotations(original, MemoryReport.model_fields, "")
    references(payload, "position_paragraph_ids", "")
    if isinstance(payload.get("position"), dict):
        payload["position"] = annotations(
            payload["position"], NarrativePosition.model_fields, "position."
        )
    if isinstance(payload.get("changes"), list):
        changes = []
        for index, item in enumerate(payload["changes"]):
            if isinstance(item, dict):
                path = f"changes[{index}]."
                item = annotations(item, FactChange.model_fields, path)
                references(item, "paragraph_ids", path)
            changes.append(item)
        payload["changes"] = changes
    return payload, notes


def memory_result(
    raw: str,
    body: str,
    state: StoryState,
    base: int,
    chapter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original, format_notes = normalize_memory_metadata(parse_object(raw), base)
    original, reference_notes = normalize_memory_format(original, body)
    format_notes.extend(reference_notes)
    diagnostics: list[dict[str, Any]] = []
    for field in ("changes", "unresolved"):
        if field == "unresolved" and field not in original:
            continue
        if not isinstance(original.get(field), list):
            diagnostics.append(
                {
                    "field": field,
                    "error": "辅助集合缺失或格式错误，保留未知",
                    "raw": original.get(field),
                }
            )
            original[field] = []
    if (
        isinstance(original.get("position"), dict)
        and set(original["position"]) - NarrativePosition.model_fields.keys()
    ):
        raise ValueError("当前现场存在未知字段，不能静默丢弃")
    report = _LocalMemoryReport.model_validate(original)
    position_evidence = evidence(report.position_paragraph_ids, body)
    if (
        not report.position.current_location.strip()
        or not report.position.recent_major_event.strip()
    ):
        raise ValueError("Memory 缺少当前地点或实际完成动作，不能形成空接力")
    changes: dict[str, Any] = {}
    accepted: dict[int, dict[str, Any]] = {}
    working = state
    seen: set[tuple[str, str]] = set()
    pending = dict(enumerate(report.changes))

    def resolve(value: Any, index: int) -> Any:
        if isinstance(value, str) and value.startswith("$change:"):
            target = int(value.removeprefix("$change:"))
            if target < 0 or target >= index or not isinstance(report.changes[target], dict):
                raise ValueError("新对象引用必须指向前面声明的变化")
            if target not in accepted:
                if target in pending:
                    raise _PendingMemoryDependency(f"依赖的变化 {target} 尚未编译成功")
                raise ValueError(f"依赖的变化 {target} 未通过校验，不能作为事实引用")
            return str(
                report.changes[target].get("object_id")
                or uuid5(NAMESPACE_URL, f"{digest(body)}:{target}")
            )
        if isinstance(value, list):
            return [resolve(v, index) for v in value]
        if isinstance(value, dict):
            return {k: resolve(v, index) for k, v in value.items()}
        return value

    def compile_change(index: int, item: Any) -> None:
        nonlocal working
        change = _LocalFactChange.model_validate(item)
        spans = evidence(change.paragraph_ids, body)
        if "id" in change.values:
            raise ValueError("对象 ID 必须在 object_id 中提供")
        model = get_args(StoryState.model_fields[change.collection].annotation)[0]
        if set(change.values) - model.model_fields.keys():
            raise ValueError("资料变化含未知字段，不能静默丢弃后当作成功")
        if change.collection == "beliefs" and change.values.get("is_true") is not None:
            raise ValueError("人物认知不能自行升级为世界真相")
        existing = {str(o.id): o for o in getattr(working, change.collection)}
        identifier = str(change.object_id or uuid5(NAMESPACE_URL, f"{digest(body)}:{index}"))
        if (change.collection, identifier) in seen:
            raise ValueError("同一对象出现重复变化，需作者明确合并")
        if change.object_id is not None and any(
            earlier < index
            and isinstance(other, dict)
            and other.get("collection") == change.collection
            and other.get("object_id") == identifier
            for earlier, other in pending.items()
        ):
            raise _PendingMemoryDependency("同一对象的前项变化尚未处理，不能越过它更新")
        prior = existing.get(identifier)
        if change.object_id is not None and prior is None:
            raise ValueError("更新对象不存在；新增对象请省略 object_id")
        value = {
            **(prior.model_dump(mode="json") if prior else {}),
            **resolve(change.values, index),
            "id": identifier,
        }
        if change.collection == "world_lore":
            if change.values.get("source", "extracted") != "extracted":
                raise ValueError("Memory 提取的设定不能标记为作者原始设定")
            value["source"] = "extracted"
        value = compile_lifecycle(
            change.collection,
            value,
            change.values,
            prior.model_dump(mode="json") if prior else None,
            chapter,
            spans,
            change.observation,
            change.promise_action,
        )
        field = f"add_{change.collection}"
        delta = StateDelta.model_validate({"base_version": base, field: [value]})
        if (
            change.collection == "reader_promises"
            and chapter is not None
            and not any(str(scene.chapter_id) == chapter["id"] for scene in working.scenes)
            and any(
                isinstance(other, dict) and other.get("collection") == "scenes"
                for other in pending.values()
            )
        ):
            raise _PendingMemoryDependency("承诺的本章证据等待同报告场景建立章节归属")
        working = delta.apply(working)
        normalized = delta.model_dump(mode="json")[field][0]
        changes.setdefault(field, []).append(normalized)
        seen.add((change.collection, identifier))
        accepted[index] = {
            **change.model_dump(mode="json"),
            "object_id": identifier,
            "evidence": spans,
            "value": normalized,
        }

    # Each progressing pass removes at least one of the (at most 64) submitted
    # changes. IDs and $change references always use the original array indices.
    while pending:
        waiting = []
        progressed = False
        for index, item in list(pending.items()):
            try:
                compile_change(index, item)
            except _PendingMemoryDependency as error:
                waiting.append({"index": index, "error": str(error), "raw": item})
                continue
            except ValueError as error:
                diagnostics.append({"index": index, "error": str(error)[:1200], "raw": item})
            del pending[index]
            progressed = True
        if not progressed:
            diagnostics.extend(waiting)
            break
    diagnostics.sort(key=lambda item: item.get("index", -1))
    return {
        "body_sha256": digest(body),
        "position": report.position.model_dump(mode="json"),
        "position_evidence": position_evidence,
        "outcome": report.outcome,
        "changes": [accepted[index] for index in sorted(accepted)],
        "factual_changes": changes,
        "diagnostics": diagnostics,
        "unresolved": report.unresolved,
        "status": "partial" if diagnostics else "complete",
        "requires_author_confirmation": bool(diagnostics or report.unresolved),
        **({"format_notes": format_notes} if format_notes else {}),
    }


def checker_result(raw: str, body: str) -> dict[str, Any]:
    report = CheckerReport.model_validate(parse_object(raw))
    for item in report.issues:
        evidence(item.paragraph_ids, body)
    if report.conclusion == "clear" and report.issues:
        raise ValueError("Checker 无问题结论与问题清单冲突")
    return {
        **report.model_dump(mode="json"),
        "body_sha256": digest(body),
        "blocking": report.conclusion == "unknown"
        or any(i.severity in {"blocking", "unknown"} for i in report.issues),
    }


def reader_result(raw: str, body: str, preceding: list[dict[str, Any]]) -> dict[str, Any]:
    report = ReaderReport.model_validate(parse_object(raw))
    accepted: dict[str, list[Any]] = {"findings": [], "problems": []}
    diagnostics = []
    for field in accepted:
        for index, item in enumerate(getattr(report, field)):
            try:
                finding = EvidenceFinding.model_validate(item)
                spans = evidence(finding.paragraph_ids, body)
                accepted[field].append({**finding.model_dump(), "evidence": spans})
            except ValueError as error:
                diagnostics.append(
                    {"field": field, "index": index, "raw": item, "error": str(error)[:1200]}
                )
    return {
        **report.model_dump(mode="json"),
        **accepted,
        "diagnostics": diagnostics,
        "status": "partial" if diagnostics else "complete",
        "body_sha256": digest(body),
        "reading_scope": {
            "candidate": {"sha256": digest(body), "start": 0, "end": len(body)},
            "public_preceding": [{k: p[k] for k in ("revision_id", "sha256")} for p in preceding],
        },
        "outcome": "unknown",
        "needs_attention": True,
        "explanation": report.experience,
        "note": "独立冷读不获知作者目标，题材兑现由作者对照；未知不是失败",
    }


def apply_edit(raw: str, body: str, allowed: list[str], protected: list[str]) -> dict[str, Any]:
    report = EditorReport.model_validate(parse_object(raw))
    evidence(allowed, body)
    known = {p["id"]: p for p in paragraphs(body)}
    seen = [d.paragraph_id for d in report.decisions]
    if len(set(seen)) != len(seen) or set(seen) != set(allowed):
        raise ValueError("每个授权段落必须明确处理或保留，不能静默丢弃")
    replacements = []
    for decision in report.decisions:
        if decision.disposition == "patched":
            if decision.paragraph_id in protected or decision.replacement is None:
                raise ValueError("不能改保护区或用空缺值充当补丁")
            span = known[decision.paragraph_id]
            replacements.append((span["start"], span["end"], decision.replacement))
        elif decision.replacement is not None:
            raise ValueError("保留或不能修改的决定不应含补丁")
    updated = body
    for start, end, text in sorted(replacements, reverse=True):
        updated = updated[:start] + text + updated[end:]
    if not updated.strip() or len(updated) > 40000:
        raise ValueError("编辑后正文为空或超过资源上限")
    return {
        "body": updated,
        "decisions": report.model_dump(mode="json")["decisions"],
        "changed": updated != body,
    }


def local_summary(memory: dict[str, Any], body: str) -> dict[str, Any]:
    if memory.get("body_sha256") != digest(body):
        raise ValueError("正式摘要与正文来源不匹配")
    return {
        "body_sha256": digest(body),
        "outcome": memory["outcome"],
        "position": memory["position"],
        "changes": memory["changes"],
        "coverage": memory["status"],
        "unresolved": memory["unresolved"],
        "method": "local-compilation-of-confirmed-memory",
    }
