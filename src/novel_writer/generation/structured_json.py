"""Complete JSON wrappers and diagnostics, without inventing missing content."""

import json
import re
from typing import Any


class IncompleteJSONError(ValueError):
    pass


def unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"JSON 存在重复字段 {key}，不能猜测应采用哪个值")
        result[key] = value
    return result


def parse_report(raw: str) -> dict[str, Any]:
    text = raw.strip()
    opening = re.match(r"^```(?:json)?\s*\r?\n", text, re.I)
    if opening:
        text = text[opening.end():].rstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as error:
        if incomplete(text):
            raise IncompleteJSONError(
                "接口已结束，但 JSON 内容未写完整；已保存原响应。"
                "本地重验不能补齐缺失内容，请核对供应商输出后选择恢复。"
            ) from error
        raise ValueError(
            f"JSON 格式错误（第 {error.lineno} 行、第 {error.colno} 列）：{error.msg}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError("需要完整 JSON 对象，不能把其他内容当作报告")
    return value


def incomplete(text: str) -> bool:
    if not text.lstrip().startswith(("{", "[")):
        return False
    stack: list[str] = []
    string, escaped = False, False
    for char in text:
        if string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                string = False
        elif char == '"':
            string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}" and (not stack or stack.pop() != {"}": "{", "]": "["}[char]):
            return False
    return string or bool(stack)
