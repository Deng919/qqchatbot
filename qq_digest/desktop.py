"""Windows desktop window for the existing local QQ Digest application."""

from __future__ import annotations

import json
import hashlib
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Callable

import uvicorn

from .archive import Archive
from .candidates import CandidateService
from .config import load_config
from .desktop_settings import DesktopBridge
from .knowledge import KnowledgeWriter
from .web.app import create_app


DESKTOP_BACKEND_ID = "2026-09-30-failure-center"


def resolve_config_path(install_dir: Path) -> Path:
    """Resolve the real data configuration next to the EXE or source checkout."""
    install_dir = Path(install_dir).resolve()
    launcher = install_dir / "launcher.json"
    if launcher.is_file():
        configured = json.loads(launcher.read_text(encoding="utf-8"))["config_path"]
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = install_dir / path
    else:
        path = install_dir / "config" / "config.yaml"
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到 QQ Digest 配置文件：{path}")
    return path


def service_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "healthz", timeout=1.5) as response:
            return json.load(response) == {"status": "ok"}
    except (OSError, ValueError, urllib.error.URLError):
        return False


def config_signature(config_path: Path) -> str:
    path = Path(config_path).resolve()
    return hashlib.sha256(str(path).casefold().encode("utf-8") + b"\0" + path.read_bytes()).hexdigest()


def matching_service(url: str, config_path: Path) -> bool:
    if not service_ready(url):
        return False
    try:
        with urllib.request.urlopen(url + "desktop-info", timeout=1.5) as response:
            info = json.load(response)
            return (info.get("config_signature") == config_signature(config_path)
                    and info.get("settings_api_version") == 1
                    and info.get("backend_id") == DESKTOP_BACKEND_ID)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class DesktopService:
    url: str
    server: uvicorn.Server | None = None
    thread: Thread | None = None

    @property
    def owned(self) -> bool:
        return self.server is not None

    @classmethod
    def connect(
        cls, url: str, *, probe: Callable[[str], bool] = service_ready,
        make_server: Callable[[], uvicorn.Server], timeout: float = 20,
    ) -> DesktopService:
        if probe(url):
            return cls(url=url)
        server = make_server()
        thread = Thread(target=server.run, name="qq-digest-desktop-server", daemon=True)
        thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if probe(url):
                return cls(url=url, server=server, thread=thread)
            if not thread.is_alive():
                raise RuntimeError("本地服务启动失败；请检查端口和配置")
            time.sleep(0.1)
        server.should_exit = True
        thread.join(timeout=3)
        raise TimeoutError("本地服务启动超时")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
            if self.thread is not None:
                self.thread.join(timeout=5)


def make_server(config_path: Path, *, port: int | None = None,
                bridge: DesktopBridge | None = None) -> uvicorn.Server:
    config = load_config(config_path)
    archive = Archive.open(config.archive_path)
    archive.seed_groups(config.groups)
    web_app = create_app(
        archive=archive,
        candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(config.resolve_knowledge_paths()),
        password_hash=config.security.web_password_hash,
        session_secret=config.resolve_session_secret(),
        session_hours=config.security.session_hours,
        config=config,
    )
    web_app.state.desktop_config_signature = config_signature(config_path)
    web_app.state.desktop_settings_api_version = 1
    web_app.state.desktop_backend_id = DESKTOP_BACKEND_ID
    web_app.state.desktop_bridge = bridge
    return uvicorn.Server(uvicorn.Config(
        web_app, host="127.0.0.1", port=port or config.web.port,
        log_level="warning", access_log=False,
    ))


def show_window(runtime: DesktopService, *, gui, storage_path: Path,
                bridge: DesktopBridge | None = None) -> None:
    storage_path.mkdir(parents=True, exist_ok=True)
    gui.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    backup_stop = Event()
    backup_thread = None
    try:
        window = gui.create_window(
            "QQ Digest", runtime.url, width=1280, height=820,
            min_size=(820, 600), background_color="#f6f7f4", text_select=True,
        )
        if bridge is not None:
            bridge._window = window
            if hasattr(bridge, "run_backup_schedule"):
                backup_thread = Thread(target=bridge.run_backup_schedule,
                                       args=(backup_stop,), name="qq-digest-backup", daemon=True)
                backup_thread.start()
        gui.start(private_mode=False, storage_path=str(storage_path))
    finally:
        backup_stop.set()
        if backup_thread is not None:
            backup_thread.join(timeout=5)
        runtime.stop()


def main() -> int:
    try:
        import webview

        install_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1]
        )
        config_path = resolve_config_path(install_dir)
        config = load_config(config_path)
        bridge = DesktopBridge(install_dir, config_path, Path(sys.executable), gui=webview)
        preferred_url = f"http://127.0.0.1:{config.web.port}/"
        if matching_service(preferred_url, config_path):
            runtime = DesktopService(url=preferred_url)
        else:
            port = available_port() if service_ready(preferred_url) else config.web.port
            runtime = DesktopService.connect(
                f"http://127.0.0.1:{port}/",
                make_server=lambda: make_server(config_path, port=port, bridge=bridge),
            )
        show_window(runtime, gui=webview, bridge=bridge,
                    storage_path=Path(r"D:\Cache\QQDigestDesktop\WebView2"))
        return 0
    except Exception as exc:
        message = f"QQ Digest 桌面版启动失败：\n{exc}"
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "QQ Digest", 0x10)
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
