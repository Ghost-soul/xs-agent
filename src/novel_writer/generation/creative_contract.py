"""Versioned creative autonomy without altering any published rendering source."""

import inspect
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import (
    craft_contract,
    craft_templates,
    creative_cast,
    output_contract_v3,
)
from novel_writer.generation.content import fingerprint, json_text, parse_object
from novel_writer.generation.craft_models import enabled as craft_enabled
from novel_writer.generation.novel import role_for
from novel_writer.generation.output_contract import QUESTION_GUIDANCE as ORIGINAL_QUESTION_GUIDANCE
from novel_writer.generation.prompt_templates import variant_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.template_catalog import TemplateText
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = creative_cast.KEY
REVISION = "creative-autonomy-v1"
QUESTION_GUIDANCE = ORIGINAL_QUESTION_GUIDANCE.replace(
    "普通创作选择由你设计；真实缺口或边界冲突须保留，不为了格式通过删除问题或编造事实。",
    "资料空白与普通创作选择自主处理；未知历史不冒充既成事实。"
    "只有无法同时遵守的明确作者要求才列作者问题，来源须引用作者原话。",
)
AUTONOMY = """【本阶段创作自主范围】
没有写明的动机、配角、场景细节和未来关系发展属于创作空间，
由 Chief／Writer 在正式事实和作者边界内设计。
不把“尚未建立关系”“没有合适人物”“当前事件不便展开”当作需要作者填资料的审批事项。
新经历可以产生新关系；已有身份、已经发生的事情和作者明确限制仍有效，不倒改过去来凑条件。
资料不足时，不声称未知的历史事实已经成立；选择无需该事实的事件路径，或把未知保留为人物尚不知情。
卡片的适用前提与故事未匹配是创作安排问题，不等于作者禁止，也不单独构成暂停理由。
questions 仅保留无法同时遵守的明确作者要求，使用 author_boundary_conflict 并引用作者原话。
资料空白及普通剧情选择不列 questions，也不要求作者确认；无明确边界冲突时三个问题字段均为空。
creative_notes 是设计时的不确定事项，既非正式事实也非必须答复的问题。按上述原则自主处理。
"""
CHIEF = """【适量新增人物】
优先使用适合当前事件的已有角色；确有叙事需要时，在 new_characters 中设计少量新人物，最多三人，
不是必须凑满。每人使用 creative_autonomy.new_character_slots 中一个未用 ID，填写姓名、身份特点、
独立目标、声音和合理出场原因，再将这个 ID 放入相关 scenes[].character_ids。
正式人物 ID 不变；新增人物也计入全阶段最多十二位承载人物，作者固定人物仍须覆盖。
新人物需要自己的目标与选择、可信的进入路径，不只为触发事件临时出现；不得替换或冒充已有角色。
初次出场时通过正文自然建立其身份、关系与作用；计划中的设想不能假装是正式库已经记载的历史。
姓名避免与已有角色、别名混淆。必要关系或背景写入事件过程，不能直接覆盖旧人物的正式关系。
没有新增人物时 new_characters=[]。
"""
WRITER = """【新人物候选与正式事实】
character_proposals 是本阶段计划中的新人物设计，尚不代表已发生事实；按当前单元自然引入并展开。
每人的 ID 贯穿本阶段，避免同名变成多人或更换身份。若已验证接力中存在此 ID，接续已写状态，
不要每个单元重复介绍或以原候选设想覆盖正文中的实际变化。不增设计划外的主要承载者。
"""
MEMORY = """【新人物身份接力】
character_proposals 只提供候选人物的 ID 和姓名以识别对象，不是事实证据。
只有当前正文明确写出的新人物，才在 changes 中创建 characters；使用给定 ID 作为 object_id，
并填写正文支持的领域字段及 paragraph_ids。给定 ID 已在开场状态中存在时按正常更新处理。
先声明人物，再引用其 ID 创建关系、事件或认知；新 ID 只能用于给定的新人物，不能用于其他类型。
未出场或缺少正文证据的人物不创建，不抄计划中的动机、关系、声音等设想为已发生事实。
"""


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint({
        "renderer": inspect.getsource(sys.modules[__name__]),
        "cast": inspect.getsource(creative_cast),
    })}


def bind_snapshot(spec: GenerationSpec, snapshot: dict[str, Any], state: dict[str, Any]) -> None:
    if craft_enabled(spec):
        snapshot[KEY] = binding()
        creative_cast.reserve(snapshot, state)


def checked(snapshot: dict[str, Any], reports: dict[str, Any] | None = None) -> bool:
    policy = (reports or {}).get(KEY, snapshot.get(KEY))
    if policy is None:
        return False
    if policy != binding():
        raise WorkflowError("创作自主合同与冻结来源不符，请新建预览；原请求不升级")
    return True


def contract_for(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    base = craft_contract.contract_for(spec, snapshot)
    if not (snapshot or {}).get(KEY):
        return base
    checked(snapshot or {})
    return fingerprint({"base": base, KEY: binding()})


def amendment_contract(spec: GenerationSpec, snapshot: dict[str, Any] | None = None) -> str:
    if not (snapshot or {}).get(KEY):
        return craft_contract.amendment_contract(spec, snapshot)
    return contract_for(spec, snapshot)


def raw_render(
    spec: GenerationSpec, snapshot: dict[str, Any], action: str,
    plan: dict[str, Any] | None = None, body: str | None = None,
    author_note: str | None = None, reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    reports = reports if reports is not None else {}
    system, raw = craft_contract.raw_render(
        spec, snapshot, action, plan, body, author_note, reports,
    )
    if not checked(snapshot, reports):
        return system, raw
    payload = parse_object(raw)
    role = role_for(action)
    if action == "plan":
        schema = creative_cast.CreativePlan.model_json_schema()
        # Preserve this preview's longform/single-unit cardinality contract.
        original = payload["output_schema"]["properties"]["scenes"]
        schema["properties"]["scenes"].update({k: original[k] for k in ("minItems", "maxItems")})
        schema["$defs"]["AuthorBlocker"]["properties"]["kind"] = {
            "type": "string", "const": "author_boundary_conflict",
        }
        payload["output_schema"] = schema
        payload["creative_autonomy"] = {
            "new_character_slots": snapshot["new_character_slots"],
            "maximum_new_characters": creative_cast.MAX_NEW,
            "maximum_total_cast": 12,
            "proposals_are_facts": False,
        }
    proposals = (plan or {}).get("new_characters", [])
    if role == "writer":
        current = payload.get("plot_execution", {}).get("current_task")
        scenes = current if isinstance(current, list) else [current] if current else []
        ids = {i for scene in scenes for i in scene.get("character_ids", [])}
        payload["character_proposals"] = [deepcopy(p) for p in proposals if p["id"] in ids]
        payload["creative_notes"] = deepcopy((plan or {}).get("creative_notes", []))
    elif role == "memory":
        payload["character_proposals"] = [{"id": p["id"], "name": p["name"]} for p in proposals]
    return system, json_text(payload)


def guidance(action: str) -> str:
    if action == "plan":
        return AUTONOMY + "\n" + CHIEF
    if action == "write" or action.startswith("write:"):
        return AUTONOMY + "\n" + WRITER
    if role_for(action) == "memory":
        return MEMORY
    return ""


def finish(
    system: str, task: str, source: dict[str, Any], action: str,
) -> tuple[str, str]:
    # This compatibility wording is changed only in the new outer contract.
    system = system.replace(
        "普通剧情选择自主解决；只有明确事实缺口或作者边界冲突才列作者问题并说明来源与阻塞原因。",
        "资料空白与普通剧情选择自主处理；仅无法同时遵守的明确作者要求列为作者问题。",
    )
    system = system.replace(
        "人物从 allowed_ids 选取且覆盖 required_ids，人物声音、既成事实和作者边界保持。",
        "已有角色从 allowed_ids 选取，新增人物只使用预留 ID；覆盖 required_ids，"
        "人物声音、既成事实和作者边界保持。",
    )
    extra = {k: source[k] for k in ("creative_autonomy", "character_proposals", "creative_notes")
             if k in source}
    try:
        contains_source = parse_object(task) == source
    except ValueError:
        contains_source = False
    if extra and not contains_source:
        task += "\n\n【创作自主范围与候选设计】\n" + json_text(extra)
    text = guidance(action)
    return (system + "\n\n" + text if text else system), task


def render_for(
    spec: GenerationSpec, snapshot: dict[str, Any], action: str,
    plan: dict[str, Any] | None = None, body: str | None = None,
    author_note: str | None = None, reports: dict[str, Any] | None = None,
) -> tuple[str, str]:
    reports = reports if reports is not None else {}
    if not checked(snapshot, reports):
        return craft_contract.render_for(spec, snapshot, action, plan, body, author_note, reports)
    system, task = raw_render(spec, snapshot, action, plan, body, author_note, reports)
    source = parse_object(task)
    bundle = reports.get("prompt_templates", snapshot.get("prompt_templates"))
    if bundle:
        craft_templates.checked_bundle(bundle)
        text = bundle["templates"].get(variant_for(action))
        if text is not None:
            system, task, _ = craft_templates.apply_template(
                variant_for(action), TemplateText.model_validate(text), source,
            )
            reports["prompt_template_revision"] = bundle["revision"]
    reports["prompt_template_source"] = source
    reports[KEY] = snapshot[KEY]
    return finish(system, task, source, action)


def prepare_output(
    request: ModelRequest, profile: ProviderProfile, action: str,
    snapshot: dict[str, Any], reports: dict[str, Any],
) -> ModelRequest:
    prepared = output_contract_v3.prepare_output(request, profile, action, snapshot, reports)
    if action != "plan" or not checked(snapshot, reports):
        return prepared
    # The final wire guidance must agree with the new nonblocking-gap contract.
    return prepared.model_copy(update={
        "system_prompt": prepared.system_prompt.replace(
            ORIGINAL_QUESTION_GUIDANCE, QUESTION_GUIDANCE,
        ),
    })
