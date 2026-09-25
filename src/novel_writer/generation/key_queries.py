"""Bounded semantic queries with complete card/entity lexical coverage for v3."""

from __future__ import annotations

import re
from typing import Any

from novel_writer.generation import knowledge_context, role_materials, role_queries
from novel_writer.generation.content import fingerprint, json_text
from novel_writer.generation.novel import role_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.knowledge import lookup


def query_plan(
    spec: GenerationSpec,
    snapshot: dict[str, Any],
    action: str,
    plan: dict[str, Any] | None,
    body: str | None,
    reports: dict[str, Any],
    author_note: str | None = None,
) -> dict[str, Any]:
    role = role_for(action)
    if role in {"editor", "reader"} or action == "title":
        return {"queries": [], "lexical_queries": [], "cards": []}
    base = role_queries.queries(spec, snapshot, action, plan, body, reports, author_note)
    lexical: list[str] = []
    coverage: list[dict[str, Any]] = []
    if role == "chief":
        base = role_queries.windows(
            "\n".join([spec.direction, spec.author_boundaries, author_note or ""]), 2
        )
        cards = {c["id"]: c for c in snapshot.get("cards", [])}
        seeds = []
        for identifier in spec.narrative_card_ids:
            card = cards[identifier]
            lines = str(card.get("text", "")).splitlines()
            definition = next(
                (i for i, line in enumerate(lines) if re.search("核心定义|适用前提", line)), 0
            )
            seed = str(card.get("name", identifier)) + "\n" + "\n".join(lines[definition:])[:220]
            seeds.append(seed)
            lexical.append(seed)
            coverage.append({"id": identifier, "lexical": True, "semantic_characters": 0})
        # Four balanced groups, with a share for every card rather than only the first four.
        for slot in range(min(4, len(seeds))):
            indices = list(range(slot, len(seeds), 4))
            share = max(0, (300 - len(indices) + 1) // len(indices))
            parts = []
            for index in indices:
                piece = seeds[index][:share]
                coverage[index]["semantic_characters"] = len(piece)
                coverage[index]["semantic_limited"] = len(piece) < len(seeds[index])
                parts.append(piece)
            if any(parts):
                base.append("\n".join(parts))
        base += role_queries.windows(
            json_text(snapshot["context"].get("narrative_position", {})) + spec.viewpoint, 2
        )
    evidence = (
        body or ""
        if role in {"memory", "checker"} or action == "rewrite"
        else json_text(role_queries.current_task(action, plan)) + (body or "")[-6000:]
        if role == "writer"
        else spec.direction + spec.author_boundaries + spec.viewpoint + (author_note or "")
    )
    state, _ = role_materials.state_at_opening(snapshot, action, reports)
    # Scan all evidence, not just evenly sampled windows. No guessed pronoun binding.
    for key in ("characters", "places", "world_lore"):
        for item in state.get(key, []):
            if role_queries.mentions(item, evidence):
                lexical.append(str(item.get("name") or item["id"]))
    lexical += re.findall(
        r"第[零一二三四五六七八九十百千万\d]+[年月日天章]|\d{1,2}[:：]\d{2}", evidence
    )
    return {
        "queries": list(dict.fromkeys(v[:300] for v in base if v.strip()))[:8],
        "lexical_queries": list(dict.fromkeys(lexical)),
        "cards": coverage,
        "evidence_sha256": fingerprint(evidence),
    }


def enrich(
    snapshot: dict[str, Any], receipt: dict[str, Any], query: dict[str, Any]
) -> dict[str, Any]:
    """Keep a small, diverse union; all hits retain the original verifiable chunk payload."""
    sources = knowledge_context.frozen_sources(snapshot)
    exact = (
        lookup.prepare(
            f"role-key-v3:{snapshot.get('knowledge_project_id')}:{receipt['version_id']}", sources
        ).rank(query["lexical_queries"], limit=32)
        if query["lexical_queries"]
        else []
    )
    selected: list[dict[str, Any]] = []
    for i in range(max(len(exact), len(receipt["hits"]))):
        for group in (exact, receipt["hits"]):
            if i >= len(group):
                continue
            hit = group[i]
            same_source = [
                h
                for h in selected
                if (h["kind"], h["source_id"]) == (hit["kind"], hit["source_id"])
            ]
            if len(same_source) >= 3 or any(
                max(h["start"], hit["start"]) < min(h["end"], hit["end"]) for h in same_source
            ):
                continue
            if len(selected) < 32:
                selected.append(hit)
    data = {
        **{k: v for k, v in receipt.items() if k != "sha256"},
        "policy": "role-key-v3",
        "hits": selected,
        "query_coverage": query,
    }
    return {**data, "sha256": fingerprint(data)}
