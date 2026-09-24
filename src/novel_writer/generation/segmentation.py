"""Globally feasible paragraph partitions; every source character is retained."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from novel_writer.generation.content import digest, paragraphs


def segment_body(body: str, target: int, maximum: int, namespace: str) -> dict[str, Any]:
    points = sorted({0, len(body), *(p["start"] for p in paragraphs(body))})
    # Broaden both sides together once. The final chapter uses exactly the same bounds.
    best: tuple[int, float, list[int], float] | None = None
    for tolerance in (0.25, 0.5):
        low, high = int(target * (1 - tolerance)), int(target * (1 + tolerance))
        paths: dict[int, tuple[float, list[int]]] = {0: (0, [0])}
        for _ in range(maximum):
            following: dict[int, tuple[float, list[int]]] = {}
            for start, (score, path) in paths.items():
                for end in points[
                    bisect_left(points, start + low) : bisect_right(points, start + high)
                ]:
                    cost = score + ((end - start - target) / target) ** 2
                    if end not in following or cost < following[end][0]:
                        following[end] = cost, [*path, end]
            for end, (score, path) in following.items():
                proposal = (end, -score, path, tolerance)
                if best is None or proposal[:2] > best[:2]:
                    best = proposal
            paths = following
        if best and best[0] == len(body):
            break
    cuts = best[2] if best else [0]
    segments = []
    for n, (start, end) in enumerate(zip(cuts, cuts[1:], strict=False), 1):
        segments.append(
            {
                "id": str(uuid5(NAMESPACE_URL, f"{namespace}:{digest(body)}:{start}:{end}")),
                "number": n,
                "start": start,
                "end": end,
                "body_sha256": digest(body[start:end]),
            }
        )
    end = cuts[-1]
    return {
        "body_sha256": digest(body),
        "segments": segments,
        "tail": {"start": end, "end": len(body)} if end < len(body) else None,
        "tolerance": best[3] if best else None,
        "lossless": True,
    }
