import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from deploy.configure import create_configuration
from deploy.web import WebGateway
from novel_writer.core.config import Settings
from novel_writer.core.security import LocalSecurityMiddleware
from scripts.prepare_docker_context import EXACT_FILES, prepare_context, source_files

PASSWORD = "synthetic-browser-password-for-tests"
INTERNAL = "synthetic-internal-token-for-tests"
ORIGIN = "http://testserver"


@pytest.fixture
def web(tmp_path):
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("started")
        yield
        events.append("stopped")

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(LocalSecurityMiddleware, settings=Settings(
        _env_file=None, local_token=INTERNAL, allowed_origins=[ORIGIN],
    ))

    @app.get("/api/read")
    async def read():
        return {"ok": True}

    @app.post("/api/write")
    async def write(request: Request):
        assert request.headers["authorization"] == f"Bearer {INTERNAL}"
        assert request.headers["x-csrf-token"] == INTERNAL
        events.append("write")
        return {"saved": True}

    @app.get("/health/live")
    async def health():
        return {"status": "ok"}

    static = tmp_path / "web"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (tmp_path / ".env").write_text("private-outside-web")
    gateway = WebGateway(
        app, directory=static, username="author", password=PASSWORD,
        internal_token=INTERNAL, origin=ORIGIN,
    )
    with TestClient(gateway) as client:
        yield client, events
    assert events[-1] == "stopped"


@pytest.mark.parametrize("path", ["/", "/projects/123", "/backend/api/read", "/api/read"])
def test_gateway_requires_browser_auth_for_ui_and_api(web, path):
    client, _ = web
    for auth in (None, ("author", "wrong")):
        response = client.get(path, auth=auth)
        assert response.status_code == 401
        assert "Basic" in response.headers["www-authenticate"]
        assert INTERNAL not in response.text and PASSWORD not in response.text


@pytest.mark.parametrize("authorization", ["Basic %%%", "Basic YQ==", "Bearer " + INTERNAL])
def test_malformed_auth_and_internal_token_cannot_bypass_browser_login(web, authorization):
    client, _ = web
    response = client.get("/backend/api/read", headers={"Authorization": authorization})
    assert response.status_code == 401


def test_gateway_preserves_real_origin_check_and_hides_internal_token(web):
    client, events = web
    auth = ("author", PASSWORD)
    assert client.get("/health/live").status_code == 200
    assert client.get("/backend/api/read", auth=auth).json() == {"ok": True}
    for origin in (None, "https://other.invalid"):
        headers = {"Origin": origin} if origin else {}
        response = client.post("/backend/api/write", auth=auth, headers=headers)
        assert response.status_code == 403
    assert "write" not in events
    response = client.post("/backend/api/write", auth=auth, headers={"Origin": ORIGIN})
    assert response.status_code == 200 and events.count("write") == 1
    assert "no-store" in response.headers["cache-control"]
    assert INTERNAL not in response.text
    assert client.get("/backend/api/read", auth=auth, headers={"Host": "other"}).status_code == 403


def test_spa_refresh_and_static_traversal(web):
    client, _ = web
    auth = ("author", PASSWORD)
    response = client.get("/projects/123", auth=auth, headers={"Accept": "text/html"})
    assert response.status_code == 200 and "app" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    for path in ("/.env", "/%2e%2e/.env", "/data/credentials/key", "/src/novel_writer/main.py"):
        response = client.get(path, auth=auth)
        assert response.status_code == 404 and "private-outside-web" not in response.text


def test_configuration_uses_new_secrets_and_refuses_overwrite(tmp_path):
    output = tmp_path / "local"
    args = dict(origin="http://localhost:8080", port=8080, bind="127.0.0.1",
                stack="isolation-test", database="novel_writer_test")
    create_configuration(output, **args)
    secrets = {p.name: p.read_bytes() for p in (output / "secrets").iterdir()}
    assert len(set(secrets.values())) == 2 and all(len(value) > 32 for value in secrets.values())
    configuration = (output / "deployment.env").read_bytes()
    assert not any(value.strip() in configuration for value in secrets.values())
    with pytest.raises(ValueError, match="拒绝覆盖"):
        create_configuration(output, **args)
    assert secrets == {p.name: p.read_bytes() for p in (output / "secrets").iterdir()}


@pytest.mark.parametrize("origin", ["http://localhost:8080/path", "http://user:pw@host", "https://x#y"])
def test_bad_origins_are_rejected_before_generating_secrets(tmp_path, origin):
    with pytest.raises(ValueError):
        create_configuration(tmp_path / "local", origin=origin, port=8080, bind="127.0.0.1",
                             stack="test", database="novel_writer_test")
    assert not (tmp_path / "local").exists()


def fixture_source(tmp_path):
    root = tmp_path / "repository"
    for name in EXACT_FILES:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture build input\n")
    for name in ("src/novel_writer/app.py", "frontend/src/App.tsx", "migrations/env.py",
                 "configs/genre-quality-cards/narrative/card.md"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("public fixture\n")
    return root


def test_build_context_excludes_private_data_and_produces_no_archive(tmp_path):
    root = fixture_source(tmp_path)
    excluded = [".env", "data/content/novel.json", "data/credentials/key", "logs/request.log",
                ".runtime/local-token", "backups/db.dump", "private/notes.md",
                "deploy/local/secrets/web_password", "frontend/.env.production",
                "frontend/src/private.test.ts", "src/novel_writer/notes.txt"]
    canary = "PRIVATE-PACKAGING-CANARY-DO-NOT-INCLUDE"
    for name in excluded:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(canary)
    result = prepare_context(root, tmp_path / "output")
    context = Path(result["context"])
    assert list((tmp_path / "output").iterdir()) == [context]
    files = {path.relative_to(context).as_posix(): path for path in context.rglob("*")
             if path.is_file()}
    assert not set(excluded).intersection(files)
    assert all(canary.encode() not in path.read_bytes() for path in files.values())
    manifest = json.loads((context / "SOURCE-MANIFEST.json").read_text(encoding="utf-8"))
    for name, sha in manifest["files"].items():
        assert hashlib.sha256(files[name].read_bytes()).hexdigest() == sha


def test_hardcoded_secret_in_allowed_program_file_blocks_build_context(tmp_path):
    root = fixture_source(tmp_path)
    (root / "src/novel_writer/app.py").write_text("key='sk-" + "x" * 30 + "'")
    with pytest.raises(ValueError, match="疑似密钥"):
        source_files(root)
