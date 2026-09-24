"""Container-only entry point; independent of the local desktop backend."""

import os
import secrets
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

from deploy.web import WebGateway
from novel_writer.api.app import create_app
from novel_writer.core.config import Settings, get_settings
from novel_writer.core.maintenance import storage_lease


def read_secret(name: str) -> str:
    value = (Path("/run/secrets") / name).read_text(encoding="utf-8").strip()
    if len(value) < 24:
        raise ValueError("部署凭据缺失或过短，请运行 configure.py")
    return value


def configure() -> Settings:
    database_url = URL.create(
        "postgresql+psycopg",
        username=os.environ.get("NOVEL_WRITER_DATABASE_USER", "novel_writer"),
        password=read_secret("database_password"),
        host=os.environ.get("NOVEL_WRITER_DATABASE_HOST", "database"), port=5432,
        database=os.environ.get("NOVEL_WRITER_DATABASE_NAME", "novel_writer"),
    ).render_as_string(hide_password=False)
    # Runtime-only environment for Alembic. Never a Docker build argument or image ENV.
    os.environ["NOVEL_WRITER_DATABASE_URL"] = database_url
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None, environment="production", host="0.0.0.0", port=8000,
        database_url=database_url, local_token=secrets.token_urlsafe(48),
        allowed_origins=[os.environ["NOVEL_WRITER_PUBLIC_ORIGIN"]],
        content_store_root=Path("/app/data/content"),
        provider_profiles_path=Path("/app/data/provider-profiles.json"),
        credentials_root=Path("/app/data/credentials"), credential_backend="file",
        project_workspace_root=Path("/app/data/projects"), log_dir=Path("/app/logs"),
        repository_root=Path("/app"),
    )
    assert settings.credentials_root is not None
    for path in (
        settings.content_store_root, settings.credentials_root, settings.log_dir,
        settings.provider_profiles_path.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)
        # Probe once on startup under the actual runtime UID, not every health poll.
        probe = path / f".write-probe-{secrets.token_hex(8)}"
        try:
            with probe.open("xb") as stream:
                stream.write(b"ready")
        finally:
            probe.unlink(missing_ok=True)
    return settings


def main() -> None:
    operation = sys.argv[1] if len(sys.argv) > 1 else "serve"
    settings = configure()
    if operation == "migrate":
        with storage_lease(settings.content_store_root):
            command.upgrade(Config("/app/alembic.ini"), "head")
        return
    if operation != "serve":
        raise ValueError("只支持 serve 或显式 migrate 命令")
    application = WebGateway(
        create_app(settings), directory=Path("/app/web"),
        username=os.environ.get("NOVEL_WRITER_WEB_USER", "author"),
        password=read_secret("web_password"),
        internal_token=settings.local_token.get_secret_value(), origin=settings.allowed_origins[0],
    )
    uvicorn.run(
        application, host="0.0.0.0", port=8000, workers=1,
        proxy_headers=False, timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # A driver exception can contain a connection string: keep secrets out of logs.
        print(
            f"容器启动失败：{type(error).__name__}；请核对配置、数据卷权限及迁移状态",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
