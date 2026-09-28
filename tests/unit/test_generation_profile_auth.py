from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_writer.generation.content import fingerprint
from novel_writer.generation.novel import revision_for
from novel_writer.generation.progression_contract import contract_for
from novel_writer.generation.service import GenerationService
from novel_writer.services.errors import ConflictError
from novel_writer.services.provider_profiles import ProviderProfileStore
from tests.unit.test_genre_generation import spec
from tests.unit.test_provider_profiles import profile


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen_scheme", [None, "bearer", "raw"])
async def test_frozen_profile_authentication_is_not_upgraded(tmp_path, monkeypatch, frozen_scheme):
    profiles = ProviderProfileStore(tmp_path / "profiles.json")
    configured = profile(authorization_scheme=frozen_scheme or "bearer")
    profiles.save(configured)
    frozen = configured.model_dump(mode="json")
    if frozen_scheme is None:
        frozen.pop("authorization_scheme")
        frozen.pop("chat_template_enable_thinking")
    requested = spec(profile_id=configured.id)
    snapshot = {"profile": frozen}
    snapshot["prompt_contract_sha256"] = contract_for(requested, snapshot)
    batch = SimpleNamespace(
        project_id=uuid4(), base_version_id=requested.base_version_id,
        spec=requested.model_dump(mode="json"), snapshot=snapshot,
        revision=revision_for(requested),
    )
    batch.preview_sha256 = fingerprint({
        "revision": batch.revision, "spec": batch.spec, "snapshot": snapshot,
    })
    before = deepcopy(snapshot)
    service = GenerationService(None, profiles)
    monkeypatch.setattr(service, "_project", AsyncMock(return_value=SimpleNamespace(
        current_version_id=batch.base_version_id, archived_at=None,
    )))
    await service.assert_current(batch)
    assert snapshot == before

    profiles.save(profile(
        authorization_scheme=frozen_scheme or "bearer", chat_template_enable_thinking=False,
    ))
    with pytest.raises(ConflictError, match="模型配置已变化"):
        await service.assert_current(batch)
    assert snapshot == before

    profiles.save(profile(authorization_scheme="bearer" if frozen_scheme == "raw" else "raw"))
    with pytest.raises(ConflictError, match="模型配置已变化"):
        await service.assert_current(batch)
    assert snapshot == before
