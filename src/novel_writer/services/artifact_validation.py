from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ArtifactStatus = Literal["complete", "partial", "unusable"]


class RejectedComponent(BaseModel):
    component: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ArtifactValidation(BaseModel):
    """Business usability of a preserved Provider response.

    Provider call status remains an immutable billing/audit fact.  This sidecar
    records whether the response produced all, some, or none of the role-owned
    components without turning soft quality loss into another paid call.
    """

    status: ArtifactStatus = "complete"
    accepted_components: list[str] = Field(default_factory=list)
    rejected_components: list[RejectedComponent] = Field(default_factory=list)
    unresolved_requirements: list[str] = Field(default_factory=list)
    recovery: str | None = None


