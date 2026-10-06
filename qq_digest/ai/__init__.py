from .client import AIClient, AIError
from .factory import build_ai_client

__all__ = [
    "AIClient",
    "AIError",
    "BridgeAIClient",
    "FallbackAIClient",
    "build_ai_client",
]


def __getattr__(name):
    if name in {"BridgeAIClient", "FallbackAIClient"}:
        from ..distribution import is_public_distribution
        if not is_public_distribution():
            from . import bridge
            return getattr(bridge, name)
    raise AttributeError(name)
