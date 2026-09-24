"""Current creation inputs; frozen specifications retain their original contracts."""

from typing import Literal

from novel_writer.generation.schemas import AmendmentRequest, NovelRunSpec


class NewGenerationRequest(NovelRunSpec):
    enable_reader: Literal[False] = False
    milestone_unit: None = None
    feedback_policy: Literal["advisory-v1", "logic-v1"] = "logic-v1"


class NewAmendmentRequest(AmendmentRequest):
    enable_reader: Literal[False] = False
    feedback_policy: Literal["advisory-v1", "logic-v1"] = "logic-v1"
