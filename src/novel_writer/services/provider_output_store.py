from __future__ import annotations

from pathlib import Path


def provider_output_path(content_store_root: Path, digest: str) -> Path:
    if len(digest) != 64 or any(item not in "0123456789abcdef" for item in digest):
        raise ValueError("invalid provider output hash")
    return content_store_root / "provider-outputs" / digest[:2] / f"{digest}.json"


