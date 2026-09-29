"""Start the local QQ Digest web app and open it in the default browser."""

from __future__ import annotations

import subprocess
import json
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = Path(r"D:\CodexTools\python\Scripts\qq-digest.exe")
CONFIG = ROOT / "config" / "config.yaml"
URL = "http://127.0.0.1:8765/"


def is_ready() -> bool:
    try:
        with urllib.request.urlopen(URL + "healthz", timeout=2) as response:
            return json.load(response).get("status") == "ok"
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    if not EXECUTABLE.is_file():
        print(f"找不到 QQ Digest 程序：{EXECUTABLE}", file=sys.stderr)
        return 1
    if not CONFIG.is_file():
        print(f"找不到配置文件：{CONFIG}", file=sys.stderr)
        return 1

    if not is_ready():
        process = subprocess.Popen(
            [str(EXECUTABLE), "serve", "--config-path", str(CONFIG)],
            cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            time.sleep(0.5)
            if is_ready():
                break
            if process.poll() is not None:
                break
        if not is_ready():
            print("服务未能在 20 秒内启动，请检查 8765 端口和项目配置。", file=sys.stderr)
            return 1

    print(f"QQ Digest 已就绪：{URL}")
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
