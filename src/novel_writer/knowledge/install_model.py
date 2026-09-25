"""Explicit download of pinned public model assets; never sends story data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx

from novel_writer.knowledge.embedding import MODEL_NAME, QUERY_PREFIX

REVISION = "75c43b069aac4d136ba6bc1122f995fedcfd2781"


def install(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for target, remote in (
            ("model_quantized.onnx", "onnx/model_quantized.onnx"),
            ("tokenizer.json", "tokenizer.json"),
        ):
            path = root / target
            temporary = path.with_suffix(path.suffix + ".part")
            with client.stream(
                "GET", f"https://huggingface.co/{MODEL_NAME}/resolve/{REVISION}/{remote}"
            ) as response:
                response.raise_for_status()
                size = 0
                with temporary.open("wb") as stream:
                    for block in response.iter_bytes(1024 * 1024):
                        size += len(block)
                        if size > 150 * 1024 * 1024:
                            raise ValueError("模型文件超过允许的下载大小")
                        stream.write(block)
            hashes[target] = hashlib.sha256(temporary.read_bytes()).hexdigest()
            temporary.replace(path)
    value = {
        "model": MODEL_NAME,
        "revision": REVISION,
        "dimension": 512,
        "pooling": "cls-l2",
        "query_prefix": QUERY_PREFIX,
        "max_length": 512,
        "files": hashes,
    }
    temporary = root / "manifest.json.part"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(root / "manifest.json")
    print(f"本地向量模型已安装：{root.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    install(parser.parse_args().destination)
