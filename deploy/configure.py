"""Generate new server-only credentials. Never reads the workstation's .env or data."""

import argparse
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit


def create_configuration(
    output: Path, *, origin: str, port: int, bind: str, stack: str, database: str,
) -> None:
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
    ):
        raise ValueError("Origin 必须为完整的网站来源，例如 http://localhost:8080，不含路径")
    if bind not in {"127.0.0.1", "::1"} and parsed.scheme != "https":
        raise ValueError("远程发布请配置 HTTPS 来源；通过 SSH 隧道使用时保留默认本机监听")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,50}", stack):
        raise ValueError("Compose 项目名只接受小写字母、数字、下划线和连字符")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database) or not 1 <= port <= 65535:
        raise ValueError("数据库名称或端口无效")
    if output.exists() and any(output.iterdir()):
        raise ValueError("配置目录非空，拒绝覆盖已有密码；更新时沿用原配置")
    secret_path = (output / "secrets").resolve().as_posix()
    if any(char in secret_path for char in "\r\n'"):
        raise ValueError("配置目录路径包含不支持的字符")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_root = output / "secrets"
    secret_root.mkdir(mode=0o700)
    for name in ("database_password", "web_password"):
        descriptor = os.open(secret_root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(36) + "\n")
        # Compose file secrets are bind mounts: host UID differs from the
        # non-root app and postgres UIDs. The 0700 host directory protects them;
        # the individual read-only mount must be readable by both containers.
        if os.name == "posix":
            (secret_root / name).chmod(0o444)
    values = {
        "NOVEL_WRITER_STACK": stack,
        "NOVEL_WRITER_IMAGE_TAG": "2026.09.24",
        "NOVEL_WRITER_PUBLIC_ORIGIN": origin,
        "NOVEL_WRITER_BIND": bind,
        "NOVEL_WRITER_HTTP_PORT": str(port),
        "NOVEL_WRITER_WEB_USER": "author",
        "NOVEL_WRITER_DATABASE_NAME": database,
        "NOVEL_WRITER_SECRET_DIR": secret_path,
    }
    (output / "deployment.env").write_text(
        "".join(f"{key}='{value}'\n" for key, value in values.items()), encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "local")
    parser.add_argument("--origin", default="http://localhost:8080")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--stack", default="novel-writer-server")
    parser.add_argument("--database", default="novel_writer")
    args = parser.parse_args()
    try:
        create_configuration(
            args.output, origin=args.origin, port=args.port, bind=args.bind,
            stack=args.stack, database=args.database,
        )
    except ValueError as error:
        parser.error(str(error))
    print(f"配置已创建：{args.output / 'deployment.env'}")
    print(f"登录用户：author；密码保存在 {args.output / 'secrets' / 'web_password'}")
    print("未读取或复制任何已有 API Key、小说或数据库。请勿分享生成的 local 目录。")


if __name__ == "__main__":
    main()
