"""Chinese Chief output for new previews; the published v1 renderer stays frozen."""

import inspect
import sys
from typing import Any

from novel_writer.generation import output_contract as v1
from novel_writer.generation.content import fingerprint
from novel_writer.generation.novel import role_for
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderProfile

KEY = v1.KEY
REVISION = "role-output-v2"
CHIEF_LANGUAGE = """【交付语言与完整性】
使用简体中文填写全部自然语言内容：阶段目标、衔接、事件、选择与回应、后果、背景、
过程设计、转折、余波、长线推进、未来建议以及问题和原因。人物姓名沿用资料中的原名。
JSON 字段名、枚举值和给定 ID 保持合同原值，不翻译，不自造英文姓名或替换人物 ID。
只交付完整 JSON 对象，不输出英文解说、Markdown 围栏或模型结束标记。
可选文字字段没有内容时用空字符串，不用 null；无问题时使用空列表和空对象。
问题映射的键复制 questions 中的完整字符串，包括问题后的补充说明，不使用缩略问题。
保持事实、人物范围与真实作者边界；本说明只规定表达和格式，不授权改变故事事实。
"""


def binding() -> dict[str, str]:
    return {"revision": REVISION, "sha256": fingerprint({
        "source": inspect.getsource(sys.modules[__name__]), "base": v1.binding(),
    })}


def prepare_output(
    request: ModelRequest,
    profile: ProviderProfile,
    action: str,
    snapshot: dict[str, Any],
    reports: dict[str, Any],
) -> ModelRequest:
    policy = reports.get(KEY, snapshot.get(KEY))
    if not policy or policy.get("revision") == v1.REVISION:
        return v1.prepare_output(request, profile, action, snapshot, reports)
    if policy != binding():
        raise WorkflowError("输出协议版本与冻结授权不符，请重新预览；旧请求不自动升级")
    local = {**reports, KEY: v1.binding()}
    prepared = v1.prepare_output(request, profile, action, snapshot, local)
    if "output_format" in local:
        reports["output_format"] = {**local["output_format"], "revision": REVISION}
        reports[KEY] = policy
    if role_for(action) == "chief":
        prepared = prepared.model_copy(update={
            "system_prompt": prepared.system_prompt + "\n\n" + CHIEF_LANGUAGE,
        })
    return prepared
