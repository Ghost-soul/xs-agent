"""Read-only explanations and preview suggestions from saved call evidence."""

from typing import Any

from novel_writer.db.models import (
    GenerationArtifactRecord,
    GenerationBatchRecord,
    GenerationCallRecord,
)
from novel_writer.generation.content import parse_object
from novel_writer.generation.novel import parser_for
from novel_writer.generation.token_limits import INPUT_TOKEN_LIMIT, TOKEN_LIMIT


def revalidation_blocker(
    batch: GenerationBatchRecord,
    call: GenerationCallRecord,
    candidate: GenerationArtifactRecord | None,
) -> str | None:
    if call.status not in {"response_saved", "local_failure"} or not call.response:
        return "仅已保存完整响应的本地失败可以重验"
    if output_diagnostic(call):
        return "输出已被截断，本地重验无法补齐缺失内容；需另行预览并确认补全"
    terminal = call.response.get("terminal") or {}
    if (
        not terminal.get("terminal_event_seen")
        or call.response.get("error_code")
        or terminal.get("terminal_status") in {"failed", "incomplete"}
    ):
        return "响应未完整结束或带有供应商错误，不能作为完整结果本地重验"
    if batch.status in {"running", "queued", "archived", "adopted", "outcome_uncertain"}:
        return "此批次当前不可本地重验"
    if f"{call.id}:{parser_for(batch.revision, call.action)}" in batch.state.get("compiled", []):
        return (
            "当前解析版本已处理过此响应，不能循环重验；"
            "已有有效计划时可手工修订，否则需重新建立预览"
        )
    if batch.state.get("plan_author_note_id") or (
        candidate and candidate.payload.get("source") == "author"
    ):
        return "作者已修订工件，不能用旧响应覆盖"
    return None


def output_diagnostic(call: GenerationCallRecord) -> dict[str, Any] | None:
    response = call.response or {}
    terminal = response.get("terminal") or {}
    exhausted = response.get("error_code") == "token_limit_exceeded" or terminal.get(
        "finish_reason"
    ) in {"length", "max_tokens", "max_output_tokens"}
    if not exhausted or not terminal.get("terminal_event_seen"):
        return None
    usage = response.get("usage") or {}
    limit = call.request.get("model_request", {}).get("max_output_tokens")
    visible = len((response.get("text") or "").strip())
    reasoning = usage.get("reasoning_tokens")
    message = f"本次达到输出上限 {limit or '（见调用设置）'} tokens。"
    if isinstance(reasoning, int) and reasoning > 0:
        message += f"供应商记录推理用量 {reasoning} tokens。"
    message += (
        "可见结果为空，尚未生成可用结果；本地重验无法补出缺失内容。"
        if not visible
        else "已保存可见内容，但响应未完整结束，不能视为该步骤完成。"
    )
    return {
        "code": "output_limit_exceeded",
        "message": message,
        "output_limit": limit,
        "reasoning_tokens": reasoning,
        "visible_characters": visible,
    }


def plan_retry_preview(
    batch: GenerationBatchRecord,
    calls: list[GenerationCallRecord],
    artifact_kinds: set[str],
) -> dict[str, Any] | None:
    # Only a failed initial design with a known terminal and no useful work is
    # eligible for this shortcut. Creating a preview is not a retry authorization.
    if (
        batch.status not in {"needs_attention", "failed"}
        or batch.spec.get("workflow") != "novel-run-v1"
        or len(calls) != 1
        or calls[0].action != "plan"
        or calls[0].status != "local_failure"
        or artifact_kinds & {"plan", "candidate", "units"}
        or (output_diagnostic(calls[0]) is None and plan_distribution_diagnostic(calls[0]) is None)
    ):
        return None
    old = batch.spec.get("chief_output_limit", 6000)
    profile = batch.snapshot.get("profile", {})
    model: dict[str, Any] = next(
        (m for m in profile.get("models", []) if m["id"] == batch.spec["chief_model"]), {}
    )
    maximum = min(TOKEN_LIMIT, model.get("max_output_tokens") or 0)
    distribution = plan_distribution_diagnostic(calls[0])
    if maximum <= old and not distribution:
        return None
    return {
        "reason": "obsolete_genre_quota" if distribution else "output_limit",
        "input_limit": INPUT_TOKEN_LIMIT,
        "chief_output_limit": TOKEN_LIMIT,
        "writer_output_limit": TOKEN_LIMIT,
        "auxiliary_output_limit": TOKEN_LIMIT,
        "previous_output_limit": old,
        "requires_new_cost_confirmation": True,
    }


def plan_distribution_diagnostic(call: GenerationCallRecord) -> dict[str, Any] | None:
    if (
        call.action != "plan"
        or call.status != "local_failure"
        or call.error_code != "local_validation_failed"
    ):
        return None
    if call.request.get("feedback_options", {}).get("writing_policy") in {
        "background-v1",
        "guided-v1",
    }:
        return None
    response = call.response or {}
    terminal = response.get("terminal") or {}
    if terminal.get("finish_reason") != "stop" or not terminal.get("terminal_event_seen"):
        return None
    try:
        plan = parse_object(response.get("text", ""))
        scenes = plan["scenes"]
        numbers = [
            [s[k] for k in ("focus_percent", "transition_percent", "other_percent")] for s in scenes
        ]
        if not numbers or not all(type(n) is int for row in numbers for n in row):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    focus, transition, other = (sum(row[i] for row in numbers) for i in range(3))
    total = focus + transition + other
    if total == 100 and focus >= 70 and transition <= 20:
        return None
    return {
        "code": "obsolete_genre_quota",
        "total_percent": total,
        "message": (
            f"Chief 已完整返回 {len(scenes)} 个场面，份额合计 {total}%。"
            "旧合同要求全阶段合计100%、主导至少70%、过渡最多20%，因此方案未接纳，Writer 尚未开始。"
            "新阶段已取消题材配额，题材卡仅作为 Chief 的背景参考；可按当前设置建立新预览。"
            "原响应与费用保留，新预览不会自动调用模型。"
        ),
    }
