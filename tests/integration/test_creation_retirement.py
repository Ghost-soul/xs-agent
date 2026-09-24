# ruff: noqa: F811
"""Current creation rejects retired actions while frozen records remain readable."""

from uuid import uuid4

import pytest

from tests.integration.support import headers
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_genre_generation import create, generation, post, read  # noqa: F401


@pytest.mark.parametrize(
    "change",
    [{"enable_reader": True}, {"milestone_unit": 2}, {"feedback_policy": "legacy-v1"}],
)
def test_retired_creation_fields_rejected_on_both_preview_routes(generation, change):
    client, control = generation
    base, frozen = create(client)
    spec = {**frozen["spec"], "feedback_policy": "logic-v1", **change}
    for path in [base, base + "/random-preview"]:
        response = client.post(path, json=spec, headers=headers(str(uuid4())))
        assert response.status_code == 422, response.text
    assert read(client, base + "/" + frozen["id"])["snapshot"] == frozen["snapshot"]
    assert control["calls"] == []


def test_single_unit_and_title_are_still_available_and_retrieval_is_retired(generation):
    client, control = generation
    base, frozen = create(client)
    spec = {
        **frozen["spec"], "workflow": "novel-run-v1", "feedback_policy": "logic-v1",
        "stage_mode": "single-unit-v1", "generate_title": True,
    }
    response = post(client, base, spec)
    assert response.status_code == 200, response.text
    assert response.json()["spec"]["generate_title"] is True
    assert response.json()["spec"]["stage_mode"] == "single-unit-v1"
    project = base.removesuffix("/generation-batches")
    assert client.get(project + "/retrieval-index", headers=headers()).status_code == 404
    assert post(client, project + "/retrieval-index/rebuild", {}).status_code == 404
    assert control["calls"] == []


def test_retired_reader_cannot_be_enabled_in_new_amendment(generation):
    client, control = generation
    base, frozen = create(client)
    response = post(client, base + "/" + frozen["id"] + "/amendment-preview", {
        "candidate_sha256": "a" * 64, "mode": "verify", "instruction": "核对事实",
        "max_cost_cny": "1", "enable_reader": True,
    })
    assert response.status_code == 422, response.text
    assert control["calls"] == []
