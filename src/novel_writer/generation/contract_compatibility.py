"""Exact compatibility for a published diagnostic-only request-builder change.

The request builder is part of plot-led-v3's source fingerprint. On 2026-09-28
only its over-capacity exception message changed. Both complete source digests
are pinned here; no arbitrary normalization or process-global monkeypatching is
allowed. Saved sources, requests and authorization hashes are never rewritten.
"""

import inspect
from typing import Any

from novel_writer.generation import (
    budget,
    craft_context,
    craft_contract,
    craft_models,
    craft_prompts,
    craft_templates,
    creative_contract,
    editable_contract,
    event_units,
    format_trial,
    narrative_drive,
    progression_contract,
    prompt_templates,
    reliability_contract,
    stage_scale,
    template_catalog,
    unit_delivery,
)
from novel_writer.generation.content import digest, fingerprint
from novel_writer.generation.schemas import GenerationSpec

CURRENT_BUILDER_SHA = "73c04690a22af5386058f62ac952907a4ff82c3ddba9d63317e5469bc41e97e5"
LEGACY_BUILDER_SHA = "f91d8026d8daac30b428c4a316aef3f6f4124d42101d6d74e766460656bade87"
CURRENT_ERROR = '''        raise WorkflowError(
            f"{role_for(action)} 模型 {model} 本次输出上限 {output:,} tokens "
            f"超过模型能力（端点配置上限 {option.max_output_tokens or 0:,} tokens）；"
            "请在创作设置中按模型容量调整后重新预览，不会静默修改或发送请求"
        )'''
LEGACY_ERROR = '        raise WorkflowError("本次输出容量超过模型能力，不会静默调整")'


def legacy_builder_source() -> str | None:
    current = inspect.getsource(budget.request_for)
    if digest(current) != CURRENT_BUILDER_SHA:
        return None
    legacy = current.replace(CURRENT_ERROR, LEGACY_ERROR, 1)
    return legacy if digest(legacy) == LEGACY_BUILDER_SHA else None


def _recipe(spec: GenerationSpec, snapshot: dict[str, Any], builder: str) -> str:
    """Replay this published hash recipe; actual rendering still uses its modules.

    The caller first checks this recipe with the CURRENT builder against the
    public dispatcher. Any future recipe drift disables this compatibility.
    """
    base = fingerprint({
        "base": narrative_drive.contract_for(event_units.previous_spec(spec)),
        "event_units": inspect.getsource(event_units),
        "request_builder": builder,
    })
    bundle = snapshot.get("prompt_templates")
    if craft_models.enabled(spec):
        if bundle:
            craft_templates.checked_bundle(bundle)
        base = fingerprint({
            "family": craft_models.POLICY,
            "base": base,
            "modules": [inspect.getsource(m) for m in (
                craft_contract, craft_models, craft_context, craft_prompts,
                craft_templates, stage_scale,
            )],
            "schema": craft_models.CraftPlan.model_json_schema(),
            "templates": bundle,
        })
    elif bundle is not None:
        prompt_templates.checked_bundle(bundle)
        base = fingerprint({
            "base": base, "bundle": bundle,
            "renderer": inspect.getsource(prompt_templates),
            "catalog": inspect.getsource(template_catalog),
        })
    # The public dispatcher has already checked each binding and template bundle.
    for layer in (creative_contract, editable_contract, unit_delivery, reliability_contract):
        if snapshot.get(layer.KEY) is not None:
            base = fingerprint({"base": base, layer.KEY: layer.binding()})
    if format_trial.KEY in snapshot:
        base = fingerprint({"base": base, format_trial.KEY: format_trial.binding()})
    if snapshot.get(progression_contract.KEY) is not None:
        base = fingerprint({"base": base, progression_contract.KEY: progression_contract.binding()})
    return base


def matches(
    spec: GenerationSpec, snapshot: dict[str, Any], stored: Any, *, amendment: bool = False,
) -> bool:
    current = (
        reliability_contract.amendment_contract(spec, snapshot) if amendment
        else progression_contract.contract_for(spec, snapshot)
    )
    if stored == current:
        return True
    if not isinstance(stored, str) or not event_units.enabled(spec):
        return False
    legacy = legacy_builder_source()
    if legacy is None:
        return False
    if _recipe(spec, snapshot, inspect.getsource(budget.request_for)) != current:
        return False
    return _recipe(spec, snapshot, legacy) == stored
