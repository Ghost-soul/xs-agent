"""Evidence-based failure labels without rewriting saved calls or retrying providers."""

import re
from typing import Any

from novel_writer.db.models import GenerationBatchRecord, GenerationCallRecord
from novel_writer.generation.chief_json import (
    PARSER_REVISION,
    ChiefJSONError,
    parse_chief_json,
    saved_schema,
)
from novel_writer.generation.content import parse_object
from novel_writer.generation.novel import parser_for, role_for
from novel_writer.generation.plan_format import (
    PlanIncompleteError,
    PlanQuestionFormatError,
    normalize_optional_text,
    normalize_questions,
    parse_plan_object,
)
from novel_writer.generation.structured_json import IncompleteJSONError, parse_report


def response_parser(batch: GenerationBatchRecord, call: GenerationCallRecord) -> str:
    from novel_writer.generation.format_trial import REVISION, enabled

    if enabled(batch.snapshot):
        return REVISION
    if role_for(call.action) == "memory":
        from novel_writer.generation.memory_compatibility import PARSER_REVISION as memory_parser

        return memory_parser
    policy = call.request.get("feedback_options", {}).get(
        "craft_policy", batch.spec.get("craft_policy")
    )
    if role_for(call.action) == "chief" and policy == "stage-craft-v1":
        return PARSER_REVISION
    return parser_for(batch.revision, call.action)


def refusal_message(call: GenerationCallRecord) -> str | None:
    response = call.response or {}
    if response.get("error_code") == "provider_refusal":
        return "供应商明确拒绝了本次请求；原响应和费用保留，原样重发不能修复内容拒绝。"
    terminal = response.get("terminal") or {}
    if (
        not terminal.get("terminal_event_seen")
        or response.get("error_code")
        or terminal.get("terminal_status") in {"failed", "incomplete"}
        or terminal.get("finish_reason") in {"length", "max_tokens", "max_output_tokens"}
    ):
        return None
    raw = (response.get("text") or "").strip()
    try:
        parsed = parse_object(raw)
    except ValueError:
        parsed = None
    if parsed is not None:
        if set(parsed) == {"refusal"} and isinstance(parsed["refusal"], str) and parsed["refusal"]:
            return "模型返回了拒绝说明，未交付本步骤结果；请查看原响应并调整请求，勿原样反复重发。"
        return None
    # A standalone service refusal is not narrative output. Match explicit
    # request/generation language, never an arbitrary "I cannot" in dialogue.
    first = raw.splitlines()[0] if raw else ""
    heading = re.sub(r"^(?:#{1,6}\s+)?(?:\*\*|__)?", "", first).rstrip("*_ ")
    explicit = (
        r"I (?:must|have to) decline (?:this (?:request|content)|"
        r"to (?:generate|create|write|provide) (?:this|the requested) "
        r"(?:creative plan|content|story|response))\.?"
    )
    # Some providers place their explanation on the same line after a bold
    # heading. Require service context there, not a character attribution.
    inline = re.match(
        r"^\*\*" + explicit + r"\*\*\s+(?:(?:The|This|Your) request\b|I cannot\b)",
        raw, re.I,
    )
    if len(raw) <= 1500 and (re.fullmatch(explicit, heading, re.I) or inline):
        return (
            "模型明确返回拒绝说明，未交付本步骤结果；原文和费用保留，后续步骤不会继续。"
            "请查看原响应，勿原样反复重发。"
        )
    # Prose and dialogue are never diagnosed from refusal-like wording.
    if role_for(call.action) in {"writer", "checker", "reader"} or len(raw) > 1500:
        return None
    first = raw.splitlines()[0] if raw else ""
    chinese = re.match(r"^(?:(?:抱歉|对不起)[，,。！!：:\s]*)?我(?:不能|无法|不会)", first)
    english = re.match(r"^(?:sorry[,.:!\s]*)?I (?:cannot|can't|won't|am unable to)\b", first, re.I)
    if (chinese and re.search(r"写|规划|生成|协助|帮助|提供|继续|满足.*要求", first)) or (
        english
        and re.search(r"\b(?:write|help|assist|comply|generate|fulfill|provide)\b", first, re.I)
    ):
        return "模型返回了拒绝说明，未交付结构化结果；请查看原响应并调整请求，勿原样反复重发。"
    return None


def failure_diagnostic(call: GenerationCallRecord) -> dict[str, Any] | None:
    # Diagnose historical false successes without rewriting their status,
    # artifacts, costs or parser receipts.
    if call.status == "completed":
        if refused := refusal_message(call):
            return {
                "code": "provider_refusal",
                "message": "此历史调用被记录为完成，但原文是模型拒绝说明，并非有效交付；"
                "历史记录保持，请勿将其当作正文或事实继续使用。",
            }
        return None
    if call.status not in {"local_failure", "outcome_uncertain", "uncertain_closed", "failed"}:
        return None
    from novel_writer.generation.provider_diagnostics import diagnostic as provider_diagnostic

    if result := provider_diagnostic(call):
        return result
    refused = refusal_message(call)
    if refused:
        return {"code": "provider_refusal", "message": refused}
    response = call.response or {}
    terminal = response.get("terminal") or {}
    if call.status == "outcome_uncertain" or response.get("error_code") == "outcome_uncertain":
        return {
            "code": "outcome_uncertain",
            "message": "供应商响应未确认完整结束，调用结果及费用可能未知；已保存片段。"
            "本地解析不能补齐，恢复前须核查原调用并确认可能重复计费。",
        }
    if response.get("error_code") or terminal.get("terminal_status") in {"failed", "incomplete"}:
        return {
            "code": response.get("error_code") or "protocol_incomplete",
            "message": "供应商报告错误或响应未完整结束；原始响应已保存，请查看调用详情。",
        }
    if "format_trial_contract" in call.request:
        return None  # Trial outputs are never diagnosed by a schema parser.
    if getattr(call, "error_code", None) not in {
        "local_validation_failed", "plan_question_format_invalid", "plan_output_incomplete",
    }:
        return None
    if role_for(call.action) in {"writer", "checker", "reader"}:
        return None
    format_receipt = None
    try:
        if role_for(call.action) == "chief" and saved_schema(call.request):
            parsed = parse_chief_json(response.get("text", ""), saved_schema(call.request))
            obj, format_receipt = parsed.value, parsed.receipt
        else:
            obj = (
                parse_plan_object(response.get("text", "")) if role_for(call.action) == "chief"
                else parse_report(response.get("text", ""))
            )
    except (PlanIncompleteError, IncompleteJSONError) as error:
        return {"code": "plan_output_incomplete", "message": str(error)}
    except ChiefJSONError as error:
        return {"code": "structured_output_invalid", "message": str(error)}
    except ValueError:
        return {
            "code": "structured_output_invalid",
            "message": "响应已结束，但未返回本步骤要求的 JSON 对象；原文已保存。"
            "请查看模型原始输出，本地重验不会重新调用模型。",
        }
    if role_for(call.action) == "memory":
        from novel_writer.generation.memory_compatibility import normalize

        try:
            _, notes = normalize(
                obj, call.request.get("prompt_template_source", {}).get("reference_boundary"),
            )
        except ValueError as error:
            return {"code": "memory_source_invalid", "message": str(error)}
        if notes:
            return {
                "code": "memory_format_compatible",
                "message": "报告存在可确定转换的待定事项层级或来源回显，可先本地重验，"
                "不增加模型调用；其余事实和证据仍须通过校验。",
            }
    if role_for(call.action) == "chief":
        scenes = obj.get("scenes")
        missing = [
            f"scenes[{i}].character_ids" for i, scene in enumerate(
                scenes if isinstance(scenes, list) else []
            )
            if isinstance(scene, dict) and "character_ids" not in scene
        ]
        if (
            "major_turn" in (saved_schema(call.request) or {}).get("required", [])
            and "major_turn" not in obj
        ):
            missing.append("major_turn")
        if missing:
            return {
                "code": "plan_required_fields_missing",
                "message": "Chief 漏填必要字段：" + "、".join(missing[:8])
                + "。完整计划内容尚未交付，不能用全部候选人物或猜测自动补填。",
            }
    # Older question contracts did not require a reason for every question.
    if role_for(call.action) == "chief" and "author_question_reasons" in obj:
        try:
            normalized = normalize_questions(normalize_optional_text(obj))
        except PlanQuestionFormatError as error:
            return {"code": "plan_question_format_invalid", "message": str(error)}
        if normalized != obj:
            return {
                "code": "plan_format_compatible",
                "message": "已保存响应包含可兼容的空值或问题键格式，可尝试本地重验（不调用模型）。"
                "其余人物、事实及范围校验仍保持；原响应不会改写。",
            }
    if format_receipt:
        return {
            "code": "plan_format_compatible",
            "message": "已保存响应存在可唯一解释的 JSON 结构符号差异，可本地重验（不调用模型）。"
            "重验仍须通过人物、事实及范围校验，原请求和响应保持。",
        }
    return None


def replay_blocker(call: GenerationCallRecord) -> str | None:
    diagnostic = failure_diagnostic(call)
    if diagnostic and diagnostic["code"] in {
        "provider_refusal", "plan_question_format_invalid", "provider_parameter_invalid",
    }:
        return str(diagnostic["message"])
    return None
