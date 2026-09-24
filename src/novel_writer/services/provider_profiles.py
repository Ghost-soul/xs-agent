from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, model_validator

from novel_writer.providers.base import ModelProvider
from novel_writer.providers.deepseek import DeepSeekChatProvider
from novel_writer.providers.openai import OpenAIResponsesProvider
from novel_writer.providers.openai_compatible import OpenAICompatibleChatProvider
from novel_writer.services.provider_capabilities import StructuredOutputCapability

ProviderProtocol = Literal["deepseek_chat", "openai_responses", "openai_chat_completions"]
StructuredOutputMode = Literal["json_schema", "json_object", "prompt_only"]
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")


class ProviderModelOption(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    label: str | None = Field(default=None, max_length=120)
    input_price_cny_per_million: Decimal | None = Field(default=None, ge=0)
    output_price_cny_per_million: Decimal | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    streaming_enabled: bool | None = None
    supports_reasoning_effort: bool | None = None
    structured_output_modes: frozenset[StructuredOutputCapability] = frozenset()
    supports_usage: bool | None = None
    supports_streaming: bool | None = None
    stream_terminal_reliable: bool | None = None
    finish_reason_semantics: dict[str, str] | None = None
    reasoning_tokens_billed_as_output: bool | None = None
    verified_at: datetime | None = None
    verification_version: str | None = Field(default=None, max_length=80)
    verification_evidence: dict[str, Any] | None = None


class ProviderProfile(BaseModel):
    id: str = Field(min_length=2, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    protocol: ProviderProtocol
    base_url: str = Field(min_length=8, max_length=500)
    enabled: bool = True
    is_local: bool = False
    credential_required: bool = True
    allow_story_data: bool = True
    structured_output_mode: StructuredOutputMode = "json_object"
    supports_reasoning_effort: bool = False
    streaming_enabled: bool = False
    default_model: str = Field(min_length=1, max_length=160)
    models: list[ProviderModelOption] = Field(min_length=1, max_length=100)
    built_in: bool = False

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if not _PROFILE_ID.fullmatch(self.id):
            raise ValueError(
                "provider profile id must use lowercase letters, digits, hyphens, or underscores"
            )
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("provider base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("provider base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("provider base_url must not contain a query or fragment")
        if parsed.scheme != "https" and not self.is_local:
            raise ValueError("remote provider profiles must use HTTPS")
        model_ids = [item.id for item in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("provider model ids must be unique")
        if self.default_model not in model_ids:
            raise ValueError("default_model must be included in models")
        if self.protocol == "openai_responses" and self.structured_output_mode != "json_schema":
            raise ValueError("OpenAI Responses profiles require json_schema structured output")
        model_streaming_configured = any(item.streaming_enabled is not None for item in self.models)
        if (
            self.streaming_enabled or model_streaming_configured
        ) and self.protocol != "openai_chat_completions":
            raise ValueError("streaming is only configurable for Chat Completions profiles")
        return self


class ProviderProfileStore:
    """Local profile registry. API keys are deliberately stored elsewhere."""

    def __init__(self, path: Path, *, legacy_compatible_base_url: str | None = None) -> None:
        self.path = path
        self.legacy_compatible_base_url = legacy_compatible_base_url
        self._active_operations: set[str] = set()

    def assert_idle(self, profile_id: str) -> None:
        if profile_id in self._active_operations:
            raise ValueError("该配置正在测试或验证能力，请等待请求结束")

    @contextmanager
    def probe_operation(self, profile_id: str) -> Iterator[None]:
        self.assert_idle(profile_id)
        self._active_operations.add(profile_id)
        try:
            yield
        finally:
            self._active_operations.discard(profile_id)

    def preferred_model(self, profile: ProviderProfile) -> str:
        """Return the UI default without changing dispatch or authorization snapshots."""

        value = self._data().get("default_models", {}).get(profile.id)
        if isinstance(value, str) and any(model.id == value for model in profile.models):
            return value
        return profile.default_model

    def set_preferred_model(self, profile_id: str, model_id: str) -> ProviderProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        if not any(model.id == model_id for model in profile.models):
            raise ValueError("默认模型必须来自该配置的模型列表")
        data = self._data()
        data.setdefault("default_models", {})[profile_id] = model_id
        self._write_data(data)
        return profile

    def revision(self, profile: ProviderProfile) -> str:
        return _profile_contract_sha256(profile)

    def is_deleted(self, profile_id: str) -> bool:
        return profile_id in self._data().get("deleted_profile_ids", [])

    def delete(self, profile_id: str) -> None:
        self.assert_idle(profile_id)
        profile = self.get(profile_id)
        if profile is None:
            if self.is_deleted(profile_id):
                return
            raise KeyError(profile_id)
        if profile.built_in:
            raise ValueError("内置供应商入口不能删除，可选择默认模型")
        data = self._data()
        data["profiles"] = [
            item for item in data.get("profiles", []) if item["id"] != profile_id
        ]
        data.setdefault("default_models", {}).pop(profile_id, None)
        data.setdefault("deleted_profile_ids", []).append(profile_id)
        self._write_data(data)

    def list_profiles(self) -> list[ProviderProfile]:
        profiles = {item.id: item for item in _built_in_profiles()}
        if self.legacy_compatible_base_url:
            legacy = ProviderProfile(
                id="openai_compatible",
                display_name="OpenAI 兼容端点",
                protocol="openai_chat_completions",
                base_url=self.legacy_compatible_base_url,
                default_model="custom-model",
                models=[ProviderModelOption(id="custom-model", label="自定义模型")],
                structured_output_mode="json_object",
                built_in=True,
            )
            profiles[legacy.id] = legacy
        for profile in self._custom_profiles():
            profiles[profile.id] = profile
        profiles = self._apply_capability_overlays(profiles)
        return sorted(profiles.values(), key=lambda item: (not item.built_in, item.display_name))

    def get(self, profile_id: str) -> ProviderProfile | None:
        return next((item for item in self.list_profiles() if item.id == profile_id), None)

    def save(self, profile: ProviderProfile, *, reset_preference: bool = True) -> ProviderProfile:
        self.assert_idle(profile.id)
        if self.is_deleted(profile.id):
            raise ValueError("该配置已删除，不能由旧页面恢复；新增配置请使用新的配置 ID")
        if (
            profile.id in {item.id for item in _built_in_profiles()}
            or profile.id == "openai_compatible"
        ):
            raise ValueError("built-in provider profiles cannot be overwritten")
        normalized = profile.model_copy(update={"built_in": False})
        current = {item.id: item for item in self._custom_profiles()}
        current[normalized.id] = normalized
        data = self._data()
        if reset_preference:
            data.setdefault("default_models", {}).pop(profile.id, None)
        data["profiles"] = [item.model_dump(mode="json") for item in current.values()]
        self._write_data(data)
        return normalized

    def set_enabled(self, profile_id: str, enabled: bool) -> ProviderProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        if profile.built_in:
            raise ValueError("built-in provider profiles cannot be disabled")
        return self.save(profile.model_copy(update={"enabled": enabled}), reset_preference=False)

    def record_verified_capability(
        self,
        profile_id: str,
        model_id: str,
        capability: ProviderModelOption,
    ) -> ProviderProfile:
        """Replace one model declaration after an explicit audited verification."""

        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        if capability.id != model_id:
            raise ValueError("verified capability model must match the selected model")
        if not any(item.id == model_id for item in profile.models):
            raise KeyError(model_id)
        overlays = {
            (str(item.get("profile_id")), str(item.get("model_id"))): item
            for item in self._capability_overlays()
        }
        compatible = [
            key
            for key, item in overlays.items()
            if _capability_overlay_matches(profile, item, list(overlays.values()))
        ]
        capability_digest = _profile_contract_sha256(profile, include_pricing=False)
        for key in compatible:
            overlays[key] = {**overlays[key], "profile_capability_sha256": capability_digest}
        overlays[(profile_id, model_id)] = {
            "profile_id": profile_id,
            "model_id": model_id,
            "profile_contract_sha256": _profile_contract_sha256(profile),
            "profile_capability_sha256": capability_digest,
            "capability": capability.model_dump(mode="json"),
        }
        self._write_capability_overlays(list(overlays.values()))
        saved = self.get(profile_id)
        assert saved is not None
        return saved

    def build_enabled_providers(self) -> dict[str, ModelProvider]:
        return {
            profile.id: build_provider(profile)
            for profile in self.list_profiles()
            if profile.enabled
        }

    def _custom_profiles(self) -> list[ProviderProfile]:
        data = self._data()
        deleted = data.get("deleted_profile_ids", [])
        return [
            ProviderProfile.model_validate(item)
            for item in data.get("profiles", [])
            if item["id"] not in deleted
        ]

    def _data(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "profiles": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("provider profile registry must be an object")
        return data

    def _write_data(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @property
    def capability_path(self) -> Path:
        return self.path.with_name("provider-capabilities.json")

    def _capability_overlays(self) -> list[dict[str, Any]]:
        if not self.capability_path.exists():
            return []
        data = json.loads(self.capability_path.read_text(encoding="utf-8"))
        values = data.get("capabilities", []) if isinstance(data, dict) else []
        return [item for item in values if isinstance(item, dict)]

    def _write_capability_overlays(self, overlays: list[dict[str, Any]]) -> None:
        self.capability_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.capability_path.with_suffix(self.capability_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"version": 1, "capabilities": overlays},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.capability_path)

    def _apply_capability_overlays(
        self,
        profiles: dict[str, ProviderProfile],
    ) -> dict[str, ProviderProfile]:
        overlays = self._capability_overlays()
        for overlay in overlays:
            profile = profiles.get(str(overlay.get("profile_id")))
            model_id = str(overlay.get("model_id"))
            capability = overlay.get("capability")
            if (
                profile is None
                or not _capability_overlay_matches(profile, overlay, overlays)
                or not isinstance(capability, dict)
            ):
                continue
            current = next((item for item in profile.models if item.id == model_id), None)
            if current is None:
                continue
            verified = ProviderModelOption.model_validate(capability)
            if verified.id != model_id:
                continue
            projected = current.model_copy(
                update={field: getattr(verified, field) for field in _CAPABILITY_FIELDS}
            )
            models = [projected if item.id == model_id else item for item in profile.models]
            profiles[profile.id] = profile.model_copy(update={"models": models})
        return profiles


_CAPABILITY_FIELDS = {
    "context_window",
    "max_output_tokens",
    "supports_reasoning_effort",
    "structured_output_modes",
    "supports_usage",
    "supports_streaming",
    "stream_terminal_reliable",
    "finish_reason_semantics",
    "reasoning_tokens_billed_as_output",
    "verified_at",
    "verification_version",
    "verification_evidence",
}


_PRICING_FIELDS = {"input_price_cny_per_million", "output_price_cny_per_million"}


def _profile_contract_sha256(profile: ProviderProfile, *, include_pricing: bool = True) -> str:
    value = profile.model_dump(mode="json")
    for model in value["models"]:
        for field in _CAPABILITY_FIELDS:
            model.pop(field, None)
        if not include_pricing:
            for field in _PRICING_FIELDS:
                model.pop(field, None)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _capability_overlay_matches(
    profile: ProviderProfile,
    overlay: dict[str, Any],
    overlays: list[dict[str, Any]],
) -> bool:
    if overlay.get("profile_id") != profile.id:
        return False
    if "profile_capability_sha256" in overlay:
        digest = overlay["profile_capability_sha256"]
        return isinstance(digest, str) and digest == _profile_contract_sha256(
            profile,
            include_pricing=False,
        )
    legacy_digest = overlay.get("profile_contract_sha256")
    if legacy_digest == _profile_contract_sha256(profile):
        return True
    prior_prices: dict[str, dict[str, Any]] = {}
    for item in overlays:
        capability = item.get("capability")
        if (
            item.get("profile_id") == profile.id
            and item.get("profile_contract_sha256") == legacy_digest
            and isinstance(capability, dict)
            and capability.get("id") == item.get("model_id")
        ):
            prior_prices[str(item["model_id"])] = {
                field: capability.get(field) for field in _PRICING_FIELDS
            }
    legacy_profile = profile.model_copy(
        update={
            "models": [
                ProviderModelOption.model_validate(
                    {
                        **option.model_dump(),
                        **prior_prices.get(option.id, {}),
                    }
                )
                for option in profile.models
            ]
        }
    )
    return legacy_digest == _profile_contract_sha256(legacy_profile)


def build_provider(
    profile: ProviderProfile, *, client: httpx.AsyncClient | None = None,
) -> ModelProvider:
    if profile.protocol == "deepseek_chat":
        return DeepSeekChatProvider(
            base_url=profile.base_url,
            provider_name=profile.id,
            supports_reasoning_effort=profile.supports_reasoning_effort,
            client=client,
        )
    if profile.protocol == "openai_responses":
        return OpenAIResponsesProvider(
            base_url=profile.base_url, provider_name=profile.id, client=client,
        )
    return OpenAICompatibleChatProvider(
        provider_name=profile.id,
        base_url=profile.base_url,
        structured_output_mode=profile.structured_output_mode,
        supports_reasoning_effort=profile.supports_reasoning_effort,
        reasoning_tokens_billed_as_output_by_model={
            item.id: item.reasoning_tokens_billed_as_output
            for item in profile.models
            if item.reasoning_tokens_billed_as_output is not None
        },
        streaming_enabled=profile.streaming_enabled,
        streaming_by_model={
            item.id: item.streaming_enabled
            for item in profile.models
            if item.streaming_enabled is not None
        },
        client=client,
    )


def _built_in_profiles() -> list[ProviderProfile]:
    return [
        ProviderProfile(
            id="deepseek",
            display_name="DeepSeek",
            protocol="deepseek_chat",
            base_url="https://api.deepseek.com",
            structured_output_mode="json_object",
            supports_reasoning_effort=True,
            default_model="deepseek-v4-pro",
            models=[
                ProviderModelOption(
                    id="deepseek-v4-pro",
                    label="DeepSeek V4 Pro",
                    input_price_cny_per_million=Decimal("9.0"),
                    output_price_cny_per_million=Decimal("27.0"),
                ),
                ProviderModelOption(
                    id="deepseek-v4-flash",
                    label="DeepSeek V4 Flash",
                    input_price_cny_per_million=Decimal("3.0"),
                    output_price_cny_per_million=Decimal("9.0"),
                ),
            ],
            built_in=True,
        ),
        ProviderProfile(
            id="openai",
            display_name="OpenAI",
            protocol="openai_responses",
            base_url="https://api.openai.com/v1",
            structured_output_mode="json_schema",
            supports_reasoning_effort=True,
            default_model="gpt-5.6-sol",
            models=[
                ProviderModelOption(
                    id="gpt-5.6-sol",
                    label="GPT-5.6 Sol",
                    input_price_cny_per_million=Decimal("33.887625"),
                    output_price_cny_per_million=Decimal("203.325750"),
                )
            ],
            built_in=True,
        ),
    ]
