"""Versioned role inputs. Never rewrite an existing knowledge-rag-v1 request."""

from __future__ import annotations

import inspect
import json
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import (
    knowledge_context,
    memory_fields,
    plan_capacity,
    role_materials,
    role_prompts,
    role_queries,
    unit_scope,
)
from novel_writer.generation.content import fingerprint, paragraphs, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.generation.reports import EditorReport
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.knowledge import lookup
from novel_writer.knowledge.text import corpus_hash
from novel_writer.services.provider_profiles import ProviderProfile

POLICY = "role-rag-v2"


def enabled(spec: GenerationSpec) -> bool:
    return spec.workflow == "novel-run-v1" and spec.context_policy == POLICY


def previous_spec(spec: GenerationSpec) -> GenerationSpec:
    return (
        spec.model_copy(update={"context_policy": knowledge_context.POLICY})
        if enabled(spec)
        else spec
    )


def contract_for(spec: GenerationSpec) -> str:
    base = knowledge_context.contract_for(previous_spec(spec))
    if not enabled(spec):
        return base
    return fingerprint(
        {
            "base": base,
            "modules": [
                inspect.getsource(m)
                for m in (
                    sys.modules[__name__],
                    role_materials,
                    role_prompts,
                    role_queries,
                    memory_fields,
                )
            ],
            "writable_fields": memory_fields.fields(),
        }
    )


def amendment_contract(spec: GenerationSpec) -> str:
    return contract_for(spec) if enabled(spec) else knowledge_context.amendment_contract(spec)


def material_target(spec: GenerationSpec) -> int:
    return (
        min(120000, max(0, spec.input_limit - 40000))
        if enabled(spec)
        else knowledge_context.material_target(spec)
    )


def fit_context(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    profile: ProviderProfile,
    latest_chapter_id: str | None,
) -> None:
    if not enabled(spec):
        knowledge_context.fit_context(spec, snapshot, profile, latest_chapter_id)
    # New projections are selected once per actual role, retaining all frozen originals.


def retrieval_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None = None,
) -> dict[str, Any]:
    if not enabled(spec):
        return knowledge_context.retrieval_for(spec, snapshot, action, plan, body, reports)
    saved = reports.get("knowledge_retrieval")
    if saved is None and action == "plan":
        saved = snapshot.get("knowledge_plan_retrieval")
    if saved is not None:
        return knowledge_context.retrieval_for(
            previous_spec(spec),
            snapshot,
            action,
            plan,
            body,
            {**reports, "knowledge_retrieval": saved},
        )
    sources = knowledge_context.frozen_sources(snapshot)
    queries = role_queries.queries(spec, snapshot, action, plan, body, reports, author_note)
    hits = (
        lookup.prepare(
            f"role-rag-v2:{snapshot.get('knowledge_project_id')}:{spec.base_version_id}",
            sources,
        ).rank(queries)
        if queries
        else []
    )
    data = {
        "policy": POLICY,
        "project_id": snapshot.get("knowledge_project_id"),
        "version_id": str(spec.base_version_id),
        "queries": queries,
        "corpus_sha256": corpus_hash(sources),
        "mode": "lexical",
        "reason": "frozen_source_fallback" if queries else "role_does_not_need_retrieval",
        "model_key": None,
        "hits": hits,
    }
    return {**data, "sha256": fingerprint(data)}


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    role = role_for(action)
    if not enabled(spec) or role == "reader" or action == "title":
        return knowledge_context.render_for(
            previous_spec(spec), snapshot, action, plan, body, author_note, reports
        )
    reports = reports or {}
    config = snapshot.get("counting", {}).get(action) or snapshot.get("counting", {}).get(role)
    count = knowledge_context._counter(config or {"method": "utf8-byte-upper-bound"})
    scope = reports.get("edit_scope", {})
    if role == "editor":
        payload = {
            "scope": {
                k: scope[k]
                for k in ("instruction", "paragraph_ids", "protected_paragraph_ids")
                if k in scope
            },
            "authorized_paragraphs": [
                {"id": p["id"], "text": p["text"]}
                for p in paragraphs(body or "")
                if p["id"] in scope.get("paragraph_ids", [])
            ],
            "read_only_neighbors": role_materials.neighbors(body or "", scope, count),
            "output_schema": memory_fields.compact(EditorReport.model_json_schema()),
        }
        return role_prompts.EDITOR + "\n" + role_prompts.COMMON, json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    receipt = retrieval_for(spec, snapshot, action, plan, body, reports, author_note)
    state, candidate_opening = role_materials.state_at_opening(snapshot, action, reports)
    reference = role_materials.select_state(
        spec, snapshot, action, role, state, receipt, body, count, plan
    )
    queries = role_queries.queries(spec, snapshot, action, plan, body, reports, author_note)
    reference.update(
        role_materials.select_world(
            spec,
            role,
            state,
            reference,
            receipt,
            "\n".join(queries) + (body or "" if role in {"memory", "checker"} else ""),
            count,
        )
    )
    history = role_materials.history(spec, role, snapshot, receipt, count)
    boundary = {
        "source_version_id": str(spec.base_version_id),
        "opening": "正式起点叠加此前已验证的单元接力，尚未正式采用"
        if candidate_opening
        else "本次被写作或审核正文开始前的正式起点",
        "omission_is_absence": False,
    }
    task = {
        "author_direction": spec.direction,
        "author_boundaries": spec.author_boundaries,
        "author_decisions": author_note,
        "viewpoint": spec.viewpoint or "由 Chief 随事件确定，延续已有叙述方式",
        "story_foundation": snapshot["context"].get("story_foundation", {}),
    }
    if spec.relationship_scope != "genre-led":
        task.update(
            relationship_scope=spec.relationship_scope,
            relationship_character_ids=spec.relationship_character_ids,
        )
    style = {
        k: snapshot["context"][k]
        for k in ("style_assets", "reference_style")
        if k in snapshot["context"]
    }
    if role == "chief":
        # Reuse the unchanged output contract; only the new input layout is replaced.
        local = {**snapshot, "context": {**deepcopy(snapshot["context"]), **reference}}
        local["context"].update(formal_summaries=[], recent_chapters=[], relevant_history=[])
        _, old_raw = unit_scope.render_for(
            spec.model_copy(update={"context_policy": "focused-v1"}),
            local,
            action,
            plan,
            body,
            author_note,
            reports,
        )
        old = parse_object(old_raw)
        if spec.stage_mode != "longform-v1":
            plan_capacity.bound_schema(old["output_schema"], 2, 4)
        cards = {c["id"]: c for c in snapshot.get("cards", [])}
        selection = snapshot.get("cast_selection") or {}
        payload = {
            "story_task": task,
            "narrative_design": {"selected_cards": [cards[i] for i in spec.narrative_card_ids]},
            "cast_scope": {
                "allowed_ids": [
                    c["id"]
                    for c in selection.get("characters", snapshot["context"].get("characters", []))
                ],
                "required_ids": selection.get("required_ids", spec.character_ids),
                "maximum_cast": 12,
            },
            "reference_boundary": boundary,
            "formal_reference": reference,
            "world_cards": {
                "primary": cards.get(spec.focus_card_id),
                "secondary": cards.get(spec.supporting_card_id or ""),
            },
            "knowledge_context": history,
            "style_guidance": style,
            "future_proposal_not_fact": snapshot.get("previous_proposal"),
            "unit_limit": spec.unit_limit,
            "output_schema": old["output_schema"],
        }
        if spec.stage_mode != "longform-v1":
            payload.pop("unit_limit")
            payload["scene_count"] = "本次完成一个叙事单元，scenes 为其中二至四个连续场面"
        if action != "plan":
            payload.update(
                current_plan=plan,
                written_candidate=[
                    {"id": p["id"], "text": p["text"]} for p in paragraphs(body or "")
                ],
                completed_units=reports.get("completed_units", 0),
            )
    elif role == "writer":
        rewriting = action == "rewrite"
        current = (
            role_queries.current_task(action, plan)
            if not rewriting
            else scope.get("instruction", "")
        )
        scenes = (plan or {}).get("scenes", [])
        ordinal = int(action.split(":")[1]) if action.startswith("write:") else 1
        payload = {
            "story_task": {
                **task,
                **({"revision_instruction": scope.get("instruction", "")} if rewriting else {}),
            },
            "task_mode": "rewrite" if rewriting else "write-unit",
            "plot_execution": {"current_task": current},
            "stage_context": {
                **{
                    k: (plan or {}).get(k)
                    for k in (
                        "chapter_goal",
                        "bridge",
                        "major_turn",
                        "questions",
                        "question_scopes",
                        "world_context",
                    )
                },
                "unit_position": {
                    "current": ordinal,
                    "total": len(scenes) if action.startswith("write:") else 1,
                },
                "later_plans_not_current_task": [s.get("consequence", "") for s in scenes[ordinal:]]
                if action.startswith("write:")
                else [],
            },
            "reference_boundary": boundary,
            "formal_reference": reference,
        }
        if rewriting:
            payload["original_draft"] = body or ""
        else:
            payload["continuity"] = role_materials.continuity(spec, body, reports, queries, count)
        payload.update(knowledge_context=history, style_guidance=style)
    elif role == "memory":
        payload = {
            "candidate": [{"id": p["id"], "text": p["text"]} for p in paragraphs(body or "")],
            "candidate_chapter": reports.get("unit_chapter", snapshot.get("candidate_chapter")),
            "reference_boundary": boundary,
            "opening_reference": reference,
            "knowledge_context": history,
            "writable_fields": memory_fields.fields(),
            "output_schema": memory_fields.output_schema(),
        }
    else:
        payload = {
            "candidate": [{"id": p["id"], "text": p["text"]} for p in paragraphs(body or "")],
            "reference_boundary": boundary,
            "formal_start": reference,
            "knowledge_context": history,
        }
    return role_prompts.SYSTEMS[role] + "\n" + role_prompts.COMMON, json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
