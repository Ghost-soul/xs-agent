from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel


class FrozenModel(BaseModel):
    model_config = {"frozen": True}



class Identified(Protocol):
    @property
    def id(self) -> UUID: ...


