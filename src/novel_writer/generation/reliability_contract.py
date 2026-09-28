"""Versioned structural guidance and capability fallback for new previews only."""

import inspect
import sys
from typing import Any

from novel_writer.generation import unit_delivery as previous
from novel_writer.generation.budget import option_for
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.craft_models import enabled
from novel_writer.generation.novel import role_for
from novel_writer.generation.output_contract import strict_schema_compatible
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = "reliability_contract"
REVISION = "structured-delivery-v1"


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint({
        "source": inspect.getsource(sys.modules[__name__]), "base": previous.binding(),
    })}


def checked(snapshot: dict[str, Any], reports: dict[str, Any] | None = None) -> bool:
    policy = (reports or {}).get(KEY, snapshot.get(KEY))
    if policy is None:
        return False
    if policy != binding():
        raise WorkflowError("可靠输出合同与冻结授权不符；原请求不能自动升级")
    return True


def bind_snapshot(spec: GenerationSpec, snapshot: dict[str, Any]) -> None:
    previous.bind_snapshot(spec, snapshot)
    if enabled(spec):
        snapshot[KEY] = binding()


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = previous.contract_for(spec, snapshot)
    return fingerprint({"base": base, KEY: binding()}) if checked(snapshot or {}) else base


def amendment_contract(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    return contract_for(spec, snapshot) if checked(snapshot or {}) else previous.amendment_contract(
        spec, snapshot,
    )


render_for = previous.render_for
raw_render = previous.raw_render


def required_example(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """A compact shape example from the very schema the compiler consumes."""
    if "$ref" in schema:
        return required_example(root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root)
    if "anyOf" in schema:
        return required_example(next(s for s in schema["anyOf"] if s.get("type") != "null"), root)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        fields = schema.get("properties", {})
        return {key: required_example(fields[key], root) for key in schema.get("required", [])}
    if kind == "array":
        return [required_example(schema.get("items", {}), root)]
    if kind == "boolean":
        return False
    if kind in {"integer", "number"}:
        return schema.get("minimum", 1)
    return "填写本次实际内容" if schema.get("format") != "uuid" else "从本次允许范围复制完整ID"


def prepare_output(
    request: ModelRequest, profile: ProviderProfile, action: str,
    snapshot: dict[str, Any], reports: dict[str, Any],
) -> ModelRequest:
    prepared = previous.prepare_output(request, profile, action, snapshot, reports)
    if not checked(snapshot, reports):
        return prepared
    reports[KEY] = binding()
    if role_for(action) == "writer":
        return prepared
    source = reports.get("prompt_template_source")
    if not isinstance(source, dict):
        source = parse_object(request.user_prompt)
    schema = source.get("output_schema") or prepared.json_schema
    if not schema or schema.get("type") != "object":
        return prepared
    capabilities = option_for(profile, request.model).structured_output_modes
    configured = profile.structured_output_mode
    mode = "prompt_only"
    reason = "仅使用提示词约束；没有已确认可用的接口格式"
    if configured == "json_schema" and "json_schema_strict" in capabilities:
        if strict_schema_compatible(schema):
            mode, reason = "json_schema", "使用已记录支持的严格 JSON Schema"
        else:
            reason = "当前结构含动态字段或可选项，严格 Schema 不适用"
    if mode == "prompt_only" and configured != "prompt_only" and "json_object" in capabilities:
        mode, reason = "json_object", "使用已记录支持的 JSON Object；必要字段仍由本地验证"
    reports["output_format"] = {"revision": REVISION, "mode": mode, "reason": reason}
    example = required_example(schema, schema)
    if role_for(action) == "memory" and isinstance(example.get("position"), dict):
        position_schema = schema.get("$defs", {}).get("NarrativePosition", {}).get("properties", {})
        for key in ("current_location", "recent_major_event"):
            if key in position_schema:
                example["position"][key] = required_example(position_schema[key], schema)
        example.update(changes=[], unresolved=[])
    guidance = (
        "【结构化交付】只输出一个完整 JSON 对象，不加代码围栏或结束标记。"
        "output_schema 是唯一字段合同；以下由同一 Schema 生成的是必填结构示例，"
        "不是故事内容或可照抄的 ID，示例中的数组长度也不是单元数量要求。"
        "可选字段仅按需要填写，输入中的来源说明和其他资料不要作为输出字段回传。\n"
        + json_text(example)
    )
    if role_for(action) == "chief":
        guidance += (
            "\n每个 scenes 单元均须填写 character_ids，逐项从本次候选或已声明新增人物的"
            "允许 ID 中选择，不能用人名代替、随意生成 ID 或遗漏整个字段。"
            "major_turn 填写本阶段实际变化；没有转折可明确说明，不强造戏剧性。"
        )
    if role_for(action) == "memory":
        guidance += (
            "\n只提取本次处理正文有证据的接力与变化。unresolved 放在顶层，"
            "不能嵌入 position；reference_boundary 是输入的只读来源说明，无需输出。"
            "计划、猜测及尚未发生的内容保留为待定，不写入已发生事实。"
        )
    reports["structured_delivery"] = {
        "schema_sha256": fingerprint(schema), "required_example": example, "guidance": guidance,
    }
    return prepared.model_copy(update={
        "system_prompt": prepared.system_prompt + "\n\n" + guidance,
        "json_schema": schema if mode != "prompt_only" else request.json_schema,
        "skip_structured_output": mode == "prompt_only",
    })


def dispatch_profile(profile: ProviderProfile, request: dict[str, Any]) -> ProviderProfile:
    if not checked(request):
        return profile
    mode = request.get("output_format", {}).get("mode")
    if mode in {"json_schema", "json_object", "prompt_only"}:
        return profile.model_copy(update={"structured_output_mode": mode})
    return profile
