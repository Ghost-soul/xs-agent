from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass

import httpx


def network_timeout(total_seconds: float) -> httpx.Timeout:
    """The outer deadline bounds the whole call, including streamed heartbeats."""
    return httpx.Timeout(total_seconds, connect=min(15, total_seconds),
                         write=min(60, total_seconds), pool=min(15, total_seconds))


@dataclass(frozen=True)
class ProviderTransport:
    """Reversible HTTP entity-body evidence captured before text parsing."""

    text: str
    format: str
    entity_body_sha256: str
    byte_length: int


class ProviderTransportReadError(httpx.StreamError):
    def __init__(self, cause: BaseException, transport: ProviderTransport) -> None:
        super().__init__(str(cause))
        self.__cause__ = cause
        self.transport = transport


async def capture_response_body(response: httpx.Response) -> ProviderTransport:
    body = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
    except (Exception, asyncio.CancelledError) as error:
        # An outer deadline cancels the reader. Retain the same received bytes;
        # adapters turn this into an unknown receipt, never a successful report.
        raise ProviderTransportReadError(error, encode_transport(bytes(body))) from error
    return encode_transport(bytes(body))


def encode_transport(body: bytes) -> ProviderTransport:
    digest = hashlib.sha256(body).hexdigest()
    try:
        return ProviderTransport(
            body.decode("utf-8", errors="strict"),
            "http-body-utf8-v1",
            digest,
            len(body),
        )
    except UnicodeDecodeError:
        payload = {
            "base64": base64.b64encode(body).decode("ascii"),
            "encoding": "base64",
            "media": "http-entity-body",
        }
        return ProviderTransport(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            "http-body-base64-v1",
            digest,
            len(body),
        )


def redacted_response_headers(response: httpx.Response) -> dict[str, str]:
    allowed = {
        "content-length",
        "content-type",
        "date",
        "openai-processing-ms",
        "request-id",
        "retry-after",
        "x-request-id",
    }
    return {
        key.casefold(): value
        for key, value in response.headers.items()
        if key.casefold() in allowed
    }
