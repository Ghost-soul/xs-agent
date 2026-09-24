from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from novel_writer.core.credentials import CredentialError, CredentialStore
from novel_writer.core.logging import get_logger
from novel_writer.db.engine import Database
from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.amendments import authorized_spec as amendment_spec
from novel_writer.generation.budget import (
    cost_for,
    option_for,
    validate_capacity,
)
from novel_writer.generation.content import (
    checked_body,
    fingerprint,
    parse_object,
    parse_plan,
    review_result,
)
from novel_writer.generation.input_recovery import effective_spec
from novel_writer.generation.memory_recovery import authorized_spec, recovery_slot
from novel_writer.generation.novel import parser_for, role_for
from novel_writer.generation.request_preparation import prepare_request
from novel_writer.generation.response_journal import ResponseJournal
from novel_writer.generation.schemas import (
    LONGFORM_REVISION,
    NOVEL_REVISION,
    FrozenGenerationSpec,
    GenerationSpec,
)
from novel_writer.generation.service import GenerationService
from novel_writer.generation.stage import (
    ReportCompilationError,
    active_slots,
    compile_stage,
    continue_independent_reader,
    edit_scope,
)
from novel_writer.generation.step_recovery import authorized_spec as recovery_spec
from novel_writer.generation.step_recovery import checkpoint, pending_replay
from novel_writer.providers.base import ModelRequest, ModelResponse, ProviderResponseError
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import (
    ProviderProfile,
    ProviderProfileStore,
    build_provider,
)


class GenerationRuntime:
    """Finite model execution, with local storage and ownership recovery only."""

    def __init__(
        self,
        database: Database,
        profiles: ProviderProfileStore,
        credentials: CredentialStore,
        *,
        response_root: Path | None = None,
        recovery_poll_seconds: float = 5.0,
        shutdown_grace_seconds: float = 300.0,
    ) -> None:
        self.database = database
        self.profiles = profiles
        self.credentials = credentials
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.available = False
        self.owner: AsyncConnection | None = None
        self.pending_starts: set[UUID] = set()
        self.closing = False
        self.draining = False
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.owner_check = asyncio.Lock()
        self.maintenance_check = asyncio.Lock()
        self.stop_maintenance = asyncio.Event()
        self.maintenance_task: asyncio.Task[None] | None = None
        self.recovery_poll_seconds = recovery_poll_seconds
        self.journal = ResponseJournal(
            response_root or profiles.path.parent / "generation-response-buffer",
            database.engine.url.render_as_string(hide_password=False),
        )
        self.pending_responses: dict[UUID, dict[str, Any]] = {}
        self.logger = get_logger("novel_writer.generation.recovery")

    async def initialize(self) -> None:
        await self.maintain_once()
        if self.maintenance_task is None and not self.closing:
            self.maintenance_task = asyncio.create_task(self.maintain())

    async def _discard_owner(self) -> None:
        self.available = False
        owner, self.owner = self.owner, None
        if owner is not None:
            try:
                await asyncio.wait_for(owner.invalidate(), timeout=3)
                await asyncio.wait_for(owner.close(), timeout=3)
            except (SQLAlchemyError, OSError, TimeoutError):
                pass

    async def check_owner(self) -> bool:
        """Check the live connection and lock, including from readiness requests."""
        async with self.owner_check:
            if self.closing or self.owner is None or self.owner.invalidated:
                self.available = False
                return False
            try:
                owned = await asyncio.wait_for(
                    self.owner.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                            "AND pid=pg_backend_pid() AND classid=0 AND objid=724918320 "
                            "AND objsubid=1 AND granted)"
                        )
                    ),
                    timeout=3,
                )
                await asyncio.wait_for(self.owner.commit(), timeout=3)
                if not owned:
                    await self._discard_owner()
                return self.available and bool(owned)
            except (SQLAlchemyError, OSError, TimeoutError):
                await self._discard_owner()
                return False

    async def _acquire_owner(self) -> None:
        try:
            await self._discard_owner()
            self.owner = await asyncio.wait_for(self.database.engine.connect(), timeout=3)
            exists = await asyncio.wait_for(
                self.owner.scalar(text("SELECT to_regclass('public.generation_batches')")),
                timeout=3,
            )
            acquired = bool(exists) and bool(
                await asyncio.wait_for(
                    self.owner.scalar(text("SELECT pg_try_advisory_lock(724918320)")),
                    timeout=3,
                )
            )
            await asyncio.wait_for(self.owner.commit(), timeout=3)
            if not acquired:
                await self._discard_owner()
                return
            await asyncio.wait_for(self.reconcile(), timeout=5)
            self.available = True
        except (SQLAlchemyError, OSError, TimeoutError):
            await self._discard_owner()

    async def maintain_once(self) -> None:
        async with self.maintenance_check:
            if self.closing or self.draining:
                return
            if not await self.check_owner():
                # An in-flight provider request must finish or reach its original
                # deadline before this process reconciles its own checkpoints.
                if any(not task.done() for task in self.tasks.values()):
                    return
                async with self.owner_check:
                    await self._acquire_owner()
            if self.available:
                await self.recover_responses()

    async def maintain(self) -> None:
        while not self.closing:
            try:
                await asyncio.wait_for(
                    self.stop_maintenance.wait(),
                    timeout=self.recovery_poll_seconds,
                )
                return
            except TimeoutError:
                pass
            try:
                await self.maintain_once()
            except Exception as error:
                self.logger.warning("local_recovery.deferred", error_type=type(error).__name__)

    async def reconcile(self) -> None:
        async with self.database.session() as session, session.begin():
            exists = bool(
                await session.scalar(text("SELECT to_regclass('public.generation_batches')"))
            )
            if not exists:
                return
            batches = list(
                await session.scalars(
                    select(GenerationBatchRecord)
                    .where(
                        GenerationBatchRecord.status.in_(("queued", "running"))
                        | GenerationBatchRecord.id.in_(
                            select(GenerationCallRecord.batch_id).where(
                                GenerationCallRecord.status == "executing"
                            )
                        ),
                    )
                    .with_for_update()
                )
            )
            for batch in batches:
                calls = list(
                    await session.scalars(
                        select(GenerationCallRecord).where(
                            GenerationCallRecord.batch_id == batch.id,
                            GenerationCallRecord.status == "executing",
                        )
                    )
                )
                for call in calls:
                    call.status = "outcome_uncertain"
                    call.error_code = "process_interrupted"
                if batch.status in {"adopted", "archived"}:
                    continue
                batch.status = "outcome_uncertain" if calls else "paused"
                batch.pause_requested = True
                batch.state = {
                    **batch.state,
                    "message": (
                        "服务已安全停止，当前成果已保存，未继续派发模型请求"
                        if self.draining and not calls
                        else "服务中断，原调用结果未知，未重发模型请求"
                        if calls
                        else "服务重启，未自动恢复或发送模型请求"
                    ),
                }

    def start(self, batch_id: UUID) -> None:
        if self.closing or self.draining:
            return
        if batch_id not in self.tasks or self.tasks[batch_id].done():
            task = asyncio.create_task(self.run(batch_id))
            self.tasks[batch_id] = task

            def completed(done: asyncio.Task[None]) -> None:
                if not done.cancelled():
                    done.exception()
                if batch_id in self.pending_starts:
                    self.pending_starts.discard(batch_id)
                    self.start(batch_id)

            task.add_done_callback(completed)
        else:
            self.pending_starts.add(batch_id)

    async def close(self, *, grace_seconds: float | None = None) -> None:
        """Finish the current response, but never claim another paid step."""
        if self.closing:
            return
        self.draining = True
        self.pending_starts.clear()
        self.stop_maintenance.set()
        if self.maintenance_task is not None:
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task, return_exceptions=True)
        tasks = [task for task in self.tasks.values() if not task.done()]
        pending: set[asyncio.Task[None]] = set()
        if tasks:
            _, pending = await asyncio.wait(
                tasks,
                timeout=self.shutdown_grace_seconds if grace_seconds is None else grace_seconds,
            )
        for task in pending:
            task.cancel()
        if pending:
            # Allow the unknown-result marker to commit. A DB outage must not
            # hang shutdown forever; startup reconciliation remains the fallback.
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=10)
            except TimeoutError:
                self.logger.warning("generation.shutdown_persistence_deferred")
        if await self.check_owner():
            try:
                await asyncio.wait_for(self.reconcile(), timeout=5)
            except (SQLAlchemyError, OSError, TimeoutError):
                self.logger.warning("generation.shutdown_reconciliation_deferred")
        self.closing = True
        self.available = False
        async with self.owner_check:
            await self._discard_owner()

    async def run(self, batch_id: UUID) -> None:
        try:
            await self._run(batch_id)
        except (Exception, asyncio.CancelledError) as error:
            # The task callback must not silently swallow a failed persistence
            # transaction and leave a batch looking active forever.
            try:
                await self.local_failure(
                    batch_id,
                    f"本地处理暂时失败，正在恢复已接收响应；不会重发模型请求：{type(error).__name__}",
                )
            except Exception:
                self.available = False
            self.logger.warning(
                "generation.task_stopped", batch_id=str(batch_id), error_type=type(error).__name__
            )

    async def _run(self, batch_id: UUID) -> None:
        for _ in range(32):
            if self.draining:
                return
            try:
                prepared = await self.claim(batch_id)
            except Exception as error:
                await self.local_failure(batch_id, str(error))
                return
            if prepared is None:
                return
            call_id, spec, profile, request, counting = prepared
            if self.draining:
                await self.finish_error(batch_id, call_id, "not_dispatched", "服务正在安全停止")
                return
            response: ModelResponse | None = None
            failure: ProviderResponseError | None = None
            try:
                api_key = self.credentials.get_api_key(profile.id) or ""
                if profile.credential_required and not api_key:
                    raise WorkflowError("供应商凭据不可用，本次尚未发送请求")
                response = await asyncio.wait_for(
                    self.dispatch(call_id, request, profile, spec, counting, api_key),
                    timeout=spec.timeout_seconds,
                )
            except ProviderResponseError as error:
                failure = error
            except (WorkflowError, CredentialError) as error:
                await self.finish_error(batch_id, call_id, "not_dispatched", str(error))
                return
            except (Exception, asyncio.CancelledError) as error:
                await self.finish_error(
                    batch_id, call_id, "outcome_uncertain", type(error).__name__
                )
                return
            # Save the complete transport BEFORE any local report compilation.
            raw = response_payload(response, failure)
            raw["sha256"] = fingerprint(raw)
            entry: dict[str, Any] = {
                "batch_id": str(batch_id),
                "call_id": str(call_id),
                "response": raw,
                "model_request_sha256": fingerprint(request.model_dump(mode="json")),
                "received_at": datetime.now(UTC).isoformat(),
            }
            self.pending_responses[call_id] = entry
            try:
                entry = await asyncio.to_thread(
                    self.journal.save,
                    batch_id,
                    call_id,
                    entry["model_request_sha256"],
                    raw,
                )
                self.pending_responses[call_id] = entry
            except OSError:
                # A disk outage need not lose a response when PostgreSQL still
                # works; keep the in-memory receipt until either copy is durable.
                self.logger.warning("response_buffer.write_failed", call_id=str(call_id))
            for attempt in range(3):
                try:
                    await asyncio.wait_for(self.persist_response(entry), timeout=5)
                    break
                except (SQLAlchemyError, OSError, TimeoutError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.1 * (attempt + 1))
            await self.forget_response(call_id)
            if not await self.check_owner():
                await self.local_failure(
                    batch_id, "响应已保存；执行器连接恢复后可本地重验，未重发模型请求"
                )
                return
            try:
                await self.compile_response(batch_id, call_id)
            except Exception as error:
                await self.local_failure(
                    batch_id, f"响应已保存，本地处理失败：{type(error).__name__}"
                )
                return

    async def persist_response(self, entry: dict[str, Any], *, recovering: bool = False) -> None:
        batch_id, call_id = UUID(entry["batch_id"]), UUID(entry["call_id"])
        raw = entry["response"]
        from novel_writer.generation.response_binding import validate_response_binding

        async with self.database.session() as session, session.begin():
            batch = await session.get(GenerationBatchRecord, batch_id, with_for_update=True)
            call = await session.get(GenerationCallRecord, call_id)
            validate_response_binding(batch, call, entry)
            assert batch is not None and call is not None
            if call.response is not None:
                # A commit acknowledgement may have been lost. The identical
                # receipt is already durable; don't reset compilation or costs.
                return
            call.response = raw
            call.status = "response_saved"
            call.finished_at = datetime.fromisoformat(entry["received_at"])
            if raw.get("usage"):
                profile = ProviderProfile.model_validate(batch.snapshot["profile"])
                usage = raw["usage"]
                call.actual_cost_cny = cost_for(
                    option_for(profile, call.model),
                    usage["input_tokens"],
                    usage["output_tokens"],
                )
            if recovering and batch.status not in {"adopted", "archived"}:
                batch.pause_requested = True
                batch.status = "needs_attention"
                batch.next_action = None
                batch.state = {
                    **batch.state,
                    "message": "数据库已恢复，原响应已补存；请本地重验后继续，未重发模型请求",
                }

    async def forget_response(self, call_id: UUID) -> None:
        self.pending_responses.pop(call_id, None)
        try:
            await asyncio.to_thread(self.journal.remove, call_id)
        except OSError:
            # A retained identical receipt is harmless and is cleaned on the
            # next local recovery tick after verifying the committed response.
            self.logger.warning("response_buffer.cleanup_deferred", call_id=str(call_id))

    async def recover_responses(self) -> None:
        entries = dict(self.pending_responses)
        for path in await asyncio.to_thread(self.journal.pending):
            try:
                entry = await asyncio.to_thread(self.journal.read, path)
                entries[UUID(entry["call_id"])] = entry
            except (ValueError, KeyError, OSError, WorkflowError):
                # Never replace a corrupted durable receipt with a convenient
                # in-memory copy: retain the original evidence for inspection.
                with suppress(ValueError):
                    entries.pop(UUID(path.stem), None)
                self.logger.warning("response_buffer.invalid", file=path.name)
        for call_id, entry in entries.items():
            task = self.tasks.get(UUID(entry["batch_id"]))
            if task is not None and not task.done():
                continue
            try:
                await asyncio.wait_for(self.persist_response(entry, recovering=True), timeout=5)
                await self.forget_response(call_id)
            except WorkflowError:
                self.logger.warning("response_buffer.binding_rejected", call_id=str(call_id))

    async def claim(
        self, batch_id: UUID
    ) -> tuple[UUID, GenerationSpec, ProviderProfile, ModelRequest, dict[str, Any]] | None:
        if not await self.check_owner():
            raise WorkflowError("生成执行器连接中断，正在恢复服务；本次未派发新的模型请求")
        if self.draining:
            return None
        async with self.database.session() as session, session.begin():
            batch = await session.get(GenerationBatchRecord, batch_id)
            if batch is None:
                return None
            service = GenerationService(session, self.profiles)
            batch = await service.batch(batch.project_id, batch_id, lock=True)
            if self.draining:
                return None
            if (
                batch.status not in {"queued", "running"}
                or not batch.authorized
                or batch.next_action is None
            ):
                return None
            if batch.pause_requested:
                batch.status = "paused"
                return None
            await service.assert_current(batch)
            spec = await effective_spec(service, batch)
            spec = await amendment_spec(service, batch, spec)
            spec = await authorized_spec(service, batch, spec)
            spec = await recovery_spec(service, batch, spec)
            profile = ProviderProfile.model_validate(batch.snapshot["profile"])
            action = batch.next_action
            slots = await active_slots(service, batch)
            if action not in slots:
                raise WorkflowError("动作不属于当前有限授权")
            replay = await pending_replay(service, batch, action)
            if replay is not None:
                batch.status = "running"
                batch.state = {
                    **batch.state,
                    "message": f"从失败步骤恢复：{action}，等待供应商返回",
                }
                return (
                    replay.id,
                    spec,
                    profile,
                    ModelRequest.model_validate(replay.request["model_request"]),
                    replay.request["counting"],
                )
            slot = slots.index(action) + (
                (100 if batch.revision == LONGFORM_REVISION else 20)
                if batch.state.get("amendment_authorized_sha256")
                else 0
            )
            slot = await recovery_slot(service, batch, action, slot)
            prior = await session.scalar(
                select(GenerationCallRecord.id).where(
                    GenerationCallRecord.batch_id == batch.id,
                    GenerationCallRecord.slot == slot,
                )
            )
            if prior is not None:
                raise WorkflowError("此一次性动作槽位已领取，不能重新发送")
            plan = await service.artifact(batch, "plan")
            candidate = await service.artifact(batch, "candidate")
            author_note = await service.artifact(batch, "plan_author_note")
            if action != "plan" and plan is None:
                raise WorkflowError("没有有效 Chief 方案")
            if action not in {"plan", "write", "write:1"} and candidate is None:
                raise WorkflowError("没有本章正文")
            reports: dict[str, Any] = {}
            if batch.revision == LONGFORM_REVISION:
                from novel_writer.generation.longform import prepared_reports

                reports = await prepared_reports(service, batch, action)
            for kind in ("memory", "checker"):
                item = await service.artifact(batch, kind)
                if item:
                    if candidate and item.payload.get("candidate_sha256") != candidate.sha256:
                        raise WorkflowError("接力报告与当前候选不匹配")
                    reports[kind] = item.payload
            scope: dict[str, Any] = {}
            if action == "editor":
                scope = await edit_scope(service, batch)
            elif action in {"amend", "rewrite"}:
                amendment = await service.artifact(batch, "amendment")
                assert amendment is not None
                scope = amendment.payload["request"]
            reports["edit_scope"] = scope
            request, count, counting, omitted_history = prepare_request(
                spec,
                batch.snapshot,
                action,
                plan.payload if plan else None,
                reports.get("input_body", candidate.payload["body"] if candidate else None),
                author_note.payload["note"] if author_note else None,
                reports,
                scope,
            )
            call = GenerationCallRecord(
                project_id=batch.project_id,
                batch_id=batch.id,
                slot=slot,
                action=action,
                provider=profile.id,
                model=request.model,
                status="executing",
                request={
                    "resume_checkpoint": checkpoint(batch.state),
                    "resume_checkpoint_sha256": fingerprint(checkpoint(batch.state)),
                    "model_request": request.model_dump(mode="json"),
                    "action_slots": slots,
                    "feedback_options": {
                        "feedback_policy": spec.feedback_policy,
                        "writing_policy": spec.writing_policy,
                        "narrative_policy": spec.narrative_policy,
                        "plan_policy": spec.plan_policy,
                        "length_policy": spec.length_policy,
                        "enable_checker": spec.enable_checker,
                        "enable_reader": spec.enable_reader,
                    },
                    "input_authorization_id": batch.state.get("input_authorization_id"),
                    "memory_output_authorization_id": batch.state.get(
                        "memory_output_authorization_id"
                    ),
                    "effective_input_limit": spec.input_limit,
                    "amendment_authorized_sha256": batch.state.get("amendment_authorized_sha256"),
                    "edit_scope": scope,
                    "input_tokens": count,
                    "counting": counting,
                    "batch_preview_sha256": batch.preview_sha256,
                    "plan_sha256": plan.sha256 if plan else None,
                    "candidate_sha256": candidate.sha256 if candidate else None,
                    "author_note_sha256": author_note.sha256 if author_note else None,
                    **(
                        {
                            "input_selection": {
                                "target": spec.input_limit,
                                "omitted_history": omitted_history,
                                "mandatory_above_target": count > spec.input_limit,
                            }
                        }
                        if batch.revision == LONGFORM_REVISION
                        else {}
                    ),
                    **(
                        {
                            "unit_chain_sha256": reports["unit_chain_sha256"],
                            "input_body": reports.get("input_body"),
                        }
                        if batch.revision == LONGFORM_REVISION
                        else {}
                    ),
                },
                request_sha256=fingerprint(request.model_dump(mode="json")),
            )
            session.add(call)
            await session.flush()
            batch.status = "running"
            batch.state = {
                **batch.state,
                "message": f"{action}：等待供应商返回，最长 {spec.timeout_seconds} 秒",
            }
            return call.id, spec, profile, request, counting

    async def dispatch(
        self,
        call_id: UUID,
        request: ModelRequest,
        profile: ProviderProfile,
        spec: GenerationSpec,
        counting: dict[str, Any],
        api_key: str,
    ) -> ModelResponse:
        async def before_send(http_request: httpx.Request) -> None:
            if self.draining:
                raise WorkflowError("服务正在安全停止，本次未发送供应商请求")
            wire = http_request.content.decode("utf-8")
            count = validate_capacity(wire, request, spec, profile, counting)
            async with self.database.session() as session, session.begin():
                call = await session.get(GenerationCallRecord, call_id)
                assert call is not None
                # Freeze actual mapped request (no authorization headers) before I/O.
                if call.request.get("replay_wire_sha256") and (
                    fingerprint(wire) != call.request["replay_wire_sha256"]
                ):
                    raise WorkflowError("恢复请求与原发送内容不一致，未发送供应商请求")
                call.request = {**call.request, "wire_body": wire, "wire_input_tokens": count}
                call.request_sha256 = fingerprint(
                    {"wire_body": wire, "batch": call.request["batch_preview_sha256"]}
                )

        async def transport_progress(received: int) -> None:
            try:
                async with asyncio.timeout(1), self.database.session() as session, session.begin():
                    call = await session.get(GenerationCallRecord, call_id)
                    assert call is not None
                    service = GenerationService(session, self.profiles)
                    batch = await service.batch(call.project_id, call.batch_id, lock=True)
                    batch.state = {
                        **batch.state,
                        "transport": {
                            "call_id": str(call_id),
                            "received_bytes": received,
                            "last_received_at": datetime.now(UTC).isoformat(),
                        },
                        "message": "已收到供应商响应，正在接收完整内容",
                    }
            except (SQLAlchemyError, OSError, TimeoutError):
                # Progress is advisory. Losing this write must not abort an
                # otherwise healthy paid response stream.
                self.logger.warning("transport_progress.write_deferred", call_id=str(call_id))

        async def observe_response(response: httpx.Response) -> None:
            if isinstance(response.stream, httpx.AsyncByteStream):
                response.stream = ProgressStream(response.stream, transport_progress)

        # Per-model reasoning capability, rather than only the profile default.
        option = option_for(profile, request.model)
        if option.supports_reasoning_effort is not None:
            profile = profile.model_copy(
                update={"supports_reasoning_effort": option.supports_reasoning_effort}
            )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=15),
            event_hooks={"request": [before_send], "response": [observe_response]},
        ) as client:
            provider = build_provider(profile, client=client)
            return await provider.generate(request, api_key)

    async def compile_response(self, batch_id: UUID, call_id: UUID) -> None:
        async with self.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, call_id)
            batch = await session.get(GenerationBatchRecord, batch_id)
            assert call is not None and batch is not None
            service = GenerationService(session, self.profiles)
            batch = await service.batch(batch.project_id, batch_id, lock=True)
            if call.batch_id != batch.id or call.response is None:
                raise WorkflowError("保存响应不属于此批次")
            parser = parser_for(batch.revision, call.action)
            binding = f"{call.id}:{parser}"
            if binding in batch.state.get("compiled", []):
                return
            # A parser upgrade permits recovery of failed saved reports, never
            # recompilation of a completed call or replacement of its handoff.
            if call.status == "completed":
                return
            batch.state = {**batch.state, "compiled": [*batch.state.get("compiled", []), binding]}
            raw = call.response
            if raw.get("sha256") != fingerprint({k: v for k, v in raw.items() if k != "sha256"}):
                raise WorkflowError("保存响应 SHA 不一致，不能重新解释")
            if call.request.get("batch_preview_sha256") != batch.preview_sha256:
                raise WorkflowError("调用与批次输入绑定不一致")
            body = raw.get("text", "")
            terminal = raw.get("terminal") or {}
            complete = (
                bool(terminal.get("terminal_event_seen"))
                and not raw.get("error_code")
                and terminal.get("finish_reason")
                not in {"length", "max_tokens", "max_output_tokens"}
                and terminal.get("terminal_status") not in {"failed", "incomplete"}
            )
            try:
                if batch.revision == LONGFORM_REVISION:
                    from novel_writer.generation.longform import compile_longform

                    await compile_longform(service, batch, call, body, complete)
                elif batch.revision == NOVEL_REVISION:
                    await compile_stage(service, batch, call, body, complete)
                elif call.action == "write" and body.lstrip().startswith(("{", "```json")):
                    try:
                        issue = parse_object(body)
                    except ValueError:
                        issue = {}
                    if "generation_blocked" in issue:
                        await service.append(batch, "writer_issue", issue)
                        raise WorkflowError("Writer 报告设计冲突，请查看已保存的问题；未生成正文")
                if batch.revision not in {NOVEL_REVISION, LONGFORM_REVISION}:
                    if call.action == "write" and body:
                        await service.append(
                            batch,
                            "candidate",
                            {
                                "body": checked_body(body),
                                "complete": complete,
                                "source_call_id": str(call.id),
                            },
                        )
                    if not complete:
                        raise WorkflowError("供应商响应不完整，已保存可见内容；不会自动重试")
                    spec = FrozenGenerationSpec.model_validate(batch.spec)
                    if call.action == "plan":
                        plan = parse_plan(body, spec.character_ids)
                        await service.append(batch, "plan", plan.model_dump(mode="json"))
                        batch.next_action = "write"
                        batch.status = (
                            "awaiting_plan" if spec.pause_after_plan or plan.questions else "queued"
                        )
                        if plan.questions:
                            batch.next_action = None
                    elif call.action == "write":
                        checked_body(body)
                        batch.next_action = "review"
                        batch.status = "queued"
                    else:
                        candidate = await service.artifact(batch, "candidate")
                        assert candidate is not None
                        if candidate.sha256 != call.request.get("candidate_sha256"):
                            raise WorkflowError("复核绑定的候选已改变")
                        result = review_result(body, candidate.payload["body"])
                        result["candidate_sha256"] = candidate.sha256
                        await service.append(batch, "review", result)
                        batch.next_action = None
                        batch.status = "needs_attention" if result["needs_attention"] else "ready"
                call.status = "completed"
                call.error_code = None
                batch.state = {
                    **batch.state,
                    "message": (
                        "已保存结果；部分报告失败，已跳过依赖动作，请核对事实和失败诊断"
                        if batch.state.get("dependency_skip_id")
                        else "Chief 方案已保存，尚未生成本批正文；请核对后继续"
                        if call.action == "plan"
                        else "已保存结果；题材评价是模型意见，请阅读正文"
                    ),
                }
            except (ValueError, WorkflowError) as error:
                uncertain = not complete and (
                    not terminal.get("terminal_event_seen")
                    or raw.get("error_code") == "outcome_uncertain"
                )
                call.status = "outcome_uncertain" if uncertain else "local_failure"
                call.error_code = raw.get("error_code") or "local_validation_failed"
                batch.status = "outcome_uncertain" if uncertain else "needs_attention"
                batch.next_action = None
                batch.state = {**batch.state, "message": f"已保存响应，{str(error)[:1000]}"}
                if (
                    complete
                    and (
                        isinstance(error, ReportCompilationError)
                        or (
                            batch.revision == LONGFORM_REVISION
                            and isinstance(error, ValueError)
                            and role_for(call.action) in {"memory", "checker", "editor"}
                            and call.action != "title"
                        )
                    )
                    and not (
                        call.slot >= 200 and call.request.get("memory_output_authorization_id")
                    )
                    and not call.request.get("step_recovery_authorization_id")
                    and await continue_independent_reader(service, batch, call)
                ):
                    batch.state = {
                        **batch.state,
                        "message": batch.state["message"]
                        + "；已跳过依赖动作，继续原授权的独立 Reader 冷读",
                    }
            if batch.pause_requested and batch.status == "queued":
                batch.status = "paused"
            await service.append(
                batch,
                "compilation",
                {
                    "call_id": str(call.id),
                    "parser_revision": parser,
                    "response_sha256": raw["sha256"],
                    "status": call.status,
                    "error_code": call.error_code,
                    "message": batch.state.get("message"),
                    "resume_state": checkpoint(batch.state),
                },
            )

    async def finish_error(self, batch_id: UUID, call_id: UUID, status: str, message: str) -> None:
        async with self.database.session() as session, session.begin():
            call = await session.get(GenerationCallRecord, call_id)
            batch = await session.get(GenerationBatchRecord, batch_id)
            if call:
                call.status = status
                call.error_code = message[:80]
                call.finished_at = datetime.now(UTC)
            if batch:
                batch.status = "outcome_uncertain" if status == "outcome_uncertain" else "failed"
                batch.next_action = None
                batch.state = {**batch.state, "message": message[:1000]}

    async def local_failure(self, batch_id: UUID, message: str) -> None:
        async with self.database.session() as session, session.begin():
            batch = await session.get(GenerationBatchRecord, batch_id)
            if batch:
                batch.status = "needs_attention"
                batch.next_action = None
                batch.state = {**batch.state, "message": message[:1000]}


def response_payload(
    response: ModelResponse | None, failure: ProviderResponseError | None
) -> dict[str, Any]:
    if response is not None:
        return {
            "text": response.text,
            "usage": response.usage.model_dump(),
            "raw_response": response.raw_response,
            "transport": response.transport_metadata.model_dump(mode="json"),
            "assembled_response": response.assembled_response,
            "request_id": response.request_id,
            "terminal": response.terminal.model_dump() if response.terminal else None,
        }
    assert failure is not None
    return {
        "text": failure.extracted_content or "",
        "raw_response": failure.raw_response,
        "transport": {
            "format": failure.raw_transport_format,
            "raw_sha256": failure.raw_entity_body_sha256,
            "headers": failure.response_headers_redacted,
        },
        "usage": failure.usage.model_dump() if failure.usage else None,
        "error_code": failure.stable_code,
        "request_id": failure.request_id,
        "terminal": failure.terminal.model_dump() if failure.terminal else None,
    }


class ProgressStream(httpx.AsyncByteStream):
    """Observe transport activity without presenting a partial stream as saved prose."""

    def __init__(
        self, source: httpx.AsyncByteStream, report: Callable[[int], Awaitable[None]]
    ) -> None:
        self.source = source
        self.report = report

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        last = 0.0
        async for chunk in self.source:
            received += len(chunk)
            now = monotonic()
            if now - last >= 2:
                await self.report(received)
                last = now
            yield chunk

    async def aclose(self) -> None:
        await self.source.aclose()
