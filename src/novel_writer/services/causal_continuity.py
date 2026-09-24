from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    StateVersionRecord,
    WritingArtifactRecord,
    WritingSessionRecord,
)
from novel_writer.domain.models import FormalBodyEvidence, FrozenModel, StoryState
from novel_writer.services.workflow_artifacts import resolve_workflow_artifact


class CausalHandoff(FrozenModel):
    contract_version: Literal["causal-handoff-v1"] = "causal-handoff-v1"
    handoff_id: str
    project_id: UUID
    source_version_id: UUID
    source_version_number: int = Field(ge=1)
    source_session_id: UUID
    source_chapter_id: UUID
    source_chapter_ordinal: int = Field(ge=1)
    source_body_sha256: str = Field(min_length=64, max_length=64)
    source_memory_chunk_sha256: str = Field(min_length=64, max_length=64)
    source_brief_sha256: str | None = None
    source_outcome_id: str
    predecessor_handoff_ids: tuple[str, ...] = ()
    cause_action: str
    result: str
    cost: str = ""
    open_consequence: str = ""
    payoff_delta: Literal["none", "setup", "advanced", "delivered", "absorbing"]
    linked_foreshadowing_ids: tuple[UUID, ...] = ()
    linked_reader_promise_ids: tuple[UUID, ...] = ()
    linked_open_question_ids: tuple[UUID, ...] = ()
    evidence: tuple[FormalBodyEvidence, ...]
    artifact_sha256: str = Field(min_length=64, max_length=64)


class CausalHandoffManifest(FrozenModel):
    contract_version: Literal["causal-handoff-manifest-v1"] = (
        "causal-handoff-manifest-v1"
    )
    project_id: UUID
    source_version_id: UUID
    source_version_number: int = Field(ge=1)
    source_session_id: UUID
    chapter_ids: tuple[UUID, ...]
    source_brief_sha256: str | None = None
    handoffs: tuple[CausalHandoff, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    manifest_sha256: str = Field(min_length=64, max_length=64)


class CausalPulseObservation(FrozenModel):
    kind: Literal[
        "passive_outcome_streak",
        "open_consequence_age",
        "unanswered_question_age",
        "payoff_debt_age",
        "missing_payoff_absorption",
    ]
    state: Literal["observed", "clear", "unknown", "insufficient_evidence"]
    source_ids: tuple[str, ...] = ()
    chapter_age: int | None = Field(default=None, ge=0)
    threshold: int | None = Field(default=None, ge=0)
    genre_rule_ids: tuple[str, ...] = ()
    advisory_only: Literal[True] = True
    note: str = ""


class CausalPulseReport(FrozenModel):
    contract_version: Literal["causal-pulse-v1"] = "causal-pulse-v1"
    observations: tuple[CausalPulseObservation, ...]
    advisory_only: Literal[True] = True
    report_sha256: str = Field(min_length=64, max_length=64)








async def load_current_causal_manifest(
    session: AsyncSession,
    *,
    project_id: UUID,
    version: StateVersionRecord,
) -> tuple[CausalHandoffManifest | None, str | None]:
    rows = list(
        (
            await session.scalars(
                select(WritingArtifactRecord)
                .join(
                    WritingSessionRecord,
                    WritingSessionRecord.id == WritingArtifactRecord.session_id,
                )
                .where(
                    WritingSessionRecord.project_id == project_id,
                    WritingArtifactRecord.stage == "merge",
                    WritingArtifactRecord.kind == "causal_handoff_manifest_v1",
                )
                .order_by(WritingArtifactRecord.created_at.desc())
            )
        ).all()
    )
    manifest: CausalHandoffManifest | None = None
    manifest_row: WritingArtifactRecord | None = None
    for row in rows:
        raw_version_id = row.content.get("source_version_id")
        try:
            candidate = CausalHandoffManifest.model_validate(row.content)
        except ValueError as error:
            if str(raw_version_id or "") == str(version.id):
                raise ValueError(
                    "current causal handoff manifest is structurally invalid"
                ) from error
            continue
        if candidate.source_version_id == version.id:
            manifest = candidate
            manifest_row = row
            break
    if manifest is None:
        return None, "legacy_handoff_unavailable"
    if manifest_row is None or manifest.source_session_id != manifest_row.session_id:
        raise ValueError("causal handoff manifest session binding is stale")
    chapter_ids = list(manifest.chapter_ids)
    revision_by_chapter = {
        UUID(chapter_id): UUID(revision_id)
        for chapter_id, revision_id in version.chapter_revisions.items()
        if UUID(chapter_id) in set(chapter_ids)
    }
    rows_by_id = {
        chapter.id: (chapter, revision)
        for chapter, revision in (
            await session.execute(
                select(ChapterRecord, ChapterRevisionRecord)
                .join(
                    ChapterRevisionRecord,
                    ChapterRevisionRecord.chapter_id == ChapterRecord.id,
                )
                .where(ChapterRevisionRecord.id.in_(list(revision_by_chapter.values())))
            )
        ).all()
        if revision_by_chapter.get(chapter.id) == revision.id
    }
    if set(rows_by_id) != set(chapter_ids):
        raise ValueError("causal handoff manifest formal chapter set is stale")
    segments = [
        {
            "chapter_id": str(chapter_id),
            "ordinal": (
                rows_by_id[chapter_id][0].display_ordinal
                or rows_by_id[chapter_id][0].ordinal
            ),
            "body": rows_by_id[chapter_id][1].body,
        }
        for chapter_id in chapter_ids
    ]
    source_artifacts = list(
        (
            await session.scalars(
                select(WritingArtifactRecord).where(
                    WritingArtifactRecord.session_id == manifest.source_session_id
                )
            )
        ).all()
    )
    memory_chunk_sha256s = frozenset(
        str(item.content.get("chunk_sha256"))
        for item in source_artifacts
        if item.stage == "memory"
        and item.kind.startswith("handoff_")
        and len(str(item.content.get("chunk_sha256") or "")) == 64
    )
    brief_artifact = next(
        (
            item
            for item in source_artifacts
            if item.stage == "brief" and item.kind == "creative_brief"
        ),
        None,
    )
    brief = await _manifest_bound_brief(
        session, brief_artifact, manifest.source_brief_sha256
    )
    validate_causal_handoff_manifest(
        manifest,
        version_id=version.id,
        version_number=version.number,
        segments=segments,
        memory_chunk_sha256s=memory_chunk_sha256s,
        brief=brief,
    )
    return manifest, None


async def _manifest_bound_brief(
    session: AsyncSession, artifact: WritingArtifactRecord | None, expected_sha256: str | None
) -> dict[str, Any] | None:
    brief = dict(artifact.content.get("brief", {})) if artifact is not None else None
    if (_sha(brief) if brief else None) == expected_sha256:
        return brief
    effective = await resolve_workflow_artifact(session, artifact)
    brief = dict(effective.content.get("brief", {})) if effective is not None else None
    if (_sha(brief) if brief else None) != expected_sha256:
        raise ValueError("causal handoff manifest brief binding is stale")
    return brief


def validate_causal_handoff_manifest(
    manifest: CausalHandoffManifest,
    *,
    version_id: UUID,
    version_number: int,
    segments: list[dict[str, Any]],
    memory_chunk_sha256s: frozenset[str] | None = None,
    brief: dict[str, Any] | None = None,
) -> None:
    if manifest.source_version_id != version_id or manifest.source_version_number != version_number:
        raise ValueError("causal handoff manifest version binding is stale")
    if manifest.chapter_ids != tuple(UUID(str(item["chapter_id"])) for item in segments):
        raise ValueError("causal handoff manifest chapter binding is stale")
    raw = manifest.model_dump(mode="json")
    actual_sha = raw.pop("manifest_sha256")
    if _sha(raw) != actual_sha:
        raise ValueError("causal handoff manifest SHA is invalid")
    bodies = {UUID(str(item["chapter_id"])): str(item["body"]) for item in segments}
    ordinals = {
        UUID(str(item["chapter_id"])): int(item["ordinal"]) for item in segments
    }
    expected_brief_sha = _sha(brief) if brief else None
    if brief is not None and manifest.source_brief_sha256 != expected_brief_sha:
        raise ValueError("causal handoff manifest brief binding is stale")
    seen_handoff_ids: set[str] = set()
    seen_outcome_ids: set[str] = set()
    for item in manifest.handoffs:
        body = bodies.get(item.source_chapter_id)
        if (
            item.project_id != manifest.project_id
            or item.source_version_id != manifest.source_version_id
            or item.source_version_number != manifest.source_version_number
            or item.source_session_id != manifest.source_session_id
            or item.source_brief_sha256 != manifest.source_brief_sha256
            or item.source_chapter_ordinal != ordinals.get(item.source_chapter_id)
            or item.handoff_id in seen_handoff_ids
            or item.source_outcome_id in seen_outcome_ids
        ):
            raise ValueError("causal handoff identity binding is stale")
        if (
            body is None
            or hashlib.sha256(body.encode("utf-8")).hexdigest()
            != item.source_body_sha256
        ):
            raise ValueError("causal handoff body binding is stale")
        if (
            memory_chunk_sha256s is not None
            and item.source_memory_chunk_sha256 not in memory_chunk_sha256s
        ):
            raise ValueError("causal handoff Memory chunk binding is stale")
        for evidence in item.evidence:
            if (
                evidence.chapter_id != item.source_chapter_id
                or evidence.chapter_ordinal != item.source_chapter_ordinal
                or body[evidence.start : evidence.end] != evidence.quote
            ):
                raise ValueError("causal handoff evidence binding is stale")
        item_values = item.model_dump(mode="json")
        item_sha = item_values.pop("artifact_sha256")
        if _sha(item_values) != item_sha:
            raise ValueError("causal handoff artifact SHA is invalid")
        seen_handoff_ids.add(item.handoff_id)
        seen_outcome_ids.add(item.source_outcome_id)


def build_causal_pulse_report(
    state: StoryState,
    handoffs: tuple[CausalHandoff, ...],
    *,
    current_chapter: int,
    thresholds: dict[str, int] | None = None,
) -> CausalPulseReport:
    limits = {
        "open_consequence_age": 5,
        "unanswered_question_age": 8,
        "payoff_debt_age": 5,
        "payoff_absorption_window": 2,
        **(thresholds or {}),
    }
    observations: list[CausalPulseObservation] = []
    observations.append(
        CausalPulseObservation(
            kind="passive_outcome_streak",
            state="insufficient_evidence",
            source_ids=tuple(str(item.id) for item in state.scenes[-3:]),
            note=(
                "人物主动性需要 SceneOutcome 与 CharacterAgencyObservation 同时明确，"
                "当前正式状态不保存后者。"
            ),
        )
    )
    referenced = {value for item in handoffs for value in item.predecessor_handoff_ids}
    open_handoffs = [
        item for item in handoffs if item.open_consequence and item.handoff_id not in referenced
    ]
    oldest_open = min(open_handoffs, key=lambda item: item.source_chapter_ordinal, default=None)
    observations.append(
        _age_observation(
            "open_consequence_age",
            oldest_open.handoff_id if oldest_open else None,
            current_chapter - oldest_open.source_chapter_ordinal if oldest_open else None,
            limits["open_consequence_age"],
            "只表示尚未被稳定交接引用，不判断是否必须立即解决。",
        )
    )
    open_questions = [item for item in state.open_questions if item.status in {"open", "partial"}]
    oldest_question = min(
        open_questions,
        key=lambda item: (
            item.raised_chapter if item.raised_chapter is not None else current_chapter
        ),
        default=None,
    )
    question_age = (
        current_chapter - oldest_question.raised_chapter
        if oldest_question is not None and oldest_question.raised_chapter is not None
        else None
    )
    observations.append(
        _age_observation(
            "unanswered_question_age",
            str(oldest_question.id) if oldest_question else None,
            question_age,
            limits["unanswered_question_age"],
            "仅按正式 OpenQuestion 稳定 ID 和状态计龄。",
        )
    )
    debts = [item for item in state.foreshadowings if item.status == "ready_for_payoff"]
    debts.extend([])
    oldest_debt = min(debts, key=lambda item: item.last_advanced_chapter, default=None)
    observations.append(
        _age_observation(
            "payoff_debt_age",
            str(oldest_debt.id) if oldest_debt else None,
            current_chapter - oldest_debt.last_advanced_chapter if oldest_debt else None,
            limits["payoff_debt_age"],
            "回报债务提醒是非绑定建议，不要求本章兑现。",
        )
    )
    delivered = [item for item in handoffs if item.payoff_delta == "delivered"]
    absorbing_sources = {
        predecessor
        for item in handoffs
        if item.payoff_delta == "absorbing"
        for predecessor in item.predecessor_handoff_ids
    }
    latest_unabsorbed = next(
        (item for item in reversed(delivered) if item.handoff_id not in absorbing_sources), None
    )
    observations.append(
        _age_observation(
            "missing_payoff_absorption",
            latest_unabsorbed.handoff_id if latest_unabsorbed else None,
            current_chapter - latest_unabsorbed.source_chapter_ordinal
            if latest_unabsorbed
            else None,
            limits["payoff_absorption_window"],
            "安静、私人或无观众的现实变化同样是合法余波。",
        )
    )
    values = {
        "contract_version": "causal-pulse-v1",
        "observations": [item.model_dump(mode="json") for item in observations],
        "advisory_only": True,
    }
    return CausalPulseReport.model_validate({**values, "report_sha256": _sha(values)})


def _age_observation(
    kind: str,
    source_id: str | None,
    age: int | None,
    threshold: int,
    note: str,
) -> CausalPulseObservation:
    return CausalPulseObservation.model_validate(
        {
            "kind": kind,
            "state": "unknown" if age is None else "observed" if age >= threshold else "clear",
            "source_ids": [source_id] if source_id else [],
            "chapter_age": age,
            "threshold": threshold,
            "note": note,
        }
    )










def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
