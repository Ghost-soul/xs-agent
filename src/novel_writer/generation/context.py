"""Source-preserving local selection, not model summarization."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from novel_writer.generation.content import fingerprint, json_text


def terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-zA-Z0-9_]{2,}", text.lower()))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(part[i : i + 2] for i in range(len(part) - 1))
    return words


def enrich_context(
    context: dict[str, Any],
    state: dict[str, Any],
    ids: list[str],
    direction: str = "",
) -> dict[str, Any]:
    selected = set(ids)
    characters = {c["id"]: c for c in state["characters"]}
    locations = {characters[i].get("location_id") for i in ids} - {None}

    def related(item: dict[str, Any]) -> bool:
        serialized = json_text(item)
        return any(i in serialized for i in selected | locations)

    # Exact entities plus local lexical retrieval; this does not assert semantic completeness.
    query = terms(direction + " " + json_text(state["narrative_position"]))
    event_terms = [terms(e["summary"]) for e in state["events"]]
    frequencies = Counter(t for tokens in event_terms for t in tokens)
    ranked = [
        (sum(1 / frequencies[t] for t in query & tokens), index, event)
        for index, (event, tokens) in enumerate(zip(state["events"], event_terms, strict=True))
        if related(event) or query & tokens
    ]
    ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    context["critical_facts"] = [event for _, _, event in ranked[:24]]
    chosen = {e["id"] for e in context["critical_facts"]}
    context["recent_events"] = [e for e in context["recent_events"] if e["id"] not in chosen]
    included = chosen | {e["id"] for e in context["recent_events"]}
    selected |= included
    context["places"] = [p for p in state["places"] if p["id"] in locations]
    context["related_state"] = {}
    for key in (
        "scenes",
        "timeline_constraints",
        "plot_threads",
        "foreshadowings",
        "reader_promises",
        "disclosures",
        "open_questions",
    ):
        items = [item for item in state[key] if related(item)]
        context["related_state"][key] = items
        selected.update(item["id"] for item in items)
    # Unknown relatedness is explicit, not evidence of absence; full originals remain local.
    context["selection_audit"] = {
        key: {
            "source_count": len(state[key]),
            "selected_count": len(items),
            "source_sha256": fingerprint(state[key]),
            "omission_is_absence": False,
        }
        for key, items in context["related_state"].items()
    }
    aliases: dict[str, list[str]] = {}
    for c in state["characters"]:
        for name in [c["name"], *c.get("aliases", [])]:
            if isinstance(name, str):
                aliases.setdefault(name, []).append(c["id"])
    context["ambiguous_aliases"] = {
        name: values for name, values in aliases.items() if len(set(values)) > 1
    }
    context["preflight"] = {
        "entry": "continuation" if context["recent_chapters"] else "opening",
        "required_character_ids": ids,
        "global_profile_gate": False,
    }
    context["selection_audit"]["events"] = {
        "source_count": len(state["events"]),
        "selected_count": len(included),
        "method": "entity-and-local-lexical-recall",
        "complete": False,
        "source_sha256": fingerprint(state["events"]),
    }
    return {
        "events": {
            "selected_ids": sorted(included),
            "omitted_ids": [e["id"] for e in state["events"] if e["id"] not in included],
            "source_sha256": fingerprint(state["events"]),
        }
    }
