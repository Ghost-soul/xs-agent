"""Bind retrieval before dispatch; paid retries continue to replay the saved ModelRequest."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation import chief_context, key_queries, knowledge_context, role_queries
from novel_writer.generation.content import fingerprint
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.knowledge import embedding
from novel_writer.knowledge.service import KnowledgeService
from novel_writer.knowledge.text import corpus_hash, queries
from novel_writer.services.errors import ConflictError

if TYPE_CHECKING:
    from novel_writer.generation.service import GenerationService


async def bind_snapshot(
    service: GenerationService, project_id: UUID, spec: GenerationSpec, snapshot: dict[str, Any]
) -> None:
    if not (knowledge_context.enabled(spec) or chief_context.uses_roles(spec)):
        return
    knowledge = KnowledgeService(service.session)
    snapshot["knowledge_project_id"] = str(project_id)
    snapshot["knowledge_sources"] = await knowledge.sources(project_id, spec.base_version_id)
    snapshot["knowledge_source_summary"] = {
        "count": len(snapshot["knowledge_sources"]),
        "sha256": corpus_hash(snapshot["knowledge_sources"]),
        "full_sources_saved_locally": True,
    }
    try:
        snapshot["knowledge_model_key"] = embedding.identity(knowledge.model_root)
    except (OSError, ValueError):
        snapshot["knowledge_model_key"] = None
    query = (
        key_queries.query_plan(spec, snapshot, "plan", None, None, {})
        if chief_context.uses_keys(spec)
        else None
    )
    receipt = await knowledge.retrieve(
        project_id,
        spec.base_version_id,
        snapshot["knowledge_sources"],
        query["queries"]
        if query
        else role_queries.queries(spec, snapshot, "plan", None, None, {})
        if chief_context.uses_roles(spec)
        else queries(spec.direction, "plan", None, None),
        model_key=snapshot["knowledge_model_key"],
    )
    snapshot["knowledge_plan_retrieval"] = (
        key_queries.enrich(snapshot, receipt, query) if query else receipt
    )


async def bind_request(
    service: GenerationService,
    batch: GenerationBatchRecord,
    spec: GenerationSpec,
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None = None,
) -> None:
    from novel_writer.generation.template_binding import bind_reports

    await bind_reports(service, batch, reports)
    if not (knowledge_context.enabled(spec) or chief_context.uses_roles(spec)):
        return
    if chief_context.uses_roles(spec) and (
        role_for(action) in {"reader", "editor"} or action == "title"
    ):
        return
    snapshot = batch.snapshot
    if chief_context.uses_roles(spec):
        await bind_role_state(service, batch, reports, action)
    recovery = await service.artifact(batch, "memory_output_authorization")
    await bind_preparation_receipt(service, batch, action, reports)
    saved = reports.get("knowledge_retrieval")
    if recovery and recovery.payload.get("action") == action:
        call = await service.session.get(
            GenerationCallRecord,
            UUID(recovery.payload["failed_call_id"]),
        )
        if call and call.batch_id == batch.id:
            saved = call.request.get("knowledge_retrieval")
    if saved is not None:
        reports["knowledge_retrieval"] = saved
    elif action == "plan" or "knowledge_sources" not in snapshot:
        reports["knowledge_retrieval"] = chief_context.retrieval_for(
            spec,
            snapshot,
            action,
            plan,
            body,
            reports,
            author_note,
        )
    else:
        query = (
            key_queries.query_plan(spec, snapshot, action, plan, body, reports, author_note)
            if chief_context.uses_keys(spec)
            else None
        )
        receipt = await KnowledgeService(service.session).retrieve(
            batch.project_id,
            spec.base_version_id,
            snapshot["knowledge_sources"],
            query["queries"]
            if query
            else role_queries.queries(spec, snapshot, action, plan, body, reports, author_note)
            if chief_context.uses_roles(spec)
            else queries(spec.direction, action, plan, body),
            model_key=snapshot["knowledge_model_key"],
        )
        reports["knowledge_retrieval"] = (
            key_queries.enrich(snapshot, receipt, query) if query else receipt
        )
    await bind_working_world(service, batch, reports)


async def bind_preparation_receipt(
    service: GenerationService, batch: GenerationBatchRecord, action: str, reports: dict[str, Any]
) -> None:
    item = await service.artifact(batch, "input_preparation_failure")
    if not item or item.payload.get("action") != action:
        return
    if item.sha256 != fingerprint(item.payload):
        raise ConflictError("本地输入失败记录已改变，不能恢复")
    data = item.payload
    if not data.get("not_dispatched") or data.get("batch_preview_sha256") != batch.preview_sha256:
        raise ConflictError("本地输入失败记录不属于当前未发送动作")
    for kind, key in (
        ("plan", "plan_sha256"),
        ("candidate", "candidate_sha256"),
        ("plan_author_note", "author_note_sha256"),
    ):
        source = await service.artifact(batch, kind)
        if data.get(key) != (source.sha256 if source else None):
            return  # Author edits get a new preview; never apply the old lookup to new content.
    if data.get("unit_chain_sha256") != reports.get("unit_chain_sha256"):
        return
    if data.get("knowledge_retrieval") is not None:
        reports["knowledge_retrieval"] = data["knowledge_retrieval"]


async def bind_role_state(
    service: GenerationService,
    batch: GenerationBatchRecord,
    reports: dict[str, Any],
    action: str,
) -> None:
    if not action.startswith(("write:", "memory:")):
        return  # Whole-draft review/rebuild/rewrite starts at the formal base.
    from novel_writer.generation.longform import handoff_for, units_for

    units = [u for u in await units_for(service, batch) if u.get("memory_id")]
    prepared = []
    for unit in units:
        handoff = await handoff_for(service, batch, unit)
        prepared.append(
            {
                "unit": unit["ordinal"],
                "start": unit["start"],
                "end": unit["end"],
                "outcome": handoff["outcome"],
                "position": handoff["position"],
                "unresolved": handoff["unresolved"],
            }
        )
        reports["role_working_state"] = handoff["working_state"]
    reports["role_units"] = prepared


async def bind_working_world(
    service: GenerationService,
    batch: GenerationBatchRecord,
    reports: dict[str, Any],
) -> None:
    # Carry complete candidate world changes only within the already validated handoff chain.
    if "working_context" in reports:
        from novel_writer.generation.longform import handoff_for, units_for

        complete = [u for u in await units_for(service, batch) if u.get("memory_id")]
        if complete:
            state = (await handoff_for(service, batch, complete[-1]))["working_state"]
            for field in ("world_lore", "world_rules"):
                reports["working_context"][field] = state.get(field, [])
