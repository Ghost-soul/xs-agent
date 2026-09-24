"""Create historical output fixtures without keeping the retired production writer."""

import hashlib
from pathlib import Path

from novel_writer.services.provider_output_store import provider_output_path


def store_provider_output(content_root: Path, value: str) -> str:
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path = provider_output_path(content_root, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return digest
