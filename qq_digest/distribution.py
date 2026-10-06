"""Build-time edition policy; source checkouts without a marker are development builds."""
import sys
from pathlib import Path
from urllib.parse import urlsplit

MARKER_PATH = Path(__file__).with_name("distribution.json")


def is_public_distribution() -> bool:
    # Presence fails closed, including a damaged marker. User configuration cannot disable it.
    return bool(getattr(sys, "_qq_digest_public_build", False)) or MARKER_PATH.is_file()


def validate_public_base_url(base_url: str) -> None:
    from .config import ConfigError
    try:
        parsed = urlsplit(base_url.strip())
        valid = (parsed.scheme == "https" and parsed.hostname == "api.deepseek.com"
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment
                 and parsed.path in ("", "/", "/v1", "/v1/")
                 and "?" not in base_url and "#" not in base_url)
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("公开版仅支持官方 DeepSeek HTTPS 接口 https://api.deepseek.com")


def validate_public_ai(providers, base_url: str) -> None:
    from .config import ConfigError
    if list(providers) != ["compatible"]:
        raise ConfigError("公开版仅支持 DeepSeek API")
    validate_public_base_url(base_url)
