from copy import deepcopy

import pytest

from novel_writer.generation import request_preparation
from novel_writer.generation.schemas import GenerationSpec, NovelRunSpec
from novel_writer.services.errors import WorkflowError
from tests.unit.test_generation_context_budget import profile
from tests.unit.test_genre_generation import spec


def test_api_schemas_default_to_200000_but_preserve_explicit_frozen_limits():
    payload = spec().model_dump(mode="json")
    del payload["input_limit"]
    for schema in (GenerationSpec, NovelRunSpec):
        assert schema.model_validate(payload).input_limit == 200000
        assert schema.model_validate({**payload, "input_limit": 58000}).input_limit == 58000


def prepare(monkeypatch, action="write:1", size=68000, optional=1000, **limits):
    provider = profile()
    provider.models[0].context_window = limits.pop("context_window", 200000)
    configuration = spec(workflow="novel-run-v1", stage_mode="longform-v1", **limits)
    snapshot = {
        "profile": provider.model_dump(mode="json"),
        "counting": {k: {"method": "utf8-byte-upper-bound"} for k in ("chief", "writer")},
        "context": {"relevant_history": [{"revision_id": "optional", "body": "x" * optional}]},
    }
    before = deepcopy(snapshot)
    monkeypatch.setattr(
        request_preparation,
        "render_for",
        lambda _, rendered, *args: (
            "system",
            "m" * size + "".join(h["body"] for h in rendered["context"]["relevant_history"]),
        ),
    )
    result = request_preparation.prepare_request(
        configuration, snapshot, action, None, None, None, {}, {}
    )
    assert snapshot == before
    return result


@pytest.mark.parametrize(
    "action", ["plan", "write:1", "chief:1", "memory:1", "checker", "reader", "editor"]
)
def test_all_roles_keep_requests_above_58000_within_the_new_limit(monkeypatch, action):
    request, count, _, omitted = prepare(monkeypatch, action)
    assert 58000 < count < 100000
    assert request.user_prompt.endswith("x" * 1000)
    assert not omitted


def test_optional_history_is_trimmed_only_when_actual_capacity_requires_it(monkeypatch):
    _, count, _, omitted = prepare(monkeypatch, optional=40000)
    assert 58000 < count < 100000
    assert [h["revision_id"] for h in omitted] == ["optional"]


@pytest.mark.parametrize(
    "limits", [{"input_limit": 58000}, {"context_window": 60000}, {"size": 100000}]
)
def test_explicit_limits_model_context_and_100000_ceiling_still_apply(monkeypatch, limits):
    with pytest.raises(WorkflowError, match="超过允许值"):
        prepare(monkeypatch, **limits)
