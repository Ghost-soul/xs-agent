"""Role-specific, bounded retrieval queries; evidence roles never query the plan."""

from __future__ import annotations

import re
from typing import Any

from novel_writer.generation.content import json_text
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec


def windows(text: str, slots: int = 4, size: int = 300) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    # Reserve both ends, then evenly spaced interior context; never only a prefix.
    starts = [round((len(text) - size) * i / (slots - 1)) for i in range(slots)]
    return list(dict.fromkeys(text[start : start + size] for start in starts))


def mentions(item: dict[str, Any], text: str) -> bool:
    for value in [item.get("id"), item.get("name"), *item.get("aliases", [])]:
        if not isinstance(value, str) or not value:
            continue
        if re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            if re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", text, re.I):
                return True
        elif value.casefold() in text.casefold():
            return True
    return False


def current_task(action: str, plan: dict[str, Any] | None) -> Any:
    scenes = (plan or {}).get("scenes", [])
    if action.startswith("write:"):
        ordinal = int(action.split(":")[1])
        if not 1 <= ordinal <= len(scenes):
            raise ValueError("当前单元超出有效计划")
        return scenes[ordinal - 1]
    return scenes if action == "write" else None


def queries(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None = None,
) -> list[str]:
    role = role_for(action)
    if role in {"reader", "editor"}:
        return []  # Cold reading, titles and local expression edits need no broad search.
    context = snapshot["context"]
    if role in {"memory", "checker"}:
        values = windows(body or "")
        evidence = body or ""
    elif action == "rewrite":
        instruction = reports.get("edit_scope", {}).get("instruction", "")
        values = [*windows(instruction, 2), *windows(body or "", 4)]
        evidence = instruction + (body or "")
    elif role == "writer":
        task = current_task(action, plan)
        tasks = task if isinstance(task, list) else [task or {}]
        # Use narrative fields before IDs so the vector model sees actual event language.
        values = [
            str(scene.get(field, ""))[:300]
            for scene in tasks
            for field in ("event", "choice_and_response", "consequence")
        ][:4]
        values += windows((body or "")[-600:], 2)
        evidence = json_text(task) + (body or "")[-600:]
    else:
        values = windows("\n".join([spec.direction, spec.author_boundaries, author_note or ""]), 2)
        selected = set(spec.narrative_card_ids)
        cards = [c for c in snapshot.get("cards", []) if c["id"] in selected]
        values += [str(c.get("name", "")) + "\n" + str(c.get("text", ""))[:220] for c in cards][:4]
        values += [json_text(context.get("narrative_position", {}))[:300], spec.viewpoint[:300]]
        evidence = "\n".join(values)
    people = reports.get("role_working_state", context).get("characters", [])
    entities = [
        str(c.get("name", "")) + " " + str(c.get("id", "")) for c in people if mentions(c, evidence)
    ]
    if entities:
        values += windows(" ".join(entities), 2)
    return list(dict.fromkeys(v[:300] for v in values if v.strip() and v != "{}"))[:8]
