import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from novel_writer.providers.base import ModelRequest
from novel_writer.providers.deepseek import DeepSeekChatProvider
from novel_writer.services.provider_profiles import (
    _CAPABILITY_FIELDS,
    ProviderModelOption,
    ProviderProfile,
    ProviderProfileStore,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max", "none", None])
@pytest.mark.parametrize("supported", [True, False])
async def test_deepseek_preserves_explicit_reasoning_and_confirmed_capacity(
    effort: str | None,
    supported: bool,
) -> None:
    request = ModelRequest.model_validate(
        {
            "model": "fixture-model",
            "system_prompt": "Return visible JSON.",
            "user_prompt": "Synthetic request.",
            "max_output_tokens": 3024,
            "max_content_tokens": 1024,
            "json_schema_name": "fixture",
            "json_schema": {"type": "object"},
            "reasoning_effort": effort,
        }
    )
    original = request.model_dump()
    calls = 0

    async def handler(incoming: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(incoming.content)
        enabled = supported and effort not in {None, "none"}
        assert payload["thinking"] == {"type": "enabled" if enabled else "disabled"}
        if enabled:
            assert payload.get("reasoning_effort") == effort
        else:
            assert "reasoning_effort" not in payload
        assert payload["max_tokens"] == 3024
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await DeepSeekChatProvider(
            client=client,
            supports_reasoning_effort=supported,
        ).generate(request, "fixture-key")
    assert calls == 1
    assert request.model_dump() == original






@pytest.mark.parametrize(
    "model_id,prices",
    [
        ("deepseek-v4-pro", ("9.0", "27.0")),
        ("deepseek-v4-flash", ("3.0", "9.0")),
    ],
)
def test_builtin_deepseek_recommends_peak_uncached_price(
    tmp_path: Path,
    model_id: str,
    prices: tuple[str, str],
) -> None:
    profile = ProviderProfileStore(tmp_path / "profiles.json").get("deepseek")
    assert profile is not None
    model = next(option for option in profile.models if option.id == model_id)
    assert model.input_price_cny_per_million == Decimal(prices[0])
    assert model.output_price_cny_per_million == Decimal(prices[1])


def _priced_profile() -> ProviderProfile:
    return ProviderProfile(
        id="fixture-profile",
        display_name="Fixture",
        protocol="deepseek_chat",
        base_url="https://fixture.example",
        default_model="model-one",
        models=[
            ProviderModelOption(
                id=model_id,
                input_price_cny_per_million=Decimal("1"),
                output_price_cny_per_million=Decimal("2"),
            )
            for model_id in ("model-one", "model-two")
        ],
    )


def _verified(option: ProviderModelOption) -> ProviderModelOption:
    return option.model_copy(
        update={
            "context_window": 100_000,
            "max_output_tokens": 10_000,
            "verified_at": datetime(2026, 9, 5, tzinfo=UTC),
            "verification_version": "fixture-v1",
            "verification_evidence": {"source": "immutable-fixture"},
        }
    )


def _save_legacy_capabilities(store: ProviderProfileStore, profile: ProviderProfile) -> None:
    value = profile.model_dump(mode="json")
    for model in value["models"]:
        for field in _CAPABILITY_FIELDS:
            model.pop(field, None)
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    store.capability_path.write_text(
        json.dumps(
            {
                "capabilities": [
                    {
                        "profile_id": profile.id,
                        "model_id": option.id,
                        "profile_contract_sha256": digest,
                        "capability": _verified(option).model_dump(mode="json"),
                    }
                    for option in profile.models
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("legacy", [True, False])
def test_price_change_preserves_capabilities_without_restoring_old_prices(
    tmp_path: Path,
    legacy: bool,
) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    original = store.save(_priced_profile())
    if legacy:
        _save_legacy_capabilities(store, original)
    else:
        for option in original.models:
            store.record_verified_capability(original.id, option.id, _verified(option))
    raw_overlay = store.capability_path.read_bytes()
    updated = original.model_copy(
        update={
            "models": [
                option.model_copy(
                    update={
                        "input_price_cny_per_million": Decimal("9"),
                        "output_price_cny_per_million": Decimal("27"),
                    }
                )
                for option in original.models
            ]
        }
    )
    store.save(updated)
    loaded = store.get(original.id)
    assert loaded is not None
    assert all(option.verified_at is not None for option in loaded.models)
    assert all(option.input_price_cny_per_million == Decimal("9") for option in loaded.models)
    assert all(option.output_price_cny_per_million == Decimal("27") for option in loaded.models)
    assert store.capability_path.read_bytes() == raw_overlay

    store.record_verified_capability(original.id, loaded.models[0].id, _verified(loaded.models[0]))
    reloaded = store.get(original.id)
    assert reloaded is not None
    assert all(option.verified_at is not None for option in reloaded.models)
    assert all(
        option.verification_evidence == {"source": "immutable-fixture"}
        for option in reloaded.models
    )

    store.save(updated.model_copy(update={"base_url": "https://changed.example"}))
    changed = store.get(original.id)
    assert changed is not None
    assert all(option.verified_at is None for option in changed.models)


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "https://changed.example"},
        {"protocol": "openai_chat_completions"},
        {"structured_output_mode": "prompt_only"},
        {"supports_reasoning_effort": True},
        {"allow_story_data": False},
        {"default_model": "model-two"},
    ],
)
def test_price_compatibility_never_hides_a_provider_contract_change(
    tmp_path: Path,
    legacy: bool,
    change: dict[str, object],
) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    original = store.save(_priced_profile())
    if legacy:
        _save_legacy_capabilities(store, original)
    else:
        for option in original.models:
            store.record_verified_capability(original.id, option.id, _verified(option))
    changed = original.model_copy(
        update={
            **change,
            "models": [
                option.model_copy(
                    update={
                        "output_price_cny_per_million": Decimal("27"),
                    }
                )
                for option in original.models
            ],
        }
    )
    store.save(changed)
    loaded = store.get(original.id)
    assert loaded is not None
    assert all(option.verified_at is None for option in loaded.models)


def test_legacy_capability_requires_complete_proof_of_price_only_changes(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    original = store.save(_priced_profile())
    _save_legacy_capabilities(store, original)
    overlay = json.loads(store.capability_path.read_text(encoding="utf-8"))
    overlay["capabilities"] = overlay["capabilities"][:1]
    store.capability_path.write_text(json.dumps(overlay), encoding="utf-8")
    store.save(
        original.model_copy(
            update={
                "models": [
                    option.model_copy(
                        update={
                            "output_price_cny_per_million": Decimal("27"),
                        }
                    )
                    for option in original.models
                ]
            }
        )
    )
    loaded = store.get(original.id)
    assert loaded is not None
    assert all(option.verified_at is None for option in loaded.models)


def test_invalid_capability_digest_cannot_fall_back_to_a_legacy_digest(tmp_path: Path) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    original = store.save(_priced_profile())
    store.record_verified_capability(
        original.id, original.models[0].id, _verified(original.models[0])
    )
    overlay = json.loads(store.capability_path.read_text(encoding="utf-8"))
    overlay["capabilities"][0]["profile_capability_sha256"] = "0" * 64
    store.capability_path.write_text(json.dumps(overlay), encoding="utf-8")
    loaded = store.get(original.id)
    assert loaded is not None
    assert loaded.models[0].verified_at is None


def test_builtin_price_refresh_preserves_proven_legacy_capabilities_read_only(
    tmp_path: Path,
) -> None:
    store = ProviderProfileStore(tmp_path / "profiles.json")
    current = store.get("deepseek")
    assert current is not None
    legacy = current.model_copy(
        update={
            "models": [
                option.model_copy(
                    update={
                        "input_price_cny_per_million": Decimal("1"),
                        "output_price_cny_per_million": Decimal("2"),
                    }
                )
                for option in current.models
            ]
        }
    )
    _save_legacy_capabilities(store, legacy)
    original_overlay = store.capability_path.read_bytes()
    loaded = store.get("deepseek")
    assert loaded is not None
    assert all(option.verified_at is not None for option in loaded.models)
    assert [option.input_price_cny_per_million for option in loaded.models] == [
        option.input_price_cny_per_million for option in current.models
    ]
    assert store.capability_path.read_bytes() == original_overlay
