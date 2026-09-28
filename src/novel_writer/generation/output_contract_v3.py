"""Event scope and explicit Writer scale for new requests; v1/v2 stay frozen."""

import inspect
import sys
from copy import deepcopy
from typing import Any

from novel_writer.generation import output_contract_v2 as v2
from novel_writer.generation.content import fingerprint, parse_object
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = v2.KEY
REVISION = "role-output-v3"
CHIEF_SCOPE = """【事件容量与阶段推进】
阶段篇幅是作者要求的展开规模，规划时必须让事件容量与之相称。优先按首选单元数组织；
减少单元数意味着每个单元承载更完整的事件，不意味着整个阶段缩成开场片段。
先确定本阶段实际完成的重要行动、人物回应和局面变化，再将连续过程组织成单元。
event 写清起点到落点；choice_and_response 写出行动触发怎样的回应、人物如何据此调整；
development.onstage_process 指出值得现场展开的具体交锋、尝试或互动；consequence 写明预期变化。
仅抵达、观察、分析条件或等待一个短时间窗口，通常不足以独立承载数千字；
把同一事件的准备、实质行动、对方回应与直接结果放在同一单元，避免将关键行动都留给下一单元。
安静的关系互动也可以构成完整事件，但需要具体交流、选择与关系或认知的变化。
在正式条件、作者边界和选定叙事内增加有因果联系的过程，不靠无关支线、强制反转或危险凑规模。
计划中的局部实现办法为 Writer 留出自由；本说明不增加输出字段或文学验收表。
"""
WRITER_SCOPE = """【完整展开当前事件】
把本单元目标篇幅用于展开有效计划里的真实过程，不把它当作可以忽略的输出上限。
从开场行动写到计划落点：人物实际尝试，对方自主回应，新信息或条件影响下一步，
人物据此选择或调整，直到本单元的直接结果及必要余波发生。
主要交锋与互动在现场呈现，用动作、对白、观察与即时感受形成相互影响的过程；
不能只用几段分析、打算或事后概述代替重要行动，也不要在第一次接触或刚取得机会时提前结束。
输出前自行对照当前事件的落点与目标篇幅；若只有开场，应继续展开本单元尚未完成的过程再交付。
避免重复内心判断、解释同一条规则、堆积景物或总结填字；保持人物声音、作者视角与文风。
不得为凑篇幅抢写后续单元、越过作者边界或改写既成事实。作者对当前范围的明确限制优先。
只输出正文，不输出检查过程、字数声明、结束标记或创作说明。
"""


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint({
        "source": inspect.getsource(sys.modules[__name__]), "base": v2.binding(),
    })}


def prepare_output(
    request: ModelRequest,
    profile: ProviderProfile,
    action: str,
    snapshot: dict[str, Any],
    reports: dict[str, Any],
) -> ModelRequest:
    policy = reports.get(KEY, snapshot.get(KEY))
    if not policy or policy.get("revision") != REVISION:
        return v2.prepare_output(request, profile, action, snapshot, reports)
    if policy != binding():
        raise WorkflowError("输出协议版本与冻结授权不符，请重新预览；旧请求不自动升级")
    local = {**reports, KEY: v2.binding()}
    prepared = v2.prepare_output(request, profile, action, snapshot, local)
    reports[KEY] = policy
    reports["output_format"] = {
        **local.get("output_format", {"mode": "prompt_only", "reason": "正文沿用文本输出"}),
        "revision": REVISION,
    }
    # Revision actions keep their own scope; natural-size mode gains no numeric target.
    if action != "plan" and action != "write" and not action.startswith("write:"):
        return prepared
    source = reports.get("prompt_template_source")
    if not isinstance(source, dict):
        source = parse_object(request.user_prompt)
    scale = source.get("stage_scale", {})
    if scale.get("scale_mode") != "stage-range":
        return prepared
    if action == "plan":
        scope = (
            f"本阶段目标 {scale['min_characters']:,}–{scale['max_characters']:,} 字，"
            f"首选 {scale['preferred_units']} 个单元，授权上限 "
            f"{scale['authorized_unit_limit']} 个单元。\n" + CHIEF_SCOPE
        )
    else:
        target = scale.get("current_unit_reference")
        if not target:
            return prepared
        reports["writer_scale"] = deepcopy(scale)
        scope = (
            f"【本次 Writer 交付规模】当前单元目标 "
            f"{target['min_characters']:,}–{target['max_characters']:,} 字。"
            "计数为正文非空白字符，包含标点，不是 token。\n"
            f"阶段目标 {scale['min_characters']:,}–{scale['max_characters']:,} 字；"
            f"此前已写 {scale.get('written_characters', 0):,} 字，"
            f"本单元之后还剩 {scale.get('remaining_units', 0)} 个单元。\n" + WRITER_SCOPE
        )
    return prepared.model_copy(update={"system_prompt": prepared.system_prompt + "\n\n" + scope})
