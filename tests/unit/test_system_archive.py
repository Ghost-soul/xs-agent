import json
import subprocess
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from novel_writer.services.system_archive import (
    ARCHIVE_FORMAT_V1,
    ARCHIVE_FORMAT_V2,
    PostgresArchiveTarget,
    SystemArchiveBuilder,
    SystemArchiveExclusion,
    _assert_no_secrets,
    _long_path,
    _safe_archive_path,
)
from scripts.system_archive import (
    ALEMBIC_VERSION_QUERY,
    EMPTY_APPLICATION_DATABASE_QUERY,
    _classify_data_assets,
    _inspect,
    _repository_alembic_head,
    _require_archive_revision,
    _restore_test,
)

ROOT = Path(__file__).resolve().parents[2]


def _v2_inputs(tmp_path: Path) -> tuple[str, dict[str, object]]:
    runtime = tmp_path / "runtime"
    audits = runtime / "provider-capability-audits" / "test-provider" / "test-model"
    audits.mkdir(parents=True)
    profiles = runtime / "provider-profiles.json"
    profiles.write_text('{"version":1,"profiles":[]}\n', encoding="utf-8")
    capabilities = runtime / "provider-capabilities.json"
    capabilities.write_text('{"version":1,"capabilities":[]}\n', encoding="utf-8")
    certificate = runtime / "system-recovery-drill.json"
    certificate.write_text('{"status":"passed"}\n', encoding="utf-8")
    (audits / "probe.json").write_text('{"status":"verified"}\n', encoding="utf-8")
    bundle = tmp_path / "repository.bundle"
    subprocess.run(
        ["git", "-C", str(ROOT), "bundle", "create", str(bundle), "--all"],
        check=True,
        capture_output=True,
        text=True,
    )
    git_commit = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return git_commit, {
        "runtime_files": {
            "provider-profiles.json": profiles,
            "provider-capabilities.json": capabilities,
            "system-recovery-drill.json": certificate,
        },
        "runtime_directories": {"provider-capability-audits": audits.parents[1]},
        "repository_bundle": bundle,
        "lock_files": {
            "uv.lock": ROOT / "uv.lock",
            "pyproject.toml": ROOT / "pyproject.toml",
            "frontend-package.json": ROOT / "frontend" / "package.json",
            "pnpm-lock.yaml": ROOT / "frontend" / "pnpm-lock.yaml",
            "python-version": ROOT / ".python-version",
            "node-version": ROOT / ".nvmrc",
        },
        "excluded_assets": (
            SystemArchiveExclusion(
                path="data/temp",
                reason="temporary files are not authoritative",
            ),
        ),
    }


def test_system_archive_round_trip_requires_empty_restore_target(tmp_path, capsys) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"postgres-custom-dump-placeholder")
    content = tmp_path / "content-source"
    (content / "provider").mkdir(parents=True)
    (content / "provider" / "abc.json").write_text("{}\n", encoding="utf-8")
    deep = content / "run-safety" / ("a" * 36) / "manifests"
    deep.mkdir(parents=True)
    deep_name = "00000002-" + "b" * 64 + ".json"
    _long_path(deep / deep_name).write_text("{}\n", encoding="utf-8")
    output = tmp_path / "system-archive.zip"
    git_commit, v2_inputs = _v2_inputs(tmp_path)
    reference_corpora = tmp_path / "reference-corpora"
    reference_corpora.mkdir()
    reference_bytes = "第一章\r\n参考正文\r\n".encode("gb18030")
    (reference_corpora / "reference.txt").write_bytes(reference_bytes)
    v2_inputs["auxiliary_directories"] = {"reference-corpora": reference_corpora}

    created = SystemArchiveBuilder.create(
        output,
        database_dump=dump,
        content_root=content,
        metadata={"git_commit": git_commit, "alembic_head": "20260801_0037"},
        **v2_inputs,
    )
    inspected = SystemArchiveBuilder.inspect(output)
    assert _inspect(Namespace(archive=output, git="git")) == 0
    inspect_output = json.loads(capsys.readouterr().out)
    restored = tmp_path / "restored"
    SystemArchiveBuilder.restore_files(output, restored)

    assert inspected.manifest_sha256 == created.manifest_sha256
    assert inspect_output["manifest_sha256"] == created.manifest_sha256
    assert inspected.format == ARCHIVE_FORMAT_V2
    assert inspected.effective_recovery_scope == "complete_system"
    assert (restored / "database.dump").read_bytes() == dump.read_bytes()
    assert (restored / "content" / "provider" / "abc.json").is_file()
    assert _long_path(
        restored / "content" / "run-safety" / ("a" * 36) / "manifests" / deep_name
    ).is_file()
    assert (
        restored / "auxiliary-audit" / "reference-corpora" / "reference.txt"
    ).read_bytes() == reference_bytes
    with pytest.raises(FileExistsError, match="empty"):
        SystemArchiveBuilder.restore_files(output, restored)


def test_system_archive_detects_tampering_and_rejects_secrets(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"dump")
    content = tmp_path / "content"
    content.mkdir()
    output = tmp_path / "archive.zip"
    with pytest.raises(ValueError, match="secret"):
        SystemArchiveBuilder.create(
            output,
            database_dump=dump,
            content_root=content,
            metadata={"api_key": "never"},
            archive_format=ARCHIVE_FORMAT_V1,
        )

    SystemArchiveBuilder.create(
        output,
        database_dump=dump,
        content_root=content,
        metadata={"git_commit": "b" * 40},
        archive_format=ARCHIVE_FORMAT_V1,
    )
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(output, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest["metadata"]["git_commit"] = "c" * 40
                data = json.dumps(manifest).encode("utf-8")
            target.writestr(name, data)
    with pytest.raises(ValueError, match="manifest hash mismatch"):
        SystemArchiveBuilder.inspect(tampered)


def test_system_archive_v2_requires_complete_runtime_inputs(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"dump")
    content = tmp_path / "content"
    content.mkdir()

    with pytest.raises(ValueError, match="missing runtime files"):
        SystemArchiveBuilder.create(
            tmp_path / "archive.zip",
            database_dump=dump,
            content_root=content,
            metadata={"git_commit": "a" * 40},
        )


def test_system_archive_v1_remains_limited_and_readable(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"dump")
    content = tmp_path / "content"
    content.mkdir()
    output = tmp_path / "legacy.zip"

    SystemArchiveBuilder.create(
        output,
        database_dump=dump,
        content_root=content,
        metadata={"git_commit": "a" * 40},
        archive_format=ARCHIVE_FORMAT_V1,
    )

    inspected = SystemArchiveBuilder.inspect(output)
    assert inspected.format == ARCHIVE_FORMAT_V1
    assert inspected.effective_recovery_scope == "database_and_content_only"


def test_system_archive_v2_rejects_secret_runtime_json(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"dump")
    content = tmp_path / "content"
    content.mkdir()
    git_commit, v2_inputs = _v2_inputs(tmp_path)
    profiles = v2_inputs["runtime_files"]
    assert isinstance(profiles, dict)
    profile_path = profiles["provider-profiles.json"]
    assert isinstance(profile_path, Path)
    profile_path.write_text('{"apiKey":"never"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="secret metadata"):
        SystemArchiveBuilder.create(
            tmp_path / "archive.zip",
            database_dump=dump,
            content_root=content,
            metadata={"git_commit": git_commit},
            **v2_inputs,
        )


@pytest.mark.parametrize(
    "value",
    (
        "C:/escape.txt",
        "C:escape.txt",
        "content/../escape.txt",
        "content/./artifact.json",
        "content//artifact.json",
        "content/NUL.json",
        "content/con.txt",
        "content/name.txt:stream",
        "content/trailing. ",
        "content/trailing.",
    ),
)
def test_system_archive_rejects_windows_unsafe_paths(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe|non-canonical"):
        _safe_archive_path(value)


def test_system_archive_rejects_case_colliding_zip_paths(tmp_path) -> None:
    archive_path = tmp_path / "case-collision.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Content/item.json", "{}")
        archive.writestr("content/ITEM.json", "{}")

    with pytest.raises(ValueError, match="platform-colliding"):
        SystemArchiveBuilder.inspect(archive_path)


def test_system_archive_rejects_secret_like_auxiliary_text(tmp_path) -> None:
    dump = tmp_path / "source.dump"
    dump.write_bytes(b"dump")
    content = tmp_path / "content"
    content.mkdir()
    auxiliary = tmp_path / "calibration"
    auxiliary.mkdir()
    (auxiliary / "review.md").write_text(
        "Authorization: Bearer abcdefghijklmnop\n",
        encoding="utf-8",
    )
    git_commit, v2_inputs = _v2_inputs(tmp_path)
    v2_inputs["auxiliary_directories"] = {"calibration": auxiliary}

    with pytest.raises(ValueError, match="secret-like text"):
        SystemArchiveBuilder.create(
            tmp_path / "archive.zip",
            database_dump=dump,
            content_root=content,
            metadata={"git_commit": git_commit},
            **v2_inputs,
        )


def test_secret_key_normalization_rejects_common_spellings() -> None:
    for key in ("api_key", "api-key", "apiKey", "clientSecret", "access_token"):
        with pytest.raises(ValueError, match="secret metadata"):
            _assert_no_secrets({key: "never"})


def test_system_archive_rejects_duplicate_zip_paths(tmp_path) -> None:
    archive_path = tmp_path / "duplicates.zip"
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(archive_path, "w") as archive,
    ):
        archive.writestr("manifest.json", "{}")
        archive.writestr("manifest.json", "{}")

    with pytest.raises(ValueError, match="duplicate paths"):
        SystemArchiveBuilder.inspect(archive_path)


def test_restore_preflight_requires_no_user_relations() -> None:
    assert "pg_class" in EMPTY_APPLICATION_DATABASE_QUERY
    assert "pg_namespace" in EMPTY_APPLICATION_DATABASE_QUERY
    assert "to_regclass('public.projects')" not in EMPTY_APPLICATION_DATABASE_QUERY


def test_restore_rejects_non_test_database_even_if_environment_name_says_test(monkeypatch):
    monkeypatch.setenv("NOVEL_WRITER_TEST_MISLABELED", "postgresql://fixture@localhost/production")
    monkeypatch.setattr(SystemArchiveBuilder, "inspect", lambda *args: None)
    with pytest.raises(SystemExit, match="name ends with _test"):
        _restore_test(Namespace(
            empty_test_database_acknowledged=True,
            database_url_env="NOVEL_WRITER_TEST_MISLABELED", archive=Path("unused.zip"),
        ))


def test_archive_classifies_reference_corpora_as_author_owned_input(tmp_path) -> None:
    data_root = tmp_path / "data"
    content_root = data_root / "content"
    reference_corpora_root = data_root / "reference-corpora"
    content_root.mkdir(parents=True)
    reference_corpora_root.mkdir()
    (reference_corpora_root / "reference.txt").write_text(
        "reference text\n",
        encoding="utf-8",
    )
    profiles = data_root / "provider-profiles.json"
    capabilities = data_root / "provider-capabilities.json"
    certificate = data_root / "system-recovery-drill.json"
    for path in (profiles, capabilities, certificate):
        path.write_text("{}\n", encoding="utf-8")
    audits = data_root / "provider-capability-audits"
    calibration = data_root / "calibration"
    audits.mkdir()
    calibration.mkdir()
    (data_root / "tokenizers").mkdir()
    (data_root / "credentials").mkdir()
    (data_root / "credentials" / "fixture.key").write_text("private synthetic value")
    (data_root / ".maintenance.lock").write_bytes(b"0")
    arguments = Namespace(
        content_root=content_root,
        provider_profiles=profiles,
        provider_capabilities=capabilities,
        provider_capability_audits=audits,
        recovery_certificate=certificate,
        calibration_root=calibration,
        reference_corpora_root=reference_corpora_root,
        include_calibration=False,
    )

    exclusions = _classify_data_assets(arguments)

    assert all(item.path != "data/reference-corpora" for item in exclusions)
    assert any(item.path == "data/calibration" for item in exclusions)
    assert any(item.path == "data/credentials" for item in exclusions)
    assert any(item.path == "data/.maintenance.lock" for item in exclusions)
    assert all(item.path != "data/tokenizers" for item in exclusions)


def test_archive_rejects_unclassified_real_data_assets(tmp_path) -> None:
    data_root = tmp_path / "data"
    content_root = data_root / "content"
    content_root.mkdir(parents=True)
    (data_root / "unknown-author-data").mkdir()
    arguments = Namespace(
        content_root=content_root,
        provider_profiles=data_root / "provider-profiles.json",
        provider_capabilities=data_root / "provider-capabilities.json",
        provider_capability_audits=data_root / "provider-capability-audits",
        recovery_certificate=data_root / "system-recovery-drill.json",
        calibration_root=data_root / "calibration",
        reference_corpora_root=data_root / "reference-corpora",
        include_calibration=False,
    )

    with pytest.raises(SystemExit, match="unknown-author-data"):
        _classify_data_assets(arguments)


def test_repository_alembic_head_matches_current_migration_chain() -> None:
    assert _repository_alembic_head(ROOT) == "20260921_0047"


def test_archive_revision_requires_repository_request_and_database_match(
    monkeypatch,
) -> None:
    target = PostgresArchiveTarget.from_url(
        "postgresql+psycopg://archive_user:secret@localhost/novel_test"
    )
    monkeypatch.setattr(
        "scripts.system_archive._repository_alembic_head",
        lambda _repository_root: "20260803_0040",
    )
    monkeypatch.setattr(
        "scripts.system_archive._scalar",
        lambda _target, _psql, query: (
            "20260803_0039" if query == ALEMBIC_VERSION_QUERY else "unexpected"
        ),
    )

    with pytest.raises(SystemExit, match="Alembic revision mismatch"):
        _require_archive_revision(
            target,
            "psql",
            ROOT,
            "20260803_0040",
        )


def test_archive_revision_accepts_exact_three_way_match(monkeypatch) -> None:
    target = PostgresArchiveTarget.from_url(
        "postgresql+psycopg://archive_user:secret@localhost/novel_test"
    )
    monkeypatch.setattr(
        "scripts.system_archive._repository_alembic_head",
        lambda _repository_root: "20260803_0040",
    )
    monkeypatch.setattr(
        "scripts.system_archive._scalar",
        lambda _target, _psql, query: (
            "20260803_0040" if query == ALEMBIC_VERSION_QUERY else "unexpected"
        ),
    )

    _require_archive_revision(
        target,
        "psql",
        ROOT,
        "20260803_0040",
    )


def test_postgres_archive_commands_keep_password_out_of_process_arguments(tmp_path) -> None:
    target = PostgresArchiveTarget.from_url(
        "postgresql+asyncpg://archive_user:p%40ss@db.example:5544/novel_test"
    )

    dump_command = target.dump_command("pg_dump", tmp_path / "database.dump")
    restore_command = target.restore_command("pg_restore", tmp_path / "database.dump")
    psql_command = target.psql_command("psql", "SELECT 1")
    all_arguments = [*dump_command, *restore_command, *psql_command]

    assert "p@ss" not in all_arguments
    assert target.client_environment({})["PGPASSWORD"] == "p@ss"
    assert target.redacted_metadata() == {
        "host": "db.example",
        "port": 5544,
        "username": "archive_user",
        "database": "novel_test",
    }


def test_postgres_archive_target_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        PostgresArchiveTarget.from_url("sqlite:///local.db")
