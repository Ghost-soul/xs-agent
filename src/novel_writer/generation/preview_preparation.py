"""CPU-heavy preview preparation, isolated from database sessions and model dispatch."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from novel_writer.generation.budget import request_for, request_preview, validate_capacity
from novel_writer.generation.chief_context import fit_context, material_target
from novel_writer.generation.prompt_templates import render_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile


def prepare_preview(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    profile: ProviderProfile,
    latest_chapter_id: str | None,
    sources: list[dict[str, Any]],
    formal_inputs: dict[str, dict[str, Any]],
    candidates: list[tuple[int, dict[str, Any]]],
) -> None:
    context = snapshot["context"]
    counting = snapshot["counting"]
    fit_context(spec, snapshot, profile, latest_chapter_id)
    if spec.workflow == "novel-run-v1" and spec.context_policy in {
        "knowledge-rag-v1",
        "role-rag-v2",
        "role-key-v3",
        "chief-focus-v4",
    }:
        # The new renderer budgets recent summaries and retrieved excerpts together.
        # Do not re-render the whole novel once per historical chapter.
        ordered = {s["revision_id"]: i for i, s in enumerate(sources)}
        available = sorted(
            context["formal_summaries"],
            key=lambda s: ordered[s["revision_id"]],
            reverse=True,
        )
        context["formal_summaries"] = available[:3]
        context["relevant_history"] = []
        snapshot["summary_sources"] = [
            {
                "revision_id": s["revision_id"],
                "body_sha256": s["body_sha256"],
                "selected": i < 3,
                "required": bool(sources and s["revision_id"] == sources[-1]["revision_id"]),
            }
            for i, s in enumerate(available)
        ]
    elif spec.workflow == "novel-run-v1":
        # Keep full summaries locally; select whole projections within the input target.
        ordered_revisions = {s["revision_id"]: index for index, s in enumerate(sources)}
        available = sorted(
            context["formal_summaries"],
            key=lambda s: ordered_revisions[s["revision_id"]],
            reverse=True,
        )
        context["formal_summaries"] = []
        snapshot["summary_sources"] = []
        for summary in available:
            context["formal_summaries"].append(summary)
            required = bool(sources and summary["revision_id"] == sources[-1]["revision_id"])
            fallback = not required and summary["coverage"] != "complete"
            if fallback:
                context["relevant_history"].append(formal_inputs[summary["revision_id"]])
            system, user = render_for(spec, snapshot, "plan")
            request = request_for(spec, profile, "plan", system, user)
            selected = True
            try:
                count = validate_capacity(
                    request_preview(request), request, spec, profile, counting["chief"]
                )
                selected = required or count <= material_target(spec)
            except WorkflowError:
                # Required handoff stays intact; the final preflight reports its blocker.
                selected = required
            if not selected:
                context["formal_summaries"].pop()
                if fallback:
                    context["relevant_history"].pop()
            elif fallback:
                for source in sources:
                    if source["revision_id"] == summary["revision_id"]:
                        source["selected"] = True
            snapshot["summary_sources"].append(
                {
                    "revision_id": summary["revision_id"],
                    "body_sha256": summary["body_sha256"],
                    "selected": selected,
                    "required": required,
                }
            )
    # Select whole, relevant history pieces while leaving space for the plan and chapter.
    for _, item in sorted(
        []
        if spec.context_policy
        in {"knowledge-rag-v1", "role-rag-v2", "role-key-v3", "chief-focus-v4"}
        else candidates,
        key=lambda pair: pair[0],
        reverse=True,
    )[:4]:
        if any(h["revision_id"] == item["revision_id"] for h in context["relevant_history"]):
            continue
        context["relevant_history"].append(item)
        system, user = render_for(spec, snapshot, "plan")
        request = request_for(spec, profile, "plan", system, user)
        try:
            count = validate_capacity(
                request_preview(request), request, spec, profile, counting["chief"]
            )
            if count > material_target(spec):
                context["relevant_history"].pop()
                continue
        except WorkflowError:
            context["relevant_history"].pop()
            continue
        for source in sources:
            if source["revision_id"] == item["revision_id"]:
                source["selected"] = True
    reports: dict[str, Any] = {}
    system, user = render_for(spec, snapshot, "plan", reports=reports)
    if "key_context_selection" in reports:
        snapshot["plan_context_selection"] = reports["key_context_selection"]
    request = request_for(spec, profile, "plan", system, user)
    blockers: list[str] = []
    try:
        snapshot["plan_input_tokens"] = validate_capacity(
            request_preview(request), request, spec, profile, counting["chief"]
        )
    except WorkflowError as error:
        blockers.append(str(error))
    if Decimal(snapshot["maximum_cost_cny"]) > spec.max_cost_cny:
        blockers.append("本次有限动作的保守费用上界超过预算，请调整后重新预览")
    snapshot["blockers"] = blockers
