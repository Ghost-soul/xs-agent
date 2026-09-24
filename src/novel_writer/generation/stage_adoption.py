"""Evidence completion owns facts; adopting a prefix never adopts its future suffix."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    GenerationArtifactRecord,
    GenerationBatchRecord,
    StateDeltaRecord,
    StateVersionRecord,
)
from novel_writer.domain.state import StateDelta, StoryState
from novel_writer.domain.story import NarrativePosition
from novel_writer.generation.content import digest, fingerprint, json_text
from novel_writer.generation.longform import handoff_for, units_for
from novel_writer.generation.schemas import LONGFORM_REVISION, StageAdoptRequest
from novel_writer.services.errors import ConflictError, WorkflowError
from novel_writer.services.formal_version_sync import invalidate_formal_dependents

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


def earlier_position_reference(
    artifacts: list[GenerationArtifactRecord], body: str, start: int, end: int
) -> dict[str, Any] | None:
    """Reuse a verified earlier scene as author-editable advice, never a chapter-end fact."""
    by_id = {str(a.id): a for a in artifacts}
    candidates = {a.sha256: a for a in artifacts if a.kind == "candidate"}
    best: dict[str, Any] | None = None
    for artifact in artifacts:
        if artifact.kind != "units" or artifact.sha256 != fingerprint(artifact.payload):
            continue
        items = artifact.payload.get("items", [])
        for index, unit in enumerate(items):
            offset = unit.get("end", 0)
            if not start < offset <= end or (best and offset <= best["source"]["end"]):
                continue
            memory = by_id.get(unit.get("memory_id", ""))
            if (
                not memory
                or memory.kind != "handoff"
                or memory.sha256 != unit.get("memory_sha256")
                or memory.sha256 != fingerprint(memory.payload)
                or memory.payload.get("unit_body_sha256") != unit.get("body_sha256")
                or memory.payload.get("parent_chain_sha256") != fingerprint(items[:index])
            ):
                continue
            source = candidates.get(memory.payload.get("candidate_sha256", ""))
            if (
                not source
                or source.sha256 != fingerprint(source.payload)
                or source.payload.get("complete") is not True
                or len(source.payload["body"]) < offset
                or source.payload["body"][:offset] != body[:offset]
                or digest(body[unit["start"] : offset]) != unit.get("body_sha256")
            ):
                continue
            try:
                position = NarrativePosition.model_validate(memory.payload["position"])
            except (ValueError, KeyError):
                continue
            if not position.current_location.strip() or not position.recent_major_event.strip():
                continue
            best = {
                "position": position.model_dump(mode="json"),
                "source": {
                    "kind": "earlier-handoff",
                    "memory_id": str(memory.id),
                    "candidate_sha256": source.sha256,
                    "end": offset,
                    "remaining_characters": len(body[offset:end].strip()),
                },
            }
    return best


def remap_value(
    value: Any, unit_map: dict[str, dict[str, Any]], segments: list[dict[str, Any]], first: int
) -> Any:
    if isinstance(value, list):
        return [remap_value(v, unit_map, segments, first) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: remap_value(v, unit_map, segments, first) for k, v in value.items()}
    chapter = value.get("chapter_id")
    if chapter in unit_map and "start" in value and "end" in value:
        unit = unit_map[chapter]
        start, end = unit["start"] + value["start"], unit["start"] + value["end"]
        target = next((s for s in segments if s["start"] <= start and end <= s["end"]), None)
        if not target:
            raise ValueError("证据尚未落入完整章节")
        result.update(
            chapter_id=target["id"],
            chapter_ordinal=first + target["number"] - 1,
            start=start - target["start"],
            end=end - target["start"],
        )
    elif result.get("evidence") and (chapter in unit_map or "chapter_ordinal" in result):
        # A lifecycle action takes effect when its last required passage becomes available.
        spans = result["evidence"]
        target = max(spans, key=lambda p: p["chapter_ordinal"])
        result["chapter_ordinal"] = target["chapter_ordinal"]
        if "chapter_id" in result:
            result["chapter_id"] = target["chapter_id"]
    if result.get("history") and "established_chapter" in result:
        result["established_chapter"] = result["history"][0]["chapter_ordinal"]
        result["last_updated_chapter"] = result["history"][-1]["chapter_ordinal"]
    if result.get("lifecycle_events") and "introduced_chapter" in result:
        result["introduced_chapter"] = result["lifecycle_events"][0]["chapter_ordinal"]
        result["last_advanced_chapter"] = result["lifecycle_events"][-1]["chapter_ordinal"]
        if result.get("fulfilled_evidence"):
            result["fulfilled_chapter"] = max(
                p["chapter_ordinal"] for p in result["fulfilled_evidence"]
            )
    return result


async def chapter_suggestions(
    service: GenerationService, batch: GenerationBatchRecord
) -> dict[str, Any]:
    candidate = await service.artifact(batch, "candidate")
    manifest = await service.artifact(batch, "segments")
    if not candidate or not manifest or manifest.payload["candidate_sha256"] != candidate.sha256:
        raise WorkflowError("没有与当前正文绑定的完整拆章结果")
    memory = await service.artifact(batch, "memory")
    if memory and memory.payload["candidate_sha256"] != candidate.sha256:
        raise WorkflowError("当前事实报告绑定失配")
    segments = manifest.payload["segments"]
    items = await units_for(service, batch)
    unit_map = {u["chapter_id"]: u for u in items}
    first = batch.snapshot["candidate_chapter"]["ordinal"]
    changes = deepcopy(memory.payload["changes"]) if memory else []
    position_artifacts = list(
        await service.session.scalars(
            select(GenerationArtifactRecord)
            .where(
                GenerationArtifactRecord.batch_id == batch.id,
                GenerationArtifactRecord.kind.in_(("units", "candidate", "handoff")),
            )
            .order_by(GenerationArtifactRecord.created_at.desc())
        )
    )
    base = await service._version(batch.base_version_id)
    existing_ids = {
        value["id"]
        for values in base.state.values()
        if isinstance(values, list)
        for value in values
        if isinstance(value, dict) and "id" in value
    }
    # Completion order, including transitive new-object dependencies.
    owners = [
        next(
            (s["number"] - 1 for s in segments if max(p["end"] for p in c["evidence"]) <= s["end"]),
            len(segments),
        )
        for c in changes
    ]
    declarations: dict[str, int] = {}
    for index, c in enumerate(changes):
        if c["object_id"] not in existing_ids:
            declarations.setdefault(c["object_id"], index)
    for _ in changes:
        changed = False
        for i, c in enumerate(changes):
            # Resolve $change:N before scheduling dependent facts across chapter boundaries.
            data = json_text(c["value"])
            required = [
                owners[j]
                for identifier, j in declarations.items()
                if identifier != c["object_id"] and identifier in data
            ]
            owner = max([owners[i], *required])
            if owner != owners[i]:
                owners[i], changed = owner, True
        if not changed:
            break
    outputs = []
    for index, segment in enumerate(segments):
        facts: dict[str, dict[str, Any]] = {}
        observations, diagnostics = [], []
        for change, owner in zip(changes, owners, strict=True):
            if owner != index:
                continue
            try:
                value = remap_value(change["value"], unit_map, segments, first)
                if change["collection"] == "scenes":
                    value.update(chapter_id=segment["id"], chapter_ordinal=first + index)
                    value["ordinal"] = len(facts.get("add_scenes", {})) + 1
                facts.setdefault("add_" + change["collection"], {})[change["object_id"]] = value
                observations.append(
                    {"observation": change["observation"], "evidence": change["evidence"]}
                )
            except ValueError as error:
                diagnostics.append(str(error))
        position = None
        position_source = None
        for unit in items:
            if (
                unit.get("memory_id")
                and unit["end"] <= segment["end"]
                and not candidate.payload["body"][unit["end"] : segment["end"]].strip()
            ):
                handoff = await handoff_for(service, batch, unit)
                position = handoff["position"]
        position_status = "known" if position else "author-required"
        if position is None:
            reference = earlier_position_reference(
                position_artifacts, candidate.payload["body"], segment["start"], segment["end"]
            )
            if reference:
                position = reference["position"]
                position_source = reference["source"]
                position_status = "reference"
        outputs.append(
            {
                **segment,
                "ordinal": first + index,
                "factual_changes": {k: list(v.values()) for k, v in facts.items()},
                "position": position,
                "position_status": position_status,
                "position_source": position_source,
                "observations": observations,
                "diagnostics": diagnostics,
                "facts_status": memory.payload["status"] if memory else "unknown",
            }
        )
    return {
        "candidate_sha256": candidate.sha256,
        "manifest_sha256": manifest.sha256,
        "chapters": outputs,
        "tail": manifest.payload["tail"],
        "deferred_fact_count": sum(o >= len(segments) for o in owners),
    }


async def preview(
    service: GenerationService, batch: GenerationBatchRecord, request: StageAdoptRequest
) -> dict[str, Any]:
    await service.assert_current(batch, dispatch=False)
    if batch.revision != LONGFORM_REVISION or batch.status not in {
        "paused",
        "needs_attention",
        "ready",
        "failed",
    }:
        raise ConflictError("当前阶段不能采用")
    candidate = await service.artifact(batch, "candidate")
    if not candidate or candidate.payload.get("complete") is False:
        raise WorkflowError("截断候选不能直接采用；先由作者补齐并重建证据")
    suggestions = await chapter_suggestions(service, batch)
    wanted = [str(c.chapter_id) for c in request.chapters]
    if wanted != [c["id"] for c in suggestions["chapters"][: len(wanted)]]:
        raise WorkflowError("只允许从第一章开始连续采用完整章节")
    reports = {}
    for kind in ("memory", "checker", "review"):
        report = await service.artifact(batch, kind)
        if report and report.payload["candidate_sha256"] != candidate.sha256:
            raise ConflictError("审核报告与当前正文失配")
        reports[kind] = report.sha256 if report else None
    base = await service._version(batch.base_version_id)
    state = StoryState.model_validate(base.state)
    chapters = []
    provisional = [u["chapter_id"] for u in await units_for(service, batch)]
    formal_bodies: dict[str, str] = {}
    for chapter_id, revision_id in base.chapter_revisions.items():
        revision = await service._revision(UUID(revision_id))
        formal_bodies[chapter_id] = revision.body
    available_bodies = dict(formal_bodies)
    for approval, segment in zip(request.chapters, suggestions["chapters"], strict=False):
        available_bodies[segment["id"]] = candidate.payload["body"][
            segment["start"] : segment["end"]
        ]
        changes = approval.factual_changes
        if (
            set(changes) - set(StateDelta.model_fields)
            or {"base_version", "set_narrative_position"} & changes.keys()
        ):
            raise WorkflowError("事实变化字段无效")
        if any(identifier in json_text(changes) for identifier in provisional):
            raise WorkflowError("事实仍引用临时单元，必须核对实际章节归属")
        validate_chapter_references(changes, available_bodies, segment["ordinal"])
        position = NarrativePosition.model_validate(approval.narrative_position)
        missing = []
        if not position.current_location.strip():
            missing.append("章末地点")
        if not position.recent_major_event.strip():
            missing.append("本章实际事件")
        if missing:
            raise WorkflowError(
                f"第 {segment['ordinal']} 章缺少{'、'.join(missing)}。"
                "请在该章采用表单中补充，或核对已有参考后再预览。"
            )
        delta = StateDelta.model_validate(
            {
                "base_version": base.number,
                **changes,
                "set_narrative_position": position.model_dump(mode="json"),
            }
        )
        state = delta.apply(state)
        chapters.append(
            {
                "id": str(approval.chapter_id),
                "title": approval.title.strip() or f"第{segment['ordinal']}章",
                "start": segment["start"],
                "end": segment["end"],
                "ordinal": segment["ordinal"],
                "body_sha256": segment["body_sha256"],
                "factual_delta": delta.model_dump(mode="json"),
            }
        )
    units = await units_for(service, batch)
    full_stage = (
        bool(batch.state.get("units_finished"))
        and not suggestions["tail"]
        and len(chapters) == len(suggestions["chapters"])
    )
    evidence = {
        "batch_id": str(batch.id),
        "base_version_id": str(base.id),
        "candidate_sha256": candidate.sha256,
        "manifest_sha256": suggestions["manifest_sha256"],
        "reports": reports,
        "chapters": chapters,
        "full_stage": full_stage,
        "proposal_eligible": full_stage
        and bool(units)
        and all(u.get("source_call_id") for u in units),
    }
    return {
        **evidence,
        "preview_sha256": fingerprint(evidence),
        "needs_genre_acknowledgement": batch.spec.get("feedback_policy")
        not in {"advisory-v1", "logic-v1"},
    }


def validate_chapter_references(value: Any, bodies: dict[str, str], maximum_ordinal: int) -> None:
    if isinstance(value, list):
        for item in value:
            validate_chapter_references(item, bodies, maximum_ordinal)
    elif isinstance(value, dict):
        if value.get("chapter_id") is not None:
            identifier = value["chapter_id"]
            if identifier not in bodies:
                raise WorkflowError("本章事实引用后章、尾稿或非正式分支，不能提前采用")
            if {"start", "end", "quote"} <= value.keys():
                start, end = value["start"], value["end"]
                if (
                    not isinstance(start, int)
                    or not isinstance(end, int)
                    or not 0 <= start < end <= len(bodies[identifier])
                    or bodies[identifier][start:end] != value["quote"]
                ):
                    raise WorkflowError("章节事实证据与将采用的正文不一致")
        for key in (
            "chapter_ordinal",
            "established_chapter",
            "last_updated_chapter",
            "introduced_chapter",
            "last_advanced_chapter",
            "fulfilled_chapter",
        ):
            if isinstance(value.get(key), int) and value[key] > maximum_ordinal:
                raise WorkflowError("事实完成位置超出当前采用边界")
        for item in value.values():
            validate_chapter_references(item, bodies, maximum_ordinal)


async def adopt(
    service: GenerationService,
    project_id: UUID,
    batch_id: UUID,
    request: StageAdoptRequest,
    key: str,
) -> dict[str, Any]:
    command = f"generation_stage_adopt:{batch_id}"
    payload = request.model_dump(mode="json")
    cached = await service._idempotent(command, key, payload)
    if cached:
        return cached
    batch = await service.batch(project_id, batch_id, lock=True)
    checked = await preview(service, batch, request)
    if not request.confirmed or checked["preview_sha256"] != request.preview_sha256:
        raise ConflictError("请明确确认当前逐章预览；旧预览不能采用")
    if checked["needs_genre_acknowledgement"] and not request.accept_genre_deviation:
        raise WorkflowError("请阅读正文并明确接受当前题材写法")
    candidate = await service.artifact(batch, "candidate")
    plan = await service.artifact(batch, "plan")
    assert candidate and plan
    base = await service._version(batch.base_version_id)
    project = await service._project(project_id)
    state = StoryState.model_validate(base.state)
    revisions, titles = dict(base.chapter_revisions), dict(base.chapter_titles or {})
    max_ordinal = (
        await service.session.scalar(
            select(func.max(ChapterRecord.ordinal)).where(ChapterRecord.project_id == project_id)
        )
        or 0
    )
    adopted = []
    for index, chapter in enumerate(checked["chapters"], 1):
        record = ChapterRecord(
            id=UUID(chapter["id"]),
            project_id=project_id,
            ordinal=max_ordinal + index,
            display_ordinal=chapter["ordinal"],
            title=chapter["title"],
        )
        service.session.add(record)
        await service.session.flush()
        revision = ChapterRevisionRecord(
            chapter_id=record.id,
            base_version_id=base.id,
            body=candidate.payload["body"][chapter["start"] : chapter["end"]],
            status="accepted",
        )
        service.session.add(revision)
        await service.session.flush()
        record.current_revision_id = revision.id
        delta = StateDelta.model_validate(chapter["factual_delta"])
        state = delta.apply(state)
        service.session.add(
            StateDeltaRecord(revision_id=revision.id, content=delta.model_dump(mode="json"))
        )
        revisions[str(record.id)], titles[str(record.id)] = str(revision.id), record.title
        adopted.append(
            {
                "chapter_id": str(record.id),
                "revision_id": str(revision.id),
                "body_sha256": digest(revision.body),
            }
        )
    await invalidate_formal_dependents(
        service.session,
        project_id,
        "作者采用题材主导阶段的连续完整章节",
        exempt_generation_id=batch.id,
    )
    version = StateVersionRecord(
        project_id=project_id,
        number=base.number + 1,
        parent_id=base.id,
        parent_number=base.number,
        state=state.model_dump(mode="json"),
        chapter_revisions=revisions,
        chapter_titles=titles,
    )
    service.session.add(version)
    await service.session.flush()
    project.current_version_id = version.id
    for entry, chapter in zip(adopted, checked["chapters"], strict=True):
        await service.append(
            batch,
            "formal_summary",
            {
                **entry,
                "version_id": str(version.id),
                "position": chapter["factual_delta"]["set_narrative_position"],
                "outcome": chapter["factual_delta"]["set_narrative_position"]["recent_major_event"],
                "factual_changes": chapter["factual_delta"],
                "coverage": "author-confirmed",
                "method": "stage-chapter-adoption",
                "adoption_sha256": checked["preview_sha256"],
            },
        )
    receipt = {
        "chapters": adopted,
        "plan_sha256": plan.sha256,
        "full_stage": checked["full_stage"],
        "proposal_eligible": checked["proposal_eligible"],
        "preview_sha256": checked["preview_sha256"],
        "version_id": str(version.id),
    }
    batch.state = {
        **batch.state,
        "adopted_version_id": str(version.id),
        "stage_adoption": {**receipt, "sha256": fingerprint(receipt)},
    }
    batch.status, batch.next_action = "adopted", None
    result = {
        "id": str(batch.id),
        "version_id": str(version.id),
        "version_number": version.number,
        "chapters": adopted,
    }
    await service._save_idempotent(command, key, result, payload)
    return result
