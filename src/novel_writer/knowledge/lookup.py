"""Bounded, rebuildable lexical indexes; never an authoritative source of story facts."""

from __future__ import annotations

import heapq
import math
import re
import sys
import threading
from array import array
from collections import Counter, OrderedDict, defaultdict
from typing import Any

from novel_writer.generation.content import digest
from novel_writer.knowledge.text import chunks, corpus_hash, terms

MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_CACHE_ENTRIES = 2
_cache: OrderedDict[tuple[str, str], Corpus] = OrderedDict()
_lock = threading.RLock()


def _retained_size(value: Any) -> int:
    """Account for shared objects once; arrays include their packed backing storage."""
    seen: set[int] = set()
    pending = [value]
    result = 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        result += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple | set):
            pending.extend(item)
    return result


class Corpus:
    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self.pieces = chunks(sources)
        for item in self.pieces:
            item["entity_ids"] = list(item["entity_ids"])
        self.by_key = {item["id"]: i for i, item in enumerate(self.pieces)}
        self.postings: dict[str, array[int]] = {}
        self.by_source: dict[str, array[int]] = {}
        for i, item in enumerate(self.pieces):
            self.by_source.setdefault(item["source_id"], array("I")).append(i)
            for token in terms(item["text"]):
                self.postings.setdefault(token, array("I")).append(i)
        self.retained_bytes = _retained_size(
            [self.pieces, self.by_key, self.postings, self.by_source]
        )

    def rank(
        self,
        queries: list[str],
        semantic: list[list[str]] | None = None,
        *,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        scores: dict[int, float] = defaultdict(float)
        channels: dict[int, set[str]] = {}
        total = len(self.pieces)
        for query in queries:
            wanted = terms(query)
            lexical: dict[int, float] = defaultdict(float)
            for token in sorted(wanted):
                matches = self.postings.get(token)
                if matches:
                    weight = math.log(1 + total / len(matches))
                    for i in matches:
                        lexical[i] += weight
            for key, matches in self.by_source.items():
                if key in query:
                    for i in matches:
                        lexical[i] += 20
            # An exact Chinese substring must contain this bigram. Other searches
            # retain substring semantics (including a single character or an English suffix).
            chinese = re.search(r"[\u3400-\u9fff]{2}", query)
            exact_candidates = (
                self.postings.get(chinese[0], ()) if chinese else range(total)
            )
            folded = query.casefold()
            if query.strip():
                for i in exact_candidates:
                    if folded in self.pieces[i]["text"].casefold():
                        lexical[i] += 40
            # Round off summation-order noise before applying the deterministic key tie break.
            top = heapq.nsmallest(
                40, lexical, key=lambda i: (-round(lexical[i], 12), self.pieces[i]["id"])
            )
            for position, i in enumerate(top):
                scores[i] += 1 / (60 + position)
                channels.setdefault(i, set()).add("lexical")
        for semantic_keys in semantic or []:
            for position, chunk_key in enumerate(semantic_keys):
                candidate = self.by_key.get(chunk_key)
                if candidate is not None:
                    scores[candidate] += 1 / (60 + position)
                    channels.setdefault(candidate, set()).add("semantic")
        result: list[dict[str, Any]] = []
        per_source: Counter[str] = Counter()
        for i in sorted(scores, key=lambda i: (-scores[i], self.pieces[i]["id"])):
            item = self.pieces[i]
            if per_source[item["source_id"]] >= 3:
                continue
            if any(
                old["source_id"] == item["source_id"]
                and max(old["start"], item["start"]) < min(old["end"], item["end"])
                for old in result
            ):
                continue
            result.append({**item, "entity_ids": list(item["entity_ids"]),
                           "channels": sorted(channels[i])})
            per_source[item["source_id"]] += 1
            if len(result) >= limit:
                break
        return result


def prepare(scope: str, sources: list[dict[str, Any]]) -> Corpus:
    # Check actual incoming text before reusing a key. Stored source hashes alone
    # cannot authorize changed text in a frozen snapshot.
    if any(digest(item["text"]) != item["sha256"] for item in sources):
        raise ValueError("知识来源内容校验失败")
    key = (scope, corpus_hash(sources))
    # Serialize cold builds so concurrent requests cannot multiply peak allocations.
    with _lock:
        found = _cache.get(key)
        if found is not None:
            _cache.move_to_end(key)
            return found
        result = Corpus(sources)
        if result.retained_bytes <= MAX_CACHE_BYTES:
            _cache[key] = result
            while len(_cache) > MAX_CACHE_ENTRIES or sum(
                value.retained_bytes for value in _cache.values()
            ) > MAX_CACHE_BYTES:
                _cache.popitem(last=False)
        return result


def clear() -> None:
    with _lock:
        _cache.clear()


def cache_info() -> dict[str, int]:
    with _lock:
        return {
            "entries": len(_cache),
            "retained_bytes": sum(value.retained_bytes for value in _cache.values()),
            "limit_bytes": MAX_CACHE_BYTES,
        }
