from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

def _config(tmp_path: Path, *, ntqq_enabled: bool = False):
    return SimpleNamespace(
        archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports",
        work_dir=tmp_path / "work",
        data_dir=tmp_path,
        ntqq=SimpleNamespace(
            enabled=ntqq_enabled,
            db_dir=str(tmp_path / "ntqq"),
            qq_number=123,
            timezone="Asia/Shanghai",
        ),
        summary=SimpleNamespace(
            timezone="Asia/Shanghai",
            window_mode="today",
        ),
        ai=SimpleNamespace(max_context_chars=1000),
        qq_bot=SimpleNamespace(
            enabled=False,
            app_id="",
            app_secret_env="QQ_BOT_APP_SECRET",
            allowed_openids=[],
            api_base="https://api.sgroup.qq.com",
            websocket_url="wss://api.sgroup.qq.com/websockets",
            max_retries=3,
            retry_base_seconds=60,
        ),
        resolve_knowledge_paths=lambda: {},
    )


def test_build_collector_uses_fixture_when_ntqq_disabled(tmp_path):
    from qq_digest.application_runner import build_collector
    from qq_digest.collector.fixture import FixtureCollector

    collector = build_collector(_config(tmp_path))

    assert isinstance(collector, FixtureCollector)


def test_build_collector_uses_ntqq_when_enabled(tmp_path, monkeypatch):
    import qq_digest.application_runner as runner

    created = {}

    class FakeCollector:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(runner, "NTQQCollector", FakeCollector)

    collector = runner.build_collector(_config(tmp_path, ntqq_enabled=True))

    assert isinstance(collector, FakeCollector)
    assert created == {
        "db_dir": str(tmp_path / "ntqq"),
        "qq_number": 123,
        "timezone_name": "Asia/Shanghai",
    }


def test_run_daily_task_closes_ai_and_archive_after_pipeline_failure(tmp_path, monkeypatch):
    import qq_digest.application_runner as runner

    config = _config(tmp_path)
    closed = []

    class FakeAI:
        def close(self):
            closed.append("ai")

    class FakeArchive:
        def close(self):
            closed.append("archive")

        def start_job(self, *args, **kwargs):
            return 7

        def finish_job(self, *args, **kwargs):
            closed.append("finish")

    class FailingPipeline:
        def __init__(self, **kwargs):
            pass

        def run_daily(self, *args, **kwargs):
            raise RuntimeError("pipeline failed")

    monkeypatch.setattr(runner.ai_factory, "build_ai_client", lambda config: FakeAI())
    monkeypatch.setattr(runner.Archive, "open", lambda path: FakeArchive())
    monkeypatch.setattr(runner, "DailyPipeline", FailingPipeline)

    result = runner.run_daily_task(
        config,
        scheduled_at=datetime(2026, 10, 11, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        refresh=False,
    )

    assert result == {
        "success": False,
        "status": "failed",
        "error": "pipeline failed",
    }
    assert closed == ["ai", "archive"]


def test_run_daily_task_records_preflight_refresh_failure(tmp_path, monkeypatch):
    import qq_digest.application_runner as runner

    config = _config(tmp_path, ntqq_enabled=True)
    jobs = []

    class FakeArchive:
        def start_job(self, job_type, **kwargs):
            jobs.append(("start", job_type, kwargs))
            return 11

        def finish_job(self, job_id, status, error):
            jobs.append(("finish", job_id, status, error))

        def close(self):
            jobs.append(("close",))

    monkeypatch.setattr(
        runner.database_refresh,
        "refresh_database",
        lambda **kwargs: SimpleNamespace(success=False, message="QQ 未运行"),
    )
    monkeypatch.setattr(runner.ai_factory, "build_ai_client", lambda config: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(runner.Archive, "open", lambda path: FakeArchive())

    result = runner.run_daily_task(
        config,
        scheduled_at=datetime(2026, 10, 11, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        refresh=True,
    )

    assert result == {
        "success": False,
        "status": "failed",
        "error": "数据库刷新失败: QQ 未运行",
    }
    assert jobs == [
        ("start", "daily_digest", {"target_date": "2026-10-11", "retry_of_job_id": None}),
        ("finish", 11, "failed", "数据库刷新失败: QQ 未运行"),
        ("close",),
    ]
