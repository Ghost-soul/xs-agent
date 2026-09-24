from __future__ import annotations

import asyncio
from random import SystemRandom
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novel_writer.db.models import (
    ChapterRecord,
    ChapterRevisionRecord,
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
    GlobalReferenceStyleDefaultRecord,
    ProjectNaturalnessPolicyRecord,
    ReferenceStyleProfileRecord,
    StateDeltaRecord,
    StateVersionRecord,
)
from novel_writer.domain.models import NarrativePosition, StoryState
from novel_writer.domain.state import StateDelta
from novel_writer.generation.budget import (
    counting_config,
    estimate_cost,
    request_for,
)
from novel_writer.generation.card_selection import selected_ids
from novel_writer.generation.casting import (
    automatic,
)
from novel_writer.generation.content import (
    checked_body,
    digest,
    fingerprint,
    json_text,
    parse_plan,
)
from novel_writer.generation.context import enrich_context
from novel_writer.generation.diagnostics import (
    output_diagnostic,
    plan_distribution_diagnostic,
    plan_retry_preview,
)
from novel_writer.generation.guidance import selected_plan
from novel_writer.generation.intent import prepare_cast, recall_text
from novel_writer.generation.logic import advisory
from novel_writer.generation.novel import invalidate_candidate, model_for, revision_for, slots_for
from novel_writer.generation.preview_preparation import prepare_preview
from novel_writer.generation.questions import reconcile_questions
from novel_writer.generation.reports import local_summary
from novel_writer.generation.schemas import (
    LONGFORM_REVISION,
    NOVEL_REVISION,
    AdoptRequest,
    FrozenGenerationSpec,
    GenerationSpec,
    PlanEdit,
)
from novel_writer.generation.tokenizer_assets import resolve_tokenizers
from novel_writer.generation.world_context import contract_for
from novel_writer.services.errors import ConflictError, NotFoundError, WorkflowError
from novel_writer.services.formal_version_sync import invalidate_formal_dependents
from novel_writer.services.provider_profiles import ProviderProfileStore
from novel_writer.services.style_profiles import (
    QUALITY_CARD_DIR,
    StyleProfileService,
    validate_genre_card_id,
)
from novel_writer.services.workflow_persistence import WorkflowPersistenceMixin


class GenerationService(WorkflowPersistenceMixin):
    def __init__(self, session: AsyncSession, profiles: ProviderProfileStore) -> None:
        self.session = session
        self.profiles = profiles

    async def batch(
        self, project_id: UUID, batch_id: UUID, *, lock: bool = False
    ) -> GenerationBatchRecord:
        if lock:
            await self._locked_project(project_id)
        statement = select(GenerationBatchRecord).where(
            GenerationBatchRecord.id == batch_id,
            GenerationBatchRecord.project_id == project_id,
        )
        if lock:
            statement = statement.with_for_update()
        batch = await self.session.scalar(statement)
        if batch is None:
            raise NotFoundError("创作批次不存在")
        return batch

    async def create(
        self, project_id: UUID, spec: GenerationSpec, key: str, *, random_narratives: bool = False
    ) -> dict[str, Any]:
        request_payload = spec.model_dump(mode="json")
        if random_narratives:
            request_payload = {"spec": request_payload, "narrative_selection": "random-two-v1"}
        command = f"generation_create:{project_id}"
        cached = await self._idempotent(command, key, request_payload)
        if cached is not None:
            return cached
        spec = resolve_tokenizers(spec)
        project = await self._locked_project(project_id)
        if project.archived_at is not None or project.current_version_id != spec.base_version_id:
            raise ConflictError("作品已归档或正式起点发生变化，请重新预览")
        version = await self._version(project.current_version_id)
        state = StoryState.model_validate(version.state).model_dump(mode="json")
        characters = {c["id"]: c for c in state["characters"]}
        if not set(spec.character_ids) <= characters.keys():
            raise WorkflowError("人物范围含不存在的正式人物，请先维护故事资料")
        cast = prepare_cast(spec, state) if automatic(spec) else None
        context_ids = [c["id"] for c in cast["characters"]] if cast else spec.character_ids
        style = await StyleProfileService(self.session).get(project_id)
        if random_narratives:
            if spec.card_selection_policy != "separate-v1":
                raise WorkflowError("随机叙事卡须使用独立题材与叙事选卡方式")
            pool = sorted({
                card["id"] for card in StyleProfileService(self.session).catalog()
                if card.get("layer") == "narrative"
            })
            if len(pool) < 2:
                raise WorkflowError("活动叙事卡不足两张，无法建立随机选卡预览")
            spec = spec.model_copy(update={"narrative_card_ids": SystemRandom().sample(pool, 2)})
        payload = spec.model_dump(mode="json")
        if (
            spec.writing_policy not in {"background-v1", "guided-v1"}
            and spec.focus_card_id == "girls_love_gl"
            and spec.relationship_scope
            in {
                "non_romantic",
                "not_applicable",
            }
        ):
            raise WorkflowError("百合主导与非爱情范围冲突，请明确本阶段关系探索方向")
        card_ids = selected_ids(spec, style)
        cards = []
        for card_id in card_ids:
            card = validate_genre_card_id(card_id)
            path = (QUALITY_CARD_DIR / card["source_file"]).resolve()
            if not path.is_relative_to(QUALITY_CARD_DIR.resolve()) or path.suffix != ".md":
                raise WorkflowError("题材来源路径无效")
            body = path.read_text(encoding="utf-8")
            cards.append(
                {
                    "id": card_id,
                    "name": card["name"],
                    "layer": card.get("layer", "genre"),
                    "content_version": card.get("content_version", ""),
                    "sha256": digest(body),
                    "text": body,
                }
            )
        profile = self.profiles.get(spec.profile_id)
        if profile is None or not profile.enabled or not profile.allow_story_data:
            raise WorkflowError("供应商不可用或不允许发送故事资料")
        counting = {
            "chief": counting_config(spec.chief_tokenizer_id, spec.chief_model),
            "writer": counting_config(spec.writer_tokenizer_id, spec.writer_model),
        }
        context: dict[str, Any] = {
            "source_version_id": str(version.id),
            "source_state_sha256": fingerprint(state),
            "characters": [characters[c] for c in context_ids],
            "relationships": [
                r
                for r in state["relationships"]
                if {r["source_character_id"], r["target_character_id"]} & set(context_ids)
            ],
            "world_rules": state.get("world_rules", []),
            "world_lore": state.get("world_lore", []),
            "beliefs": [b for b in state.get("beliefs", []) if b["character_id"] in context_ids],
            "recent_events": state.get("events", [])[-12:],
            "future_material_not_obligations": {
                "plot_threads": state.get("plot_threads", [])[-12:],
                "open_questions": state.get("open_questions", [])[-12:],
            },
            "narrative_position": state.get("narrative_position", {}),
            "story_foundation": state.get("story_foundation", {}),
            "style_assets": style.get("assets", []),
            "recent_chapters": [],
            "relevant_history": [],
        }
        formal = list(
            await self.session.scalars(
                select(ChapterRecord)
                .where(
                    ChapterRecord.project_id == project_id,
                    ChapterRecord.id.in_([UUID(c) for c in version.chapter_revisions]),
                )
                .order_by(
                    func.coalesce(ChapterRecord.display_ordinal, ChapterRecord.ordinal),
                    ChapterRecord.ordinal,
                )
            )
        )
        sources: list[dict[str, Any]] = []
        formal_inputs: dict[str, dict[str, Any]] = {}
        candidates: list[tuple[int, dict[str, Any]]] = []
        names = [characters[c]["name"] for c in context_ids]
        for chapter in formal:
            revision = await self._revision(UUID(version.chapter_revisions[str(chapter.id)]))
            item = {
                "revision_id": str(revision.id),
                "sha256": digest(revision.body),
                "body": revision.body,
            }
            formal_inputs[item["revision_id"]] = item
            sources.append(
                {
                    "revision_id": item["revision_id"],
                    "sha256": item["sha256"],
                    "selected": chapter == formal[-1],
                }
            )
            if chapter == formal[-1]:
                context["recent_chapters"] = [item]
            else:
                score = sum(revision.body.count(name) for name in names)
                if score:
                    candidates.append((score, item))
        state_selection: dict[str, Any] = {}
        if spec.workflow == "novel-run-v1":
            state_selection = enrich_context(context, state, context_ids, recall_text(spec))
            policy = await self.session.get(ProjectNaturalnessPolicyRecord, project_id)
            reference = None
            if policy is None or policy.enabled:
                if policy and policy.reference_style_profile_id:
                    reference = await self.session.get(
                        ReferenceStyleProfileRecord, policy.reference_style_profile_id
                    )
                else:
                    reference = await self.session.scalar(
                        select(ReferenceStyleProfileRecord)
                        .where(
                            ReferenceStyleProfileRecord.project_id == project_id,
                            ReferenceStyleProfileRecord.status == "active",
                        )
                        .order_by(ReferenceStyleProfileRecord.version.desc())
                        .limit(1)
                    )
                    if reference is None:
                        default = await self.session.get(
                            GlobalReferenceStyleDefaultRecord, "writer"
                        )
                        if default:
                            reference = await self.session.get(
                                ReferenceStyleProfileRecord, default.profile_id
                            )
            if reference and reference.status == "active":
                context["reference_style"] = {
                    "profile_id": str(reference.id),
                    "version": reference.version,
                    "positive_contract": reference.positive_contract,
                    "negative_transfer_rules": reference.negative_transfer_rules,
                    "contains_reference_text": False,
                }
            for action in [
                *slots_for(spec),
                "amend",
                "rewrite",
                "memory_amend",
                "checker_amend",
                "reader_amend",
            ]:
                model, _, tokenizer = model_for(spec, action)
                counting[action] = counting_config(tokenizer, model)
            summaries = await self.session.scalars(
                select(GenerationArtifactRecord).where(
                    GenerationArtifactRecord.project_id == project_id,
                    GenerationArtifactRecord.kind == "formal_summary",
                )
            )
            revisions = {source["revision_id"]: source["sha256"] for source in sources}
            context["formal_summaries"] = [
                {k: v for k, v in a.payload.items() if k != "changes"}
                | {
                    "changes": [
                        {k: v for k, v in change.items() if k != "evidence"}
                        for change in a.payload.get("changes", [])
                    ]
                }
                for a in summaries
                if a.sha256 == fingerprint(a.payload)
                and revisions.get(a.payload.get("revision_id")) == a.payload.get("body_sha256")
            ]
        snapshot: dict[str, Any] = {
            "prompt_contract_sha256": contract_for(spec),
            "cards": cards,
            "profile": profile.model_dump(mode="json"),
            "counting": counting,
            "context": context,
            "sources": sources,
            "state_selection": state_selection,
            "candidate_chapter": {"id": str(uuid4()), "ordinal": len(formal) + 1}
            if spec.workflow == "novel-run-v1"
            else None,
            "normal_calls": len(
                [
                    a
                    for a in slots_for(spec)
                    if a not in {"editor", "memory_edit", "checker_edit", "title"}
                ]
            ),
            "maximum_calls": len(slots_for(spec)),
            "action_slots": slots_for(spec),
            "action_models": {
                action: {
                    "model": model_for(spec, action)[0],
                    "output_limit": request_for(
                        spec, profile, action, "本地容量预检", "仅核对配置，不发送"
                    ).max_output_tokens,
                }
                for action in slots_for(spec)
            },
            "maximum_cost_cny": str(estimate_cost(spec, profile)),
            "future_input_is_estimate": True,
        }
        if random_narratives:
            snapshot["narrative_selection_policy"] = "random-two-v1"
        if cast:
            snapshot["cast_selection"] = cast
        if spec.stage_mode == "longform-v1":
            from novel_writer.generation.stage_history import bind_history

            await bind_history(self, project_id, spec, snapshot, state)
            snapshot["normal_calls"] = len(
                [
                    a
                    for a in slots_for(spec)
                    if a not in {"editor", "memory_edit", "checker_edit", "title"}
                ]
            )
        await asyncio.to_thread(
            prepare_preview, spec, snapshot, profile,
            str(formal[-1].id) if formal else None, sources, formal_inputs, candidates,
        )
        frozen_sha = fingerprint(
            {"revision": revision_for(spec), "spec": payload, "snapshot": snapshot}
        )
        batch = GenerationBatchRecord(
            project_id=project_id,
            base_version_id=version.id,
            revision=revision_for(spec),
            spec=payload,
            snapshot=snapshot,
            preview_sha256=frozen_sha,
            status="draft",
            authorized=False,
            pause_requested=False,
            next_action="plan",
            state={},
        )
        self.session.add(batch)
        await self.session.flush()
        result = await self.detail(batch)
        await self._save_idempotent(command, key, result, request_payload)
        return result

    async def authorize(
        self, project_id: UUID, batch_id: UUID, sha: str, key: str
    ) -> dict[str, Any]:
        command = f"generation_authorize:{batch_id}"
        payload = {"preview_sha256": sha}
        cached = await self._idempotent(command, key, payload)
        if cached is not None:
            return cached
        batch = await self.batch(project_id, batch_id, lock=True)
        await self.assert_current(batch)
        if batch.preview_sha256 != sha or batch.snapshot.get("blockers"):
            raise ConflictError("预览绑定无效或仍有阻塞，未发送供应商请求")
        if batch.status not in {"draft", "paused", "awaiting_plan"} or batch.next_action is None:
            raise ConflictError("当前批次不能启动；未知结果或失败不能自动重发")
        active = await self.session.scalar(
            select(GenerationCallRecord.id)
            .where(
                GenerationCallRecord.project_id == project_id,
                GenerationCallRecord.status.in_(("executing", "outcome_uncertain")),
            )
            .limit(1)
        )
        if active:
            raise ConflictError("该作品有执行中或结果未知调用，不能再次派发")
        queued = await self.session.scalar(
            select(GenerationBatchRecord.id)
            .where(
                GenerationBatchRecord.project_id == project_id,
                GenerationBatchRecord.id != batch.id,
                GenerationBatchRecord.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        if queued is not None:
            raise ConflictError("该作品已有创作任务排队，请先暂停或等待完成")
        batch.authorized = True
        batch.pause_requested = False
        batch.status = "queued"
        result = {"id": str(batch.id), "status": batch.status}
        await self._save_idempotent(command, key, result, payload)
        return result

    async def assert_current(self, batch: GenerationBatchRecord, *, dispatch: bool = True) -> None:
        project = await self._project(batch.project_id)
        if project.archived_at is not None or project.current_version_id != batch.base_version_id:
            raise ConflictError("正式版本已经变化，请从最新正式起点建立新批次")
        if (
            batch.revision != revision_for(FrozenGenerationSpec.model_validate(batch.spec))
            or fingerprint(
                {"revision": batch.revision, "spec": batch.spec, "snapshot": batch.snapshot}
            )
            != batch.preview_sha256
        ):
            raise ConflictError("冻结批次来源或实际流程修订不匹配")
        if not dispatch:
            return
        if batch.snapshot.get("prompt_contract_sha256") != contract_for(
            FrozenGenerationSpec.model_validate(batch.spec)
        ):
            raise ConflictError("生成提示词已修订，请建立新批次；不会升级旧批次输入")
        profile = self.profiles.get(batch.spec["profile_id"])
        if profile is None or profile.model_dump(mode="json") != batch.snapshot["profile"]:
            raise ConflictError("模型配置已变化，需要重新预览，不能静默升级批次")

    async def detail(self, batch: GenerationBatchRecord) -> dict[str, Any]:
        from novel_writer.generation.content import paragraphs
        from novel_writer.generation.diagnostics import revalidation_blocker
        from novel_writer.generation.input_recovery import eligible
        from novel_writer.generation.memory_recovery import eligible as memory_eligible
        from novel_writer.generation.memory_recovery import resolved_failures
        from novel_writer.generation.step_recovery import eligible as step_eligible

        candidate = await self.artifact(batch, "candidate")
        artifacts = list(
            await self.session.scalars(
                select(GenerationArtifactRecord)
                .where(
                    GenerationArtifactRecord.batch_id == batch.id,
                )
                .order_by(GenerationArtifactRecord.created_at, GenerationArtifactRecord.id)
            )
        )
        calls = list(
            await self.session.scalars(
                select(GenerationCallRecord)
                .where(
                    GenerationCallRecord.batch_id == batch.id,
                )
                .order_by(GenerationCallRecord.slot)
            )
        )
        resolved = await resolved_failures(self, batch, calls)
        return {
            "id": str(batch.id),
            "passages": [
                {k: p[k] for k in ("id", "start", "end")}
                for p in paragraphs(candidate.payload["body"])
            ]
            if candidate
            else [],
            "project_id": str(batch.project_id),
            "status": batch.status,
            "revision": batch.revision,
            "spec": batch.spec,
            "preview_sha256": batch.preview_sha256,
            "snapshot": batch.snapshot,
            "next_action": batch.next_action,
            "state": batch.state,
            "plan_retry_preview": plan_retry_preview(batch, calls, {a.kind for a in artifacts}),
            "input_recovery_available": eligible(batch, calls),
            "memory_recovery_available": memory_eligible(batch, calls),
            "step_recovery_available": step_eligible(batch, calls),
            "plan_edit_revision": "author-plan-v1",
            "artifacts": [
                {
                    "id": str(a.id),
                    "kind": a.kind,
                    "sha256": a.sha256,
                    "payload": a.payload,
                    "created_at": a.created_at.isoformat(),
                }
                for a in artifacts
            ],
            "calls": [
                {
                    "id": str(c.id),
                    "action": c.action,
                    "status": c.status,
                    "request_sha256": c.request_sha256,
                    "error_code": c.error_code,
                    "started_at": c.started_at.isoformat(),
                    "finished_at": c.finished_at.isoformat() if c.finished_at else None,
                    "actual_cost_cny": str(c.actual_cost_cny)
                    if c.actual_cost_cny is not None
                    else None,
                    "usage": (c.response or {}).get("usage"),
                    "diagnostic": output_diagnostic(c) or plan_distribution_diagnostic(c),
                    "can_revalidate": revalidation_blocker(batch, c, candidate) is None,
                    "revalidation_blocker": revalidation_blocker(batch, c, candidate),
                    "replaced_by_recovery": str(c.id) in resolved,
                    "retry_of_call_id": c.request.get("retry_of_call_id"),
                    "input_tokens": c.request.get("input_tokens"),
                }
                for c in calls
            ],
        }

    async def artifact(
        self, batch: GenerationBatchRecord, kind: str
    ) -> GenerationArtifactRecord | None:
        pointer = batch.state.get(f"{kind}_id")
        item = await self.session.get(GenerationArtifactRecord, UUID(pointer)) if pointer else None
        if pointer and item is None:
            raise ConflictError("当前工件引用缺失")
        if item and (
            item.batch_id != batch.id
            or item.kind != kind
            or item.sha256 != fingerprint(item.payload)
        ):
            raise ConflictError("工件来源或 SHA 不一致")
        return item

    async def append(
        self, batch: GenerationBatchRecord, kind: str, payload: dict[str, Any]
    ) -> GenerationArtifactRecord:
        artifact = GenerationArtifactRecord(
            project_id=batch.project_id,
            batch_id=batch.id,
            kind=kind,
            sha256=fingerprint(payload),
            payload=payload,
        )
        self.session.add(artifact)
        await self.session.flush()
        batch.state = {**batch.state, f"{kind}_id": str(artifact.id)}
        return artifact

    async def edit_candidate(
        self, batch: GenerationBatchRecord, body: str, expected: str
    ) -> dict[str, Any]:
        if batch.status in {"queued", "running", "outcome_uncertain", "adopted", "archived"}:
            raise ConflictError("请先暂停并等待在途请求完成")
        candidate = await self.artifact(batch, "candidate")
        if candidate is None or digest(candidate.payload["body"]) != expected:
            raise ConflictError("正文已经变化，请刷新")
        await self.append(batch, "candidate", {"body": checked_body(body), "source": "author"})
        batch.state = invalidate_candidate(batch.state)
        if batch.revision == LONGFORM_REVISION:
            batch.state = {
                k: v for k, v in batch.state.items() if k not in {"units_id", "chief_comparison_id"}
            }
            from novel_writer.generation.longform import freeze_segments

            await freeze_segments(self, batch)
        batch.status = "needs_attention"
        batch.next_action = None
        return await self.detail(batch)

    async def edit_plan(self, batch: GenerationBatchRecord, edit: PlanEdit) -> dict[str, Any]:
        await self.assert_current(batch)
        if batch.revision == LONGFORM_REVISION:
            from novel_writer.generation.longform import edit_stage_plan

            return await edit_stage_plan(self, batch, edit)
        if batch.status not in {"awaiting_plan", "paused", "needs_attention"}:
            raise ConflictError("当前阶段不能修改方案")
        written = await self.session.scalar(
            select(GenerationCallRecord.id)
            .where(
                GenerationCallRecord.batch_id == batch.id,
                GenerationCallRecord.slot >= 1,
            )
            .limit(1)
        )
        if written is not None:
            raise ConflictError("Writer 已领取槽位，不能改写其方案来源")
        prior = await self.artifact(batch, "plan")
        if (prior.sha256 if prior else None) != edit.expected_plan_sha256:
            raise ConflictError("方案已变化，请刷新")
        try:
            raw_plan = json_text(edit.plan.model_dump(mode="json"))
            plan = (
                selected_plan(
                    raw_plan, FrozenGenerationSpec.model_validate(batch.spec), batch.snapshot
                )
                if batch.revision == NOVEL_REVISION
                else parse_plan(raw_plan, batch.spec["character_ids"])
            )
            question_items = []
            if batch.revision == NOVEL_REVISION:
                questions = await self.artifact(batch, "questions")
                question_items = reconcile_questions(
                    questions.payload["items"] if questions else [],
                    selected_plan(
                        raw_plan, FrozenGenerationSpec.model_validate(batch.spec), batch.snapshot
                    ),
                    edit.question_answers,
                    edit.deferred_questions,
                    edit.author_note,
                )
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        if plan.questions and batch.revision != NOVEL_REVISION:
            raise WorkflowError("请在作者说明中答复待决问题，并提交可执行的方案")
        await self.append(batch, "plan", plan.model_dump(mode="json"))
        await self.append(
            batch,
            "plan_author_note",
            {
                "note": edit.author_note
                + (
                    "\n作者逐项决定\n" + json_text(question_items)
                    if batch.revision == NOVEL_REVISION
                    else ""
                ),
                "previous_sha256": edit.expected_plan_sha256,
            },
        )
        if batch.revision == NOVEL_REVISION:
            await self.append(
                batch,
                "questions",
                {"items": question_items},
            )
        batch.status = "awaiting_plan"
        batch.next_action = "write"
        return await self.detail(batch)

    async def resolve_unknown(self, batch: GenerationBatchRecord, note: str) -> dict[str, Any]:
        if batch.status != "outcome_uncertain":
            raise ConflictError("当前批次没有待核对的未知调用")
        calls = list(
            await self.session.scalars(
                select(GenerationCallRecord).where(
                    GenerationCallRecord.batch_id == batch.id,
                )
            )
        )
        if any(c.status == "executing" for c in calls):
            raise ConflictError("调用仍在执行，请等待")
        # Original response and status remain evidence; closure is a separate author receipt.
        for call in calls:
            if call.status == "outcome_uncertain":
                await self.append(
                    batch,
                    "uncertain_resolution",
                    {
                        "call_id": str(call.id),
                        "note": note,
                        "previous_status": call.status,
                        "cost_remains_unknown": call.actual_cost_cny is None,
                    },
                )
                call.status = "uncertain_closed"
        batch.status = "needs_attention"
        batch.next_action = None
        batch.authorized = False
        return await self.detail(batch)

    async def adoption_preview(
        self,
        batch: GenerationBatchRecord,
        position: dict[str, Any],
        title: str,
        factual_changes: dict[str, Any] | None = None,
        facts_confirmed: bool = False,
    ) -> dict[str, Any]:
        await self.assert_current(batch, dispatch=False)
        if batch.revision == LONGFORM_REVISION:
            raise WorkflowError("多章阶段请使用逐章事实确认与连续前缀采用入口")
        if batch.status not in {"ready", "needs_attention", "paused", "failed"}:
            raise ConflictError("当前批次不可采用")
        candidate = await self.artifact(batch, "candidate")
        if candidate is None or candidate.payload.get("complete") is False:
            raise WorkflowError("没有完整候选正文；截断稿可先手工补齐并保存")
        review = await self.artifact(batch, "review")
        memory = await self.artifact(batch, "memory")
        checker = await self.artifact(batch, "checker")
        for report in (memory, checker, review):
            if report and report.payload.get("candidate_sha256") != candidate.sha256:
                raise ConflictError("审核报告与当前正文绑定不一致")
        if batch.revision == NOVEL_REVISION and not facts_confirmed:
            raise WorkflowError("请核对本章事实变化并明确确认；报告缺失或部分可用时需作者补充")
        try:
            narrative = NarrativePosition.model_validate(position)
        except ValueError as error:
            raise WorkflowError("叙事位置字段无效，请核对当前人物和文本字段") from error
        if not narrative.current_location.strip() or not narrative.recent_major_event.strip():
            raise WorkflowError("采用前请确认当前地点与本章实际完成的事件")
        base = await self._version(batch.base_version_id)
        changes = factual_changes or {}
        if set(changes) - set(StateDelta.model_fields) or {
            "base_version",
            "set_narrative_position",
        } & set(changes):
            raise WorkflowError("事实变更字段无效；起点与叙事位置由审核预览绑定")
        try:
            delta = StateDelta.model_validate(
                {
                    **changes,
                    "base_version": base.number,
                    "set_narrative_position": narrative.model_dump(mode="json"),
                }
            )
            delta.apply(StoryState.model_validate(base.state))
        except ValueError as error:
            raise WorkflowError("事实变更结构或人物/世界引用无效，请核对后重新预览") from error
        evidence = {
            "batch_id": str(batch.id),
            "base_version_id": str(batch.base_version_id),
            "candidate_sha256": candidate.sha256,
            "review_sha256": review.sha256 if review else None,
            "memory_sha256": memory.sha256 if memory else None,
            "checker_sha256": checker.sha256 if checker else None,
            "facts_confirmed": facts_confirmed,
            "position": narrative.model_dump(mode="json"),
            "title": title.strip(),
            "factual_delta": delta.model_dump(mode="json"),
        }
        return {
            **evidence,
            "preview_sha256": fingerprint(evidence),
            "needs_genre_acknowledgement": not advisory(
                FrozenGenerationSpec.model_validate(batch.spec)
            )
            and (review is None or review.payload.get("needs_attention", True)),
        }

    async def adopt(
        self, project_id: UUID, batch_id: UUID, payload: AdoptRequest, key: str
    ) -> dict[str, Any]:
        command = f"generation_adopt:{batch_id}"
        request = payload.model_dump(mode="json")
        cached = await self._idempotent(command, key, request)
        if cached is not None:
            return cached
        batch = await self.batch(project_id, batch_id, lock=True)
        preview = await self.adoption_preview(
            batch,
            payload.narrative_position,
            payload.title,
            payload.factual_changes,
            payload.facts_confirmed,
        )
        if preview["preview_sha256"] != payload.preview_sha256:
            raise ConflictError("审核预览发生变化，请重新核对正文和资料")
        if preview["needs_genre_acknowledgement"] and not payload.accept_genre_deviation:
            raise WorkflowError("该稿题材效果未确认；请明确选择保留当前写法")
        candidate = await self.artifact(batch, "candidate")
        assert candidate is not None
        project = await self._project(project_id)
        base = await self._version(batch.base_version_id)
        state = StoryState.model_validate(base.state)
        delta = StateDelta.model_validate(preview["factual_delta"])
        updated = delta.apply(state)
        ordinal = (
            await self.session.scalar(
                select(func.max(ChapterRecord.ordinal)).where(
                    ChapterRecord.project_id == project_id
                )
            )
            or 0
        ) + 1
        display = len(base.chapter_revisions) + 1
        chapter = ChapterRecord(
            id=UUID(batch.snapshot["candidate_chapter"]["id"])
            if batch.revision == NOVEL_REVISION
            else uuid4(),
            project_id=project_id,
            ordinal=ordinal,
            display_ordinal=display,
            title=payload.title.strip() or f"第{display}章",
        )
        self.session.add(chapter)
        await self.session.flush()
        revision = ChapterRevisionRecord(
            chapter_id=chapter.id,
            base_version_id=base.id,
            body=candidate.payload["body"],
            status="accepted",
        )
        self.session.add(revision)
        await self.session.flush()
        chapter.current_revision_id = revision.id
        self.session.add(
            StateDeltaRecord(
                revision_id=revision.id,
                content=delta.model_dump(mode="json"),
            )
        )
        await invalidate_formal_dependents(
            self.session, project_id, "作者采用新的题材主导创作章节", exempt_generation_id=batch.id
        )
        version = StateVersionRecord(
            project_id=project_id,
            number=base.number + 1,
            parent_id=base.id,
            parent_number=base.number,
            state=updated.model_dump(mode="json"),
            chapter_revisions={**base.chapter_revisions, str(chapter.id): str(revision.id)},
            chapter_titles={**(base.chapter_titles or {}), str(chapter.id): chapter.title},
        )
        self.session.add(version)
        await self.session.flush()
        project.current_version_id = version.id
        if batch.revision == NOVEL_REVISION:
            memory = await self.artifact(batch, "memory")
            summary: dict[str, Any] = {
                "body_sha256": digest(candidate.payload["body"]),
                "position": preview["position"],
                "factual_changes": preview["factual_delta"],
                "coverage": "author-confirmed",
                "method": "author-adoption",
            }
            if (
                memory
                and memory.payload["position"] == preview["position"]
                and {
                    k: v
                    for k, v in preview["factual_delta"].items()
                    if k not in {"base_version", "set_narrative_position"} and v
                }
                == memory.payload["factual_changes"]
            ):
                summary = local_summary(memory.payload, candidate.payload["body"])
            await self.append(
                batch,
                "formal_summary",
                {
                    **summary,
                    "revision_id": str(revision.id),
                    "version_id": str(version.id),
                    "chapter_id": str(chapter.id),
                    "adoption_sha256": preview["preview_sha256"],
                },
            )
        batch.status = "adopted"
        batch.next_action = None
        batch.state = {**batch.state, "adopted_version_id": str(version.id), "adoption": preview}
        result = {
            "id": str(batch.id),
            "version_id": str(version.id),
            "version_number": version.number,
            "chapter_id": str(chapter.id),
        }
        await self._save_idempotent(command, key, result, request)
        return result
