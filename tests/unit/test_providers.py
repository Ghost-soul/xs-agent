import hashlib
import json

import httpx
import pytest

from novel_writer.providers.base import (
    ModelRequest,
    ProviderResponseError,
    TokenUsage,
)
from novel_writer.providers.deepseek import DeepSeekChatProvider
from novel_writer.providers.openai import OpenAIResponsesProvider
from novel_writer.providers.openai_compatible import OpenAICompatibleChatProvider


def request() -> ModelRequest:
    return ModelRequest(
        model="verified-model-id",
        system_prompt="system",
        user_prompt="user",
        max_output_tokens=123,
        json_schema_name="result",
        json_schema={"type": "object", "properties": {"body": {"type": "string"}}},
        reasoning_effort="medium",
    )


def test_reasoning_request_keeps_content_reference_separate_from_provider_ceiling() -> None:
    separated = ModelRequest.model_validate({**request().model_dump(), "max_content_tokens": 8_000})

    assert separated.max_content_tokens == 8_000
    assert separated.max_output_tokens == 50_000

    plain = ModelRequest.model_validate(
        {**separated.model_dump(), "reasoning_effort": "none", "max_output_tokens": 8_000}
    )
    assert plain.max_content_tokens == 8_000
    assert plain.max_output_tokens == 8_000


@pytest.mark.asyncio
async def test_openai_responses_adapter_maps_structured_output_and_usage() -> None:
    body = (
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"{\\"body\\":"}\n\n'
        "event: response.output_text.delta\n"
        'data: {"type":"response.output_text.delta","delta":"\\"ok\\"}"}\n\n'
        "event: response.completed\n"
        'data: {"type":"response.completed","response":{"id":"resp_123",'
        '"usage":{"input_tokens":20,"output_tokens":10,'
        '"input_tokens_details":{"cached_tokens":5,"cache_write_tokens":3},'
        '"output_tokens_details":{"reasoning_tokens":2}}}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url.path == "/v1/responses"
        assert http_request.headers["authorization"] == "Bearer secret"
        payload = json.loads(http_request.content)
        assert payload["model"] == "verified-model-id"
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["max_output_tokens"] == 123
        assert payload["reasoning"] == {"effort": "medium"}
        assert payload["stream"] is True
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-request-id": "request-header",
                "set-cookie": "must-not-persist=yes",
            },
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert result.text == '{"body":"ok"}'
    assert result.request_id == "resp_123"
    assert result.usage == TokenUsage(
        input_tokens=20,
        output_tokens=10,
        cached_input_tokens=5,
        cache_write_tokens=3,
        reasoning_tokens=2,
    )
    assert result.raw_response == body
    assert result.raw_transport_format == "sse-transcript-v1"
    assert result.raw_entity_body_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert result.response_headers_redacted["x-request-id"] == "request-header"
    assert "set-cookie" not in result.response_headers_redacted

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "failure_code"),
    [(400, "provider_terminal_failure"), (500, "outcome_uncertain")],
)
async def test_openai_responses_http_failure_preserves_transport(
    status_code: int,
    failure_code: str,
) -> None:
    body = b'{"error":{"message":"provider failure"}}'

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={
                "content-type": "application/json",
                "request-id": "failed-request",
                "authorization": "must-not-persist",
            },
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert raised.value.code == failure_code
    assert raised.value.raw_response == body.decode()
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(body).hexdigest()
    assert raised.value.response_headers_redacted["request-id"] == "failed-request"
    assert "authorization" not in raised.value.response_headers_redacted


@pytest.mark.asyncio
async def test_openai_responses_non_utf8_body_preserves_entity_sha() -> None:
    body = b"\xff\xfe\x80"

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert raised.value.code == "text_encoding_invalid"
    assert raised.value.raw_transport_format == "http-body-base64-v1"
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_openai_responses_malformed_fields_are_typed_transport_errors() -> None:
    body = b'{"id":"broken","status":"completed","usage":[1]}'

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="fields were invalid") as raised:
            await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert raised.value.code == "response_schema_invalid"
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_openai_responses_adapter_accepts_json_fallback() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_json",
                "status": "completed",
                "output_text": '{"body":"fallback"}',
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert result.text == '{"body":"fallback"}'
    assert result.request_id == "resp_json"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["incomplete", "failed"])
async def test_openai_responses_adapter_rejects_known_terminal_failure(status: str) -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "resp_bad", "status": status, "output": []},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as captured:
            await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert json.loads(captured.value.raw_response)["status"] == status


@pytest.mark.asyncio
async def test_openai_responses_adapter_preserves_refusal_for_audit() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_refusal",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as captured:
            await OpenAIResponsesProvider(client=client).generate(request(), "secret")

    assert "resp_refusal" in captured.value.raw_response


@pytest.mark.asyncio
async def test_deepseek_adapter_maps_chat_completion_and_cache_usage() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url.path == "/chat/completions"
        payload = json.loads(http_request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["stream"] is False
        return httpx.Response(
            200,
            json={
                "id": "chat_123",
                "choices": [
                    {
                        "message": {
                            "reasoning_content": "先核对结构，再生成内容。",
                            "content": '{"body":"ok"}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 30,
                    "completion_tokens": 12,
                    "prompt_cache_hit_tokens": 7,
                    "completion_tokens_details": {"reasoning_tokens": 8},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekChatProvider(client=client).generate(request(), "secret")

    assert result.request_id == "chat_123"
    assert result.usage.cached_input_tokens == 7
    assert result.usage.reasoning_tokens == 8
    assert result.usage.content_tokens == 4
    assert result.reasoning_content == "先核对结构，再生成内容。"
    assert result.raw_transport_format == "http-body-utf8-v1"
    assert result.raw_entity_body_sha256 == hashlib.sha256(
        (result.raw_response or "").encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_deepseek_adapter_can_explicitly_disable_reasoning() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        assert payload["thinking"] == {"type": "disabled"}
        return httpx.Response(
            200,
            json={
                "id": "chat_without_reasoning",
                "choices": [
                    {"message": {"content": '{"body":"ok"}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    disabled_request = request().model_copy(update={"reasoning_effort": "none"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await DeepSeekChatProvider(client=client).generate(disabled_request, "secret")

    assert result.request_id == "chat_without_reasoning"


@pytest.mark.asyncio
async def test_deepseek_rejects_complete_json_when_finish_reason_is_length() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chat_complete_at_limit",
                "choices": [
                    {
                        "message": {"content": '{"body":"complete"}'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await DeepSeekChatProvider(client=client).generate(request(), "secret")

    assert raised.value.code == "token_limit_exceeded"
    assert raised.value.extracted_content == '{"body":"complete"}'
    assert raised.value.raw_transport_format == "http-body-utf8-v1"
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(
        raised.value.raw_response.encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_deepseek_rejects_invalid_json_when_finish_reason_is_length() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chat_truncated",
                "choices": [
                    {
                        "message": {"content": '{"body":"incomplete'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="token limit"):
            await DeepSeekChatProvider(client=client).generate(request(), "secret")


@pytest.mark.asyncio
async def test_deepseek_rejects_complete_fenced_json_when_finish_reason_is_length() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chat_complete_whitespace",
                "choices": [
                    {
                        "message": {"content": '```json\n{"body":"complete"}\n```'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await DeepSeekChatProvider(client=client).generate(request(), "secret")

    assert raised.value.code == "token_limit_exceeded"


@pytest.mark.asyncio
async def test_deepseek_rejects_uppercase_fenced_json_when_finish_reason_is_length() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chat_complete_uppercase_fence",
                "choices": [
                    {
                        "message": {"content": '```JSON\n{"body":"complete"}\n```'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await DeepSeekChatProvider(client=client).generate(request(), "secret")

    assert raised.value.code == "token_limit_exceeded"


@pytest.mark.asyncio
async def test_deepseek_rejects_unparseable_fenced_json_when_finish_reason_is_length() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chat_fenced_truncated",
                "choices": [
                    {
                        "message": {"content": '```json\\n{\\"body\\":\\"incomplete\\"'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="token limit"):
            await DeepSeekChatProvider(client=client).generate(request(), "secret")






@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_response_format", "expects_schema_instruction"),
    [
        ("json_schema", "json_schema", False),
        ("json_object", "json_object", True),
        ("prompt_only", None, True),
    ],
)
async def test_openai_compatible_adapter_supports_structured_output_modes(
    mode: str,
    expected_response_format: str | None,
    expects_schema_instruction: bool,
) -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url == httpx.URL("https://gateway.example/api/v1/chat/completions")
        assert http_request.headers["authorization"] == "Bearer secret"
        assert http_request.headers["user-agent"].startswith(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        )
        assert http_request.headers["accept"] == "application/json"
        payload = json.loads(http_request.content)
        assert payload["model"] == "verified-model-id"
        assert payload["max_tokens"] == 123
        assert payload["stream"] is False
        assert "stream_options" not in payload
        assert payload["reasoning_effort"] == "medium"
        response_format = payload.get("response_format")
        assert (
            response_format.get("type") if isinstance(response_format, dict) else None
        ) == expected_response_format
        user_content = payload["messages"][1]["content"]
        assert ("Return only JSON matching this schema" in user_content) is (
            expects_schema_instruction
        )
        if mode == "json_schema":
            assert response_format["json_schema"]["schema"] == request().json_schema
        return httpx.Response(
            200,
            json={
                "id": "compatible-request",
                "choices": [{"message": {"content": '{"body":"ok"}'}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            provider_name="gateway-profile",
            base_url="https://gateway.example/api/v1/",
            structured_output_mode=mode,
            supports_reasoning_effort=True,
            client=client,
        )
        result = await provider.generate(request(), "secret")

    assert provider.name == "gateway-profile"
    assert result.request_id == "compatible-request"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 4
    assert result.usage.cached_input_tokens == 3
    assert result.usage.reasoning_tokens == 2
    assert result.model_dump() == {
        "text": '{"body":"ok"}',
        "usage": {
            "input_tokens": 12,
            "output_tokens": 4,
            "cached_input_tokens": 3,
            "cache_write_tokens": 0,
            "reasoning_tokens": 2,
            "content_tokens": 2,
        },
        "request_id": "compatible-request",
    }


@pytest.mark.asyncio
async def test_openai_compatible_skips_schema_for_writer_plain_text_transport() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        assert payload["max_tokens"] == 11_960
        assert "response_format" not in payload
        assert "Return only JSON matching this schema" not in payload["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "id": "plain-writer-request",
                "choices": [
                    {"message": {"content": "直接正文"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 400,
                    "completion_tokens_details": {"reasoning_tokens": 300},
                },
            },
        )

    plain_request = request().model_copy(
        update={
            "max_output_tokens": 11_960,
            "max_content_tokens": 3_768,
            "skip_structured_output": True,
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleChatProvider(
            provider_name="gateway-profile",
            base_url="https://gateway.example/v1",
            structured_output_mode="prompt_only",
            client=client,
        ).generate(plain_request, "secret")

    assert result.text == "直接正文"
    assert result.reasoning_content is None
    assert result.usage.reasoning_tokens == 300
    assert result.usage.content_tokens == 100


@pytest.mark.asyncio
async def test_openai_compatible_local_endpoint_omits_empty_authorization_header() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert "authorization" not in http_request.headers
        return httpx.Response(
            200,
            json={
                "id": "local-request",
                "choices": [{"message": {"content": '{"body":"ok"}'}, "finish_reason": "stop"}],
                "usage": {},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleChatProvider(
            provider_name="ollama-local",
            base_url="http://127.0.0.1:11434/v1",
            structured_output_mode="prompt_only",
            client=client,
        ).generate(request(), "")

    assert result.request_id == "local-request"


@pytest.mark.asyncio
async def test_openai_compatible_reports_incomplete_length_response_as_truncated() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "truncated-request",
                "choices": [
                    {"message": {"content": '{"body":"unfinished"'}, "finish_reason": "length"}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="token limit") as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                client=client,
            ).generate(request(), "secret")

    assert raised.value.is_truncated is True
    assert raised.value.request_id == "truncated-request"
    assert raised.value.usage is not None
    assert raised.value.usage.output_tokens == 123
    assert "secret" not in raised.value.raw_response


@pytest.mark.asyncio
async def test_openai_compatible_rejects_complete_json_at_length_limit() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "complete-but-limited",
                "choices": [
                    {"message": {"content": '{"body":"complete"}'}, "finish_reason": "length"}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 123},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                client=client,
            ).generate(request(), "secret")

    assert raised.value.code == "token_limit_exceeded"
    assert raised.value.extracted_content == '{"body":"complete"}'


@pytest.mark.asyncio
async def test_openai_compatible_model_streams_and_assembles_complete_response() -> None:
    body = "".join(
        [
            'data: {"id":"stream-request","model":"qwen-model","choices":'
            '[{"delta":{"role":"assistant","content":"{\\"body\\":"},'
            '"finish_reason":null}]}\n\n',
            'data: {"id":"stream-request","choices":[{"delta":'
            '{"content":"\\"ok\\"}"},"finish_reason":"stop"}]}\n\n',
            'data: {"id":"stream-request","choices":[],"usage":'
            '{"prompt_tokens":12,"completion_tokens":4,'
            '"prompt_tokens_details":{"cached_tokens":3},'
            '"completion_tokens_details":{"reasoning_tokens":2}}}\n\n',
            "data: [DONE]\n\n",
        ]
    )

    async def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleChatProvider(
            provider_name="qwen",
            base_url="https://gateway.example/v1",
            streaming_by_model={"verified-model-id": True},
            client=client,
        ).generate(request(), "secret")

    assert result.text == '{"body":"ok"}'
    assert result.request_id == "stream-request"
    assert result.usage == TokenUsage(
        input_tokens=12,
        output_tokens=4,
        cached_input_tokens=3,
        reasoning_tokens=2,
    )
    assert result.raw_response is not None
    assert result.raw_response == body
    assert result.raw_transport_format == "sse-transcript-v1"
    assert result.assembled_response is not None
    stored = json.loads(result.assembled_response)
    assert stored["choices"][0]["message"]["content"] == result.text


@pytest.mark.asyncio
async def test_openai_compatible_model_can_disable_profile_streaming_default() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        assert payload["stream"] is False
        assert "stream_options" not in payload
        return httpx.Response(
            200,
            json={
                "id": "non-stream-model-request",
                "choices": [{"message": {"content": '{"body":"ok"}'}, "finish_reason": "stop"}],
                "usage": {},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleChatProvider(
            provider_name="gateway-profile",
            base_url="https://gateway.example/v1",
            streaming_enabled=True,
            streaming_by_model={"verified-model-id": False},
            client=client,
        ).generate(request(), "secret")

    assert result.request_id == "non-stream-model-request"


@pytest.mark.asyncio
async def test_openai_compatible_stream_reports_incomplete_length_as_truncated() -> None:
    body = (
        'data: {"id":"stream-truncated","choices":[{"delta":'
        '{"content":"{\\"body\\":\\"unfinished\\""},"finish_reason":"length"}],'
        '"usage":{"prompt_tokens":12,"completion_tokens":123}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="token limit") as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=True,
                client=client,
            ).generate(request(), "secret")

    assert raised.value.is_truncated is True
    assert raised.value.request_id == "stream-truncated"
    assert raised.value.usage is not None
    assert raised.value.usage.output_tokens == 123
    assert raised.value.raw_response == body
    assert raised.value.raw_transport_format == "sse-transcript-v1"


@pytest.mark.asyncio
async def test_openai_compatible_stream_does_not_accept_missing_terminal_event() -> None:
    body = (
        'data: {"id":"interrupted","choices":[{"delta":'
        '{"content":"{\\"body\\":\\"partial"},"finish_reason":null}]}\n\n'
    )

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="ended before a terminal event"):
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=True,
                client=client,
            ).generate(request(), "secret")


@pytest.mark.asyncio
async def test_openai_compatible_stream_preserves_malformed_event_transcript() -> None:
    body = 'data: {"id":"broken"\n\ndata: [DONE]\n\n'

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="valid JSON") as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=True,
                client=client,
            ).generate(request(), "secret")

    assert raised.value.code == "response_schema_invalid"
    assert raised.value.raw_response == body
    assert raised.value.raw_transport_format == "sse-transcript-v1"


@pytest.mark.asyncio
async def test_openai_compatible_stream_interruption_is_uncertain_with_partial_transport() -> None:
    partial = b'data: {"id":"interrupted","choices":[]}\n\n'

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield partial
            raise httpx.ReadError("connection lost")

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=InterruptedStream(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="interrupted") as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=True,
                client=client,
            ).generate(request(), "secret")

    assert raised.value.code == "outcome_uncertain"
    assert raised.value.raw_response == partial.decode()
    assert raised.value.raw_transport_format == "sse-transcript-v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "failure_code"),
    [(429, "provider_terminal_failure"), (503, "outcome_uncertain")],
)
async def test_openai_compatible_http_failure_preserves_transport(
    status_code: int,
    failure_code: str,
) -> None:
    body = b'{"error":{"message":"gateway failure"}}'

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={
                "content-type": "application/json",
                "x-request-id": "gateway-request",
                "set-cookie": "must-not-persist=yes",
            },
            content=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=True,
                client=client,
            ).generate(request(), "secret")

    assert raised.value.code == failure_code
    assert raised.value.raw_response == body.decode()
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(body).hexdigest()
    assert raised.value.response_headers_redacted["x-request-id"] == "gateway-request"
    assert "set-cookie" not in raised.value.response_headers_redacted


@pytest.mark.asyncio
async def test_openai_compatible_non_utf8_body_preserves_entity_sha() -> None:
    body = b"\xff\xfe\x80"

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=False,
                client=client,
            ).generate(request(), "secret")

    assert raised.value.code == "text_encoding_invalid"
    assert raised.value.raw_transport_format == "http-body-base64-v1"
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_openai_compatible_malformed_choices_are_typed_transport_errors() -> None:
    body = b'{"id":"broken","choices":{"not":"a-list"}}'

    async def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderResponseError, match="fields were invalid") as raised:
            await OpenAICompatibleChatProvider(
                provider_name="gateway-profile",
                base_url="https://gateway.example/v1",
                streaming_enabled=False,
                client=client,
            ).generate(request(), "secret")

    assert raised.value.code == "response_schema_invalid"
    assert raised.value.raw_entity_body_sha256 == hashlib.sha256(body).hexdigest()
