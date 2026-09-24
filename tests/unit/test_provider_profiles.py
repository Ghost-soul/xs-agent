from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from novel_writer.api.routes.provider_profiles import (
    TestProviderProfileRequest as ProviderConnectionTestRequest,
)
from novel_writer.api.routes.provider_profiles import (
    VerifyProviderCapabilityRequest,
    verify_provider_capability,
)
from novel_writer.api.routes.provider_profiles import (
    test_provider_profile as run_provider_profile_test,
)
from novel_writer.core.credentials import MemoryCredentialStore
from novel_writer.providers.base import (
    ModelResponse,
    ProviderFailureCode,
    ProviderResponseError,
    ProviderTerminalMetadata,
    TokenUsage,
)
from novel_writer.providers.deepseek import DeepSeekChatProvider
from novel_writer.providers.openai import OpenAIResponsesProvider
from novel_writer.providers.openai_compatible import OpenAICompatibleChatProvider
from novel_writer.services.provider_profiles import (
    ProviderModelOption,
    ProviderProfile,
    ProviderProfileStore,
    build_provider,
)


def profile(**updates: object) -> ProviderProfile:
    values: dict[str, object] = {
        "id": "openrouter-main",
        "display_name": "OpenRouter 主线路",
        "protocol": "openai_chat_completions",
        "base_url": "https://openrouter.example/api/v1",
        "structured_output_mode": "json_schema",
        "supports_reasoning_effort": True,
        "default_model": "vendor/novel-model",
        "models": [
            ProviderModelOption(
                id="vendor/novel-model",
                input_price_cny_per_million=Decimal("7.5"),
                output_price_cny_per_million=Decimal("30"),
            )
        ],
    }
    values.update(updates)
    return ProviderProfile.model_validate(values)


def test_profile_preserves_custom_base_url_and_model_ids() -> None:
    configured = profile()

    assert configured.base_url == "https://openrouter.example/api/v1"
    assert configured.default_model == "vendor/novel-model"
    assert configured.models[0].id == "vendor/novel-model"


def test_verified_compatible_model_exposes_implicit_reasoning_billing_to_writer() -> None:
    configured = profile(
        supports_reasoning_effort=False,
        models=[
            ProviderModelOption(
                id="vendor/novel-model",
                reasoning_tokens_billed_as_output=True,
            )
        ],
    )
    provider = build_provider(configured)

    assert provider.reasoning_tokens_billed_as_output("vendor/novel-model") is True
    assert provider.reasoning_tokens_billed_as_output("unknown-model") is False


def test_streaming_is_limited_to_chat_completions_profiles() -> None:
    configured = profile(streaming_enabled=True)

    assert configured.streaming_enabled is True
    with pytest.raises(ValidationError, match="only configurable for Chat Completions"):
        profile(protocol="deepseek_chat", streaming_enabled=True)
    for enabled in (True, False):
        with pytest.raises(ValidationError, match="only configurable for Chat Completions"):
            profile(
                protocol="deepseek_chat",
                models=[ProviderModelOption(id="vendor/novel-model", streaming_enabled=enabled)],
            )


def test_remote_profile_requires_https() -> None:
    with pytest.raises(ValidationError, match="remote provider profiles must use HTTPS"):
        profile(base_url="http://gateway.example/v1")


def test_local_profile_may_use_http_without_credentials() -> None:
    configured = profile(
        id="ollama-local",
        display_name="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        is_local=True,
        credential_required=False,
    )

    assert configured.is_local is True
    assert configured.credential_required is False


@pytest.mark.parametrize(
    "base_url,error",
    [
        ("https://user:secret@gateway.example/v1", "must not contain credentials"),
        ("https://gateway.example/v1?key=secret", "must not contain a query or fragment"),
        ("https://gateway.example/v1#models", "must not contain a query or fragment"),
    ],
)
def test_profile_rejects_secrets_and_volatile_parts_in_base_url(base_url: str, error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        profile(base_url=base_url)


def test_profile_store_round_trips_custom_profiles_without_credentials(tmp_path) -> None:
    path = tmp_path / "provider-profiles.json"
    store = ProviderProfileStore(path)

    saved = store.save(profile())
    loaded = ProviderProfileStore(path).get(saved.id)
    raw = path.read_text(encoding="utf-8")

    assert loaded == saved
    assert "api_key" not in raw
    assert "secret" not in raw


def test_built_in_profiles_cannot_be_overwritten_or_disabled(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    deepseek = store.get("deepseek")

    assert deepseek is not None
    with pytest.raises(ValueError, match="cannot be overwritten"):
        store.save(deepseek)
    with pytest.raises(ValueError, match="cannot be disabled"):
        store.set_enabled("deepseek", False)


def test_disabled_custom_profile_is_not_registered(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    store.save(profile(enabled=False))

    providers = store.build_enabled_providers()

    assert "openrouter-main" not in providers
    assert "deepseek" in providers
    assert "openai" in providers


def test_delete_is_persistent_and_prevents_stale_save_or_capability_revival(tmp_path):
    store = ProviderProfileStore(tmp_path / "profiles.json")
    saved = store.save(profile())
    other = store.save(profile(id="other"))
    store.record_verified_capability(saved.id, saved.default_model, saved.models[0])
    evidence = store.capability_path.read_bytes()
    store.delete(saved.id)
    reloaded = ProviderProfileStore(store.path)
    assert reloaded.get(saved.id) is None
    assert reloaded.get(other.id) == other
    assert store.capability_path.read_bytes() == evidence
    reloaded.delete(saved.id)
    with pytest.raises(ValueError, match="已删除"):
        reloaded.save(saved)
    with pytest.raises(KeyError):
        reloaded.record_verified_capability(saved.id, saved.default_model, saved.models[0])
    with pytest.raises(ValueError, match="内置"):
        reloaded.delete("deepseek")
    with pytest.raises(KeyError):
        reloaded.delete("missing")


def test_default_preference_does_not_change_dispatch_revision_or_capability(tmp_path):
    store = ProviderProfileStore(tmp_path / "profiles.json")
    original = store.get("deepseek")
    assert original is not None
    option = original.models[1].model_copy(update={"verified_at": datetime.now(UTC)})
    verified = store.record_verified_capability(original.id, option.id, option)
    revision = store.revision(verified)
    store.set_preferred_model("deepseek", option.id)
    store.save(profile())
    store.delete("openrouter-main")
    reloaded = ProviderProfileStore(store.path)
    assert reloaded.get("deepseek") == verified
    assert reloaded.revision(verified) == revision
    assert reloaded.preferred_model(verified) == option.id
    with pytest.raises(ValueError, match="模型列表"):
        reloaded.set_preferred_model("deepseek", "not-official")
    with pytest.raises(KeyError):
        reloaded.set_preferred_model("openrouter-main", "model")


def test_probe_prevents_delete_or_save_and_releases_after_failure(tmp_path):
    store = ProviderProfileStore(tmp_path / "profiles.json")
    saved = store.save(profile())
    with pytest.raises(RuntimeError), store.probe_operation(saved.id):
        with pytest.raises(ValueError, match="正在测试"):
            store.delete(saved.id)
        with pytest.raises(ValueError, match="正在测试"):
            store.save(saved)
        raise RuntimeError("fake probe failed")
    store.delete(saved.id)
    assert store.get(saved.id) is None


def test_enabled_custom_profile_builds_generic_compatible_adapter(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    store.save(profile(streaming_enabled=True))

    provider = store.build_enabled_providers()["openrouter-main"]

    assert isinstance(provider, OpenAICompatibleChatProvider)
    assert provider.name == "openrouter-main"
    assert provider._streaming_enabled is True


def test_model_streaming_overrides_profile_default(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    store.save(
        profile(
            streaming_enabled=True,
            models=[
                ProviderModelOption(id="vendor/novel-model", streaming_enabled=False),
                ProviderModelOption(id="vendor/long-model", streaming_enabled=True),
            ],
        )
    )

    provider = store.build_enabled_providers()["openrouter-main"]

    assert isinstance(provider, OpenAICompatibleChatProvider)
    assert provider._streaming_enabled is True
    assert provider._streaming_by_model == {
        "vendor/novel-model": False,
        "vendor/long-model": True,
    }


def test_legacy_openai_compatible_setting_remains_available(tmp_path) -> None:
    store = ProviderProfileStore(
        tmp_path / "provider-profiles.json",
        legacy_compatible_base_url="https://legacy.example/v1",
    )

    legacy = store.get("openai_compatible")

    assert legacy is not None
    assert legacy.built_in is True
    assert legacy.base_url == "https://legacy.example/v1"
    assert legacy.default_model == "custom-model"


def test_built_in_capability_uses_revision_bound_local_overlay(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    built_in = store.get("deepseek")
    assert built_in is not None
    option = next(item for item in built_in.models if item.id == "deepseek-v4-flash")
    verified = option.model_copy(
        update={
            "context_window": 1_000_000,
            "max_output_tokens": 384_000,
            "supports_reasoning_effort": True,
            "structured_output_modes": frozenset({"json_object"}),
            "supports_usage": True,
            "supports_streaming": False,
            "stream_terminal_reliable": False,
            "finish_reason_semantics": {"success": "stop", "output_limit": "length"},
            "reasoning_tokens_billed_as_output": True,
            "verified_at": datetime.now(UTC),
            "verification_version": "fixture-v1",
        }
    )

    saved = store.record_verified_capability("deepseek", option.id, verified)

    assert saved.built_in is True
    assert store.capability_path.exists()
    assert not store.path.exists()
    loaded = store.get("deepseek")
    assert loaded is not None
    loaded_option = next(item for item in loaded.models if item.id == option.id)
    assert loaded_option.context_window == 1_000_000
    assert loaded_option.verification_version == "fixture-v1"


def test_custom_profile_change_invalidates_capability_overlay(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    option = configured.models[0].model_copy(
        update={
            "context_window": 128_000,
            "max_output_tokens": 32_000,
            "supports_reasoning_effort": True,
            "structured_output_modes": frozenset({"json_schema_strict"}),
            "supports_usage": True,
            "supports_streaming": False,
            "stream_terminal_reliable": False,
            "finish_reason_semantics": {"success": "stop", "output_limit": "length"},
            "reasoning_tokens_billed_as_output": True,
            "verified_at": datetime.now(UTC),
            "verification_version": "fixture-v1",
        }
    )
    store.record_verified_capability(configured.id, option.id, option)
    verified_profile = store.get(configured.id)
    assert verified_profile is not None
    assert verified_profile.models[0].verified_at is not None

    store.save(configured.model_copy(update={"base_url": "https://changed.example/v1"}))

    changed = store.get(configured.id)
    assert changed is not None
    assert changed.models[0].verified_at is None


def test_custom_openai_responses_profile_keeps_reasoning_capability(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    store.save(
        profile(
            id="reasoning-gateway",
            display_name="Reasoning Gateway",
            protocol="openai_responses",
            base_url="https://reasoning.example/v1",
        )
    )

    provider = store.build_enabled_providers()["reasoning-gateway"]

    assert isinstance(provider, OpenAIResponsesProvider)
    assert provider.name == "reasoning-gateway"
    assert provider.supports_reasoning_effort is True
    assert provider.protocol == "openai_responses"


def test_custom_deepseek_protocol_profile_uses_protocol_not_profile_id(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    store.save(
        profile(
            id="deepseek-proxy",
            display_name="DeepSeek Proxy",
            protocol="deepseek_chat",
            base_url="https://deepseek-proxy.example/v1",
            supports_reasoning_effort=False,
        )
    )

    provider = store.build_enabled_providers()["deepseek-proxy"]

    assert isinstance(provider, DeepSeekChatProvider)
    assert provider.name == "deepseek-proxy"
    assert provider.protocol == "deepseek_chat"
    assert provider.supports_reasoning_effort is False


@pytest.mark.asyncio
async def test_profile_connection_test_uses_minimal_non_story_request(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=ModelResponse(
                text='{"ok":true}',
                usage=TokenUsage(input_tokens=8, output_tokens=4),
                request_id="connection-test-1",
            )
        )
    )
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider",
        lambda _profile: provider,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore({configured.id: "test-key"}),
            )
        )
    )

    result = await run_provider_profile_test(
        configured.id,
        ProviderConnectionTestRequest(confirmed=True),
        request,
    )

    assert result["ok"] is True
    assert result["request_id"] == "connection-test-1"
    generated_request, api_key = provider.generate.await_args.args
    assert api_key == "test-key"
    assert generated_request.max_output_tokens == 5
    assert generated_request.json_schema_name == "provider_connection_test"
    assert generated_request.system_prompt == "Connection test."
    assert generated_request.skip_structured_output is True
    assert generated_request.force_non_streaming is True
    assert "story" not in generated_request.user_prompt.casefold()


@pytest.mark.asyncio
async def test_connection_uses_saved_official_default_and_rejects_unknown_model(
    tmp_path, monkeypatch
):
    store = ProviderProfileStore(tmp_path / "profiles.json")
    store.set_preferred_model("deepseek", "deepseek-v4-flash")
    provider = SimpleNamespace(
        generate=AsyncMock(return_value=ModelResponse(
            text="Hello", raw_response="{}", usage=TokenUsage(input_tokens=1, output_tokens=1)
        ))
    )
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider", lambda _profile: provider
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        provider_profile_store=store,
        credential_store=MemoryCredentialStore({"deepseek": "fake-key"}),
    )))
    result = await run_provider_profile_test(
        "deepseek", ProviderConnectionTestRequest(confirmed=True), request
    )
    assert result["model"] == "deepseek-v4-flash"
    assert provider.generate.await_args.args[0].model == "deepseek-v4-flash"
    with pytest.raises(HTTPException) as error:
        await run_provider_profile_test(
            "deepseek", ProviderConnectionTestRequest(confirmed=True, model="not-listed"), request
        )
    assert error.value.status_code == 404
    assert provider.generate.await_count == 1


@pytest.mark.asyncio
async def test_profile_connection_test_requires_configured_credential(tmp_path) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore(),
            )
        )
    )

    with pytest.raises(HTTPException) as captured:
        await run_provider_profile_test(
            configured.id,
            ProviderConnectionTestRequest(confirmed=True),
            request,
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == "请先保存 API Key"


@pytest.mark.asyncio
async def test_profile_connection_test_accepts_truncated_response_with_content(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    provider = SimpleNamespace(
        generate=AsyncMock(
            side_effect=ProviderResponseError(
                "OpenAI-compatible output reached the Provider token limit",
                '{"choices":[{"message":{"content":"Hi!"},"finish_reason":"length"}]}',
                code=ProviderFailureCode.TOKEN_LIMIT_EXCEEDED,
                extracted_content="Hi!",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                request_id="probe-truncated",
            )
        )
    )
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider",
        lambda _profile: provider,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore({configured.id: "test-key"}),
            )
        )
    )

    result = await run_provider_profile_test(
        configured.id,
        ProviderConnectionTestRequest(confirmed=True),
        request,
    )

    assert result["ok"] is True
    assert result["truncated"] is True
    assert result["request_id"] == "probe-truncated"


@pytest.mark.asyncio
async def test_profile_connection_test_exposes_bounded_provider_error_body(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    provider = SimpleNamespace(
        generate=AsyncMock(
            side_effect=ProviderResponseError(
                "OpenAI-compatible HTTP response status was 403",
                "  access denied\nby gateway  " + "x" * 600,
            )
        )
    )
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider",
        lambda _profile: provider,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore({configured.id: "test-key"}),
            )
        )
    )

    with pytest.raises(HTTPException) as captured:
        await run_provider_profile_test(
            configured.id,
            ProviderConnectionTestRequest(confirmed=True),
            request,
        )

    assert captured.value.status_code == 502
    assert "网关响应：access denied by gateway" in captured.value.detail
    assert len(captured.value.detail) < 650


@pytest.mark.asyncio
async def test_capability_verification_uses_exactly_two_non_story_probes_and_persists_evidence(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(
        profile(
            structured_output_mode="json_object",
            supports_reasoning_effort=False,
            streaming_enabled=False,
        )
    )
    success = ModelResponse(
        text='{"ok":true}',
        usage=TokenUsage(input_tokens=20, output_tokens=5),
        request_id="capability-success",
        raw_response='{"success":true}',
        terminal=ProviderTerminalMetadata(
            protocol="openai_chat_completions",
            terminal_event_seen=True,
            terminal_status="completed",
            finish_reason="stop",
        ),
    )
    limit_error = ProviderResponseError(
        "output limit",
        '{"limited":true}',
        code=ProviderFailureCode.TOKEN_LIMIT_EXCEEDED,
        usage=TokenUsage(input_tokens=22, output_tokens=1),
        request_id="capability-limit",
        terminal=ProviderTerminalMetadata(
            protocol="openai_chat_completions",
            terminal_event_seen=True,
            finish_reason="length",
        ),
    )
    provider = SimpleNamespace(generate=AsyncMock(side_effect=[success, limit_error]))
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider",
        lambda _profile: provider,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore({configured.id: "test-key"}),
            )
        )
    )

    result = await verify_provider_capability(
        configured.id,
        VerifyProviderCapabilityRequest(
            confirmed=True,
            acknowledge_provider_calls_and_cost=True,
            expected_provider_calls=2,
            model="vendor/novel-model",
            context_window=128_000,
            max_output_tokens=32_000,
            reasoning_tokens_billed_as_output=False,
            source_note="Provider documentation checked by the author",
            max_cost_cny=Decimal("0.1"),
        ),
        request,
    )

    assert result["verified"] is True
    assert result["provider_calls"] == 2
    assert provider.generate.await_count == 2
    requests = [call.args[0] for call in provider.generate.await_args_list]
    assert [item.max_output_tokens for item in requests] == [32, 1]
    assert all("story data" in item.user_prompt for item in requests)
    saved = store.get(configured.id)
    assert saved is not None
    model = saved.models[0]
    assert model.context_window == 128_000
    assert model.max_output_tokens == 32_000
    assert model.supports_usage is True
    assert model.finish_reason_semantics == {"success": "stop", "output_limit": "length"}
    assert model.verified_at is not None
    assert model.verification_version is not None
    assert model.verification_evidence is not None
    evidence = list((tmp_path / "provider-capability-audits").rglob("*.json"))
    assert len(evidence) == 2
    assert all("test-key" not in item.read_text(encoding="utf-8") for item in evidence)


@pytest.mark.asyncio
async def test_capability_verification_failure_does_not_mark_model_verified(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    provider = SimpleNamespace(generate=AsyncMock(side_effect=RuntimeError("gateway down")))
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider",
        lambda _profile: provider,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore({configured.id: "test-key"}),
            )
        )
    )

    with pytest.raises(HTTPException) as captured:
        await verify_provider_capability(
            configured.id,
            VerifyProviderCapabilityRequest(
                confirmed=True,
                acknowledge_provider_calls_and_cost=True,
                expected_provider_calls=2,
                model="vendor/novel-model",
                context_window=128_000,
                max_output_tokens=32_000,
                reasoning_tokens_billed_as_output=True,
                source_note="Provider documentation checked by the author",
                max_cost_cny=Decimal("0.1"),
            ),
            request,
        )

    assert captured.value.status_code == 502
    assert provider.generate.await_count == 1
    saved = store.get(configured.id)
    assert saved is not None
    assert saved.models[0].verified_at is None
    assert len(list((tmp_path / "provider-capability-audits").rglob("*.json"))) == 1


@pytest.mark.asyncio
async def test_profile_connection_test_returns_readable_gateway_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProviderProfileStore(tmp_path / "provider-profiles.json")
    configured = store.save(profile())
    provider = SimpleNamespace(generate=AsyncMock(side_effect=RuntimeError("404 Not Found")))
    monkeypatch.setattr(
        "novel_writer.api.routes.provider_profiles.build_provider",
        lambda _profile: provider,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=store,
                credential_store=MemoryCredentialStore({configured.id: "test-key"}),
            )
        )
    )

    with pytest.raises(HTTPException) as captured:
        await run_provider_profile_test(
            configured.id,
            ProviderConnectionTestRequest(confirmed=True),
            request,
        )

    assert captured.value.status_code == 502
    assert "404 Not Found" in str(captured.value.detail)
