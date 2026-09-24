# ruff: noqa: F401, F811
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from tests.integration.support import pytestmark as pytestmark
from tests.integration.test_genre_generation import create, generation, post, read


def test_preview_preparation_does_not_block_other_requests_or_start_models(generation, monkeypatch):
    from novel_writer.generation import service

    client, control = generation
    base, first = create(client, feedback_policy="logic-v1")
    entered, release = Event(), Event()
    original = service.prepare_preview

    def slow_preparation(*args):
        entered.set()
        assert release.wait(5), "Preview worker was not released"
        return original(*args)

    monkeypatch.setattr(service, "prepare_preview", slow_preparation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        preview = pool.submit(post, client, base, first["spec"])
        try:
            assert entered.wait(3)
            # With synchronous preparation this read would wait behind the preview.
            history = pool.submit(read, client, base)
            assert history.result(timeout=2)[0]["id"] == first["id"]
            assert not preview.done()
        finally:
            release.set()
        response = preview.result(timeout=5)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "draft"
    assert response.json()["snapshot"]["plan_input_tokens"] == (
        first["snapshot"]["plan_input_tokens"]
    )
    assert not control["calls"]
