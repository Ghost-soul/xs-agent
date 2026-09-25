"""Render and count a remaining request without claiming a slot or sending it."""

from copy import deepcopy
from typing import Any

from novel_writer.generation import chief_context
from novel_writer.generation.budget import request_for, request_preview, validate_capacity
from novel_writer.generation.chief_context import uses_roles as enabled
from novel_writer.generation.content import fingerprint
from novel_writer.generation.novel import role_for
from novel_writer.generation.prompt_templates import render_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile


class InputCapacityError(WorkflowError):
    """A fully prepared request exceeded capacity before any call slot was claimed."""

    def __init__(self, message: str, request: ModelRequest) -> None:
        super().__init__(message)
        self.request = request


def prepare_request(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    author_note: str | None,
    reports: dict[str, Any],
    scope: dict[str, Any],
) -> tuple[ModelRequest, int, dict[str, Any], list[dict[str, Any]]]:
    longform = spec.stage_mode == "longform-v1"
    profile = ProviderProfile.model_validate(snapshot["profile"])
    rendered = deepcopy(snapshot) if longform else snapshot
    counting = (
        snapshot["counting"].get(action)
        or snapshot["counting"]["writer" if role_for(action) == "writer" else "chief"]
    )
    omitted: list[dict[str, Any]] = []
    render_reports = dict(reports)
    while True:
        system, user = render_for(spec, rendered, action, plan, body, author_note, render_reports)
        if "key_context_selection" in render_reports:
            reports["key_context_selection"] = render_reports["key_context_selection"]
        for key in ("prompt_template_source", "prompt_template_revision"):
            if key in render_reports:
                reports[key] = render_reports[key]
        if action == "rewrite" and not enabled(spec):
            user += "\n作者明确改写要求\n" + str(scope["instruction"])
            if not longform:
                user += "\n待改写原稿\n" + (body or "")
        request = request_for(spec, profile, action, system, user)
        capacity_error = None
        try:
            count = validate_capacity(request_preview(request), request, spec, profile, counting)
        except WorkflowError as error:
            capacity_error = error
            count = spec.input_limit + 1
        if capacity_error and chief_context.uses_keys(spec):
            target = render_reports.get("key_context_selection", {}).get("soft_target", 0)
            if target > 0:
                render_reports["_key_material_target"] = target // 2 if target > 2000 else 0
                continue
            raise InputCapacityError(str(capacity_error), request) from capacity_error
        if not longform or capacity_error is None:
            if capacity_error:
                raise InputCapacityError(str(capacity_error), request) from capacity_error
            return request, count, counting, omitted
        context = rendered["context"]
        optional = context.get("relevant_history", [])
        required_revision = (context.get("recent_chapters") or [{}])[-1].get("revision_id")
        summaries = context.get("formal_summaries", [])
        if optional:
            removed = optional.pop(0)
        else:
            removed = next(
                (s for s in summaries if s.get("revision_id") != required_revision), None
            )
            if removed:
                summaries.remove(removed)
        if not removed:
            if capacity_error:
                raise InputCapacityError(str(capacity_error), request) from capacity_error
            return request, count, counting, omitted
        omitted.append(
            {"revision_id": removed.get("revision_id"), "source_sha256": fingerprint(removed)}
        )
