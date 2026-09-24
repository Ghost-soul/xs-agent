import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from novel_writer.generation import budget
from novel_writer.generation.content import digest
from novel_writer.services.errors import WorkflowError


def install(root, vocabulary):
    tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(3)
    tokenizer.enable_padding(length=100)
    path = root / "fixture.json"
    tokenizer.save(str(path))
    manifest = {"models": ["fixture"], "source": "offline test",
                "sha256": digest(path.read_text(encoding="utf-8"))}
    (root / "fixture.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_reuses_parser_without_changing_counts_and_revalidates_cached_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "TOKENIZER_ROOT", tmp_path)
    budget._loaded_tokenizer.cache_clear()
    install(tmp_path, {"[UNK]": 0, "word": 1})
    load = Mock(wraps=Tokenizer.from_file)
    monkeypatch.setattr(budget, "Tokenizer", SimpleNamespace(from_file=load))
    config = budget.counting_config("fixture", "fixture")
    for count in [1, 50, 500, 50]:
        assert budget.input_tokens("word " * count, config) == 2048 + count
    assert load.call_count == 1
    (tmp_path / "fixture.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(WorkflowError, match="SHA"):
        budget.input_tokens("word", config)
    assert load.call_count == 1


def test_replaced_asset_or_manifest_cannot_reuse_a_frozen_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "TOKENIZER_ROOT", tmp_path)
    budget._loaded_tokenizer.cache_clear()
    install(tmp_path, {"[UNK]": 0, "word": 1})
    old = budget.counting_config("fixture", "fixture")
    assert budget.input_tokens("word", old) == 2049
    install(tmp_path, {"[UNK]": 0, "word": 1, "new": 2})
    with pytest.raises(WorkflowError, match="已改变"):
        budget.input_tokens("new", old)
    new = budget.counting_config("fixture", "fixture")
    assert budget.input_tokens("word new", new) == 2050
    path = tmp_path / "fixture.manifest.json"
    manifest = json.loads(path.read_text())
    manifest["source"] = "changed source"
    path.write_text(json.dumps(manifest))
    with pytest.raises(WorkflowError, match="已改变"):
        budget.input_tokens("new", new)
