"""Read-only UI descriptions; never part of a frozen renderer or model request."""

from typing import Any

from novel_writer.generation import creative_cast, editable_rules, progression_rules
from novel_writer.generation import output_contract_v3 as output


def program_rules(variant: str, bundle: dict[str, Any] | None = None) -> list[dict[str, str]]:
    return progression_rules.rules(variant, editable_rules.settings(variant, bundle))


def program_constraints(variant: str, bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    if variant == "chief":
        return {
            "maximum_new_characters": editable_rules.limit(bundle),
            "maximum_total_cast": 12,
            "new_characters": "使用预览预留 ID；必选人物、单元数量和输出结构见具体任务 Prompt",
        }
    return {}


def source_bindings(source: dict[str, Any]) -> dict[str, Any]:
    """Describe the selected saved source, without substituting current defaults."""
    template = source.get("prompt_templates") or {}
    return {
        "template_revision": source.get("prompt_template_revision") or template.get("revision"),
        "creative_autonomy": source.get(creative_cast.KEY),
        "role_output": source.get(output.KEY),
        "editable_rules": source.get("prompt_rules_contract"),
        "unit_delivery": source.get("unit_delivery_contract"),
        "structured_delivery": source.get("reliability_contract"),
        "event_progression": source.get("progression_contract"),
    }
