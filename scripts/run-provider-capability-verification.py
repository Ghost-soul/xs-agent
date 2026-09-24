from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

from novel_writer.api.routes.provider_profiles import (
    VerifyProviderCapabilityRequest,
    verify_provider_capability,
)
from novel_writer.core.config import Settings
from novel_writer.core.credentials import WindowsCredentialStore
from novel_writer.services.provider_profiles import ProviderProfileStore


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one explicitly authorized two-call Provider capability verification."
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--context-window", required=True, type=int)
    parser.add_argument("--max-output-tokens", required=True, type=int)
    parser.add_argument("--source-note", required=True)
    parser.add_argument("--max-cost-cny", required=True, type=Decimal)
    parser.add_argument("--reasoning-tokens-billed-as-output", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--expected-provider-calls", required=True, type=int)
    args = parser.parse_args()
    if not args.confirmed:
        parser.error("--confirmed is required")
    if args.expected_provider_calls != 2:
        parser.error("--expected-provider-calls must be exactly 2")
    return args


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = Settings()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                provider_profile_store=ProviderProfileStore(settings.provider_profiles_path),
                credential_store=WindowsCredentialStore(),
            )
        )
    )
    payload = VerifyProviderCapabilityRequest(
        confirmed=True,
        acknowledge_provider_calls_and_cost=True,
        expected_provider_calls=2,
        model=args.model,
        context_window=args.context_window,
        max_output_tokens=args.max_output_tokens,
        reasoning_tokens_billed_as_output=args.reasoning_tokens_billed_as_output,
        source_note=args.source_note,
        max_cost_cny=args.max_cost_cny,
    )
    return await verify_provider_capability(args.profile, payload, request)


def main() -> None:
    result = asyncio.run(_run(_arguments()))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
