"""Deterministic source binding, paragraph windows and Chinese lexical recall."""

from __future__ import annotations

import math
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from typing import Any

from novel_writer.generation.content import digest, fingerprint, json_text

CHUNKER = "paragraph-window-v1"
_CACHE_CHARACTERS = 2_000_000
_lexical_cache: OrderedDict[str, tuple[int, list[set[str]], Counter[str]]] = OrderedDict()
_cache_lock = threading.Lock()
COLLECTIONS = (
    "characters",
    "relationships",
    "places",
    "world_rules",
    "world_lore",
    "beliefs",
    "events",
    "timeline_constraints",
    "foreshadowings",
    "reader_promises",
)


def terms(text: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9_-]{2,}", text.casefold()))
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(part[i : i + 2] for i in range(len(part) - 1))
    return result


def source(kind: str, key: str, body: str, **metadata: Any) -> dict[str, Any]:
    return {"kind": kind, "source_id": key, "text": body, "sha256": digest(body), **metadata}


def state_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        source(
            collection,
            str(item.get("id") or f"legacy:{fingerprint(item)}"),
            json_text(item),
            entity_ids=[str(item["id"])] if item.get("id") else [],
        )
        for collection in COLLECTIONS
        for item in state.get(collection, [])
    ]


def chunks(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep exact offsets. Short overlapping windows are retrieval units, not story units."""
    result = []
    for item in sources:
        body = item["text"]
        if digest(body) != item["sha256"]:
            raise ValueError("知识来源内容校验失败")
        start = 0
        while start < len(body):
            end = min(len(body), start + 320)
            if end < len(body):
                boundary = body.rfind("\n", start + 160, end)
                if boundary >= 0:
                    end = boundary + 1
            text = body[start:end]
            item_key = fingerprint(
                [CHUNKER, item["kind"], item["source_id"], item["sha256"], start, end]
            )
            result.append(
                {
                    "id": item_key,
                    "kind": item["kind"],
                    "source_id": item["source_id"],
                    "source_sha256": item["sha256"],
                    "start": start,
                    "end": end,
                    "text": text,
                    "text_sha256": digest(text),
                    "ordinal": item.get("ordinal"),
                    "chapter_id": item.get("chapter_id"),
                    "entity_ids": item.get("entity_ids", []),
                }
            )
            if end == len(body):
                break
            start = max(start + 1, end - 40)
    return result


def corpus_hash(sources: list[dict[str, Any]]) -> str:
    return fingerprint([{k: v for k, v in item.items() if k != "text"} for item in sources])


def queries(
    direction: str, action: str, plan: dict[str, Any] | None, body: str | None
) -> list[str]:
    scenes = (plan or {}).get("scenes", [])
    if action.startswith("write:"):
        ordinal = int(action.split(":")[1])
        scenes = scenes[ordinal - 1 : ordinal]
    values = [json_text(scene) for scene in scenes[:4]]
    if not values:
        values = [direction]
    if body:
        values.append(body[-600:])
    # Query size is separate from evidence size; the role still gets its complete evidence.
    return [value[:900] for value in values[:4] if value.strip()]


def rank(
    candidates: list[dict[str, Any]],
    texts: list[str],
    semantic: list[list[str]] | None = None,
    *,
    limit: int = 32,
) -> list[dict[str, Any]]:
    # Cache only derived lexical features, bounded across every novel/version in the process.
    # Include actual text hashes, so edited or tampered content never reuses stale features.
    cache_key = fingerprint([(item["id"], digest(item["text"])) for item in candidates])
    with _cache_lock:
        cached = _lexical_cache.get(cache_key)
        if cached is not None:
            _lexical_cache.move_to_end(cache_key)
    if cached is None:
        tokens = [terms(item["text"]) for item in candidates]
        df = Counter(token for group in tokens for token in group)
        size = sum(len(item["text"]) for item in candidates)
        if size <= _CACHE_CHARACTERS:
            with _cache_lock:
                _lexical_cache[cache_key] = (size, tokens, df)
                while (len(_lexical_cache) > 2 or
                       sum(value[0] for value in _lexical_cache.values()) > _CACHE_CHARACTERS):
                    _lexical_cache.popitem(last=False)
    else:
        _, tokens, df = cached
    scores: dict[str, float] = defaultdict(float)
    channels: dict[str, set[str]] = {}
    for query in texts:
        wanted = terms(query)
        ranked = []
        for item, available in zip(candidates, tokens, strict=True):
            score = sum(math.log(1 + len(candidates) / df[t]) for t in wanted & available)
            if item["source_id"] in query:
                score += 20
            if query.strip() and query.casefold() in item["text"].casefold():
                score += 40
            if score:
                ranked.append((score, item["id"]))
        for position, (_, key) in enumerate(sorted(ranked, key=lambda v: (-v[0], v[1]))[:40]):
            scores[key] += 1 / (60 + position)
            channels.setdefault(key, set()).add("lexical")
    allowed = {item["id"]: item for item in candidates}
    for matches in semantic or []:
        for position, key in enumerate(matches):
            if key in allowed:
                scores[key] += 1 / (60 + position)
                channels.setdefault(key, set()).add("semantic")
    result: list[dict[str, Any]] = []
    per_source: Counter[str] = Counter()
    for key in sorted(scores, key=lambda k: (-scores[k], k)):
        item = allowed[key]
        if per_source[item["source_id"]] >= 3:
            continue
        if any(
            old["source_id"] == item["source_id"]
            and max(old["start"], item["start"]) < min(old["end"], item["end"])
            for old in result
        ):
            continue
        result.append({**item, "channels": sorted(channels[key])})
        per_source[item["source_id"]] += 1
        if len(result) >= limit:
            break
    return result
