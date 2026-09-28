# ruff: noqa: F401 F811
"""Old hashes survive the capacity-error copy edit; authorization stays exact."""

import inspect

import pytest

from novel_writer.generation import budget
from novel_writer.generation import contract_compatibility as compat
from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_format_trial import create_trial, formal_rows
from tests.integration.test_generation_automation import automated, settle
from tests.integration.test_generation_longform import longform
from tests.integration.test_genre_generation import generation, post, read, start
from tests.integration.test_novel_run_rebuild import current
from tests.integration.test_stage_craft import craft, create_craft


@pytest.mark.parametrize("trial", [False, True])
def test_old_preview_can_run_without_repreview_or_contract_migration(craft, monkeypatch, trial):
    client, control = craft
    original = inspect.getsource
    legacy = compat.legacy_builder_source()
    with monkeypatch.context() as old:
        old.setattr(inspect, "getsource", lambda obj: legacy
                    if obj is budget.request_for else original(obj))
        base, draft = (create_trial if trial else create_craft)(client, pause_after_plan=True)
    before = formal_rows(client, base.split("/")[3])
    done = start(client, base, draft)
    assert control["calls"] == ["plan"]
    assert done["calls"][0]["status"] == "completed", done["state"]
    assert done["snapshot"] == draft["snapshot"]
    assert done["preview_sha256"] == draft["preview_sha256"]
    assert done["spec"] == draft["spec"]
    assert formal_rows(client, base.split("/")[3]) == before


def test_genuine_request_builder_change_still_prevents_authorization(craft, monkeypatch):
    client, control = craft
    original = inspect.getsource
    legacy = compat.legacy_builder_source()
    with monkeypatch.context() as old:
        old.setattr(inspect, "getsource", lambda obj: legacy
                    if obj is budget.request_for else original(obj))
        base, draft = create_craft(client)
    monkeypatch.setattr(inspect, "getsource", lambda obj: original(obj) + "\n# new builder\n"
                        if obj is budget.request_for else original(obj))
    response = post(client, f"{base}/{draft['id']}/authorize", {
        "confirmed": True, "preview_sha256": draft["preview_sha256"],
    })
    assert response.status_code == 409 and "提示词已修订" in response.text
    assert control["calls"] == []
