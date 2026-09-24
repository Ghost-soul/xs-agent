import json

import httpx
import pytest

from novel_writer.generation.budget import request_for, validate_capacity
from novel_writer.generation.novel import model_for, slots_for
from novel_writer.generation.schemas import FrozenGenerationSpec, GenerationSpec, NovelRunSpec
from novel_writer.generation.token_limits import INPUT_TOKEN_LIMIT, TOKEN_LIMIT
from novel_writer.providers.base import ProviderResponseError
from novel_writer.providers.deepseek import DeepSeekChatProvider
from novel_writer.providers.openai import OpenAIResponsesProvider
from novel_writer.providers.openai_compatible import OpenAICompatibleChatProvider
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_genre_generation import spec


def all_tokens_spec(schema=NovelRunSpec):
    payload = spec(
        workflow="novel-run-v1",
        stage_mode="longform-v1",
        unit_limit=6,
        enable_editor=True,
        generate_title=True,
    ).model_dump(mode="json")
    for field in (
        "input_limit",
        "chief_output_limit",
        "writer_output_limit",
        "auxiliary_output_limit",
    ):
        payload.pop(field)
    payload["roles"] = {
        role: {"model": "test"} for role in ("memory", "checker", "reader", "editor")
    }
    return schema.model_validate(payload)


ACTIONS = [
    *slots_for(all_tokens_spec()),
    "write",
    "memory",
    "review",
    "amend",
    "rewrite",
    "memory_amend",
    "checker_amend",
    "reader_amend",
]


@pytest.mark.parametrize("schema", [GenerationSpec, NovelRunSpec])
def test_every_new_action_inherits_200000_input_and_100000_output(schema):
    config = all_tokens_spec(schema)
    assert config.input_limit == INPUT_TOKEN_LIMIT
    assert all(model_for(config, action)[1] == TOKEN_LIMIT for action in ACTIONS)
    frozen = FrozenGenerationSpec.model_validate(
        {
            k: v
            for k, v in config.model_dump().items()
            if k
            not in {"roles", "chief_output_limit", "writer_output_limit", "auxiliary_output_limit"}
        }
    )
    assert model_for(frozen, "memory")[1] == 6000
    assert model_for(frozen, "write")[1] == 12000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_type,field",
    [
        (OpenAIResponsesProvider, "max_output_tokens"),
        (OpenAICompatibleChatProvider, "max_tokens"),
        (DeepSeekChatProvider, "max_tokens"),
    ],
)
async def test_every_action_maps_100000_to_each_wire_protocol(provider_type, field):
    config = all_tokens_spec()
    capable = profile()
    capable.models[0].max_output_tokens = 100000
    capable.models[0].context_window = 300000
    sent = []

    async def transport(request):
        payload = json.loads(request.content)
        assert payload[field] == 100000
        sent.append(payload)
        # Deliberate local rejection ends each probe without a model or remote transport.
        return httpx.Response(400, json={"error": {"message": "fixture"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = (
            provider_type(client=client, provider_name="fixture", base_url="http://fixture.invalid")
            if provider_type is OpenAICompatibleChatProvider
            else provider_type(client=client)
        )
        for action in ACTIONS:
            request = request_for(config, capable, action, "system", "x" * 150000)
            assert (
                100000
                < validate_capacity(
                    request.user_prompt,
                    request,
                    config,
                    capable,
                    {"method": "utf8-byte-upper-bound"},
                )
                < 200000
            )
            with pytest.raises((ProviderResponseError, httpx.HTTPStatusError)):
                await provider.generate(request, "fixture-key")
    assert len(sent) == len(ACTIONS)
