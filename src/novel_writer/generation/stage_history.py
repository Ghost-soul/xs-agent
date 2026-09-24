"""Current-branch factual diagnostics and explicitly separate future proposals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import func, select

from novel_writer.db.models import ChapterRevisionRecord, StateVersionRecord
from novel_writer.generation.content import fingerprint
from novel_writer.generation.schemas import LONGFORM_REVISION, GenerationSpec
from novel_writer.services.errors import WorkflowError

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


async def phase_age(
    service: GenerationService, project_id: UUID, base_id: UUID, state: dict[str, Any]
) -> dict[str, Any]:
    phases = [p for p in state.get("narrative_phases", []) if p["status"] == "active"]
    if len(phases) != 1:
        return {"status": "unknown", "reason": "没有唯一正式活动阶段"}
    phase = phases[0]
    current = await service._version(base_id)
    latest_ids = set(current.chapter_revisions)
    seen = set()
    versions: list[dict[str, Any]] = []
    baseline: set[str] = set()
    oldest = current
    known = False
    while len(versions) < 512:
        if current.id in seen or current.project_id != project_id:
            break
        seen.add(current.id)
        active = [
            p["id"] for p in current.state.get("narrative_phases", []) if p["status"] == "active"
        ]
        if active != [phase["id"]]:
            baseline = set(current.chapter_revisions)
            known = True
            break
        oldest = current
        versions.append(
            {
                "id": str(current.id),
                "number": current.number,
                "state_sha256": fingerprint(current.state),
                "chapters_sha256": fingerprint(current.chapter_revisions),
            }
        )
        if current.parent_id is None:
            known = True
            break
        parent = await service.session.get(StateVersionRecord, current.parent_id)
        if parent is None:
            break
        current = parent
    latest = await service._version(base_id)
    counted = [
        UUID(revision)
        for chapter, revision in latest.chapter_revisions.items()
        if chapter in latest_ids - baseline
    ]
    characters = (
        await service.session.scalar(
            select(func.sum(func.length(ChapterRevisionRecord.body))).where(
                ChapterRevisionRecord.id.in_(counted)
            )
        )
        if known
        else None
    )
    return {
        "status": "known" if known else "unknown",
        "phase_id": phase["id"],
        "name": phase["name"],
        "activated_version_id": str(oldest.id) if known else None,
        "formal_chapters_since_activation": len(latest_ids - baseline) if known else None,
        "formal_characters_since_activation": (characters or 0) if known else None,
        "formal_versions_since_activation": len(versions) if known else None,
        "ancestry_sha256": fingerprint(versions),
        "ancestry_complete": known,
        "goal_reference": {
            "goal": phase["goal"],
            "expected_change": phase["expected_change"],
            "source": "formal-state",
            "author_origin": "unknown",
            "binding_instruction": False,
        },
    }


async def bind_history(
    service: GenerationService,
    project_id: UUID,
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    state: dict[str, Any],
) -> None:
    sources = snapshot["sources"]
    snapshot["run"] = {"id": str(uuid4()), "stage_index": 1, "previous_stage_id": None}
    snapshot["macro_diagnostic"] = {
        "phase_age": await phase_age(service, project_id, spec.base_version_id, state),
        "source_version_id": str(spec.base_version_id),
        "formal_sources_sha256": fingerprint(sources),
        "formal_chapters": len(sources),
        "formal_state_sha256": fingerprint(state),
        "narrative_position": state["narrative_position"],
        "actual_recent_changes": [
            {k: s.get(k) for k in ("revision_id", "body_sha256", "outcome", "position", "coverage")}
            for s in snapshot["context"].get("formal_summaries", [])[-12:]
        ],
        "open_promises": sum(p["status"] == "open" for p in state["reader_promises"]),
        "active_threads": len(state["plot_threads"]),
        "interpretation": "当前正式分支的事实诊断，不决定剧情方向；摘要遗漏不代表未发生",
    }
    if not spec.previous_stage_id:
        return
    parent = await service.batch(project_id, spec.previous_stage_id)
    receipt = parent.state.get("stage_adoption", {})
    if (
        parent.revision != LONGFORM_REVISION
        or parent.status != "adopted"
        or parent.state.get("adopted_version_id") != str(spec.base_version_id)
    ):
        raise WorkflowError("接续阶段必须是当前正式分支的最后一次完整采用")
    if (
        not receipt.get("full_stage")
        or not receipt.get("proposal_eligible")
        or receipt.get("sha256") != fingerprint({k: v for k, v in receipt.items() if k != "sha256"})
    ):
        raise WorkflowError("前阶段非完整采用或接续凭证损坏")
    known = {s["revision_id"]: s["sha256"] for s in sources}
    if any(known.get(c["revision_id"]) != c["body_sha256"] for c in receipt["chapters"]):
        raise WorkflowError("前阶段正式正文来源发生变化")
    if [s["revision_id"] for s in sources[-len(receipt["chapters"]) :]] != [
        c["revision_id"] for c in receipt["chapters"]
    ]:
        raise WorkflowError("前阶段不再是当前分支的完整末尾")
    plan = await service.artifact(parent, "plan")
    if not plan or plan.sha256 != receipt["plan_sha256"]:
        raise WorkflowError("前阶段未来提案来源失配")
    snapshot["run"] = {
        "id": parent.snapshot["run"]["id"],
        "stage_index": parent.snapshot["run"]["stage_index"] + 1,
        "previous_stage_id": str(parent.id),
    }
    snapshot["previous_proposal"] = {
        "text": plan.payload.get("future_proposal", ""),
        "plan_sha256": plan.sha256,
        "adoption_sha256": receipt["sha256"],
        "is_fact": False,
        "instruction": "结合当前完整事实重新判断，不照抄提案或将其视为义务",
    }
