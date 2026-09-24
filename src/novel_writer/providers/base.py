from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

REASONING_OUTPUT_FLOOR_TOKENS = 50_000


class ProviderName(StrEnum):
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    DEEPSEEK = "deepseek"


class ProviderFailureCode(StrEnum):
    """Stable provider/validation failure categories used by every executor."""

    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"
    PROTOCOL_INCOMPLETE = "protocol_incomplete"
    RESPONSE_SCHEMA_INVALID = "response_schema_invalid"
    TEXT_ENCODING_INVALID = "text_encoding_invalid"
    UNDERFILLED_OUTPUT = "underfilled_output"
    PROVIDER_REFUSAL = "provider_refusal"
    PROVIDER_TERMINAL_FAILURE = "provider_terminal_failure"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


class ProviderTerminalMetadata(BaseModel):
    """Provider-neutral evidence that a response reached a safe terminal state."""

    protocol: str = Field(min_length=1, max_length=80)
    terminal_event_seen: bool
    terminal_status: str | None = None
    finish_reason: str | None = None
    stop_reason: str | None = None
    incomplete_reason: str | None = None
    stream_completed: bool | None = None

    @property
    def reached_output_limit(self) -> bool:
        values = {
            value.casefold()
            for value in (self.finish_reason, self.stop_reason, self.incomplete_reason)
            if value
        }
        return bool(values & {"length", "max_tokens", "max_output_tokens"})


class ProviderTransportMetadata(BaseModel):
    raw_transport_format: str | None = None
    raw_entity_body_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    response_headers_redacted: dict[str, str] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=160)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    max_output_tokens: int = Field(ge=1)
    max_content_tokens: int | None = Field(default=None, ge=1)
    json_schema_name: str = Field(min_length=1, max_length=64)
    json_schema: dict[str, Any]
    # Infrastructure probes may intentionally omit structured-output hints so
    # restrictive gateways can be tested with a plain chat request.
    skip_structured_output: bool = False
    # Connection probes should be able to avoid a gateway's SSE-only quirks.
    force_non_streaming: bool = False
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None

    @model_validator(mode="after")
    def separate_content_and_reasoning_capacity(self) -> "ModelRequest":
        """Keep the author limit about visible content, not hidden reasoning.

        Most reasoning APIs expose only one technical generation ceiling.  For a
        reasoning request we therefore retain the caller's value as the visible
        content reference and raise only the Provider-facing total ceiling.  The
        finite 50k floor preserves the previously proven recovery envelope without
        pretending that hidden reasoning is authored content.
        """

        # A missing content limit identifies a legacy or infrastructure request;
        # preserve its exact Provider cap for backward compatibility.
        if self.max_content_tokens is None:
            return self
        content_limit = self.max_content_tokens
        # Equal limits identify legacy callers without a separate reasoning
        # reservation. A larger Provider limit is an explicit confirmed layer.
        if (
            self.reasoning_effort not in {None, "none"}
            and self.max_output_tokens <= content_limit
        ):
            self.max_output_tokens = max(
                self.max_output_tokens,
                content_limit,
                REASONING_OUTPUT_FLOOR_TOKENS,
            )
        return self


class TokenUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    content_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def separate_content_usage(self) -> "TokenUsage":
        if self.content_tokens is None:
            self.content_tokens = max(0, self.output_tokens - self.reasoning_tokens)
        return self


class ModelResponse(BaseModel):
    text: str
    usage: TokenUsage
    request_id: str | None = None
    raw_response: str | None = Field(default=None, exclude=True)
    raw_transport_format: str | None = Field(default=None, exclude=True)
    raw_entity_body_sha256: str | None = Field(default=None, exclude=True)
    response_headers_redacted: dict[str, str] = Field(default_factory=dict, exclude=True)
    assembled_response: str | None = Field(default=None, exclude=True)
    reasoning_content: str | None = Field(default=None, exclude=True)
    terminal: ProviderTerminalMetadata | None = Field(default=None, exclude=True)

    @property
    def raw_transport(self) -> str | None:
        """Compatibility-safe name for the immutable transport layer."""

        return self.raw_response

    @property
    def extracted_content(self) -> str:
        return self.text

    @property
    def transport_metadata(self) -> ProviderTransportMetadata:
        return ProviderTransportMetadata(
            raw_transport_format=self.raw_transport_format,
            raw_entity_body_sha256=self.raw_entity_body_sha256,
            response_headers_redacted=self.response_headers_redacted,
        )


class ProviderResponseError(ValueError):
    """A provider returned a terminal response that cannot be consumed."""

    def __init__(
        self,
        message: str,
        raw_response: str,
        *,
        code: str | ProviderFailureCode | None = None,
        usage: TokenUsage | None = None,
        request_id: str | None = None,
        extracted_content: str | None = None,
        terminal: ProviderTerminalMetadata | None = None,
        raw_transport_format: str | None = None,
        raw_entity_body_sha256: str | None = None,
        response_headers_redacted: dict[str, str] | None = None,
        assembled_response: str | None = None,
    ) -> None:
        super().__init__(message)
        # Keep a machine-readable category alongside the human-readable message.
        # Provider messages can gain context as they cross service boundaries, so
        # callers must not classify terminal failures with an exact string match.
        self.code = code.value if isinstance(code, ProviderFailureCode) else code
        self.raw_response = raw_response
        self.usage = usage
        self.request_id = request_id
        self.extracted_content = extracted_content
        self.terminal = terminal
        self.raw_transport_format = raw_transport_format
        self.raw_entity_body_sha256 = raw_entity_body_sha256
        self.response_headers_redacted = dict(response_headers_redacted or {})
        self.assembled_response = assembled_response

    @property
    def is_truncated(self) -> bool:
        """Whether this response ended before a consumable JSON document."""
        return self.code in {"truncated", ProviderFailureCode.TOKEN_LIMIT_EXCEEDED.value} or (
            "output was truncated" in str(self).casefold()
        )

    @property
    def stable_code(self) -> str:
        if self.is_truncated:
            return ProviderFailureCode.TOKEN_LIMIT_EXCEEDED.value
        return self.code or ProviderFailureCode.RESPONSE_SCHEMA_INVALID.value

    @property
    def transport_metadata(self) -> ProviderTransportMetadata:
        return ProviderTransportMetadata(
            raw_transport_format=self.raw_transport_format,
            raw_entity_body_sha256=self.raw_entity_body_sha256,
            response_headers_redacted=self.response_headers_redacted,
        )


ProviderIdentifier = str | ProviderName


def provider_identifier(value: ProviderIdentifier) -> str:
    """Return the stable profile identifier used in plans, hashes, and audit records."""
    return value.value if isinstance(value, ProviderName) else value


class ModelProvider(Protocol):
    @property
    def name(self) -> ProviderIdentifier: ...

    async def generate(self, request: ModelRequest, api_key: str) -> ModelResponse: ...
