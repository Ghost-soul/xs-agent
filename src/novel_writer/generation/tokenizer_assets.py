"""Select installed, source-bound tokenizer data for new previews only."""

from __future__ import annotations

import json

from novel_writer.generation.budget import TOKENIZER_ROOT, counting_config
from novel_writer.generation.schemas import GenerationSpec, RoleModel
from novel_writer.services.errors import WorkflowError


def resolve_tokenizers(spec: GenerationSpec) -> GenerationSpec:
    if spec.workflow != "novel-run-v1" or spec.context_policy not in {
        "bounded-v1",
        "focused-v1",
        "world-bounded-v1",
        "knowledge-rag-v1",
        "role-rag-v2",
        "role-key-v3",
        "chief-focus-v4",
    }:
        return spec
    found: dict[str, str | None] = {}

    def choose(identifier: str | None, model: str) -> str | None:
        if identifier:
            counting_config(identifier, model)
            return identifier
        if model not in found:
            matches = []
            for path in sorted(TOKENIZER_ROOT.glob("*.manifest.json")):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(manifest, dict) or model not in manifest.get("models", []):
                    continue
                name = path.name.removesuffix(".manifest.json")
                counting_config(name, model)
                matches.append(name)
            if len(matches) > 1:
                raise WorkflowError("该模型有多个本地分词配置，请在详细设置指定一个")
            found[model] = matches[0] if matches else None
        return found[model]

    return spec.model_copy(
        update={
            "chief_tokenizer_id": choose(spec.chief_tokenizer_id, spec.chief_model),
            "writer_tokenizer_id": choose(spec.writer_tokenizer_id, spec.writer_model),
            "roles": {
                role: RoleModel.model_validate(
                    {
                        **config.model_dump(),
                        "tokenizer_id": choose(config.tokenizer_id, config.model),
                    }
                )
                for role, config in spec.roles.items()
            },
        }
    )
