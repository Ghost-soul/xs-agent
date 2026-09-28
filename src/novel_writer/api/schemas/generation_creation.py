"""Current creation inputs; frozen specifications retain their original contracts."""

from typing import Literal

from novel_writer.generation.craft_models import CraftSpec
from novel_writer.generation.schemas import AmendmentRequest


class NewGenerationRequest(CraftSpec):
    enable_reader: Literal[False] = False
    milestone_unit: None = None
    feedback_policy: Literal["logic-v1"] = "logic-v1"


class NewAmendmentRequest(AmendmentRequest):
    craft_policy: Literal["stage-craft-v1"] = "stage-craft-v1"
    enable_reader: Literal[False] = False
    feedback_policy: Literal["advisory-v1", "logic-v1"] = "logic-v1"
