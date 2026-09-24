from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url

from novel_writer.services.provider_profiles import ProviderProfileStore

ARCHIVE_FORMAT_V1 = "novel-writer-system-archive-v1"
ARCHIVE_FORMAT_V2 = "novel-writer-system-archive-v2"
ARCHIVE_FORMAT = ARCHIVE_FORMAT_V2
_FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "secret",
        "token",
        "accesstoken",
    }
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_ARCHIVE_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".txt"})
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:sk|rk)-[a-z0-9_-]{12,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token)"
        r"\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    ),
)
_V2_RUNTIME_FILES = frozenset(
    {
        "provider-profiles.json",
        "provider-capabilities.json",
        "system-recovery-drill.json",
    }
)
_V2_LOCK_FILES = frozenset(
    {
        "uv.lock",
        "pyproject.toml",
        "frontend-package.json",
        "pnpm-lock.yaml",
        "python-version",
        "node-version",
    }
)


class SystemArchiveEntry(BaseModel):
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    bytes: int = Field(ge=0)
    category: str | None = None


class SystemArchiveExclusion(BaseModel):
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    rebuild_command: str | None = None


class SystemArchiveManifest(BaseModel):
    format: str = ARCHIVE_FORMAT
    metadata: dict[str, Any]
    entries: tuple[SystemArchiveEntry, ...]
    recovery_scope: str | None = None
    excluded_assets: tuple[SystemArchiveExclusion, ...] = ()
    manifest_sha256: str = Field(min_length=64, max_length=64)

    @property
    def effective_recovery_scope(self) -> str:
        if self.recovery_scope:
            return self.recovery_scope
        return (
            "database_and_content_only" if self.format == ARCHIVE_FORMAT_V1 else "complete_system"
        )


class PostgresArchiveTarget(BaseModel):
    """Process-safe PostgreSQL CLI arguments with the password kept out of argv."""

    host: str
    port: int = Field(ge=1, le=65535)
    username: str
    database: str
    password: str | None = Field(default=None, exclude=True)

    @classmethod
    def from_url(cls, database_url: str) -> PostgresArchiveTarget:
        url = make_url(database_url)
        if not url.drivername.startswith("postgresql"):
            raise ValueError("system archive requires a PostgreSQL database URL")
        if not url.username or not url.database:
            raise ValueError("PostgreSQL archive URL requires username and database")
        return cls(
            host=url.host or "localhost",
            port=url.port or 5432,
            username=url.username,
            database=url.database,
            password=url.password,
        )

    def client_environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        values = dict(base or os.environ)
        if self.password is not None:
            values["PGPASSWORD"] = self.password
        else:
            values.pop("PGPASSWORD", None)
        return values

    def psql_command(self, executable: str, query: str) -> list[str]:
        return [*self._connection_args(executable), "--no-psqlrc", "-At", "-c", query]

    def dump_command(self, executable: str, output: Path) -> list[str]:
        return [
            *self._connection_args(executable),
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(output),
        ]

    def restore_command(self, executable: str, dump: Path) -> list[str]:
        return [
            *self._connection_args(executable),
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            str(dump),
        ]

    def redacted_metadata(self) -> dict[str, str | int]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "database": self.database,
        }

    def _connection_args(self, executable: str) -> list[str]:
        return [
            executable,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--username",
            self.username,
            "--dbname",
            self.database,
        ]


class SystemArchiveBuilder:
    """Build and verify versioned system archives without including credentials."""

    @staticmethod
    def create(
        output: Path,
        *,
        database_dump: Path,
        content_root: Path,
        metadata: dict[str, Any],
        runtime_files: dict[str, Path] | None = None,
        runtime_directories: dict[str, Path] | None = None,
        repository_bundle: Path | None = None,
        lock_files: dict[str, Path] | None = None,
        auxiliary_directories: dict[str, Path] | None = None,
        excluded_assets: tuple[SystemArchiveExclusion, ...] = (),
        archive_format: str = ARCHIVE_FORMAT,
        git_executable: str = "git",
    ) -> SystemArchiveManifest:
        if archive_format not in {ARCHIVE_FORMAT_V1, ARCHIVE_FORMAT_V2}:
            raise ValueError(f"unsupported system archive format: {archive_format}")
        if output.exists():
            raise FileExistsError(f"system archive already exists: {output}")
        if not database_dump.is_file():
            raise FileNotFoundError(database_dump)
        if not content_root.is_dir():
            raise FileNotFoundError(content_root)
        _assert_no_secrets(metadata)
        runtime_files = dict(runtime_files or {})
        runtime_directories = dict(runtime_directories or {})
        lock_files = dict(lock_files or {})
        auxiliary_directories = dict(auxiliary_directories or {})
        if archive_format == ARCHIVE_FORMAT_V2:
            _validate_v2_inputs(
                metadata,
                runtime_files,
                runtime_directories,
                repository_bundle,
                lock_files,
                git_executable,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="novel-writer-archive-") as temporary:
            staging = Path(temporary)
            shutil.copy2(database_dump, staging / "database.dump")
            _copy_tree(content_root, staging / "content")
            if archive_format == ARCHIVE_FORMAT_V2:
                for name, source in runtime_files.items():
                    _copy_file(source, staging / "runtime-config" / _safe_asset_name(name))
                for name, source in runtime_directories.items():
                    _copy_tree(source, staging / "runtime-config" / _safe_asset_name(name))
                assert repository_bundle is not None
                _copy_file(repository_bundle, staging / "source" / "repository.bundle")
                for name, source in lock_files.items():
                    _copy_file(source, staging / "source" / "locks" / _safe_asset_name(name))
                for name, source in auxiliary_directories.items():
                    _copy_tree(source, staging / "auxiliary-audit" / _safe_asset_name(name))
                _validate_staged_runtime(staging / "runtime-config")
                _validate_staged_auxiliary(staging / "auxiliary-audit")
            (staging / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            entries = tuple(_entries(staging))
            manifest_payload = {
                "format": archive_format,
                "metadata": metadata,
                "entries": [item.model_dump(mode="json") for item in entries],
                "recovery_scope": (
                    "complete_system"
                    if archive_format == ARCHIVE_FORMAT_V2
                    else "database_and_content_only"
                ),
                "excluded_assets": [item.model_dump(mode="json") for item in excluded_assets],
            }
            digest = _canonical_sha(manifest_payload)
            manifest = SystemArchiveManifest(
                **manifest_payload,
                manifest_sha256=digest,
            )
            (staging / "manifest.json").write_text(
                manifest.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_output = output.with_name(f".{output.name}.{os.getpid()}.tmp")
            try:
                with zipfile.ZipFile(
                    temporary_output, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
                        archive.write(path, path.relative_to(staging).as_posix())
                SystemArchiveBuilder.inspect(
                    temporary_output,
                    git_executable=git_executable,
                )
                os.link(temporary_output, output)
            finally:
                temporary_output.unlink(missing_ok=True)
        return manifest

    @staticmethod
    def inspect(
        archive_path: Path,
        *,
        git_executable: str = "git",
    ) -> SystemArchiveManifest:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("system archive contains duplicate paths")
            safe_paths = [_safe_archive_path(name) for name in names]
            normalized_names = [_archive_path_identity(path) for path in safe_paths]
            if len(normalized_names) != len(set(normalized_names)):
                raise ValueError("system archive contains platform-colliding paths")
            if "manifest.json" not in names or "database.dump" not in names:
                raise ValueError("system archive is missing its manifest or database dump")
            raw_manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(raw_manifest, dict):
                raise ValueError("system archive manifest must be an object")
            payload = dict(raw_manifest)
            saved_manifest_sha = payload.pop("manifest_sha256", None)
            manifest = SystemArchiveManifest.model_validate(raw_manifest)
            if manifest.format not in {ARCHIVE_FORMAT_V1, ARCHIVE_FORMAT_V2}:
                raise ValueError(f"unsupported system archive format: {manifest.format}")
            if saved_manifest_sha != _canonical_sha(payload):
                raise ValueError("system archive manifest hash mismatch")
            expected = {item.path: item for item in manifest.entries}
            if len(expected) != len(manifest.entries):
                raise ValueError("system archive manifest contains duplicate paths")
            actual = {name for name in names if name != "manifest.json"}
            if actual != set(expected):
                raise ValueError("system archive file set does not match its manifest")
            for name, entry in expected.items():
                data = archive.read(name)
                if len(data) != entry.bytes or hashlib.sha256(data).hexdigest() != entry.sha256:
                    raise ValueError(f"system archive entry hash mismatch: {name}")
            _assert_no_secrets(manifest.metadata)
            if manifest.format == ARCHIVE_FORMAT_V2:
                _inspect_v2_archive(archive, manifest, git_executable)
            return manifest

    @staticmethod
    def restore_files(
        archive_path: Path,
        target: Path,
        *,
        git_executable: str = "git",
    ) -> SystemArchiveManifest:
        manifest = SystemArchiveBuilder.inspect(
            archive_path,
            git_executable=git_executable,
        )
        if target.exists() and any(target.iterdir()):
            raise FileExistsError("system archive restore target must be empty")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="novel-writer-restore-", dir=target.parent
        ) as temporary:
            staging = Path(temporary) / "payload"
            staging.mkdir()
            with zipfile.ZipFile(archive_path, "r") as archive:
                for name in archive.namelist():
                    safe = _safe_archive_path(name)
                    destination = staging.joinpath(*safe.parts)
                    _assert_restore_destination(staging, destination)
                    writable_destination = _long_path(destination)
                    writable_destination.parent.mkdir(parents=True, exist_ok=True)
                    writable_destination.write_bytes(archive.read(name))
            if target.exists():
                target.rmdir()
            staging.replace(target)
        return manifest


def _entries(root: Path) -> list[SystemArchiveEntry]:
    values = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        data = path.read_bytes()
        values.append(
            SystemArchiveEntry(
                path=path.relative_to(root).as_posix(),
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
                category=_entry_category(path.relative_to(root).as_posix()),
            )
        )
    return values


def _safe_archive_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError(f"unsafe system archive path: {value}")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"unsafe system archive path: {value}")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if path.is_absolute() or windows_path.drive or windows_path.root:
        raise ValueError(f"unsafe system archive path: {value}")
    for part in raw_parts:
        if (
            ":" in part
            or part.endswith((".", " "))
            or any(ord(character) < 32 for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        ):
            raise ValueError(f"unsafe system archive path: {value}")
    if path.as_posix() != value:
        raise ValueError(f"non-canonical system archive path: {value}")
    return path


def _safe_asset_name(value: str) -> Path:
    safe = _safe_archive_path(value)
    if not safe.parts or safe.parts[0] in {"manifest.json", "database.dump"}:
        raise ValueError(f"unsafe system archive asset name: {value}")
    return Path(*safe.parts)


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise FileNotFoundError(source)
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"system archive source contains a symbolic link: {path}")
    shutil.copytree(source, destination)


def _validate_v2_inputs(
    metadata: dict[str, Any],
    runtime_files: dict[str, Path],
    runtime_directories: dict[str, Path],
    repository_bundle: Path | None,
    lock_files: dict[str, Path],
    git_executable: str,
) -> None:
    missing_runtime = _V2_RUNTIME_FILES - set(runtime_files)
    if missing_runtime:
        raise ValueError(
            "system archive v2 is missing runtime files: " + ", ".join(sorted(missing_runtime))
        )
    if "provider-capability-audits" not in runtime_directories:
        raise ValueError("system archive v2 is missing provider capability audits")
    missing_locks = _V2_LOCK_FILES - set(lock_files)
    if missing_locks:
        raise ValueError(
            "system archive v2 is missing lock files: " + ", ".join(sorted(missing_locks))
        )
    if repository_bundle is None or not repository_bundle.is_file():
        raise ValueError("system archive v2 requires a repository bundle")
    git_commit = str(metadata.get("git_commit", ""))
    if len(git_commit) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in git_commit.casefold()
    ):
        raise ValueError("system archive v2 requires a valid git commit")
    _verify_repository_bundle(repository_bundle, git_commit, git_executable)


def _validate_staged_runtime(runtime_root: Path) -> None:
    for path in runtime_root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"system archive runtime JSON is invalid: {path.name}") from error
        _assert_no_secrets(payload)
        _assert_no_secret_text(path.read_text(encoding="utf-8"), path)
    ProviderProfileStore(runtime_root / "provider-profiles.json").list_profiles()


def _validate_staged_auxiliary(auxiliary_root: Path) -> None:
    if not auxiliary_root.exists():
        return
    for path in auxiliary_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.casefold() not in _ARCHIVE_TEXT_SUFFIXES:
            raise ValueError(f"unsupported auxiliary archive file type: {path.name}")
        text = _read_auxiliary_text(path)
        _assert_no_secret_text(text, path)
        if path.suffix.casefold() in {".json", ".jsonl"}:
            try:
                values = (
                    [json.loads(line) for line in text.splitlines() if line.strip()]
                    if path.suffix.casefold() == ".jsonl"
                    else [json.loads(text)]
                )
            except json.JSONDecodeError as error:
                raise ValueError(f"auxiliary archive JSON is invalid: {path.name}") from error
            for value in values:
                _assert_no_secrets(value)


def _read_auxiliary_text(path: Path) -> str:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ValueError(f"auxiliary archive file cannot be read: {path.name}") from error
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        "auxiliary archive text encoding is unsupported "
        f"(expected UTF-8, GB18030/GBK, or UTF-16): {path.name}"
    )


def _inspect_v2_archive(
    archive: zipfile.ZipFile,
    manifest: SystemArchiveManifest,
    git_executable: str,
) -> None:
    names = {item.path for item in manifest.entries}
    required = {
        "database.dump",
        "metadata.json",
        "source/repository.bundle",
        *{f"runtime-config/{name}" for name in _V2_RUNTIME_FILES},
        *{f"source/locks/{name}" for name in _V2_LOCK_FILES},
    }
    missing = required - names
    if missing:
        raise ValueError(
            "system archive v2 is missing required entries: " + ", ".join(sorted(missing))
        )
    if not any(name.startswith("runtime-config/provider-capability-audits/") for name in names):
        raise ValueError("system archive v2 has no provider capability audit evidence")
    if manifest.effective_recovery_scope != "complete_system":
        raise ValueError("system archive v2 has an invalid recovery scope")
    with tempfile.TemporaryDirectory(prefix="novel-writer-bundle-inspect-") as temporary:
        bundle = Path(temporary) / "repository.bundle"
        bundle.write_bytes(archive.read("source/repository.bundle"))
        _verify_repository_bundle(
            bundle,
            str(manifest.metadata.get("git_commit", "")),
            git_executable,
        )
        runtime_root = Path(temporary) / "runtime-config"
        for name in archive.namelist():
            if not name.startswith("runtime-config/"):
                continue
            relative = _safe_archive_path(name).relative_to("runtime-config")
            destination = runtime_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))
        _validate_staged_runtime(runtime_root)


def _verify_repository_bundle(bundle: Path, git_commit: str, git_executable: str) -> None:
    completed = subprocess.run(
        [git_executable, "bundle", "list-heads", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError("system archive repository bundle is invalid")
    advertised = {
        line.split(maxsplit=1)[0].casefold()
        for line in completed.stdout.splitlines()
        if line.strip()
    }
    if git_commit.casefold() not in advertised:
        raise ValueError("system archive repository bundle does not contain git_commit")


def _entry_category(path: str) -> str:
    root = PurePosixPath(path).parts[0]
    return {
        "database.dump": "database",
        "content": "immutable_content",
        "runtime-config": "runtime_config",
        "source": "source_and_locks",
        "auxiliary-audit": "auxiliary_audit",
        "metadata.json": "metadata",
    }.get(root, "other")


def _archive_path_identity(path: PurePosixPath) -> str:
    return "/".join(part.casefold() for part in path.parts)


def _assert_restore_destination(root: Path, destination: Path) -> None:
    resolved_root = root.resolve()
    resolved_destination = destination.resolve()
    if not resolved_destination.is_relative_to(resolved_root):
        raise ValueError(f"system archive destination escapes restore root: {destination}")


def _long_path(path: Path) -> Path:
    """Use the Win32 extended path form for deeply nested immutable artifacts."""

    resolved = str(path.resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        return Path(f"\\\\?\\{resolved}")
    return Path(resolved)


def _canonical_sha(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_no_secrets(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _FORBIDDEN_METADATA_KEYS or normalized.endswith("secret"):
                raise ValueError("secret metadata is forbidden: " + ".".join((*path, str(key))))
            _assert_no_secrets(item, (*path, str(key)))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_no_secrets(item, (*path, str(index)))


def _assert_no_secret_text(value: str, source: Path) -> None:
    if any(pattern.search(value) for pattern in _SENSITIVE_TEXT_PATTERNS):
        raise ValueError(f"secret-like text is forbidden in system archive: {source.name}")
