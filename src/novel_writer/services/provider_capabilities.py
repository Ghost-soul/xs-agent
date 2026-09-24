from __future__ import annotations

from typing import Literal

StructuredOutputCapability = Literal[
    "json_schema_strict",
    "json_object",
    "prompt_only_schema",
]


