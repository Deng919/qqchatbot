import json
from pathlib import Path
from threading import Event

import pytest

from qq_digest.desktop import DesktopService, config_signature, matching_service, resolve_config_path, show_window


def test_desktop_reads_external_configuration_without_moving_data(tmp_path):
    config = tmp_path / "project" / "config" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("web: {}", encoding="utf-8")
    install = tmp_path / "installed"
    install.mkdir()
    (install / "launcher.json").write_text(
        json.dumps({"config_path": str(config)}), encoding="utf-8"
    )

    assert resolve_config_path(install) == config
    with pytest.raises(FileNotFoundError):
        resolve_config_path(tmp_path / "missing")


def test_make_server_binds_desktop_bridge_to_app_coordinator(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from qq_digest.desktop import make_server
    from qq_digest.web.operations import OperationCoordinator
    coordinator = OperationCoordinator()
    application = SimpleNamespace(state=SimpleNamespace(operations=coordinator))
    config = SimpleNamespace(archive_path=tmp_path / "archive.sqlite", groups=[],
                             resolve_knowledge_paths=lambda: tmp_path / "knowledge",
                             security=SimpleNamespace(web_password_hash="unused", session_hours=24),
                             resolve_session_secret=lambda: "unused", web=SimpleNamespace(port=8765))
    bridge = SimpleNamespace()
    monkeypatch.setattr("qq_digest.desktop.load_config", lambda _: config)
    monkeypatch.setattr("qq_digest.desktop.create_app", lambda **kwargs: application)
    monkeypatch.setattr("qq_digest.desktop.config_signature", lambda _: "test")
    monkeypatch.setattr("qq_digest.desktop.uvicorn.Config", lambda app, **kwargs: app)
    monkeypatch.setattr("qq_digest.desktop.uvicorn.Server", lambda config: config)
    make_server(tmp_path / "config.yaml", bridge=bridge)
    assert bridge._operations is coordinator


def test_desktop_does_not_reuse_an_older_backend(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("web: {}", encoding="utf-8")
    monkeypatch.setattr("qq_digest.desktop.service_ready", lambda url: True)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({
                "config_signature": config_signature(config),
                "settings_api_version": 1,
            }).encode("utf-8")

    monkeypatch.setattr("qq_digest.desktop.urllib.request.urlopen", lambda *args, **kwargs: Response())

    assert matching_service("http://127.0.0.1:8765/", config) is False


def test_desktop_reuses_running_service_and_does_not_stop_it():
    runtime = DesktopService.connect(
        "http://127.0.0.1:8765/", probe=lambda url: True,
        make_server=lambda: pytest.fail("must not start a duplicate service"),
    )
    assert runtime.owned is False
    runtime.stop()


def test_desktop_starts_and_stops_its_own_service():
    started, stopped = Event(), Event()

    class FakeServer:
        should_exit = False

        def run(self):
            started.set()
            while not self.should_exit:
                stopped.wait(0.01)

    server = FakeServer()
    runtime = DesktopService.connect(
        "http://127.0.0.1:8765/", probe=lambda url: started.is_set(),
        make_server=lambda: server, timeout=2,
    )
    assert runtime.owned is True
    runtime.stop()
    assert server.should_exit is True


def test_window_closure_always_stops_owned_service(tmp_path):
    class FakeRuntime:
        url = "http://127.0.0.1:8765/"
        stopped = False

        def stop(self):
            self.stopped = True

    class FakeWebview:
        settings = {}
        window = None

        def create_window(self, title, url, **kwargs):
            self.window = (title, url, kwargs)

        def start(self, **kwargs):
            raise RuntimeError("window failed")

    runtime = FakeRuntime()
    gui = FakeWebview()
    with pytest.raises(RuntimeError, match="window failed"):
        show_window(runtime, gui=gui, storage_path=tmp_path)
    assert gui.window[0] == "QQ Digest"
    assert gui.window[1] == runtime.url
    assert runtime.stopped is True


def test_window_binds_desktop_settings_bridge(tmp_path):
    class FakeRuntime:
        url = "http://127.0.0.1:8765/"
        def stop(self):
            pass

    class FakeWebview:
        settings = {}
        def create_window(self, title, url, **kwargs):
            self.options = kwargs
            return "window-handle"
        def start(self, **kwargs):
            pass

    class FakeBridge:
        _window = None

    bridge = FakeBridge()
    gui = FakeWebview()
    show_window(FakeRuntime(), gui=gui, storage_path=tmp_path, bridge=bridge)
    assert "js_api" not in gui.options
    assert bridge._window == "window-handle"


def test_window_runs_backup_schedule_until_closed(tmp_path):
    started = Event()
    stopped = Event()

    class Runtime:
        url = "http://127.0.0.1:8765/"
        def stop(self):
            pass

    class GUI:
        settings = {}
        def create_window(self, *args, **kwargs):
            return "window"
        def start(self, **kwargs):
            assert started.wait(1)

    class Bridge:
        _window = None
        def run_backup_schedule(self, stop_event):
            started.set()
            stop_event.wait()
            stopped.set()

    show_window(Runtime(), gui=GUI(), storage_path=tmp_path, bridge=Bridge())
    assert stopped.wait(1)
