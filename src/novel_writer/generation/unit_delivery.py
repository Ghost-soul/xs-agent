"""Equal unit references and substantial outcomes, isolated from frozen contracts."""

import inspect
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import creative_contract, editable_rules, unit_delivery_rules
from novel_writer.generation import editable_contract as previous
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.craft_models import enabled
from novel_writer.generation.prompt_templates import variant_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.template_catalog import TemplateText
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = "unit_delivery_contract"
REVISION = "balanced-units-v1"


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint({
        "source": inspect.getsource(sys.modules[__name__]),
        "rules": inspect.getsource(unit_delivery_rules), "base": previous.binding(),
    })}


def checked(snapshot: dict[str, Any], reports: dict[str, Any] | None = None) -> bool:
    policy = (reports or {}).get(KEY, snapshot.get(KEY))
    if policy is None:
        return False
    if policy != binding():
        raise WorkflowError("单元产出合同与冻结授权不符，请重新预览；原请求不升级")
    return True


def bind_snapshot(spec: GenerationSpec, snapshot: dict[str, Any]) -> None:
    if enabled(spec) and spec.stage_mode == "longform-v1":
        snapshot[KEY] = binding()


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = previous.contract_for(spec, snapshot)
    return fingerprint({"base": base, KEY: binding()}) if checked(snapshot or {}) else base


def amendment_contract(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    if not checked(snapshot or {}):
        return previous.amendment_contract(spec, snapshot)
    return contract_for(spec, snapshot)


def reference(scale: dict[str, Any], count: int, ordinal: int) -> dict[str, int]:
    if not 1 <= ordinal <= count <= 6:
        raise WorkflowError("单元位置与有效计划不符，无法分配篇幅")
    # Cumulative integer boundaries distribute remainders without losing the total.
    return {
        key: scale[key] * ordinal // count - scale[key] * (ordinal - 1) // count
        for key in ("min_characters", "max_characters")
    }


def project_source(source: dict[str, Any], action: str) -> dict[str, Any]:
    if action != "plan" and not action.startswith("write:"):
        return source
    if source.get("stage_scale", {}).get("scale_mode") != "stage-range":
        return source
    source = deepcopy(source)
    scale = source["stage_scale"]
    scale.update(allocation_policy="equal-units-v1", unit_kind="阶段性产出")
    if action == "plan":
        scale["planning_unit_reference"] = reference(scale, scale["preferred_units"], 1)
        fields = source["output_schema"]["$defs"]["CraftUnit"]["properties"]
        fields["size_weight"]["description"] = "旧结构兼容字段，新计划填 1；篇幅按有效单元数均分。"
        fields["event"]["description"] = "本轮完整行动的起点、推进与结果，可包含多个连续场景。"
        fields["consequence"]["description"] = (
            "本单元完成后的具体变化，是下一步的条件，不只为以后铺垫。"
        )
    else:
        position = source.get("stage_context", {}).get("unit_position", {})
        ordinal = int(action.split(":")[1])
        count = position.get("total")
        if type(count) is not int or position.get("current") != ordinal:
            raise WorkflowError("当前单元缺少绑定的计划位置，无法分配篇幅")
        scale["current_unit_reference"] = reference(scale, count, ordinal)
    return source


def raw_render(
    spec: GenerationSpec, snapshot: dict[str, Any], action: str,
    plan: dict[str, Any] | None = None, body: str | None = None,
    author_note: str | None = None, reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    system, raw = previous.raw_render(spec, snapshot, action, plan, body, author_note, reports)
    if not checked(snapshot, reports):
        return system, raw
    source = project_source(parse_object(raw), action)
    if action == "plan" and source.get("stage_scale", {}).get("scale_mode") == "stage-range":
        # This is the built-in base only; never rewrite a saved author's system text.
        system = system.replace(
            "size_weight 只表达展开份量，没有固定幕数、反转配额或关系进度。允许不发生代价或反转。",
            "各单元按阶段性产出组织，篇幅均衡；size_weight 填 1。"
            "没有固定幕数、反转配额或关系进度。",
        )
    return system, json_text(source)


def render_for(
    spec: GenerationSpec, snapshot: dict[str, Any], action: str,
    plan: dict[str, Any] | None = None, body: str | None = None,
    author_note: str | None = None, reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    reports = reports if reports is not None else {}
    if not checked(snapshot, reports):
        return previous.render_for(spec, snapshot, action, plan, body, author_note, reports)
    system, task = raw_render(spec, snapshot, action, plan, body, author_note, reports)
    source = parse_object(task)
    variant = variant_for(action)
    bundle = reports.get("prompt_templates", snapshot.get("prompt_templates"))
    value = unit_delivery_rules.effective(variant, editable_rules.settings(variant, bundle))
    if bundle:
        editable_rules.checked_bundle(bundle)
        text = bundle["templates"].get(variant)
        if text is not None:
            system, task, _ = previous.apply_template(
                variant, TemplateText.model_validate(text), source, value,
            )
        elif value.texts.get("engine_system"):
            system += "\n\n" + value.texts["engine_system"]
        reports["prompt_template_revision"] = bundle["revision"]
    reports.update({
        "prompt_template_source": source,
        creative_contract.KEY: reports.get(
            creative_contract.KEY, snapshot.get(creative_contract.KEY),
        ),
        previous.KEY: previous.binding(), KEY: binding(),
        editable_rules.RECEIPT: {variant: value.model_dump(exclude_none=True)},
    })
    return previous.finish(system, task, source, action, value)


def prepare_output(
    request: ModelRequest, profile: ProviderProfile, action: str,
    snapshot: dict[str, Any], reports: dict[str, Any],
) -> ModelRequest:
    if checked(snapshot, reports):
        reports[KEY] = binding()
        variant = variant_for(action)
        value = editable_rules.ProgramSettings.model_validate(
            reports.get(editable_rules.RECEIPT, {}).get(variant, {}),
        )
        reports[editable_rules.RECEIPT] = {
            variant: unit_delivery_rules.effective(variant, value).model_dump(exclude_none=True),
        }
    return previous.prepare_output(request, profile, action, snapshot, reports)
