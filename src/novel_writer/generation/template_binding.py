"""Freeze editable defaults at new preview boundaries, including independent revisions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from novel_writer.generation.prompt_templates import KEY, supported
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.prompt_template_store import PromptTemplateStore

if TYPE_CHECKING:
    from novel_writer.db.models import GenerationBatchRecord
    from novel_writer.generation.service import GenerationService


async def defaults(service: GenerationService, spec: GenerationSpec) -> dict[str, Any] | None:
    if not supported(spec):
        return None
    bundle = await asyncio.to_thread(PromptTemplateStore(service.profiles.path).current)
    return bundle if bundle["templates"] else None


async def bind_reports(
    service: GenerationService,
    batch: GenerationBatchRecord,
    reports: dict[str, Any],
) -> None:
    if batch.state.get("amendment_authorized_sha256"):
        amendment = await service.artifact(batch, "amendment")
        # Explicit None prevents inheritance of the original batch's customization.
        reports[KEY] = amendment.payload.get(KEY) if amendment else None
