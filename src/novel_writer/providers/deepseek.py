import json
import time
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
from novel_writer.providers.transport import encode_transport, redacted_response_headers


class DeepSeekChatProvider:
    protocol = "deepseek_chat"
    name: ProviderIdentifier = ProviderName.DEEPSEEK

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.deepseek.com",
        provider_name: ProviderIdentifier = ProviderName.DEEPSEEK,
        supports_reasoning_effort: bool = True,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self.name = provider_name
        self._supports_reasoning_effort = supports_reasoning_effort

    @property
    def supports_reasoning_effort(self) -> bool:
        return self._supports_reasoning_effort

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
        schema_instruction = (
            ""
            if request.skip_structured_output
            else "\n\nReturn only JSON matching this schema:\n"
            + json.dumps(request.json_schema, ensure_ascii=False)
        )
        reasoning_enabled = (
            self._supports_reasoning_effort
            and request.reasoning_effort not in {None, "none"}
        )
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": request.user_prompt + schema_instruction,
                },
            ],
            "max_tokens": request.max_output_tokens,
            "thinking": {"type": "enabled" if reasoning_enabled else "disabled"},
            "stream": False,
        }
        if not request.skip_structured_output:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_enabled:
            payload["reasoning_effort"] = request.reasoning_effort
        response = await self._post(payload, api_key)
        transport = encode_transport(response.content)
        response_headers = redacted_response_headers(response)
        if transport.format != "http-body-utf8-v1":
            raise ProviderResponseError(
                "DeepSeek response was not valid UTF-8",
                transport.text,
                code=ProviderFailureCode.TEXT_ENCODING_INVALID,
                raw_transport_format=transport.format,
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )
        try:
            data = json.loads(transport.text)
        except json.JSONDecodeError as error:
            raise ProviderResponseError(
                "DeepSeek response was not valid JSON",
                transport.text,
                code=ProviderFailureCode.RESPONSE_SCHEMA_INVALID,
                raw_transport_format=transport.format,
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            ) from error
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        choices = data.get("choices") or []
        if not choices:
            raise ProviderResponseError(
                "DeepSeek response did not contain a choice",
                response.text,
                code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
                request_id=data.get("id"),
                terminal=ProviderTerminalMetadata(
                    protocol=self.protocol,
                    terminal_event_seen=False,
                ),
                raw_transport_format=transport.format,
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )
        message = choices[0].get("message", {})
        content = message.get("content")
        reasoning_content = message.get("reasoning_content")
        token_usage = TokenUsage(
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cached_input_tokens=int(
                prompt_details.get("cached_tokens", usage.get("prompt_cache_hit_tokens", 0))
            ),
            reasoning_tokens=int(completion_details.get("reasoning_tokens", 0)),
        )
        finish_reason = choices[0].get("finish_reason")
        terminal = ProviderTerminalMetadata(
            protocol=self.protocol,
            terminal_event_seen=isinstance(finish_reason, str) and bool(finish_reason),
            terminal_status="completed" if finish_reason == "stop" else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            stream_completed=False,
        )
        if not isinstance(content, str):
            raise ProviderResponseError(
                "DeepSeek response did not contain text",
                response.text,
                code=ProviderFailureCode.PROTOCOL_INCOMPLETE,
                usage=token_usage,
                request_id=data.get("id"),
                terminal=terminal,
                raw_transport_format=transport.format,
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )
        if finish_reason == "length":
            raise ProviderResponseError(
                "DeepSeek output reached the Provider token limit",
                response.text,
                code=ProviderFailureCode.TOKEN_LIMIT_EXCEEDED,
                usage=token_usage,
                request_id=data.get("id"),
                extracted_content=content,
                terminal=terminal,
                raw_transport_format=transport.format,
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )
        if finish_reason != "stop":
            code = (
                ProviderFailureCode.PROTOCOL_INCOMPLETE
                if finish_reason is None
                else ProviderFailureCode.PROVIDER_TERMINAL_FAILURE
            )
            raise ProviderResponseError(
                "DeepSeek response has no safe successful terminal reason",
                response.text,
                code=code,
                usage=token_usage,
                request_id=data.get("id"),
                extracted_content=content,
                terminal=terminal,
                raw_transport_format=transport.format,
                raw_entity_body_sha256=transport.entity_body_sha256,
                response_headers_redacted=response_headers,
            )
        return ModelResponse(
            text=content,
            usage=token_usage,
            request_id=data.get("id"),
            raw_response=transport.text,
            raw_transport_format=transport.format,
            raw_entity_body_sha256=transport.entity_body_sha256,
            response_headers_redacted=response_headers,
            reasoning_content=(
                reasoning_content if isinstance(reasoning_content, str) else None
            ),
            terminal=terminal,
        )

    async def _post(self, payload: dict[str, Any], api_key: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {api_key}"}
        if self._client is not None:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        else:
            async with httpx.AsyncClient(timeout=600) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        response.raise_for_status()
        return response
