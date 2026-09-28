"""Lossless representation compatibility for Chief questions, never inferred answers."""

from copy import deepcopy
from json import JSONDecodeError
from typing import Any

from pydantic import BaseModel

from novel_writer.generation.content import parse_object
from novel_writer.generation.craft_models import CraftPlan, CraftUnit, Development


class PlanIncompleteError(ValueError):
    pass


def parse_plan_object(raw: str) -> dict[str, Any]:
    try:
        return parse_object(raw)
    except JSONDecodeError as error:
        if error.msg.startswith("Unterminated string") or raw.rstrip().endswith("<|eos|>"):
            raise PlanIncompleteError(
                "Chief 的 JSON 正文未写完整，即使供应商报告 stop 也不能视为完整方案。"
                "原响应已保留；本地重验不能补齐缺失内容，需另行确认失败步骤恢复。"
            ) from error
        raise


def normalize_optional_text(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)

    def defaults(obj: dict[str, Any], model: type[BaseModel]) -> None:
        for key, field in model.model_fields.items():
            if field.annotation is str and field.default == "" and key in obj and obj[key] is None:
                obj[key] = ""

    defaults(result, CraftPlan)
    scenes = result.get("scenes")
    if isinstance(scenes, list):
        for scene in scenes:
            if isinstance(scene, dict):
                defaults(scene, CraftUnit)
                development = scene.get("development")
                if isinstance(development, dict):
                    defaults(development, Development)
    return result


class PlanQuestionFormatError(ValueError):
    pass


def _full_question(key: Any, questions: list[str]) -> Any:
    if not isinstance(key, str) or key in questions or not key.endswith(("?", "？")):
        return key
    # Only an exact, complete question sentence plus explanation qualifies.
    # No semantic matching, paraphrase, ordinal or partial-word matching.
    matches = [q for q in questions if q.startswith(key)]
    return matches[0] if len(matches) == 1 else key


def _question_keys(value: Any, questions: list[str]) -> Any:
    if not isinstance(value, dict):
        return value
    mapped = {}
    for key, item in value.items():
        full = _full_question(key, questions)
        if full in mapped:
            raise PlanQuestionFormatError("同一作者问题出现重复映射，不能覆盖原原因或范围")
        mapped[full] = item
    return mapped


def normalize_questions(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    questions = result.get("questions", [])
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        return result  # The domain validator explains malformed question content.
    reasons = result.get("author_question_reasons", {})
    scopes = result.get("question_scopes", {})
    if isinstance(reasons, dict) and set(reasons) == {"kind", "source", "why_blocked"}:
        if len(questions) != 1:
            raise PlanQuestionFormatError(
                f"Chief 返回了 {len(questions)} 个作者问题和一份总原因，无法确定逐项对应关系。"
                "原响应已保留；请查看问题并补充创作要求，再建立新预览。不能自动猜配。"
            )
        reasons = {questions[0]: reasons}
    elif isinstance(reasons, list):
        mapped = {}
        for entry in reasons:
            question = _full_question(entry.get("question"), questions) if isinstance(
                entry, dict
            ) else None
            if (
                not isinstance(entry, dict)
                or set(entry) != {"question", "kind", "source", "why_blocked"}
                or question not in questions
                or question in mapped
            ):
                raise PlanQuestionFormatError(
                    "作者问题原因列表缺少明确对应、包含额外字段或重复问题"
                )
            mapped[question] = {k: v for k, v in entry.items() if k != "question"}
        reasons = mapped
    if isinstance(scopes, str) and scopes in {"current_unit", "later"} and len(questions) == 1:
        scopes = {questions[0]: scopes}
    reasons = _question_keys(reasons, questions)
    scopes = _question_keys(scopes, questions)
    if isinstance(reasons, dict) and set(reasons) != set(questions):
        raise PlanQuestionFormatError(
            "作者问题与原因未逐项对应；原响应已保留，请查看问题并补充创作要求后重新预览"
        )
    if isinstance(scopes, dict) and set(scopes) - set(questions):
        raise PlanQuestionFormatError(
            "问题影响范围的键必须是对应的问题原文，值为 current_unit 或 later；不能猜测对应关系"
        )
    if "author_question_reasons" in result:
        result["author_question_reasons"] = reasons
    if "question_scopes" in result:
        result["question_scopes"] = scopes
    return result
