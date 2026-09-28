"""Opt-in, format-requested trial. Outputs are drafts, never validated facts."""

from __future__ import annotations

import inspect
import sys
from typing import Any

from novel_writer.generation.content import fingerprint
from novel_writer.generation.craft_models import CraftSpec
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.errors import ConflictError, WorkflowError

KEY = "format_trial_contract"
REVISION = "format-requested-unchecked-v1"


def enabled(snapshot: dict[str, Any]) -> bool:
    return KEY in snapshot


def binding() -> dict[str, str]:
    from novel_writer.generation import trial_pipeline, trial_prompts

    return {
        "revision": REVISION,
        "sha256": fingerprint(
            {
                "sources": [
                    inspect.getsource(m)
                    for m in (
                        sys.modules[__name__],
                        trial_pipeline,
                        trial_prompts,
                    )
                ],
            }
        ),
    }


def checked(snapshot: dict[str, Any]) -> bool:
    if not enabled(snapshot):
        return False
    if snapshot[KEY] != binding():
        raise WorkflowError("试验合同与冻结预览不符，请重新预览；原请求不能自动升级")
    return True


def bind_snapshot(spec: GenerationSpec, snapshot: dict[str, Any]) -> None:
    if not isinstance(spec, CraftSpec) or spec.stage_mode != "longform-v1":
        raise WorkflowError("跳过格式校验试验只支持新版长篇阶段，请选择阶段创作")
    snapshot[KEY] = binding()


def require_standard(snapshot: dict[str, Any]) -> None:
    if enabled(snapshot):
        raise ConflictError(
            "此阶段跳过本地格式校验，结果仅供阅读和下载；不支持正式采用、事实入库或独立修订"
        )
