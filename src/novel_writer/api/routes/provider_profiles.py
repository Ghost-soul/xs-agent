import hashlib
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from novel_writer.api.dependencies import Session
from novel_writer.core.credentials import CredentialError, CredentialStore
from novel_writer.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderFailureCode,
    ProviderResponseError,
    TokenUsage,
)
from novel_writer.services.provider_profile_management import guard_profile_deletion
from novel_writer.services.provider_profiles import (
    ProviderModelOption,
    ProviderProfile,
    ProviderProfileStore,
    build_provider,
)

router = APIRouter(prefix="/api/provider-profiles", tags=["provider-profiles"])


class SaveProviderProfileRequest(BaseModel):
    profile: ProviderProfile
    confirmed: Literal[True]


class SaveCredentialRequest(BaseModel):
    api_key: SecretStr
    confirmed: Literal[True]


class SetProfileEnabledRequest(BaseModel):
    enabled: bool
    confirmed: Literal[True]


class TestProviderProfileRequest(BaseModel):
    confirmed: Literal[True]
    model: str | None = Field(default=None, min_length=1, max_length=160)


class SetDefaultModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=160)
    confirmed: Literal[True]


class DeleteProviderProfileRequest(BaseModel):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


class VerifyProviderCapabilityRequest(BaseModel):
    confirmed: Literal[True]
    acknowledge_provider_calls_and_cost: Literal[True]
    expected_provider_calls: Literal[2]
    model: str = Field(min_length=1, max_length=160)
    context_window: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    reasoning_tokens_billed_as_output: bool
    source_note: str = Field(min_length=3, max_length=500)
    max_cost_cny: Decimal = Field(gt=0, le=1)


def _store(request: Request) -> ProviderProfileStore:
    return cast(ProviderProfileStore, request.app.state.provider_profile_store)


def _response(
    profile: ProviderProfile, credentials: CredentialStore, store: ProviderProfileStore
) -> dict[str, object]:
    return {
        **profile.model_dump(mode="json"),
        "default_model": store.preferred_model(profile),
        "profile_revision": store.revision(profile),
        "has_api_key": bool(credentials.get_api_key(profile.id)),
    }


@router.get("")
async def list_provider_profiles(request: Request) -> list[dict[str, object]]:
    credentials: CredentialStore = request.app.state.credential_store
    store = _store(request)
    return [_response(item, credentials, store) for item in store.list_profiles()]


@router.post("/{profile_id}/default-model")
async def set_provider_default_model(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: SetDefaultModelRequest,
    request: Request,
) -> dict[str, object]:
    store = _store(request)
    try:
        saved = store.set_preferred_model(profile_id, payload.model)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="provider profile not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _response(saved, request.app.state.credential_store, store)


@router.delete("/{profile_id}")
async def delete_provider_profile(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: DeleteProviderProfileRequest,
    request: Request,
    session: Session,
) -> dict[str, object]:
    store = _store(request)
    profile = store.get(profile_id)
    if profile is None:
        if store.is_deleted(profile_id):
            return {"profile_id": profile_id, "deleted": True}
        raise HTTPException(status_code=404, detail="provider profile not found")
    if profile.built_in:
        raise HTTPException(status_code=409, detail="内置供应商入口不能删除，可选择默认模型")
    await guard_profile_deletion(session, profile_id)
    current = store.get(profile_id)
    if current is None or store.revision(current) != payload.expected_revision:
        raise HTTPException(status_code=409, detail="配置已变化，请刷新后重新确认删除")
    credentials: CredentialStore = request.app.state.credential_store
    try:
        store.assert_idle(profile_id)
        credentials.delete_api_key(profile_id)
        store.delete(profile_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except CredentialError as error:
        raise HTTPException(
            status_code=503, detail="无法清除安全凭据，配置未删除，请稍后重试"
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=503, detail="配置文件清理失败，API Key 可能已清除；请刷新核对后重新删除"
        ) from error
    request.app.state.providers.pop(profile_id, None)
    return {"profile_id": profile_id, "deleted": True}


@router.put("/{profile_id}")
async def save_provider_profile(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: SaveProviderProfileRequest,
    request: Request,
) -> dict[str, object]:
    if payload.profile.id != profile_id:
        raise HTTPException(status_code=422, detail="profile id must match the URL")
    try:
        saved = _store(request).save(payload.profile)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if saved.enabled:
        request.app.state.providers[saved.id] = build_provider(saved)
    else:
        request.app.state.providers.pop(saved.id, None)
    credentials: CredentialStore = request.app.state.credential_store
    return _response(saved, credentials, _store(request))


@router.post("/{profile_id}/credential")
async def save_provider_credential(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: SaveCredentialRequest,
    request: Request,
) -> dict[str, object]:
    profile = _store(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="provider profile not found")
    api_key = payload.api_key.get_secret_value().strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="API key must not be empty")
    if not api_key.isascii():
        raise HTTPException(
            status_code=422,
            detail="API key 只能包含 ASCII 字符，请移除中文、全角空格或 Markdown 文本后重试。",
        )
    credentials: CredentialStore = request.app.state.credential_store
    try:
        credentials.set_api_key(profile_id, api_key)
    except CredentialError as error:
        raise HTTPException(
            status_code=503,
            detail="系统安全凭据存储当前不可用，请保持 Windows 登录会话后重试。",
        ) from error
    return {"profile_id": profile_id, "has_api_key": True}


@router.post("/{profile_id}/enabled")
async def set_provider_profile_enabled(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: SetProfileEnabledRequest,
    request: Request,
) -> dict[str, object]:
    try:
        saved = _store(request).set_enabled(profile_id, payload.enabled)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="provider profile not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if saved.enabled:
        request.app.state.providers[saved.id] = build_provider(saved)
    else:
        request.app.state.providers.pop(saved.id, None)
    credentials: CredentialStore = request.app.state.credential_store
    return _response(saved, credentials, _store(request))


@router.post("/{profile_id}/test")
async def test_provider_profile(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: TestProviderProfileRequest,
    request: Request,
) -> dict[str, object]:
    try:
        with _store(request).probe_operation(profile_id):
            return await _test_provider_profile(profile_id, payload, request)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


async def _test_provider_profile(
    profile_id: str, payload: TestProviderProfileRequest, request: Request
) -> dict[str, object]:
    profile = _store(request).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="provider profile not found")
    model = payload.model or _store(request).preferred_model(profile)
    if not any(option.id == model for option in profile.models):
        raise HTTPException(status_code=404, detail="provider model not found")
    if not profile.enabled:
        raise HTTPException(status_code=409, detail="provider profile is disabled")
    credentials: CredentialStore = request.app.state.credential_store
    api_key = credentials.get_api_key(profile_id) or ""
    if profile.credential_required and not api_key:
        raise HTTPException(status_code=409, detail="请先保存 API Key")
    provider = build_provider(profile)
    started = time.perf_counter()
    try:
        response = await provider.generate(
            ModelRequest(
                model=model,
                system_prompt="Connection test.",
                user_prompt="Hi",
                max_output_tokens=5,
                json_schema_name="provider_connection_test",
                json_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                skip_structured_output=True,
                force_non_streaming=True,
            ),
            api_key,
        )
    except ProviderResponseError as error:
        # A liveness probe only needs a response from the endpoint. A gateway
        # may spend its tiny probe budget on hidden reasoning and finish with
        # `length` after already returning usable content; that still proves
        # connectivity and must not be reported as a failed connection.
        if error.is_truncated and (error.extracted_content or error.raw_response):
            return {
                "ok": True,
                "profile_id": profile.id,
                "model": model,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "usage": error.usage.model_dump(mode="json") if error.usage else {},
                "request_id": error.request_id,
                "truncated": True,
            }
        response_detail = _safe_provider_error_excerpt(error.raw_response)
        suffix = f"；网关响应：{response_detail}" if response_detail else ""
        raise HTTPException(
            status_code=502,
            detail=f"连接测试失败：{type(error).__name__}: {str(error)[:300]}{suffix}",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"连接测试失败：{type(error).__name__}: {str(error)[:300]}",
        ) from error
    return {
        "ok": True,
        "profile_id": profile.id,
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "usage": response.usage.model_dump(mode="json"),
        "request_id": response.request_id,
    }


def _safe_provider_error_excerpt(raw_response: str) -> str:
    """Expose a bounded provider error without leaking markup or control characters."""

    normalized = " ".join(raw_response.split())
    if not normalized:
        return ""
    return normalized[:500]


@router.post("/{profile_id}/capability-verification")
async def verify_provider_capability(
    profile_id: Annotated[str, Field(min_length=2, max_length=40)],
    payload: VerifyProviderCapabilityRequest,
    request: Request,
) -> dict[str, object]:
    try:
        with _store(request).probe_operation(profile_id):
            return await _verify_provider_capability(profile_id, payload, request)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


async def _verify_provider_capability(
    profile_id: str, payload: VerifyProviderCapabilityRequest, request: Request
) -> dict[str, object]:
    """Run exactly two minimal, non-story probes and persist immutable local evidence."""

    store = _store(request)
    profile = store.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="provider profile not found")
    option = next((item for item in profile.models if item.id == payload.model), None)
    if option is None:
        raise HTTPException(status_code=404, detail="provider model not found")
    if not profile.enabled:
        raise HTTPException(status_code=409, detail="provider profile is disabled")
    credentials: CredentialStore = request.app.state.credential_store
    api_key = credentials.get_api_key(profile_id) or ""
    if profile.credential_required and not api_key:
        raise HTTPException(status_code=409, detail="请先保存 API Key")
    if option.input_price_cny_per_million is None or option.output_price_cny_per_million is None:
        raise HTTPException(
            status_code=409,
            detail="capability verification requires model pricing",
        )

    # Both prompts are fixed and contain no story data.  The estimate is deliberately
    # conservative for these tiny requests and is checked before the first dispatch.
    estimated_input_tokens = 4096
    estimated_output_tokens = 33
    estimated_cost = (
        Decimal(estimated_input_tokens) * option.input_price_cny_per_million
        + Decimal(estimated_output_tokens) * option.output_price_cny_per_million
    ) / Decimal(1_000_000)
    if estimated_cost > payload.max_cost_cny:
        raise HTTPException(
            status_code=409,
            detail=(
                "declared capability probe ceiling is too small; "
                f"require at least ¥{estimated_cost}"
            ),
        )

    provider = build_provider(profile)
    audit_id = str(uuid4())
    model_path_id = hashlib.sha256(payload.model.encode("utf-8")).hexdigest()[:20]
    audit_root = store.path.parent / "provider-capability-audits" / profile.id / model_path_id
    success_request = ModelRequest(
        model=payload.model,
        system_prompt="You are a capability test. Return only the requested JSON.",
        user_prompt='Return {"ok": true}. This request contains no story data.',
        max_output_tokens=32,
        json_schema_name="provider_capability_success",
        json_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean", "const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    )
    limit_request = ModelRequest(
        model=payload.model,
        system_prompt="You are a capability limit test. Return only the requested JSON.",
        user_prompt='Return {"value": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}. This contains no story data.',
        max_output_tokens=1,
        json_schema_name="provider_capability_limit",
        json_schema={
            "type": "object",
            "properties": {"value": {"type": "string", "minLength": 26}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )

    try:
        success = await provider.generate(success_request, api_key)
    except Exception as error:
        evidence = _write_capability_probe(
            audit_root, audit_id, "success", profile, payload, error=error
        )
        raise HTTPException(
            status_code=502,
            detail=f"capability success probe failed; evidence={evidence.name}",
        ) from error
    success_file = _write_capability_probe(
        audit_root, audit_id, "success", profile, payload, response=success
    )
    if not _valid_success_probe(success):
        raise HTTPException(
            status_code=502,
            detail=(
                "capability success probe lacked safe JSON/usage/terminal evidence; "
                f"evidence={success_file.name}"
            ),
        )

    limit_error: ProviderResponseError | None = None
    try:
        unexpected = await provider.generate(limit_request, api_key)
    except ProviderResponseError as error:
        limit_error = error
        limit_file = _write_capability_probe(
            audit_root, audit_id, "output-limit", profile, payload, error=error
        )
    except Exception as error:
        limit_file = _write_capability_probe(
            audit_root, audit_id, "output-limit", profile, payload, error=error
        )
        raise HTTPException(
            status_code=502,
            detail=f"capability output-limit probe failed ambiguously; evidence={limit_file.name}",
        ) from error
    else:
        limit_file = _write_capability_probe(
            audit_root, audit_id, "output-limit", profile, payload, response=unexpected
        )
    if limit_error is None or not _valid_output_limit_probe(limit_error):
        raise HTTPException(
            status_code=502,
            detail=(
                "model did not expose a verified output-limit terminal; "
                f"evidence={limit_file.name}"
            ),
        )

    limit_usage = limit_error.usage or TokenUsage(input_tokens=0, output_tokens=0)
    actual_cost = _usage_cost(success.usage, option) + _usage_cost(limit_usage, option)
    if actual_cost > payload.max_cost_cny:
        raise HTTPException(
            status_code=409,
            detail="observed capability probe cost exceeded the author-confirmed ceiling",
        )
    tested_streaming = (
        option.streaming_enabled
        if option.streaming_enabled is not None
        else profile.streaming_enabled
    )
    success_terminal = success.terminal
    assert success_terminal is not None
    limit_terminal = limit_error.terminal
    assert limit_terminal is not None
    inferred_mode = {
        "json_schema": "json_schema_strict",
        "json_object": "json_object",
        "prompt_only": "prompt_only_schema",
    }[profile.structured_output_mode]
    verified_at = datetime.now(UTC)
    verification_version = f"capability-audit-v1:{audit_id}"
    capability = option.model_copy(
        update={
            "context_window": payload.context_window,
            "max_output_tokens": payload.max_output_tokens,
            "supports_reasoning_effort": (
                option.supports_reasoning_effort
                if option.supports_reasoning_effort is not None
                else profile.supports_reasoning_effort
            ),
            "structured_output_modes": frozenset({inferred_mode}),
            "supports_usage": True,
            "supports_streaming": bool(tested_streaming),
            "stream_terminal_reliable": bool(
                tested_streaming and success_terminal.stream_completed is True
            ),
            "finish_reason_semantics": {
                "success": success_terminal.finish_reason
                or success_terminal.terminal_status
                or "completed",
                "output_limit": limit_terminal.finish_reason
                or limit_terminal.incomplete_reason
                or "output_limit",
            },
            "reasoning_tokens_billed_as_output": payload.reasoning_tokens_billed_as_output,
            "verified_at": verified_at,
            "verification_version": verification_version,
            "verification_evidence": {
                "audit_id": audit_id,
                "provider_calls": 2,
                "story_data_sent": False,
                "source_note": payload.source_note,
                "success_evidence": success_file.name,
                "output_limit_evidence": limit_file.name,
                "actual_cost_cny": str(actual_cost),
            },
        }
    )
    saved = store.record_verified_capability(profile.id, payload.model, capability)
    return {
        "verified": True,
        "profile_id": saved.id,
        "model": payload.model,
        "provider_calls": 2,
        "story_data_sent": False,
        "actual_cost_cny": str(actual_cost),
        "verified_at": verified_at,
        "verification_version": verification_version,
        "evidence_files": [success_file.name, limit_file.name],
    }


def _valid_success_probe(response: ModelResponse) -> bool:
    try:
        body = json.loads(response.text)
    except json.JSONDecodeError:
        return False
    terminal = response.terminal
    return bool(
        isinstance(body, dict)
        and body.get("ok") is True
        and response.usage.input_tokens > 0
        and response.usage.output_tokens > 0
        and terminal is not None
        and terminal.terminal_event_seen
        and not terminal.reached_output_limit
    )


def _valid_output_limit_probe(error: ProviderResponseError) -> bool:
    return bool(
        error.stable_code == ProviderFailureCode.TOKEN_LIMIT_EXCEEDED.value
        and error.terminal is not None
        and error.terminal.terminal_event_seen
        and error.terminal.reached_output_limit
    )


def _usage_cost(usage: TokenUsage, option: ProviderModelOption) -> Decimal:
    assert option.input_price_cny_per_million is not None
    assert option.output_price_cny_per_million is not None
    return (
        Decimal(usage.input_tokens) * option.input_price_cny_per_million
        + Decimal(usage.output_tokens) * option.output_price_cny_per_million
    ) / Decimal(1_000_000)


def _write_capability_probe(
    root: Path,
    audit_id: str,
    kind: str,
    profile: ProviderProfile,
    payload: VerifyProviderCapabilityRequest,
    *,
    response: ModelResponse | None = None,
    error: Exception | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{audit_id}-{kind}.json"
    raw_transport = (
        response.raw_transport
        if response is not None
        else error.raw_response
        if isinstance(error, ProviderResponseError)
        else None
    )
    usage = (
        response.usage
        if response is not None
        else error.usage
        if isinstance(error, ProviderResponseError)
        else None
    )
    terminal = (
        response.terminal
        if response is not None
        else error.terminal
        if isinstance(error, ProviderResponseError)
        else None
    )
    record = {
        "version": 1,
        "audit_id": audit_id,
        "probe": kind,
        "tested_at": datetime.now(UTC).isoformat(),
        "profile_id": profile.id,
        "model": payload.model,
        "provider_calls_authorized": payload.expected_provider_calls,
        "max_cost_cny": str(payload.max_cost_cny),
        "story_data_sent": False,
        "source_note": payload.source_note,
        "request_id": (
            response.request_id
            if response is not None
            else error.request_id
            if isinstance(error, ProviderResponseError)
            else None
        ),
        "usage": usage.model_dump(mode="json") if usage is not None else None,
        "terminal": terminal.model_dump(mode="json") if terminal is not None else None,
        "failure_code": (
            error.stable_code if isinstance(error, ProviderResponseError) else None
        ),
        "error_type": type(error).__name__ if error is not None else None,
        "raw_transport": raw_transport,
        "raw_transport_sha256": (
            hashlib.sha256(raw_transport.encode("utf-8")).hexdigest()
            if raw_transport is not None
            else None
        ),
        "raw_transport_format": (
            response.raw_transport_format
            if response is not None
            else error.raw_transport_format
            if isinstance(error, ProviderResponseError)
            else None
        ),
        "raw_entity_body_sha256": (
            response.raw_entity_body_sha256
            if response is not None
            else error.raw_entity_body_sha256
            if isinstance(error, ProviderResponseError)
            else None
        ),
        "response_headers_redacted": (
            response.response_headers_redacted
            if response is not None
            else error.response_headers_redacted
            if isinstance(error, ProviderResponseError)
            else {}
        ),
    }
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return path
