"""Optional, offline-only CPU embeddings. No credentials, network client or implicit download."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

MODEL_NAME = "Xenova/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
_lock = threading.RLock()
_instance: tuple[str, tuple[Any, Any, Any]] | None = None
_queries: OrderedDict[tuple[str, str], list[float]] = OrderedDict()


def asset_root() -> Path:
    from novel_writer.core.config import get_settings

    return get_settings().knowledge_model_root


def manifest(root: Path | None = None) -> dict[str, Any] | None:
    root = root or asset_root()
    path = root / "manifest.json"
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if (
        data.get("model") != MODEL_NAME
        or data.get("dimension") != 512
        or data.get("pooling") != "cls-l2"
        or data.get("query_prefix") != QUERY_PREFIX
        or data.get("max_length") != 512
    ):
        raise ValueError("本地向量模型清单不兼容")
    return data


def identity(root: Path | None = None) -> str | None:
    data = manifest(root)
    if data is None:
        return None
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _load(root: Path, key: str) -> tuple[Any, Any, Any]:
    global _instance
    if _instance is not None and _instance[0] == key:
        return _instance[1]
    import numpy as np
    import onnxruntime as ort  # type: ignore[import-untyped]
    from tokenizers import Tokenizer  # type: ignore[import-untyped]

    data = manifest(root)
    assert data is not None
    for name in ("model_quantized.onnx", "tokenizer.json"):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != data["files"].get(name):
            raise ValueError("本地向量模型文件校验失败")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    model = ort.InferenceSession(
        str(root / "model_quantized.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
    tokenizer.no_truncation()
    tokenizer.enable_padding()
    _instance = (key, (model, tokenizer, np))
    return _instance[1]


def embed(texts: list[str], *, query: bool = False, root: Path | None = None) -> list[list[float]]:
    root = root or asset_root()
    key = identity(root)
    if key is None:
        raise ValueError("本地向量模型尚未安装")
    results = []
    with _lock:
        model, tokenizer, np = _load(root, key)
        for text in texts:
            value = QUERY_PREFIX + text if query else text
            cached = _queries.get((key, value)) if query else None
            if cached is not None:
                _queries.move_to_end((key, value))
                results.append(cached)
                continue
            encoded = tokenizer.encode(value)
            if len(encoded.ids) > 512:
                raise ValueError("向量输入超过模型容量，保留文本检索")
            arrays = {
                "input_ids": np.array([encoded.ids], dtype=np.int64),
                "attention_mask": np.array([encoded.attention_mask], dtype=np.int64),
                "token_type_ids": np.array([encoded.type_ids], dtype=np.int64),
            }
            outputs = model.run(None, {i.name: arrays[i.name] for i in model.get_inputs()})
            vector = outputs[0][0, 0, :]  # BGE uses the CLS token, followed by L2 normalization.
            norm = float(np.linalg.norm(vector))
            if vector.shape != (512,) or not np.isfinite(vector).all() or not norm > 0:
                raise ValueError("向量模型输出无效")
            result = (vector / norm).tolist()
            results.append(result)
            if query:
                _queries[(key, value)] = result
                while len(_queries) > 128:
                    _queries.popitem(last=False)
    return results
