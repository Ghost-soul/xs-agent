"""Preview/apply a verified copy of receipts after changing a database address."""

import argparse
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_writer.core.maintenance import offline_database, storage_lease  # noqa: E402
from novel_writer.core.private_files import atomic_private_write  # noqa: E402
from novel_writer.generation.content import json_text  # noqa: E402
from novel_writer.generation.response_journal import ResponseJournal  # noqa: E402
from novel_writer.generation.response_migration import (  # noqa: E402
    apply_migration,
    preview_migration,
)
from novel_writer.services.errors import WorkflowError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "apply"))
    parser.add_argument("--source-database-url-env", required=True)
    parser.add_argument("--target-database-url-env", required=True)
    parser.add_argument("--content-root", type=Path, required=True)
    parser.add_argument("--source-content-root", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approve-plan-sha256")
    args = parser.parse_args()
    source_url = os.environ.get(args.source_database_url_env, "")
    target_url = os.environ.get(args.target_database_url_env, "")
    if not source_url or not target_url:
        raise WorkflowError("请通过指定环境变量提供源与目标数据库地址")
    if any(
        not make_url(url).drivername.startswith("postgresql") for url in (source_url, target_url)
    ):
        raise WorkflowError("响应迁移只支持 PostgreSQL")
    source_content = args.source_content_root or args.content_root
    source = ResponseJournal(source_content / "generation-response-buffer", source_url)
    target = ResponseJournal(args.content_root / "generation-response-buffer", target_url)
    if args.command == "preview":
        if args.plan.exists():
            raise WorkflowError("预览文件已存在，请使用新文件名，保留原预览")
        engine = create_engine(target_url, connect_args={"connect_timeout": 5})
        try:
            with Session(engine) as session, session.begin():
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                plan = preview_migration(session, source, target)
            atomic_private_write(args.plan, json_text(plan).encode("utf-8"))
            print(json_text(plan))
        finally:
            engine.dispose()
    else:
        if not args.approve_plan_sha256:
            raise WorkflowError("执行迁移需要明确确认预览中的 sha256")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        # Both source and target apps must be offline. A restored single volume
        # uses one lease; separate content roots require both leases.
        roots = {root.resolve().parent: root for root in (source_content, args.content_root)}
        with ExitStack() as stack:
            for parent in sorted(roots):
                stack.enter_context(storage_lease(roots[parent]))
            connection = stack.enter_context(offline_database(target_url, require_idle=False))
            session = stack.enter_context(Session(connection))
            receipt = apply_migration(
                session, source, target, plan, args.approve_plan_sha256,
                args.content_root / "generation-response-migrations",
            )
            print(json_text(receipt))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (WorkflowError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as error:
        # Driver exceptions may contain credentials or connection strings.
        print(f"迁移失败，原缓冲保留：{type(error).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
