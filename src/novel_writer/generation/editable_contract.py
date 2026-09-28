"""Editable appended guidance with frozen defaults and unchanged historical dispatch."""

import inspect
import re
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import (
    configurable_cast,
    craft_templates,
    creative_contract,
    output_contract_v2,
    output_contract_v3,
)
from novel_writer.generation import editable_rules as rules
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.prompt_templates import variant_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.template_catalog import TemplateText
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = configurable_cast.KEY
REVISION = "editable-rules-v1"


def binding() -> dict[str, str]:
    return {
        "revision": REVISION,
        "sha256": fingerprint(
            {
                "modules": [
                    inspect.getsource(m) for m in (sys.modules[__name__], rules, configurable_cast)
                ],
                "base": creative_contract.binding(),
            }
        ),
    }


def checked(snapshot: dict[str, Any], reports: dict[str, Any] | None = None) -> bool:
    policy = (reports or {}).get(KEY, snapshot.get(KEY))
    if policy is None:
        return False
    if policy != binding():
        raise WorkflowError("可编辑指导合同与冻结来源不符，请新建预览；原请求不升级")
    return True


def bind_snapshot(snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    if snapshot.get(creative_contract.KEY):
        snapshot[KEY] = binding()
        configurable_cast.reserve(snapshot, state)


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = creative_contract.contract_for(spec, snapshot)
    if not checked(snapshot or {}):
        return base
    bundle = (snapshot or {}).get("prompt_templates")
    if bundle:
        rules.checked_bundle(bundle)
    return fingerprint({"base": base, KEY: binding()})


def amendment_contract(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    if not checked(snapshot or {}):
        return creative_contract.amendment_contract(spec, snapshot)
    return contract_for(spec, snapshot)


def configure_source(source: dict[str, Any], maximum: int, slots: list[str]) -> dict[str, Any]:
    source = deepcopy(source)
    schema = configurable_cast.CreativePlan.model_json_schema()
    original = source["output_schema"]["properties"]["scenes"]
    schema["properties"]["scenes"].update({k: original[k] for k in ("minItems", "maxItems")})
    schema["properties"]["new_characters"]["maxItems"] = maximum
    schema["$defs"]["AuthorBlocker"]["properties"]["kind"] = {
        "type": "string",
        "const": "author_boundary_conflict",
    }
    source["output_schema"] = schema
    source["creative_autonomy"] = {
        "new_character_slots": slots,
        "maximum_new_characters": maximum,
        "maximum_total_cast": 12,
        "proposals_are_facts": False,
    }
    return source


def raw_render(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system, raw = creative_contract.raw_render(
        spec, snapshot, action, plan, body, author_note, reports
    )
    if not checked(snapshot, reports):
        return system, raw
    # Adapt known built-in wording only. User-authored system text stays verbatim.
    normalized, _ = creative_contract.finish(system, raw, parse_object(raw), action)
    suffix = creative_contract.guidance(action)
    system = normalized.removesuffix("\n\n" + suffix) if suffix else normalized
    if action == "plan":
        source = configure_source(
            parse_object(raw),
            snapshot["new_character_limit"],
            snapshot["new_character_slots"],
        )
        raw = json_text(source)
    return system, raw


def apply_template(
    variant: str,
    template: TemplateText,
    source: dict[str, Any],
    value: rules.ProgramSettings,
) -> tuple[str, str, dict[str, Any]]:
    _, task, info = craft_templates.apply_template(variant, template, source)
    # Replace the generated suffix, never search/replace an author's own wording.
    text = rules.effective(variant, value).texts["engine_system"]
    return template.system_text + ("\n\n" + text if text else ""), task, info


def finish(
    system: str,
    task: str,
    source: dict[str, Any],
    action: str,
    value: rules.ProgramSettings,
) -> tuple[str, str]:
    # Reuse the frozen source attachment without its hardcoded creative text.
    _, task = creative_contract.finish("", task, source, action)
    text = rules.effective(variant_for(action), value).texts.get("creative_guidance", "")
    return system + ("\n\n" + text if text else ""), task


def render_for(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None = None,
    body: str | None = None,
    author_note: str | None = None,
    reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    reports = reports if reports is not None else {}
    if not checked(snapshot, reports):
        return creative_contract.render_for(
            spec, snapshot, action, plan, body, author_note, reports
        )
    system, task = raw_render(spec, snapshot, action, plan, body, author_note, reports)
    source = parse_object(task)
    variant = variant_for(action)
    bundle = reports.get("prompt_templates", snapshot.get("prompt_templates"))
    value = rules.settings(variant, bundle)
    if bundle:
        rules.checked_bundle(bundle)
        text = bundle["templates"].get(variant)
        if text is not None:
            system, task, _ = apply_template(
                variant, TemplateText.model_validate(text), source, value
            )
        elif value.texts.get("engine_system"):
            system += "\n\n" + value.texts["engine_system"]
        reports["prompt_template_revision"] = bundle["revision"]
    reports["prompt_template_source"] = source
    reports[creative_contract.KEY] = reports.get(
        creative_contract.KEY, snapshot.get(creative_contract.KEY)
    )
    reports[KEY] = binding()
    reports[rules.RECEIPT] = {
        variant: rules.effective(variant, value).model_dump(exclude_none=True)
    }
    return finish(system, task, source, action, value)


def prepare_output(
    request: ModelRequest,
    profile: ProviderProfile,
    action: str,
    snapshot: dict[str, Any],
    reports: dict[str, Any],
) -> ModelRequest:
    if not checked(snapshot, reports):
        return creative_contract.prepare_output(request, profile, action, snapshot, reports)
    variant = variant_for(action)
    value = rules.effective(
        variant,
        rules.ProgramSettings.model_validate(reports.get(rules.RECEIPT, {}).get(variant, {})),
    )
    # Build only the generated suffix in the old renderer, keeping author text isolated.
    blank = request.model_copy(update={"system_prompt": ""})
    prepared = creative_contract.prepare_output(blank, profile, action, snapshot, reports)
    suffix = prepared.system_prompt
    replacements = {
        original: value.texts[key]
        for original, key in (
            (creative_contract.QUESTION_GUIDANCE, "question_guidance"),
            (output_contract_v2.CHIEF_LANGUAGE, "chief_language"),
            (output_contract_v3.CHIEF_SCOPE, "chief_scope"),
            (output_contract_v3.WRITER_SCOPE, "writer_scope"),
        )
        if key in value.texts
    }
    if replacements:
        suffix = re.sub(
            "|".join(map(re.escape, replacements)), lambda m: replacements[m[0]], suffix
        )
    return prepared.model_copy(update={"system_prompt": request.system_prompt + suffix})
