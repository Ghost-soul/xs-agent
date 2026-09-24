"""One finite action graph on the existing executor; no historical executors."""

from __future__ import annotations

from typing import Any

from novel_writer.generation.schemas import (
    LONGFORM_PARSER_REVISION,
    LONGFORM_PLAN_PARSER_REVISION,
    LONGFORM_REVISION,
    NOVEL_PARSER_REVISION,
    NOVEL_REVISION,
    PARSER_REVISION,
    REVISION,
    GenerationSpec,
)


def revision_for(spec: GenerationSpec) -> str:
    if spec.stage_mode == "longform-v1":
        return LONGFORM_REVISION
    return NOVEL_REVISION if spec.workflow == "novel-run-v1" else REVISION


def parser_for(revision: str, action: str | None = None) -> str:
    if action is not None and role_for(action) == "memory":
        return "memory-evidence-v4"
    if revision == LONGFORM_REVISION:
        if action is not None and role_for(action) == "chief":
            return LONGFORM_PLAN_PARSER_REVISION
        return LONGFORM_PARSER_REVISION
    return NOVEL_PARSER_REVISION if revision == NOVEL_REVISION else PARSER_REVISION


def role_for(action: str) -> str:
    if ":" in action:
        return {"write": "writer", "memory": "memory", "chief": "chief"}[action.split(":")[0]]
    if action == "reader_early":
        return "reader"
    return {
        "plan": "chief",
        "write": "writer",
        "review": "chief",
        "memory": "memory",
        "checker": "checker",
        "reader": "reader",
        "editor": "editor",
        "title": "editor",
        "rewrite": "writer",
        "amend": "editor",
        "memory_edit": "memory",
        "checker_edit": "checker",
        "memory_amend": "memory",
        "checker_amend": "checker",
        "reader_amend": "reader",
    }[action]


def slots_for(spec: GenerationSpec) -> list[str]:
    advisory = spec.workflow == "novel-run-v1" and spec.feedback_policy in {
        "advisory-v1",
        "logic-v1",
    }
    if spec.stage_mode == "longform-v1":
        slots = ["plan"]
        for n in range(1, spec.unit_limit + 1):
            slots += [f"write:{n}", f"memory:{n}"]
        if spec.unit_limit > 1:
            slots += (
                (["chief:1"] if spec.milestone_unit else [])
                if advisory
                else ["reader_early", "chief:1", "chief:2"]
            )
        if not advisory or spec.enable_checker:
            slots += ["checker"]
        if (
            spec.enable_editor
            and spec.feedback_policy != "logic-v1"
            and (not advisory or spec.enable_checker)
        ):
            slots += ["editor", "memory_edit", "checker_edit"]
        if not advisory or spec.enable_reader:
            slots += ["reader"]
        if spec.generate_title:
            slots += ["title"]
        return slots
    if spec.workflow != "novel-run-v1":
        return ["plan", "write", "review"]
    slots = ["plan", "write", "memory", "checker"]
    if advisory and not spec.enable_checker:
        slots.remove("checker")
    if (
        spec.enable_editor
        and spec.feedback_policy != "logic-v1"
        and (not advisory or spec.enable_checker)
    ):
        slots += ["editor", "memory_edit", "checker_edit"]
    if not advisory or spec.enable_reader:
        slots += ["reader"]
    if spec.generate_title:
        slots += ["title"]
    return slots


def model_for(spec: GenerationSpec, action: str) -> tuple[str, int, str | None]:
    role = role_for(action)
    if role in spec.roles:
        config = spec.roles[role]  # type: ignore[index]
        return config.model, config.output_limit, config.tokenizer_id
    if role == "writer":
        return spec.writer_model, spec.writer_output_limit, spec.writer_tokenizer_id
    if role != "chief" and spec.auxiliary_output_limit is not None:
        return spec.chief_model, spec.auxiliary_output_limit, spec.chief_tokenizer_id
    return spec.chief_model, spec.chief_output_limit, spec.chief_tokenizer_id


def next_action(
    slots: list[str], completed: str, *, editable: bool = False, changed: bool = True
) -> str | None:
    remaining = slots[slots.index(completed) + 1 :]
    if completed == "checker" and not editable:
        remaining = [a for a in remaining if a not in {"editor", "memory_edit", "checker_edit"}]
    if completed == "editor" and not changed:
        remaining = [a for a in remaining if a not in {"memory_edit", "checker_edit"}]
    return remaining[0] if remaining else None


def invalidate_candidate(state: dict[str, Any]) -> dict[str, Any]:
    stale = {
        f"{kind}_id"
        for kind in (
            "memory",
            "checker",
            "review",
            "title",
            "segments",
            "dependency_skip",
            "early_review",
        )
    }
    return {k: v for k, v in state.items() if k not in stale}
