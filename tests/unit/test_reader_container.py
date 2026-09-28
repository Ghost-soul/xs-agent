import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest

from deploy.web import WebGateway
from reader.app import create_app


@pytest.fixture
def reader_gateway(tmp_path):
    database = AsyncMock()
    database.projects.return_value = [{"id": "fixture", "title": "合成作品"}]
    (tmp_path / "index.html").write_text("<h1>小说书架</h1>", encoding="utf8")
    gateway = WebGateway(
        create_app(database),
        directory=tmp_path,
        username="reader",
        password="synthetic-reader-password-123",
        internal_token="synthetic-internal-token-123",
        origin="http://reader.test",
    )
    return gateway, database


@pytest.mark.asyncio
async def test_reader_auth_and_write_surfaces_are_unreachable(reader_gateway):
    gateway, database = reader_gateway
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://reader.test"
    ) as client:
        assert (await client.get("/backend/api/projects")).status_code == 401
        assert (await client.get("/health/live")).json() == {"status": "ok"}
        client.auth = httpx.BasicAuth("reader", "synthetic-reader-password-123")
        assert (await client.get("/", headers={"Accept": "text/html"})).status_code == 200
        response = await client.get("/backend/api/projects")
        assert response.json() == [{"id": "fixture", "title": "合成作品"}]
        assert "no-store" in response.headers["cache-control"]
        assert (
            await client.get("/backend/api/projects", headers={"Host": "wrong.test"})
        ).status_code == 403
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert (
                await client.request(method, "/backend/api/projects", json={})
            ).status_code == 405
        for path in (
            "/api/provider-profiles",
            "/api/prompt-templates",
            "/api/generation",
            "/api/search/rebuild",
            "/openapi.json",
        ):
            assert (await client.get("/backend" + path)).status_code == 404
        assert (
            await client.get("/backend/api/projects/not-a-uuid/reader/manifest")
        ).status_code == 422
        assert (
            await client.get(
                "/backend/api/projects/00000000-0000-0000-0000-000000000001/reader/search",
                params={"version_id": "00000000-0000-0000-0000-000000000002", "q": " "},
            )
        ).status_code == 422
    database.projects.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_failures_do_not_expose_secrets(reader_gateway):
    gateway, database = reader_gateway
    database.projects.side_effect = psycopg.OperationalError("private-password-and-novel-body")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway),
        base_url="http://reader.test",
        auth=("reader", "synthetic-reader-password-123"),
    ) as client:
        response = await client.get("/backend/api/projects")
    assert response.status_code == 503
    assert "private-password" not in response.text


def test_reader_context_contains_no_writer_or_private_files(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "reader_context", root / "scripts/prepare_reader_context.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.prepare(root, tmp_path / "context")
    assert result["files"] == 19
    paths = {
        p.relative_to(tmp_path / "context").as_posix()
        for p in (tmp_path / "context").rglob("*")
        if p.is_file()
    }
    assert all(not name.startswith(("src/", "configs/", "data/", ".runtime/")) for name in paths)
    with pytest.raises(ValueError, match="已存在"):
        module.prepare(root, tmp_path / "context")
    dockerfile = (root / "reader/Dockerfile").read_text(encoding="utf8")
    assert "10001:10001" in dockerfile and "migrate" not in dockerfile
