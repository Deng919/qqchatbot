from __future__ import annotations

from ..config import Config
from ..ai_usage import usage_for
from .client import AIClient


def build_ai_client(config: Config, *, purpose="summary") -> AIClient:
    """Use the same Cockpit connection for every purpose, with usage accounting."""
    return AIClient(
        base_url=config.resolve_base_url(), api_key=config.resolve_api_key(),
        model=config.ai.model, json_mode=config.ai.json_mode,
        timeout_seconds=config.ai.timeout_seconds,
        max_retries=config.ai.max_retries,
        retry_base_seconds=config.ai.retry_base_seconds,
        usage_service=usage_for(config), purpose=purpose,
    )
