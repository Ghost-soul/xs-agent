"""Offline maintenance excludes the live app and holds a write-frozen DB snapshot.

The lock file is persistent: deleting it would permit two different inode locks.
All processes sharing content must use the same content root and local filesystem.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Connection

ACTIVE_WORK_QUERY = """
SELECT
    (SELECT count(*) FROM writing_chunk_calls WHERE status = 'executing') +
    (SELECT count(*) FROM generation_calls WHERE status = 'executing') +
    (SELECT count(*) FROM generation_batches WHERE status IN ('queued', 'running')) +
    (SELECT count(*) FROM local_tasks WHERE status IN ('queued', 'running'))
""".strip()


@contextmanager
def storage_lease(content_root: Path) -> Iterator[None]:
    """Nonblocking, process-death-safe exclusive lease, on Windows and POSIX."""
    path = content_root.resolve().parent / ".maintenance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("维护锁不能使用符号链接")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("内容目录正由后端或维护工具使用；请等待安全停止后再试") from error
        yield
    finally:
        # Closing releases the kernel lock, including on process termination.
        os.close(descriptor)


@contextmanager
def offline_database(database_url: str, *, require_idle: bool = True) -> Iterator[Connection]:
    """Hold through dump AND file capture. No application rows are modified.

The generation owner lock also excludes a backend using another content path.
SHARE table locks block DML while allowing pg_dump's ACCESS SHARE reads.
"""
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            if not connection.scalar(text("SELECT pg_try_advisory_xact_lock(724918320)")):
                raise RuntimeError("数据库仍有生成执行器在线；请安全停止后再维护")
            tables = connection.scalars(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )).all()
            if not tables:
                raise RuntimeError("目标数据库尚未初始化")
            quote = connection.dialect.identifier_preparer.quote
            names = ", ".join(f"public.{quote(name)}" for name in tables)
            connection.execute(text(f"LOCK TABLE {names} IN SHARE MODE"))
            if require_idle and connection.scalar(text(ACTIVE_WORK_QUERY)) != 0:
                raise RuntimeError("仍有执行中调用、排队或运行中阶段／本地任务，拒绝备份")
            yield connection
            # Read-only maintenance should never commit accidental SQL changes.
            connection.rollback()
    finally:
        engine.dispose()
