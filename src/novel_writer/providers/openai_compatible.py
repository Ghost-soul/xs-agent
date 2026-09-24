import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx

from novel_writer.core.logging import log_provider_failure, log_provider_success
from novel_writer.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderFailureCode,
    ProviderIdentifier,
    ProviderResponseError,
    ProviderTerminalMetadata,
    TokenUsage,
    provider_identifier,
)
from novel_writer.providers.transport import (
    ProviderTransport,
    ProviderTransportReadError,
    capture_response_body,
    encode_transport,
    redacted_response_headers,
)

StructuredOutputMode = Literal["json_schema", "json_object", "prompt_only"]

# Some OpenAI-compatible gateways apply browser/WAF rules to the transport
# before they inspect the API key. Keep this stable and explicit for both
# buffered and streaming requests; it is not an authentication mechanism.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36"
)


class OpenAICompatibleChatProvider:
    protocol = "openai_chat_completions"
    """Generic adapter for third-party OpenAI-compatible chat-completions APIs."""

    def __init__(
        self,
        *,
        provider_name: ProviderIdentifier,
        base_url: str,
        structured_output_mode: StructuredOutputMode = "json_object",
        supports_reasoning_effort: bool = False,
        reasoning_tokens_billed_as_output_by_model: dict[str, bool] | None = None,
        streaming_enabled: bool = False,
        streaming_by_model: dict[str, bool] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = provider_name
        self._base_url = base_url.rstrip("/")
        self._structured_output_mode = structured_output_mode
        self._supports_reasoning_effort = supports_reasoning_effort
        self._reasoning_tokens_billed_as_output_by_model = dict(
            reasoning_tokens_billed_as_output_by_model or {}
        )
        self._streaming_enabled = streaming_enabled
        self._streaming_by_model = dict(streaming_by_model or {})
        self._client = client

    @property
    def supports_reasoning_effort(self) -> bool:
        return self._supports_reasoning_effort

    @property
    def structured_output_mode(self) -> StructuredOutputMode:
        return self._structured_output_mode

    def reasoning_tokens_billed_as_output(self, model: str) -> bool:
        return self._reasoning_tokens_billed_as_output_by_model.get(model, False)

    async def generate(self, request: ModelRequest, api_key: str) -> ModelResponse:
        started = time.perf_counter()
        try:
            response = await self._generate(request, api_key)
        except Exception as error:
            log_provider_failure(
                provider_identifier(self.name),
                request,
                error,
                (time.perf_counter() - started) * 1000,
            )
            raise
        log_provider_success(
            provider_identifier(self.name),
            request,
            response,
            (time.perf_counter() - started) * 1000,
        )
        return response

    async def _generate(self, request: ModelRequest, api_key: str) -> ModelResponse:
        streaming_enabled = (
            False
            if request.force_non_streaming
            else self._streaming_by_model.get(request.model, self._streaming_enabled)
        )
        user_prompt = request.user_prompt
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "stream": streaming_enabled,
        }
        if request.skip_structured_output:
            pass
        elif self._structured_output_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.json_schema_name,
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        else:
            schema_instruction = "\n\nReturn only JSON matching this schema:\n" + json.dumps(
                request.json_schema, ensure_ascii=False
            )
            payload["messages"][1]["content"] = user_prompt + schema_instruction
            if self._structured_output_mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
        if self._supports_reasoning_effort and request.reasoning_effort is not None:
            payload["reasoning_effort"] = request.reasoning_effort

        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if streaming_enabled:
            payload["stream_options"] = {"include_usage": True}
            async with self._stream(payload, headers) as response:
                response_headers = redacted_response_headers(response)
                try:
                    transport = await capture_response_body(response)
                except ProviderTransportReadError as error:
                    raise _interrupted_transport_error(
                        error,
                        response_headers=response_headers,
                    ) from error
                if response.status_code >= 300:
                    raise _http_response_error(response, transport, response_headers)
                if "text/event-stream" in response.headers.get("content-type", ""):
                    return _consume_chat_sse(transport, response_headers)
                return _validated_model_response(
                    _decode_json_object(transport, response_headers=response_headers),
                    transport,
                    transport_format=transport.format,
                    response_headers=response_headers,
                )

        response = await self._post(payload, headers)
        transport = encode_transport(response.content)
        response_headers = redacted_response_headers(response)
        if response.status_code >= 300:
            raise _http_response_error(response, transport, response_headers)
        return _validated_model_response(
            _decode_json_object(transport, response_headers=response_headers),
            transport,
            transport_format=transport.format,
            response_headers=response_headers,
        )

    async def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        url = f"{self._base_url}/chat/completions"
        if self._client is not None:
            response = await self._client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=600) as client:
                response = await client.post(url, json=payload, headers=headers)
        return response

    @asynccontextmanager
    async def _stream(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[httpx.Response]:
        url = f"{self._base_url}/chat/completions"
        if self._client is not None:
            async with self._client.stream("POST", url, json=payload, headers=headers) as response:
                yield response
            return
        async with (
            httpx.AsyncClient(timeout=600) as client,
            client.stream("POST", url, json=payload, headers=headers) as response,
        ):
            yield response


def _model_response(data: dict[str, Any], raw_response: str) -> ModelResponse:
    usage = data.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    choices = data.get("choices") or []
    if not choices:
        raise ProviderResponseError(
            "OpenAI-compatible response did not contain a choice",
            raw_response,
            code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
            request_id=data.get("id"),
            terminal=ProviderTerminalMetadata(
                protocol="openai_chat_completions",
                terminal_event_seen=False,
            ),
        )
    choice = choices[0]
    message = choice.get("message", {})
    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    token_usage = TokenUsage(
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cached_input_tokens=int(prompt_details.get("cached_tokens", 0)),
        reasoning_tokens=int(completion_details.get("reasoning_tokens", 0)),
    )
    finish_reason = choice.get("finish_reason")
    terminal = ProviderTerminalMetadata(
        protocol="openai_chat_completions",
        terminal_event_seen=isinstance(finish_reason, str) and bool(finish_reason),
        terminal_status="completed" if finish_reason == "stop" else None,
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
    )
    if not isinstance(content, str):
        raise ProviderResponseError(
            "OpenAI-compatible response did not contain text",
            raw_response,
            code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
            usage=token_usage,
            request_id=data.get("id"),
            terminal=terminal,
        )
    if finish_reason == "length":
        raise ProviderResponseError(
            "OpenAI-compatible output reached the Provider token limit",
            raw_response,
            code=ProviderFailureCode.TOKEN_LIMIT_EXCEEDED,
            usage=token_usage,
            request_id=data.get("id"),
            extracted_content=content,
            terminal=terminal,
        )
    if finish_reason != "stop":
        code = (
            ProviderFailureCode.PROTOCOL_INCOMPLETE
            if finish_reason is None
            else ProviderFailureCode.PROVIDER_TERMINAL_FAILURE
        )
        raise ProviderResponseError(
            "OpenAI-compatible response has no safe successful terminal reason",
            raw_response,
            code=code,
            usage=token_usage,
            request_id=data.get("id"),
            extracted_content=content,
            terminal=terminal,
        )
    return ModelResponse(
        text=content,
        usage=token_usage,
        request_id=data.get("id"),
        raw_response=raw_response,
        reasoning_content=(reasoning_content if isinstance(reasoning_content, str) else None),
        terminal=terminal,
    )


def _consume_chat_sse(
    transport: ProviderTransport,
    response_headers: dict[str, str],
) -> ModelResponse:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    request_id: str | None = None
    model: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    terminal_reason_seen = False
    done_seen = False

    if transport.format != "http-body-utf8-v1":
        raise ProviderResponseError(
            "OpenAI-compatible stream was not valid UTF-8",
            transport.text,
            code=ProviderFailureCode.TEXT_ENCODING_INVALID,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        )
    for line in transport.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw:
            continue
        if raw == "[DONE]":
            done_seen = True
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as error:
            raise _stream_error(
                "OpenAI-compatible stream event was not valid JSON",
                transport,
                text_parts,
                request_id=request_id,
                response_headers=response_headers,
            ) from error
        if not isinstance(event, dict):
            raise _stream_error(
                "OpenAI-compatible stream event was not an object",
                transport,
                text_parts,
                request_id=request_id,
                response_headers=response_headers,
            )
        if event.get("error") is not None:
            raise ProviderResponseError(
                "OpenAI-compatible stream returned an error",
                transport.text,
                code=ProviderFailureCode.PROVIDER_TERMINAL_FAILURE,
                request_id=_optional_string(event.get("id")) or request_id,
                extracted_content="".join(text_parts) or None,
                raw_transport_format="sse-transcript-v1",
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )
        request_id = _optional_string(event.get("id")) or request_id
        model = _optional_string(event.get("model")) or model
        event_usage = event.get("usage")
        if isinstance(event_usage, dict) and event_usage:
            usage = event_usage
        choices = event.get("choices") or []
        if not isinstance(choices, list):
            raise _stream_error(
                "OpenAI-compatible stream choices were not a list",
                transport,
                text_parts,
                request_id=request_id,
                response_headers=response_headers,
            )
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                reasoning_content = delta.get("reasoning_content")
                if isinstance(reasoning_content, str):
                    reasoning_parts.append(reasoning_content)
                refusal = delta.get("refusal")
                if refusal:
                    raise ProviderResponseError(
                        "OpenAI-compatible stream contained a refusal",
                        transport.text,
                        code=ProviderFailureCode.PROVIDER_REFUSAL,
                        request_id=request_id,
                        extracted_content="".join(text_parts) or None,
                        raw_transport_format="sse-transcript-v1",
                        raw_entity_body_sha256=transport.entity_body_sha256,
                        response_headers_redacted=response_headers,
                    )
            reason = choice.get("finish_reason")
            if isinstance(reason, str):
                finish_reason = reason
                terminal_reason_seen = True

    if not terminal_reason_seen or not done_seen:
        content = "".join(text_parts)
        terminal = ProviderTerminalMetadata(
            protocol="openai_chat_completions",
            terminal_event_seen=terminal_reason_seen,
            finish_reason=finish_reason,
            stream_completed=done_seen,
        )
        raise ProviderResponseError(
            "OpenAI-compatible stream ended before a terminal event",
            transport.text,
            code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
            usage=_token_usage(usage),
            request_id=request_id,
            extracted_content=content,
            terminal=terminal,
            raw_transport_format="sse-transcript-v1",
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        )
    content = "".join(text_parts)
    envelope: dict[str, Any] = {
        "id": request_id,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "".join(reasoning_parts) or None,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    if model is not None:
        envelope["model"] = model
    assembled_response = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    result = _validated_model_response(
        envelope,
        transport,
        transport_format="sse-transcript-v1",
        assembled_response=assembled_response,
        response_headers=response_headers,
    )
    result.raw_transport_format = "sse-transcript-v1"
    result.raw_entity_body_sha256 = transport.entity_body_sha256
    result.response_headers_redacted = response_headers
    result.assembled_response = assembled_response
    assert result.terminal is not None
    result.terminal.stream_completed = True
    return result


def _validated_model_response(
    data: dict[str, Any],
    transport: ProviderTransport,
    *,
    transport_format: str,
    assembled_response: str | None = None,
    response_headers: dict[str, str] | None = None,
) -> ModelResponse:
    try:
        result = _model_response(data, transport.text)
    except ProviderResponseError as error:
        error.raw_transport_format = transport_format
        error.raw_entity_body_sha256 = transport.entity_body_sha256
        error.response_headers_redacted = dict(response_headers or {})
        error.assembled_response = assembled_response
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ProviderResponseError(
            "OpenAI-compatible response fields were invalid",
            transport.text,
            code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
            raw_transport_format=transport_format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        ) from error
    result.raw_transport_format = transport_format
    result.raw_entity_body_sha256 = transport.entity_body_sha256
    result.response_headers_redacted = dict(response_headers or {})
    result.assembled_response = assembled_response
    return result


def _decode_json_object(
    transport: ProviderTransport,
    *,
    response_headers: dict[str, str],
) -> dict[str, Any]:
    if transport.format != "http-body-utf8-v1":
        raise ProviderResponseError(
            "OpenAI-compatible response was not valid UTF-8",
            transport.text,
            code=ProviderFailureCode.TEXT_ENCODING_INVALID,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        )
    try:
        payload = json.loads(transport.text)
    except json.JSONDecodeError as error:
        raise ProviderResponseError(
            "OpenAI-compatible response was not valid JSON",
            transport.text,
            code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        ) from error
    if not isinstance(payload, dict):
        raise ProviderResponseError(
            "OpenAI-compatible response was not an object",
            transport.text,
            code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        )
    return payload


def _stream_error(
    message: str,
    transport: ProviderTransport,
    text_parts: list[str],
    *,
    request_id: str | None,
    response_headers: dict[str, str],
) -> ProviderResponseError:
    return ProviderResponseError(
        message,
        transport.text,
        code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
        request_id=request_id,
        extracted_content="".join(text_parts) or None,
        raw_transport_format="sse-transcript-v1",
        raw_entity_body_sha256=transport.entity_body_sha256,
        response_headers_redacted=response_headers,
    )


def _interrupted_transport_error(
    error: ProviderTransportReadError,
    *,
    response_headers: dict[str, str],
) -> ProviderResponseError:
    transport_format = (
        "sse-transcript-v1"
        if error.transport.format == "http-body-utf8-v1"
        else error.transport.format
    )
    return ProviderResponseError(
        "OpenAI-compatible stream was interrupted before completion",
        error.transport.text,
        code=ProviderFailureCode.OUTCOME_UNCERTAIN,
        terminal=ProviderTerminalMetadata(
            protocol="openai_chat_completions",
            terminal_event_seen=False,
            stream_completed=False,
        ),
        raw_transport_format=transport_format,
        raw_entity_body_sha256=error.transport.entity_body_sha256,
        response_headers_redacted=response_headers,
    )


def _http_response_error(
    response: httpx.Response,
    transport: ProviderTransport,
    response_headers: dict[str, str],
) -> ProviderResponseError:
    request_id = response_headers.get("request-id") or response_headers.get("x-request-id")
    return ProviderResponseError(
        f"OpenAI-compatible HTTP response status was {response.status_code}",
        transport.text,
        code=(
            ProviderFailureCode.OUTCOME_UNCERTAIN
            if response.status_code >= 500
            else ProviderFailureCode.PROVIDER_TERMINAL_FAILURE
        ),
        request_id=request_id,
        terminal=ProviderTerminalMetadata(
            protocol="openai_chat_completions",
            terminal_event_seen=True,
            terminal_status=f"http_{response.status_code}",
            stream_completed=True,
        ),
        raw_transport_format=transport.format,
        raw_entity_body_sha256=transport.entity_body_sha256,
        response_headers_redacted=response_headers,
    )


def _token_usage(usage: dict[str, Any]) -> TokenUsage:
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return TokenUsage(
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cached_input_tokens=int(prompt_details.get("cached_tokens", 0)),
        reasoning_tokens=int(completion_details.get("reasoning_tokens", 0)),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


