"""Freeze editable defaults at new preview boundaries, including independent revisions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from novel_writer.generation.craft_models import enabled as craft_enabled
from novel_writer.generation.prompt_templates import KEY, supported
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.services.craft_template_store import CraftTemplateStore
from novel_writer.services.prompt_template_store import PromptTemplateStore

if TYPE_CHECKING:
    from novel_writer.db.models import GenerationBatchRecord
    from novel_writer.generation.service import GenerationService


async def defaults(service: GenerationService, spec: GenerationSpec) -> dict[str, Any] | None:
    if not supported(spec):
        return None
    store = (
        CraftTemplateStore(service.profiles.path)
        if craft_enabled(spec)
        else PromptTemplateStore(service.profiles.path)
    )
    bundle = await asyncio.to_thread(
        store.install if isinstance(store, CraftTemplateStore) else store.current
    )
    return bundle if bundle["templates"] or bundle.get("program_settings") else None


async def bind_reports(
    service: GenerationService,
    batch: GenerationBatchRecord,
    reports: dict[str, Any],
) -> None:
    if batch.state.get("amendment_authorized_sha256"):
        amendment = await service.artifact(batch, "amendment")
        # Explicit None prevents inheritance of the original batch's customization.
        reports[KEY] = amendment.payload.get(KEY) if amendment else None
        from novel_writer.generation.output_contract import KEY as OUTPUT_KEY

        reports[OUTPUT_KEY] = amendment.payload.get(OUTPUT_KEY) if amendment else None
        from novel_writer.generation.creative_cast import KEY as CREATIVE_KEY

        reports[CREATIVE_KEY] = amendment.payload.get(CREATIVE_KEY) if amendment else None
        from novel_writer.generation.editable_contract import KEY as RULES_KEY

        reports[RULES_KEY] = amendment.payload.get(RULES_KEY) if amendment else None
        from novel_writer.generation.unit_delivery import KEY as UNITS_KEY

        reports[UNITS_KEY] = amendment.payload.get(UNITS_KEY) if amendment else None
        from novel_writer.generation.reliability_contract import KEY as RELIABILITY_KEY

        reports[RELIABILITY_KEY] = amendment.payload.get(RELIABILITY_KEY) if amendment else None
        # Revisions have their own scope; never inherit stage expansion guidance.
        from novel_writer.generation.progression_contract import KEY as PROGRESSION_KEY

        reports[PROGRESSION_KEY] = amendment.payload.get(PROGRESSION_KEY) if amendment else None
