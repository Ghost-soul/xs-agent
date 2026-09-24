from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_writer.core.maintenance import (  # noqa: E402
    ACTIVE_WORK_QUERY,
    offline_database,
    storage_lease,
)
from novel_writer.services.system_archive import (  # noqa: E402
    PostgresArchiveTarget,
    SystemArchiveBuilder,
    SystemArchiveExclusion,
)

EXECUTING_CALLS_QUERY = ACTIVE_WORK_QUERY
ALEMBIC_VERSION_QUERY = "SELECT version_num FROM alembic_version"
EMPTY_APPLICATION_DATABASE_QUERY = """
SELECT count(*)
FROM pg_class AS object
JOIN pg_namespace AS namespace ON namespace.oid = object.relnamespace
WHERE object.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
  AND namespace.nspname NOT LIKE 'pg_toast%'
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or restore a complete Novel Writer system archive."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    archive = subparsers.add_parser("create")
    archive.add_argument("--output", type=Path, required=True)
    archive.add_argument("--content-root", type=Path, required=True)
    archive.add_argument(
        "--provider-profiles",
        type=Path,
        default=ROOT / "data" / "provider-profiles.json",
    )
    archive.add_argument(
        "--provider-capabilities",
        type=Path,
        default=ROOT / "data" / "provider-capabilities.json",
    )
    archive.add_argument(
        "--provider-capability-audits",
        type=Path,
        default=ROOT / "data" / "provider-capability-audits",
    )
    archive.add_argument(
        "--recovery-certificate",
        type=Path,
        default=ROOT / "data" / "system-recovery-drill.json",
    )
    archive.add_argument("--repository-root", type=Path, default=ROOT)
    archive.add_argument("--calibration-root", type=Path, default=ROOT / "data" / "calibration")
    archive.add_argument("--include-calibration", action="store_true")
    archive.add_argument(
        "--reference-corpora-root",
        type=Path,
        default=ROOT / "data" / "reference-corpora",
    )
    archive.add_argument("--git-commit", required=True)
    archive.add_argument("--git", default="git")
    archive.add_argument("--alembic-head", required=True)
    archive.add_argument("--prompt-manifest-sha256", default=None)
    archive.add_argument("--database-url-env", default="NOVEL_WRITER_ARCHIVE_DATABASE_URL")
    archive.add_argument("--pg-dump", default="pg_dump")
    archive.add_argument("--psql", default="psql")
    archive.add_argument("--worker-disabled-acknowledged", action="store_true")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--git", default="git")

    restore = subparsers.add_parser("restore-test")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--target-files", type=Path, required=True)
    restore.add_argument("--database-url-env", default="NOVEL_WRITER_TEST_DATABASE_URL")
    restore.add_argument("--pg-restore", default="pg_restore")
    restore.add_argument("--psql", default="psql")
    restore.add_argument("--empty-test-database-acknowledged", action="store_true")
    args = parser.parse_args()
    if args.command == "create":
        return _create(args)
    if args.command == "inspect":
        return _inspect(args)
    return _restore_test(args)


def _create(args: argparse.Namespace) -> int:
    if not args.worker_disabled_acknowledged:
        raise SystemExit("refusing archive: confirm the run-to-review worker is disabled")
    target = _target_from_environment(args.database_url_env)
    # Hold both guards through pg_dump AND the final file copy, not only a
    # point-in-time idle check. The live app holds the same storage lease.
    with storage_lease(args.content_root), offline_database(os.environ[args.database_url_env]):
        return _create_offline(args, target)


def _create_offline(args: argparse.Namespace, target: PostgresArchiveTarget) -> int:
    repository_root = args.repository_root.resolve()
    _require_clean_repository(repository_root, args.git, args.git_commit)
    _require_archive_revision(
        target,
        args.psql,
        repository_root,
        args.alembic_head,
    )
    _require_scalar(target, args.psql, EXECUTING_CALLS_QUERY, "0")
    exclusions = _classify_data_assets(args)
    with tempfile.TemporaryDirectory(prefix="novel-writer-system-archive-") as temporary:
        temporary_root = Path(temporary)
        dump = temporary_root / "novel-writer.dump"
        repository_bundle = temporary_root / "repository.bundle"
        _run(target, target.dump_command(args.pg_dump, dump))
        _run_git(
            args.git,
            repository_root,
            ["bundle", "create", str(repository_bundle), "--all"],
        )
        _require_clean_repository(repository_root, args.git, args.git_commit)
        _require_archive_revision(
            target,
            args.psql,
            repository_root,
            args.alembic_head,
        )
        _require_scalar(target, args.psql, EXECUTING_CALLS_QUERY, "0")
        auxiliary_directories: dict[str, Path] = {}
        if args.include_calibration:
            auxiliary_directories["calibration"] = args.calibration_root
        if args.reference_corpora_root.is_dir():
            auxiliary_directories["reference-corpora"] = args.reference_corpora_root
        source_paths = {
            "provider_profiles": str(args.provider_profiles.resolve()),
            "provider_capabilities": str(args.provider_capabilities.resolve()),
            "provider_capability_audits": str(args.provider_capability_audits.resolve()),
            "recovery_certificate": str(args.recovery_certificate.resolve()),
            "frontend_package": "frontend/package.json",
            "frontend_lock": "frontend/pnpm-lock.yaml",
        }
        if args.reference_corpora_root.is_dir():
            source_paths["reference_corpora"] = str(args.reference_corpora_root.resolve())
        manifest = SystemArchiveBuilder.create(
            args.output,
            database_dump=dump,
            content_root=args.content_root,
            runtime_files={
                "provider-profiles.json": args.provider_profiles,
                "provider-capabilities.json": args.provider_capabilities,
                "system-recovery-drill.json": args.recovery_certificate,
            },
            runtime_directories={
                "provider-capability-audits": args.provider_capability_audits,
                **({"tokenizers": args.content_root.resolve().parent / "tokenizers"}
                   if (args.content_root.resolve().parent / "tokenizers").is_dir() else {}),
            },
            repository_bundle=repository_bundle,
            lock_files={
                "uv.lock": repository_root / "uv.lock",
                "pyproject.toml": repository_root / "pyproject.toml",
                "frontend-package.json": repository_root / "frontend" / "package.json",
                "pnpm-lock.yaml": repository_root / "frontend" / "pnpm-lock.yaml",
                "python-version": repository_root / ".python-version",
                "node-version": repository_root / ".nvmrc",
            },
            auxiliary_directories=auxiliary_directories,
            excluded_assets=tuple(exclusions),
            metadata={
                "git_commit": args.git_commit,
                "alembic_head": args.alembic_head,
                "prompt_manifest_sha256": args.prompt_manifest_sha256,
                "database": target.redacted_metadata(),
                "credentials_included": False,
                "worker_disabled_acknowledged": True,
                "consistency": "offline-storage-lease-and-database-share-locks-v1",
                "credential_restore": "reconfigure-separately",
                "source_paths": source_paths,
            },
            git_executable=args.git,
        )
    print(manifest.model_dump_json(indent=2))
    return 0


def _inspect(args: argparse.Namespace) -> int:
    manifest = SystemArchiveBuilder.inspect(
        args.archive,
        git_executable=args.git,
    )
    print(manifest.model_dump_json(indent=2))
    return 0


def _restore_test(args: argparse.Namespace) -> int:
    if not args.empty_test_database_acknowledged:
        raise SystemExit("refusing restore: confirm the target is a disposable empty test DB")
    if not str(args.database_url_env).startswith("NOVEL_WRITER_TEST_"):
        raise SystemExit("restore only accepts a NOVEL_WRITER_TEST_* environment variable")
    target = _target_from_environment(args.database_url_env)
    manifest = SystemArchiveBuilder.inspect(args.archive)
    if not target.database.endswith("_test"):
        raise SystemExit("restore only accepts a database whose name ends with _test")
    _require_scalar(target, args.psql, EMPTY_APPLICATION_DATABASE_QUERY, "0")
    SystemArchiveBuilder.restore_files(args.archive, args.target_files)
    dump = args.target_files / "database.dump"
    _run(target, target.restore_command(args.pg_restore, dump))
    print(
        json.dumps(
            {
                "status": "restored_for_drill",
                "archive_manifest_sha256": manifest.manifest_sha256,
                "archive_format": manifest.format,
                "recovery_scope": manifest.effective_recovery_scope,
                "database": target.redacted_metadata(),
                "files": str(args.target_files.resolve()),
                "provider_credentials_configured": False,
                "release_certificate_written": False,
                "next_step": "run the documented read-only verification matrix",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _target_from_environment(name: str) -> PostgresArchiveTarget:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required database URL environment variable is missing: {name}")
    return PostgresArchiveTarget.from_url(value)


def _require_clean_repository(repository_root: Path, git: str, git_commit: str) -> None:
    head = _run_git(git, repository_root, ["rev-parse", "HEAD"], capture_output=True)
    if head.stdout.strip().casefold() != git_commit.casefold():
        raise SystemExit("refusing archive: git_commit must equal the clean repository HEAD")
    status = _run_git(
        git,
        repository_root,
        ["status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
    )
    if status.stdout.strip():
        raise SystemExit("refusing archive: repository worktree is not clean")


def _classify_data_assets(args: argparse.Namespace) -> list[SystemArchiveExclusion]:
    data_root = args.content_root.resolve().parent
    reference_corpora_root = args.reference_corpora_root.resolve()
    if reference_corpora_root.exists() and not reference_corpora_root.is_dir():
        raise SystemExit("refusing archive: reference corpora root is not a directory")
    classified = {
        args.content_root.resolve(),
        args.provider_profiles.resolve(),
        args.provider_capabilities.resolve(),
        args.provider_capability_audits.resolve(),
        args.recovery_certificate.resolve(),
        args.calibration_root.resolve(),
        reference_corpora_root,
        (data_root / "projects").resolve(),
        (data_root / "lancedb").resolve(),
        (data_root / "temp").resolve(),
        (data_root / "tokenizers").resolve(),
        (data_root / "credentials").resolve(),
        (data_root / ".maintenance.lock").resolve(),
    }
    unknown = [path for path in data_root.iterdir() if path.resolve() not in classified]
    if unknown:
        labels = ", ".join(sorted(path.name for path in unknown))
        raise SystemExit(f"refusing archive: unclassified data assets: {labels}")
    exclusions = [
        SystemArchiveExclusion(
            path="data/credentials",
            reason="private credentials excluded; reconfigure separately on the restored server",
        ),
        SystemArchiveExclusion(
            path="data/.maintenance.lock",
            reason="process lease is recreated locally and must never be restored or deleted live",
        ),
        SystemArchiveExclusion(
            path="data/projects",
            reason="readable project workspaces are derived from PostgreSQL",
            rebuild_command="POST /api/projects/{project_id}/workspace/sync",
        ),
        SystemArchiveExclusion(
            path="data/lancedb",
            reason="retired retrieval indexes are derived data; formal text remains in PostgreSQL",
        ),
        SystemArchiveExclusion(
            path="data/temp",
            reason="temporary runtime files are not authoritative",
        ),
    ]
    if not args.include_calibration:
        exclusions.append(
            SystemArchiveExclusion(
                path="data/calibration",
                reason="author calibration material was not explicitly selected for this archive",
            )
        )
    return exclusions


def _require_archive_revision(
    target: PostgresArchiveTarget,
    psql: str,
    repository_root: Path,
    requested_head: str,
) -> None:
    repository_head = _repository_alembic_head(repository_root)
    database_head = _scalar(target, psql, ALEMBIC_VERSION_QUERY)
    if not (repository_head == requested_head and database_head == requested_head):
        raise SystemExit(
            "refusing archive: Alembic revision mismatch "
            f"(repository={repository_head!r}, requested={requested_head!r}, "
            f"database={database_head!r})"
        )


def _repository_alembic_head(repository_root: Path) -> str:
    configuration_path = repository_root / "alembic.ini"
    migrations_path = repository_root / "migrations"
    if not configuration_path.is_file() or not migrations_path.is_dir():
        raise SystemExit("refusing archive: repository Alembic configuration is incomplete")
    configuration = Config(str(configuration_path))
    configuration.set_main_option("script_location", str(migrations_path))
    configuration.set_main_option("path_separator", "os")
    heads = ScriptDirectory.from_config(configuration).get_heads()
    if len(heads) != 1:
        raise SystemExit("refusing archive: repository must have exactly one Alembic head")
    return heads[0]


def _require_scalar(
    target: PostgresArchiveTarget,
    psql: str,
    query: str,
    expected: str,
) -> None:
    actual = _scalar(target, psql, query)
    if actual != expected:
        raise SystemExit(
            f"PostgreSQL safety preflight failed: expected {expected!r}, got {actual!r}"
        )


def _scalar(target: PostgresArchiveTarget, psql: str, query: str) -> str:
    completed = _run(target, target.psql_command(psql, query), capture_output=True)
    return completed.stdout.strip()


def _run(
    target: PostgresArchiveTarget,
    command: list[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=capture_output,
        text=True,
        env=target.client_environment(),
    )


def _run_git(
    git: str,
    repository_root: Path,
    arguments: list[str],
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git, "-C", str(repository_root), *arguments],
        check=True,
        capture_output=capture_output,
        text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
