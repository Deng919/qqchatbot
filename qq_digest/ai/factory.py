from __future__ import annotations

from pathlib import Path

from ..config import Config
from .bridge import BridgeAIClient, ChatProvider, FallbackAIClient
from .client import AIClient


def _build_provider(config: Config, name: str) -> ChatProvider:
    if name == "chatgpt_bridge":
        return BridgeAIClient(
            wrapper_path=Path(config.ai.bridge_wrapper_path),
            account_directory=Path(config.ai.bridge_account_directory),
            model=config.ai.bridge_model,
            timeout_seconds=config.ai.bridge_timeout_seconds,
            queue_timeout_seconds=config.ai.bridge_queue_timeout_seconds,
            request_directory=config.work_dir,
        )
    if name == "compatible":
        return AIClient(
            base_url=config.resolve_base_url(),
            api_key=config.resolve_api_key(),
            model=config.ai.model,
            json_mode=config.ai.json_mode,
            timeout_seconds=config.ai.timeout_seconds,
            max_retries=config.ai.max_retries,
            retry_base_seconds=config.ai.retry_base_seconds,
        )
    raise ValueError(f"Unsupported AI provider: {name}")


def build_ai_client(config: Config) -> ChatProvider:
    names = config.ai.provider_priority
    primary = _build_provider(config, names[0])
    if len(names) == 1:
        return primary
    return FallbackAIClient(
        primary=primary,
        fallback_factory=lambda: _build_provider(config, names[1]),
        primary_name=names[0],
        fallback_name=names[1],
    )
