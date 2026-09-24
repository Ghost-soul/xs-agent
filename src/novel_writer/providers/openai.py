import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from novel_writer.core.logging import log_provider_failure, log_provider_success
from novel_writer.providers.base import (
    ModelRequest,
    ModelResponse,
    ProviderFailureCode,
    ProviderIdentifier,
    ProviderName,
    ProviderResponseError,
    ProviderTerminalMetadata,
    TokenUsage,
    provider_identifier,
)
from novel_writer.providers.transport import (
    ProviderTransport,
    ProviderTransportReadError,
    capture_response_body,
    redacted_response_headers,
)


class OpenAIResponsesProvider:
    protocol = "openai_responses"
    name: ProviderIdentifier = ProviderName.OPENAI

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.openai.com/v1",
        provider_name: ProviderIdentifier = ProviderName.OPENAI,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self.name = provider_name

    @property
    def supports_reasoning_effort(self) -> bool:
        return True

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
        payload: dict[str, Any] = {
            "model": request.model,
            "input": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_output_tokens": request.max_output_tokens,
            "stream": True,
        }
        if not request.skip_structured_output:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.json_schema_name,
                    "schema": request.json_schema,
                    "strict": True,
                }
            }
        if request.reasoning_effort is not None:
            payload["reasoning"] = {"effort": request.reasoning_effort}

        headers = {"Authorization": f"Bearer {api_key}"}
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
            if "text/event-stream" not in response.headers.get("content-type", ""):
                return _validated_model_response(
                    _decode_json_object(transport, response_headers=response_headers),
                    transport,
                    terminal_event_seen=None,
                    response_headers=response_headers,
                )
            return _consume_sse(transport, response_headers)

    @asynccontextmanager
    async def _stream(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[httpx.Response]:
        url = f"{self._base_url}/responses"
        if self._client is not None:
            async with self._client.stream("POST", url, json=payload, headers=headers) as response:
                yield response
            return
        async with (
            httpx.AsyncClient(timeout=600) as client,
            client.stream("POST", url, json=payload, headers=headers) as response,
        ):
            yield response


def _consume_sse(
    transport: ProviderTransport,
    response_headers: dict[str, str],
) -> ModelResponse:
    text_parts: list[str] = []
    completed: dict[str, Any] | None = None
    done_seen = False
    if transport.format != "http-body-utf8-v1":
        raise ProviderResponseError(
            "OpenAI stream was not valid UTF-8",
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
            if completed is None:
                raise _openai_stream_error(
                    "OpenAI stream ended before response.completed",
                    transport,
                    text_parts,
                    response_headers=response_headers,
                )
            done_seen = True
            continue
        if done_seen:
            raise _openai_stream_error(
                "OpenAI stream contained an event after [DONE]",
                transport,
                text_parts,
                response_headers=response_headers,
            )
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as error:
            raise _openai_stream_error(
                "OpenAI stream event was not valid JSON",
                transport,
                text_parts,
                response_headers=response_headers,
            ) from error
        if not isinstance(event, dict):
            raise _openai_stream_error(
                "OpenAI stream event was not an object",
                transport,
                text_parts,
                response_headers=response_headers,
            )
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)
        elif event_type == "response.completed":
            value = event.get("response")
            if isinstance(value, dict):
                completed = value
        elif event_type in {"response.failed", "response.incomplete"}:
            value = event.get("response")
            payload = value if isinstance(value, dict) else event
            _validated_model_response(
                payload,
                transport,
                "".join(text_parts) or None,
                terminal_event_seen=True,
                response_headers=response_headers,
            )
            raise AssertionError("terminal failure unexpectedly validated")
        elif event_type == "error":
            raise ProviderResponseError(
                "OpenAI stream returned an error terminal event",
                transport.text,
                code=ProviderFailureCode.PROVIDER_TERMINAL_FAILURE,
                terminal=ProviderTerminalMetadata(
                    protocol="openai_responses",
                    terminal_event_seen=True,
                    terminal_status="error",
                    stream_completed=True,
                ),
                extracted_content="".join(text_parts) or None,
                raw_transport_format="sse-transcript-v1",
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )

    if completed is None or not done_seen:
        raise ProviderResponseError(
            "OpenAI stream ended without complete terminal evidence",
            transport.text,
            code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
            extracted_content="".join(text_parts) or None,
            terminal=ProviderTerminalMetadata(
                protocol="openai_responses",
                terminal_event_seen=completed is not None,
                stream_completed=done_seen,
            ),
            raw_transport_format="sse-transcript-v1",
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        )
    streamed_text = "".join(text_parts)
    return _validated_model_response(
        completed,
        transport,
        streamed_text or None,
        terminal_event_seen=True,
        response_headers=response_headers,
    )


def _model_response(
    data: dict[str, Any],
    text: str | None = None,
    *,
    terminal_event_seen: bool | None = None,
    raw_transport: ProviderTransport | None = None,
) -> ModelResponse:
    status = data.get("status")
    incomplete_details = data.get("incomplete_details") or {}
    incomplete_reason = (
        incomplete_details.get("reason") if isinstance(incomplete_details, dict) else None
    )
    terminal_seen = (
        isinstance(status, str) if terminal_event_seen is None else terminal_event_seen
    )
    terminal = ProviderTerminalMetadata(
        protocol="openai_responses",
        terminal_event_seen=terminal_seen,
        terminal_status=(
            status if isinstance(status, str) else "completed" if terminal_seen else None
        ),
        incomplete_reason=(incomplete_reason if isinstance(incomplete_reason, str) else None),
        stream_completed=terminal_event_seen,
    )
    usage = data.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    token_usage = TokenUsage(
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cached_input_tokens=int(input_details.get("cached_tokens", 0)),
        cache_write_tokens=int(input_details.get("cache_write_tokens", 0)),
        reasoning_tokens=int(output_details.get("reasoning_tokens", 0)),
    )
    assembled_response = json.dumps(data, ensure_ascii=False, sort_keys=True)
    raw_response = raw_transport.text if raw_transport is not None else assembled_response
    extracted = text or _optional_response_text(data)
    if not terminal_seen or status is None and terminal_event_seen is None:
        raise ProviderResponseError(
            "OpenAI response omitted successful terminal evidence",
            raw_response,
            code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
            usage=token_usage,
            request_id=data.get("id"),
            extracted_content=extracted,
            terminal=terminal,
        )
    if terminal.reached_output_limit:
        raise ProviderResponseError(
            "OpenAI output reached the Provider token limit",
            raw_response,
            code=ProviderFailureCode.TOKEN_LIMIT_EXCEEDED,
            usage=token_usage,
            request_id=data.get("id"),
            extracted_content=extracted,
            terminal=terminal,
        )
    if status not in {None, "completed"}:
        raise ProviderResponseError(
            f"OpenAI response status is {status}",
            raw_response,
            code=ProviderFailureCode.PROVIDER_TERMINAL_FAILURE,
            usage=token_usage,
            request_id=data.get("id"),
            extracted_content=extracted,
            terminal=terminal,
        )
    if _has_refusal(data):
        raise ProviderResponseError(
            "OpenAI response contained a refusal",
            raw_response,
            code=ProviderFailureCode.PROVIDER_REFUSAL,
            usage=token_usage,
            request_id=data.get("id"),
            extracted_content=extracted,
            terminal=terminal,
        )
    if extracted is None:
        raise ProviderResponseError(
            "OpenAI response did not contain output text",
            raw_response,
            code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
            usage=token_usage,
            request_id=data.get("id"),
            terminal=terminal,
        )
    return ModelResponse(
        text=extracted,
        usage=token_usage,
        request_id=data.get("id"),
        raw_response=raw_response,
        reasoning_content=_response_reasoning_content(data),
        terminal=terminal,
        assembled_response=(assembled_response if raw_transport is not None else None),
    )


def _decode_json_object(
    transport: ProviderTransport,
    *,
    response_headers: dict[str, str],
) -> dict[str, Any]:
    if transport.format != "http-body-utf8-v1":
        raise ProviderResponseError(
            "OpenAI response was not valid UTF-8",
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
            "OpenAI response was not valid JSON",
            transport.text,
            code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        ) from error
    if not isinstance(payload, dict):
        raise ProviderResponseError(
            "OpenAI response was not an object",
            transport.text,
            code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
        )
    return payload


def _validated_model_response(
    data: dict[str, Any],
    transport: ProviderTransport,
    text: str | None = None,
    *,
    terminal_event_seen: bool | None,
    response_headers: dict[str, str],
) -> ModelResponse:
    transport_format = (
        "sse-transcript-v1" if terminal_event_seen is not None else transport.format
    )
    assembled_response = json.dumps(data, ensure_ascii=False, sort_keys=True)
    try:
        result = _model_response(
            data,
            text,
            terminal_event_seen=terminal_event_seen,
            raw_transport=transport,
        )
    except ProviderResponseError as error:
        error.raw_transport_format = transport_format
        error.raw_entity_body_sha256 = transport.entity_body_sha256
        error.response_headers_redacted = dict(response_headers)
        error.assembled_response = assembled_response
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ProviderResponseError(
            "OpenAI response fields were invalid",
            transport.text,
            code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
            raw_transport_format=transport_format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
            assembled_response=assembled_response,
        ) from error
    result.raw_transport_format = transport_format
    result.raw_entity_body_sha256 = transport.entity_body_sha256
    result.response_headers_redacted = dict(response_headers)
    result.assembled_response = assembled_response
    return result


def _openai_stream_error(
    message: str,
    transport: ProviderTransport,
    text_parts: list[str],
    *,
    response_headers: dict[str, str],
) -> ProviderResponseError:
    return ProviderResponseError(
        message,
        transport.text,
        code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
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
        "OpenAI response body was interrupted before completion",
        error.transport.text,
        code=ProviderFailureCode.OUTCOME_UNCERTAIN,
        terminal=ProviderTerminalMetadata(
            protocol="openai_responses",
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
        f"OpenAI HTTP response status was {response.status_code}",
        transport.text,
        code=(
            ProviderFailureCode.OUTCOME_UNCERTAIN
            if response.status_code >= 500
            else ProviderFailureCode.PROVIDER_TERMINAL_FAILURE
        ),
        request_id=request_id,
        terminal=ProviderTerminalMetadata(
            protocol="openai_responses",
            terminal_event_seen=True,
            terminal_status=f"http_{response.status_code}",
            stream_completed=True,
        ),
        raw_transport_format=transport.format,
        raw_entity_body_sha256=transport.entity_body_sha256,
        response_headers_redacted=response_headers,
    )


def _optional_response_text(data: dict[str, Any]) -> str | None:
    try:
        return _response_text(data)
    except ValueError:
        return None


def _response_reasoning_content(data: dict[str, Any]) -> str | None:
    """Capture only Provider-exposed reasoning summaries; hidden CoT stays hidden."""

    parts: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for summary in item.get("summary", []):
            if isinstance(summary, dict) and isinstance(summary.get("text"), str):
                parts.append(summary["text"])
    joined = "\n\n".join(part for part in parts if part)
    return joined or None


def _response_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct:
        return direct
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return str(content["text"])
    raise ValueError("OpenAI response did not contain output text")


def _has_refusal(data: dict[str, Any]) -> bool:
    return any(
        content.get("type") == "refusal"
        for item in data.get("output", [])
        if isinstance(item, dict)
        for content in item.get("content", [])
        if isinstance(content, dict)
    )
