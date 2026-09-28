"""New-preview wire policy; snapshots without this binding keep their exact requests."""

import inspect
import sys
from typing import Any

from novel_writer.generation.budget import option_for
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.generation.novel import role_for
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = "role_output_contract"
REVISION = "role-output-v1"
QUESTION_GUIDANCE = """【作者问题的输出格式】
没有真实阻塞时，questions=[]，question_scopes={}，author_question_reasons={}。
有问题时，这两个对象必须用 questions 内的问题原文作键，逐项一一对应。例如：
{"questions":["需要作者明确的边界是什么？"],
"question_scopes":{"需要作者明确的边界是什么？":"current_unit"},
"author_question_reasons":{"需要作者明确的边界是什么？":{
"kind":"author_boundary_conflict","source":"具体的作者要求或正式资料位置",
"why_blocked":"说明为何当前事件无法在该边界内继续"}}}
示例不是本故事事实；不要照抄示例问题。多个问题必须分别给原因，不能用一份总原因替代。
普通创作选择由你设计；真实缺口或边界冲突须保留，不为了格式通过删除问题或编造事实。
"""


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint(inspect.getsource(sys.modules[__name__]))}


def strict_schema_compatible(schema: Any) -> bool:
    if isinstance(schema, list):
        return all(strict_schema_compatible(s) for s in schema)
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "object" and (
        schema.get("additionalProperties") is not False
        or set(schema.get("required", [])) != set(schema.get("properties", {}))
    ):
        return False
    return all(strict_schema_compatible(v) for v in schema.values())


def prepare_output(
    request: ModelRequest,
    profile: ProviderProfile,
    action: str,
    snapshot: dict[str, Any],
    reports: dict[str, Any],
) -> ModelRequest:
    policy = reports.get(KEY, snapshot.get(KEY))
    if not policy:
        return request
    if policy != binding():
        raise WorkflowError("输出协议版本与冻结授权不符，请重新预览；旧请求不自动升级")
    if role_for(action) == "writer":
        return request
    reports[KEY] = policy
    source = reports.get("prompt_template_source")
    if not isinstance(source, dict):
        source = parse_object(request.user_prompt)
    schema = source.get("output_schema")
    if schema is None and role_for(action) in {"checker", "reader"}:
        reports["output_format"] = {
            "revision": REVISION, "mode": "prompt_only",
            "reason": "本角色接受自由文本报告，沿用原输出合同",
        }
        return request
    if action == "title" and schema is None:
        titles = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3}
        chapter = {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "titles": titles},
            "required": ["id", "titles"],
        }
        name = "chapters" if "chapters" in source else "titles"
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                name: {"type": "array", "items": chapter} if name == "chapters" else titles,
            },
            "required": [name],
        }
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise WorkflowError("当前角色缺少受保护的对象输出合同，未发送请求")
    mode = profile.structured_output_mode
    capabilities = option_for(profile, request.model).structured_output_modes
    required = {"json_object": "json_object", "json_schema": "json_schema_strict"}.get(mode)
    reason = "供应商配置使用提示词格式约束"
    enabled = required is not None
    if capabilities and required not in capabilities:
        enabled, reason = False, "模型已记录的能力不支持所配置的接口格式"
    if enabled and mode == "json_schema" and not strict_schema_compatible(schema):
        enabled, reason = False, "当前合同含动态对象或可选字段，不能无损使用严格 Schema"
    reports["output_format"] = {
        "revision": REVISION,
        "mode": mode if enabled else "prompt_only",
        "reason": "按冻结的供应商配置启用接口格式" if enabled else reason,
    }
    system = request.system_prompt
    if role_for(action) == "chief":
        system += "\n\n" + QUESTION_GUIDANCE
    return request.model_copy(update={
        "system_prompt": system,
        "json_schema": schema if enabled else request.json_schema,
        "skip_structured_output": not enabled,
    })
