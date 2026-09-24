from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NOVEL_WRITER_",
        extra="ignore",
    )

    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = (
        "postgresql+psycopg://novel_writer:novel_writer@127.0.0.1:54329/novel_writer"
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    local_token: SecretStr = SecretStr("")
    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    max_request_body_bytes: int = Field(default=4 * 1024 * 1024, ge=64 * 1024)
    openai_compatible_base_url: HttpUrl | None = None
    provider_profiles_path: Path = Path("data/provider-profiles.json")
    credential_backend: Literal["auto", "windows", "file"] = "auto"
    credentials_root: Path | None = None
    generation_recovery_poll_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    generation_shutdown_grace_seconds: float = Field(default=300.0, ge=0, le=3600)
    content_store_root: Path = Path("data/content")
    project_workspace_root: Path = Path("data/projects")
    local_task_worker_enabled: bool = True
    local_task_poll_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    system_recovery_drill_certificate: Path = Path("data/system-recovery-drill.json")
    release_seal_receipt: Path = Path("data/release-seal-receipt.json")
    build_commit: str | None = None
    build_alembic_head: str | None = None
    build_lock_sha256: str | None = None
    repository_root: Path = Path(".")
    git_executable: str = "git"


@lru_cache
def get_settings() -> Settings:
    return Settings()
