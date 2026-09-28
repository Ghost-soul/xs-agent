"""Editable defaults for forward-moving events; released defaults stay frozen."""

from novel_writer.generation import editable_rules
from novel_writer.generation import unit_delivery_rules as previous

CHIEF_SCOPE = """【事件容量、均衡单元与连续推进】
保留 stage_scale 的阶段目标与首选单元数，按有效单元数均分篇幅，size_weight 填 1。
先设计本阶段真正发生的行动与变化，使事件容量足以支撑目标，再划分规模相近的完整单元。
每个单元从上一个单元已经造成的局面出发，完成下一轮行动，结束在新的具体结果上。
同一次交涉、拦截、递交或等待窗口中的并行动作，通常合并为一个单元；
不能换一个人物视角，就把同一段时间、同一组动作与同一结果再安排为下一单元。
event 写清开场条件、行动目标、实际推进和结束位置；choice_and_response 写出
各方带着不同目标采取行动、怎样改变对方条件、人物如何据此调整或作出取舍。
development.onstage_process 写值得现场展开的实质过程，而非站位、脚步与气氛清单；
consequence 写明本单元已经产生的资源、处境、关系、认知或行动条件变化，供下一单元承接。
development.payoff_or_aftermath 安排结果怎样被人物承受、利用或传递，让情绪与关系有展开空间。
不要将整个阶段困在反复悬笔、观察、解释同一规则或等待几息的局面。
需要更多容量时，在作者范围内设计相互因果关联的尝试、对方应对、办法调整及后果，
允许必要的时间与地点推进，使本阶段实际完成关键行动，不把它们全部推给下一阶段。
安静的关系互动同样可以成为完整单元，重点是交流如何改变双方的决定与后续行动。
相邻单元若共享同一个结束结果，应合并重复过程，重新安排后续实质行动；
准备、入场和了解规则服务于实际行动，不能单靠“为以后铺垫”或主题宣告承载数千字。
叙事卡通过人物目标、选择、回应和代价改变事件链；卡名与创作术语不作为人物台词或正文总结。
Writer 对场面与实现办法保有自由。遵守正式事实与作者边界，不强添无关支线、固定反转、
危险或关系进度，不新增文学评分、逐项验收或输出字段。
"""

WRITER_SCOPE = """【展开完整事件并接续已经发生的结果】
按本次目标篇幅充分展开当前单元，可包含多个连续场景，保持各单元篇幅近似均衡。
先分清资料性质：当前计划是要实现的事件，连续原文与已验证接力说明已经发生了什么，
正式起点是阶段开始时的状态；不能用旧开场或尚未兑现的计划覆盖正文已经造成的变化。
从最新正文结束的位置、动作和结果继续。当前计划若部分已在上一单元发生，
承接该结果，展开尚未完成的行动与必要余波，不重新演一遍已完成的递交、确认、会面或决定。
以 event、choice_and_response、development 和 consequence 组织现场过程：人物尝试，
他者按自己的目标回应，新的信息与条件影响下一步，人物调整办法、作出取舍并承担结果。
对白、动作、观察与即时感受应带来新的信息、选择、关系回应或局面变化；
可以充分停留于有变化的互动，不靠反复描述同一站位、景物、规则或心理结论延长时间。
不要在进门、第一次对话、看见线索或决定行动时收束，继续展开当前单元尚未完成的实质过程。
已经签字、取得文件或达成约定后，后文从该结果出发；若有撤回或失败，写出新的原因和过程，
不能无缘无故回到签字前、再次取出同一附件或重新达成同一约定。
连续原文如有重复段落，不将其理解为多次发生，不模仿其循环，也不补造哪次才是真的；
依据有支持的最新结果继续，保留尚不确定的信息。
目标篇幅用于增加有因果作用的过程与结果消化。场面结束后，在授权范围内推进到下一步，
不以同义改写、复制段落或倒退状态凑字；完整行动与必要余波写完后自然收束。
题材与叙事标签是设计依据，不把卡名、读者合同、群像结构等创作说明写进故事叙述。
保留人物声音、作者视角及文风，不抢写后续单元、不改写正式事实或越过作者边界。
只交付正文，不附字数声明、自检、结束标记或创作说明。
"""

SCOPES = {"chief_scope": CHIEF_SCOPE, "writer_scope": WRITER_SCOPE}


def effective(
    variant: str, value: editable_rules.ProgramSettings | None = None,
) -> editable_rules.ProgramSettings:
    current = previous.effective(variant, value)
    # Exact built-in defaults only; an author's custom text or explicit empty wins.
    return current.model_copy(update={"texts": {
        key: SCOPES[key] if key in SCOPES and text == previous.SCOPES[key] else text
        for key, text in current.texts.items()
    }})


def rules(
    variant: str, value: editable_rules.ProgramSettings | None = None,
) -> list[dict[str, str]]:
    current = effective(variant, value)
    return [
        {**rule, "default_text": SCOPES.get(rule["key"], rule["default_text"]),
         "text": current.texts[rule["key"]]}
        for rule in editable_rules.definitions(variant)
    ]
