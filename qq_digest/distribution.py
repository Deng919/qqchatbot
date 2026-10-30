"""Build-time edition policy; source checkouts without a marker are development builds."""
import sys
from pathlib import Path
from urllib.parse import urlsplit

MARKER_PATH = Path(__file__).with_name("distribution.json")


def is_public_distribution() -> bool:
    # Presence fails closed, including a damaged marker. User configuration cannot disable it.
    return bool(getattr(sys, "_qq_digest_public_build", False)) or MARKER_PATH.is_file()


def validate_ai_base_url(base_url: str) -> None:
    from .config import ConfigError
    try:
        parsed = urlsplit(base_url.strip())
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                 and parsed.port != 0 and not parsed.username and not parsed.password
                 and "?" not in base_url and "#" not in base_url
                 and len(base_url) <= 2000
                 and not any(char.isspace() or ord(char) < 32 or char == "\\" for char in base_url))
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError("Cockpit 接口需为 HTTP(S) 地址，不含账号、密码、查询参数或片段")
