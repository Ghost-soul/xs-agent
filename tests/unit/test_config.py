from pathlib import Path

from novel_writer.core.config import Settings


def test_defaults_bind_only_to_loopback() -> None:
    settings = Settings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.project_workspace_root.as_posix() == "data/projects"
    assert all(
        origin.startswith(("http://127.0.0.1", "http://localhost"))
        for origin in settings.allowed_origins
    )


def test_example_environment_can_be_loaded_without_optional_provider_configuration() -> None:
    root = Path(__file__).resolve().parents[2]

    settings = Settings(_env_file=root / ".env.example")

    assert settings.openai_compatible_base_url is None
