"""Linear-time exact paragraph observations, never a fiction acceptance gate."""

import re
from typing import Any

from novel_writer.generation.content import digest

MIN_PARAGRAPH_CHARACTERS = 40
MAX_EXAMPLES = 3


def observe(body: str) -> dict[str, Any]:
    groups: dict[str, list[int]] = {}
    total = repeated = repeat_count = paragraph_count = 0
    for paragraph in re.split(r"[\r\n]+", body):
        normalized = "".join(paragraph.split()).removesuffix("<|eos|>")
        if not normalized:
            continue
        paragraph_count += 1
        total += len(normalized)
        if len(normalized) < MIN_PARAGRAPH_CHARACTERS:
            continue
        positions = groups.setdefault(normalized, [])
        if positions:
            repeated += len(normalized)
            repeat_count += 1
        positions.append(paragraph_count)
    duplicates = [(p, positions) for p, positions in groups.items() if len(positions) > 1]
    return {
        "method": "exact-paragraph-v1", "body_sha256": digest(body),
        "minimum_paragraph_characters": MIN_PARAGRAPH_CHARACTERS,
        "characters": total, "paragraphs": paragraph_count,
        "repeated_characters": repeated, "repeated_paragraphs": repeat_count,
        "percent": round(repeated / total * 100, 1) if total else 0.0,
        "groups": len(duplicates),
        "examples": [
            {"first_paragraph": positions[0], "repeat_paragraph": positions[1],
             "occurrences": len(positions), "paragraph_characters": len(p)}
            for p, positions in duplicates[:MAX_EXAMPLES]
        ],
    }
