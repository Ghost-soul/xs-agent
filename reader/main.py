"""Cloud reader entrypoint. Secrets exist only at runtime; no local persistence."""

import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from deploy.web import WebGateway
from reader.app import create_app
from reader.database import ReaderDatabase


def read_secret(name: str) -> str:
    runtime_value = os.environ.get(name.upper())
    if runtime_value:
        return runtime_value.strip()
    value = (Path("/run/secrets") / name).read_text(encoding="utf8").strip()
    if not value:
        raise ValueError("缺少运行凭据")
    return value


def main() -> None:
    origin = os.environ["READER_PUBLIC_ORIGIN"].rstrip("/")
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path:
        raise ValueError("阅读地址须包含协议和主机，不能包含路径")
    database = ReaderDatabase(
        {
            "host": os.environ["READER_DATABASE_HOST"],
            "port": int(os.environ.get("READER_DATABASE_PORT", "5432")),
            "dbname": os.environ.get("READER_DATABASE_NAME", "novel_writer"),
            "user": os.environ.get("READER_DATABASE_USER", "novel_reader"),
            "password": read_secret("reader_database_password"),
            "sslmode": os.environ.get("READER_DATABASE_SSLMODE", "prefer"),
        }
    )
    application = WebGateway(
        create_app(database),
        directory=Path("/app/web"),
        username=os.environ.get("READER_WEB_USER", "reader"),
        password=read_secret("reader_web_password"),
        internal_token=secrets.token_urlsafe(48),
        origin=origin,
    )
    uvicorn.run(
        application,
        host="0.0.0.0",
        port=8000,
        workers=1,
        proxy_headers=False,
        access_log=False,
        timeout_graceful_shutdown=15,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"阅读服务启动失败：{type(error).__name__}；请核对运行配置", file=sys.stderr)
        raise SystemExit(1) from None
