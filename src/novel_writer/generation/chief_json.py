"""Bounded punctuation recovery against the saved Chief schema, without changing values."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from novel_writer.generation.content import digest, json_text
from novel_writer.generation.plan_format import PlanIncompleteError
from novel_writer.generation.schemas import BackgroundPlan

REVISION = "chief-json-punctuation-v1"
PARSER_REVISION = "craft-plan-format-v4"
MAX_EDITS = 6
MAX_STATES = 256
MAX_WORK = 8_000_000
MAX_CHARACTERS = 300_000
_TOKEN = re.compile(
    r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    r'|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
    r'|true|false|null|[{}\[\],:]'
)
_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(.*)\r?\n```\Z", re.I | re.S)


class ChiefJSONError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedChiefJSON:
    value: dict[str, Any]
    receipt: dict[str, Any] | None = None


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ChiefJSONError("Chief JSON 包含重复字段，不能覆盖或猜选其中一个值。")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise ChiefJSONError("Chief JSON 包含非标准数值，不能猜测转换。")


def _decode(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
    except RecursionError as error:
        raise ChiefJSONError("Chief JSON 嵌套层级过深，原响应保留。") from error
    if not isinstance(decoded, dict):
        raise ChiefJSONError("Chief 必须返回完整 JSON 对象，不能从多个结果中猜选。")
    return decoded


def _tokens(value: str) -> list[re.Match[str]]:
    tokens = list(_TOKEN.finditer(value))
    previous = 0
    for token in tokens:
        if value[previous:token.start()].strip():
            raise ChiefJSONError("Chief JSON 的文字、引号或数值不完整，不能仅靠结构符号修复。")
        previous = token.end()
    if value[previous:].strip():
        raise ChiefJSONError("Chief JSON 的文字、引号或数值不完整，不能仅靠结构符号修复。")
    return tokens


def _scalars(tokens: list[re.Match[str]]) -> tuple[str, ...]:
    return tuple(t.group() for t in tokens if t.group() not in {"{", "}", "[", "]", ",", ":"})


def saved_schema(request: dict[str, Any]) -> dict[str, Any] | None:
    source = request.get("prompt_template_source")
    schema = source.get("output_schema") if isinstance(source, dict) else None
    return schema if isinstance(schema, dict) and schema else None


def _shape(value: Any, schema: dict[str, Any], root: dict[str, Any], depth: int = 0) -> bool:
    """Check field ownership/types only; the existing domain parser remains authoritative."""
    if depth > 64:
        return False
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            return False
        target = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        return isinstance(target, dict) and _shape(value, target, root, depth + 1)
    if "anyOf" in schema:
        return any(_shape(value, part, root, depth + 1) for part in schema["anyOf"])
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties", {})
        if {"chapter_goal", "scenes", "world_context"} <= properties.keys() and "scenes" in value:
            # Reuse the existing legacy-field projection only for shape checking.
            # The recovered object/receipt still retains every original scalar.
            project = cast(Callable[[Any], Any], BackgroundPlan.discard_obsolete_quotas)
            value = project(value)
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = properties.get(key, extra)
            if child is False or (
                isinstance(child, dict) and not _shape(item, child, root, depth + 1)
            ):
                return False
        return True
    if kind == "array":
        return isinstance(value, list) and all(
            _shape(item, schema.get("items", {}), root, depth + 1) for item in value
        )
    if kind == "string":
        return isinstance(value, str) or (value is None and schema.get("default") == "")
    if kind in {"number", "integer"}:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def _alternatives(value: str, error: json.JSONDecodeError) -> Iterator[tuple[str, dict[str, Any]]]:
    tokens = _tokens(value)
    index = next((i for i, t in enumerate(tokens) if t.start() == error.pos), None)
    # Never complete EOF, a partial string, or a partial numeric token.
    if index is None:
        return
    current = tokens[index]
    symbol = current.group()
    previous = tokens[index - 1] if index else None
    edits: set[tuple[str, int, str]] = set()
    if error.msg == "Expecting ',' delimiter":
        if symbol in {"}", "]"}:
            edits.add(("remove", current.start(), symbol))
            edits.add(("insert", current.start(), "]" if symbol == "}" else "}"))
        elif previous and (
            current.start() > previous.end()
            or symbol.startswith(('"', "{", "["))
            or previous.group().startswith(('"', "}", "]"))
        ):
            edits.add(("insert", current.start(), ","))
    elif error.msg == "Expecting ':' delimiter" and symbol not in {"}", "]", ",", ":"}:
        edits.add(("insert", current.start(), ":"))
    elif error.msg in {"Expecting value", "Expecting property name enclosed in double quotes"}:
        if symbol in {"}", "]", ","} and previous and previous.group() == ",":
            edits.add(("remove", previous.start(), ","))
    elif error.msg == "Extra data":
        if symbol in {"}", "]"}:
            # Include earlier closures so nested/outer ownership cannot be guessed.
            edits.update(("remove", t.start(), t.group()) for t in tokens[:index + 1]
                         if t.group() in {"}", "]"})
        elif symbol == "," and previous and previous.group() in {"}", "]"}:
            edits.add(("remove", previous.start(), previous.group()))
    for operation, position, character in sorted(edits):
        changed = (
            value[:position] + character + value[position:]
            if operation == "insert" else value[:position] + value[position + 1:]
        )
        yield changed, {
            "operation": operation, "position": position, "symbol": character,
        }


def parse_chief_json(raw: str, schema: dict[str, Any] | None = None) -> ParsedChiefJSON:
    value = raw.strip().removeprefix("\ufeff").strip()
    wrapped = _FENCE.fullmatch(value)
    if wrapped:
        value = wrapped[1].strip()

    def result(obj: dict[str, Any], text: str, edits: list[dict[str, Any]]) -> ParsedChiefJSON:
        receipt = None
        if edits or wrapped or raw.strip().startswith("\ufeff"):
            receipt = {
                "revision": REVISION, "source_sha256": digest(raw),
                "normalized_sha256": digest(text), "position_basis": "sequential-zero-based",
                "unwrapped": bool(wrapped), "edits": edits,
            }
        return ParsedChiefJSON(obj, receipt)

    try:
        return result(_decode(value), value, [])
    except json.JSONDecodeError as original:
        if (
            original.pos >= len(value) or original.msg.startswith("Unterminated")
            or value.endswith("<|eos|>")
        ):
            raise PlanIncompleteError(
                "Chief 的 JSON 正文未写完整，不能靠本地格式修复补齐缺失内容；原响应已保留。"
            ) from original
        if not schema:
            raise ChiefJSONError(
                "Chief JSON 语法错误且没有绑定的输出结构，不能猜测修复；原响应已保留。"
            ) from original
        if len(value) > MAX_CHARACTERS:
            raise ChiefJSONError("Chief JSON 超过本地符号修复容量，原响应保留。") from original
        scalars = _scalars(_tokens(value))
        queue: dict[str, list[dict[str, Any]]] = {value: []}
        seen = {value}
        work = 0
        for depth in range(MAX_EDITS + 1):
            successes: dict[str, ParsedChiefJSON] = {}
            following: dict[str, list[dict[str, Any]]] = {}
            for candidate, edits in queue.items():
                work += len(candidate)
                if work > MAX_WORK:
                    raise ChiefJSONError(
                        "Chief JSON 修复候选过多，不能确定唯一结构。"
                    ) from original
                try:
                    obj = _decode(candidate)
                except json.JSONDecodeError as error:
                    if depth == MAX_EDITS:
                        continue
                    for changed, edit in _alternatives(candidate, error):
                        if changed in seen:
                            continue
                        if len(seen) >= MAX_STATES:
                            raise ChiefJSONError(
                                "Chief JSON 修复候选过多，不能确定唯一结构。"
                            ) from original
                        seen.add(changed)
                        following[changed] = [*edits, edit]
                except ChiefJSONError:
                    continue
                else:
                    if _scalars(_tokens(candidate)) == scalars and _shape(obj, schema, schema):
                        successes[json_text(obj)] = result(obj, candidate, edits)
            if len(successes) > 1:
                raise ChiefJSONError(
                    "Chief JSON 有多种可能的字段归属，不能自动猜选。"
                ) from original
            if successes:
                return next(iter(successes.values()))
            if not following:
                break
            queue = following
        raise ChiefJSONError(
            "Chief JSON 结构错误无法在有限符号调整内唯一修复；原响应已保存，未补造内容。"
        ) from original
