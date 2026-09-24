from pathlib import Path

import pytest

from novel_writer.services.provider_output_store import provider_output_path


@pytest.mark.parametrize("digest", ["../outside", "a" * 63, "a" * 65, "A" * 64, "z" * 64])
def test_historical_output_path_rejects_invalid_digest(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ValueError, match="invalid provider output hash"):
        provider_output_path(tmp_path, digest)


def test_historical_output_path_preserves_address(tmp_path: Path) -> None:
    digest = "ab" * 32
    assert provider_output_path(tmp_path, digest) == (
        tmp_path / "provider-outputs" / "ab" / f"{digest}.json"
    )
