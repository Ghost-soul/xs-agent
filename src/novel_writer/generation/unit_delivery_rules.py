"""Current editable scope defaults; published editable-rules-v1 stays frozen."""

from novel_writer.generation import editable_rules as previous
from novel_writer.generation import output_contract_v3 as old_scope

CHIEF_SCOPE = """【均衡单元与阶段性产出】
以阶段结束时要完成的实际变化为起点，规划规模相近的完整行动单元。
按 stage_scale 的首选单元数设计，每个单元都有自己的阶段性产出，并成为后续行动的条件。
阶段篇幅按有效计划的单元数均分；size_weight 仅兼容旧结构，新计划填 1，不用于缩小开场或收尾单元。
event 写清本轮行动从什么条件开始、要做成什么、到什么结果结束；同一目标的入场、接触、
试探、交锋、调整和直接结果放进一个单元，可跨多个连续场景，不把它们分别列成单元。
choice_and_response 写清各方怎样推动或改变行动；development.onstage_process 写值得现场
展开的关键过程；consequence 写本单元结束时实际改变的处境、关系、认知、资源或行动条件。
调查单元的产出可以是经过行动核实的线索及其后果，关系单元可以是经过互动形成的新约定或变化；
成功、失败或部分达成都可以，不能把产出仅写成“为后续铺垫”“准备行动”或宏大主题的宣告。
抵达、喝酒、看见物件、了解规矩通常是过程，需与它们服务的实质行动合并。
如果相邻单元仍在重复观察、犹豫或决定以后再做，重新组织行动链，把当前应发生的变化写进本阶段。
先保证事件容量，再分配篇幅；不要给一个几分钟的入场片段贴上数千字目标。
保留选定叙事、人物自主性、正式事实及作者边界；不为均衡强添支线、固定反转、危险或关系进度。
这些是规划指导，不新增文学评分、逐项验收或输出字段。
"""

WRITER_SCOPE = """【完成本单元的阶段性产出】
当前单元是一轮完整行动，可以包含多个连续场景。以当前有效计划中的 event、
choice_and_response 和 consequence 为主线，将开场条件推进到本单元的实际结果及必要余波。
把目标篇幅用于充分展开这轮行动：人物作出尝试，各方按自身目标回应，人物根据新条件调整，
让关键选择在现场产生后果。动作、对白、观察和即时感受应共同推动过程。
不要在进门、第一次对话、喝酒、看见线索或决定行动时收束；这些步骤之后，继续写完
本单元尚未发生的实质行动。通过情节呈现产出，不用主题总结或未来打算代替结果。
各单元以近似均衡的篇幅展开，开场与收尾同样需要足够的事件容量；目标不是可忽略的输出上限。
已有结果影响下一步，不重演同一轮试探，不靠重复解释、风景堆积或内心总结填字。
可以自主安排当前行动的局部办法与连续场景，但不抢写后续单元、不改写正式事实或越过作者边界。
只交付正文，不附字数声明、自检、结束标记或创作说明。
"""

SCOPES = {"chief_scope": CHIEF_SCOPE, "writer_scope": WRITER_SCOPE}
OLD_SCOPES = {"chief_scope": old_scope.CHIEF_SCOPE, "writer_scope": old_scope.WRITER_SCOPE}


def effective(
    variant: str, value: previous.ProgramSettings | None = None,
) -> previous.ProgramSettings:
    current = previous.effective(variant, value)
    # Only exact released defaults change. Custom text and explicit empty text win.
    return current.model_copy(update={"texts": {
        key: SCOPES[key] if key in SCOPES and text == OLD_SCOPES[key] else text
        for key, text in current.texts.items()
    }})


def rules(variant: str, value: previous.ProgramSettings | None = None) -> list[dict[str, str]]:
    current = effective(variant, value)
    return [
        {**rule, "default_text": SCOPES.get(rule["key"], rule["default_text"]),
         "text": current.texts[rule["key"]]}
        for rule in previous.definitions(variant)
    ]
