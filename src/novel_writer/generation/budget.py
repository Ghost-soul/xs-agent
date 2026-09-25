from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from tokenizers import Tokenizer  # type: ignore[import-untyped]

from novel_writer.generation.content import digest, json_text
from novel_writer.generation.novel import model_for, role_for, slots_for
from novel_writer.generation.schemas import GenerationSpec
from novel_writer.generation.token_limits import INPUT_TOKEN_LIMIT
from novel_writer.providers.base import ModelRequest
from novel_writer.services.errors import WorkflowError
from novel_writer.services.provider_profiles import ProviderModelOption, ProviderProfile

TOKENIZER_ROOT = Path(__file__).resolve().parents[3] / "data" / "tokenizers"


def counting_config(identifier: str | None, model: str) -> dict[str, Any]:
    if identifier is None:
        return {"method": "utf8-byte-upper-bound", "model": model}
    path = TOKENIZER_ROOT / f"{identifier}.json"
    manifest_path = TOKENIZER_ROOT / f"{identifier}.manifest.json"
    if not path.is_file() or not manifest_path.is_file():
        raise WorkflowError("本地分词文件/来源清单不存在；系统不会自动下载或使用其他模型分词器")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = digest(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WorkflowError("本地分词资产不可读或来源清单无效") from error
    if not isinstance(manifest, dict):
        raise WorkflowError("分词来源清单必须是对象")
    if model not in manifest.get("models", []) or manifest.get("sha256") != actual:
        raise WorkflowError("分词配置模型或文件 SHA 不匹配")
    if not isinstance(manifest.get("source"), str) or not manifest["source"].strip():
        raise WorkflowError("分词配置必须记录模型对应的来源")
    return {
        "method": "local-tokenizer",
        "id": identifier,
        "model": model,
        "sha256": actual,
        "source": manifest["source"],
    }


@lru_cache(maxsize=4)
def _loaded_tokenizer(path: str, verified_sha256: str) -> Tokenizer:
    # The caller revalidates the file and manifest before every use, including cache hits.
    # Bind the cached parser to both location and content, never just the asset name.
    tokenizer = Tokenizer.from_file(path)
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def input_tokens(text: str, config: dict[str, Any]) -> int:
    # 2048 reserves framing/special tokens. JSON wire keys are counted as well,
    # rather than pretending that an unknown gateway exposes an exact tokenizer.
    if config["method"] == "utf8-byte-upper-bound":
        return len(text.encode("utf-8")) + 2048
    current = counting_config(config["id"], config["model"])
    if current != config:
        raise WorkflowError("分词配置已改变，需要创建新的预览")
    try:
        tokenizer = _loaded_tokenizer(
            str((TOKENIZER_ROOT / f"{config['id']}.json").resolve()), current["sha256"]
        )
        return len(tokenizer.encode(text, add_special_tokens=True).ids) + 2048
    except Exception as error:
        raise WorkflowError("该分词资产无法完成本地计数，未发送请求") from error


def option_for(profile: ProviderProfile, model: str) -> ProviderModelOption:
    model_option = next((m for m in profile.models if m.id == model), None)
    if model_option is None:
        raise WorkflowError("模型不在所选供应商配置中")
    if model_option.context_window is None or model_option.max_output_tokens is None:
        raise WorkflowError("请先在模型设置中提供并验证上下文和输出容量")
    if (
        model_option.input_price_cny_per_million is None
        or model_option.output_price_cny_per_million is None
    ):
        raise WorkflowError("请先设置模型价格；本地免费端点也需明确填写 0")
    return model_option


def cost_for(option: ProviderModelOption, inputs: int, outputs: int) -> Decimal:
    if option.input_price_cny_per_million is None or option.output_price_cny_per_million is None:
        raise WorkflowError("模型价格未知")
    return (
        option.input_price_cny_per_million * inputs + option.output_price_cny_per_million * outputs
    ) / Decimal(1_000_000)


def estimate_cost(spec: GenerationSpec, profile: ProviderProfile) -> Decimal:
    return sum(
        (
            cost_for(
                option_for(profile, model_for(spec, a)[0]), spec.input_limit, model_for(spec, a)[1]
            )
            for a in slots_for(spec)
        ),
        Decimal(0),
    )


def request_for(
    spec: GenerationSpec,
    profile: ProviderProfile,
    action: str,
    system: str,
    user: str,
) -> ModelRequest:
    model, output, _ = model_for(spec, action)
    option = option_for(profile, model)
    if output > (option.max_output_tokens or 0):
        raise WorkflowError("本次输出容量超过模型能力，不会静默调整")
    support = option.supports_reasoning_effort
    if support is None:
        support = profile.supports_reasoning_effort
    return ModelRequest(
        model=model,
        system_prompt=system,
        user_prompt=user,
        max_output_tokens=output,
        json_schema_name="genre_generation",
        json_schema={},
        skip_structured_output=True,
        reasoning_effort=(
            "medium"
            if spec.workflow == "novel-run-v1"
            and (
                role_for(action) == "chief"
                or (
                    role_for(action) == "writer"
                    and spec.writing_policy in {"creative-v1", "background-v1", "guided-v1"}
                    and spec.narrative_policy != "plot-led-v3"
                )
                or (
                    role_for(action) == "checker"
                    and spec.feedback_policy not in {"advisory-v1", "logic-v1"}
                )
            )
            else "none"
        )
        if support
        else None,
    )


def validate_capacity(
    text: str,
    request: ModelRequest,
    spec: GenerationSpec,
    profile: ProviderProfile,
    config: dict[str, Any],
) -> int:
    count = input_tokens(text, config)
    option = option_for(profile, request.model)
    limit = min(
        spec.input_limit,
        INPUT_TOKEN_LIMIT,
        (option.context_window or 0) - request.max_output_tokens,
    )
    if count > limit:
        raise WorkflowError(
            f"最终输入计数/上界 {count} 超过允许值 {limit}；"
            "请配置匹配模型的本地分词器、减少可选材料"
            "或重新预览容量。完整题材卡和关键事实不会被截断。"
        )
    return count


def request_preview(request: ModelRequest) -> str:
    # All protocol renderers add fewer framing bytes than this local envelope;
    # the actual serialized HTTP body is independently checked before network I/O.
    return json_text(request.model_dump(mode="json"))
