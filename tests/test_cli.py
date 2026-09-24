import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from qq_digest.cli import _console_safe, app
from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage


runner = CliRunner()


def test_hash_password():
    result = runner.invoke(app, ["hash-password", "password123"])

    assert result.exit_code == 0
    assert result.stdout.count("$") == 3


def test_pyproject_entrypoint_points_to_main():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run-daily" in result.stdout


def test_run_daily_command_executes_daily_pipeline_command():
    command = next(item for item in app.registered_commands if item.name == "run-daily")

    assert command.callback.__name__ == "run_daily"


def test_console_safe_replaces_characters_unsupported_by_windows_encoding():
    assert _console_safe("学码🌸群", encoding="gbk") == "学码?群"


def test_run_daily_reports_partial_group_failures(tmp_path, monkeypatch):
    config = SimpleNamespace(
        archive_path=tmp_path / "archive.sqlite",
        groups=[],
        ntqq=SimpleNamespace(enabled=False, db_dir=None),
        qq_bot=SimpleNamespace(enabled=False, app_id=""),
        report_dir=tmp_path / "reports",
        ai=SimpleNamespace(max_context_chars=1000),
        summary=SimpleNamespace(timezone="Asia/Shanghai", window_mode="today"),
        resolve_knowledge_paths=lambda: {},
    )
    archive = SimpleNamespace(seed_groups=lambda groups: None)
    daily_result = SimpleNamespace(
        groups_processed=1,
        messages_inserted=2,
        report_paths=[tmp_path / "report.md"],
        candidate_ids=[],
        failed_groups=[
            SimpleNamespace(group_name="失败群", stage="summary")
        ],
    )
    pipeline = SimpleNamespace(run_daily=lambda now: daily_result)
    monkeypatch.setattr("qq_digest.cli.load_config", lambda path: config)
    monkeypatch.setattr("qq_digest.cli.Archive.open", lambda path: archive)
    monkeypatch.setattr("qq_digest.cli.build_ai_client", lambda config: object())
    monkeypatch.setattr("qq_digest.cli.DailyPipeline", lambda **kwargs: pipeline)

    result = runner.invoke(app, ["run-daily"])

    assert result.exit_code == 0
    assert "处理 1 个群，新增 2 条消息" in result.stdout
    assert "失败群(summary)" in result.stderr


def test_summarize_regenerates_legacy_report_then_reuses_adaptive_fingerprint(
    tmp_path, monkeypatch
):
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(timezone)
    report_date = now.date().isoformat()
    archive_path = tmp_path / "archive.sqlite"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    group = GroupConfig(group_id=123, name="测试群")
    archive = Archive.open(archive_path)
    archive.upsert_groups([group])
    archive.ingest(
        [
            NormalizedMessage(
                msg_id="m1",
                group_id=123,
                sender_qq=1,
                timestamp=now - timedelta(minutes=1),
                text="发布了一个需要摘要的重要更新",
                collected_at=now,
            )
        ]
    )
    old_markdown = report_dir / "old.md"
    old_json = report_dir / "old.json"
    old_markdown.write_text("旧摘要", encoding="utf-8")
    old_json.write_text("{}", encoding="utf-8")
    archive.record_report(
        group_id=123,
        report_date=report_date,
        markdown_path=old_markdown,
        json_path=old_json,
        candidate_ids=[],
        effective_template="concise",
        input_fingerprint="",
    )
    archive.connection.close()

    config = SimpleNamespace(
        archive_path=archive_path,
        groups=[group],
        report_dir=report_dir,
        ai=SimpleNamespace(max_context_chars=1000),
        summary=SimpleNamespace(timezone="Asia/Shanghai", window_mode="today"),
        resolve_knowledge_paths=lambda: {
            "resource": tmp_path / "knowledge" / "resources.md",
            "experience": tmp_path / "knowledge" / "experiences.md",
        },
    )

    class FakeAI:
        def __init__(self):
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            return {
                "group_id": 123,
                "overview": "今日发布了一项重要更新。",
                "main_topics": [
                    {"topic": "重要更新", "summary": "群内发布了一项重要更新。"}
                ],
                "conclusions": [],
                "resources": [],
                "tasks": [],
                "open_questions": [],
                "candidates": [],
            }

        def close(self):
            pass

    ai = FakeAI()
    monkeypatch.setattr("qq_digest.cli.load_config", lambda path: config)
    monkeypatch.setattr("qq_digest.cli.build_ai_client", lambda config: ai)

    first = runner.invoke(app, ["summarize", "--config-path", "unused.yaml"])
    second = runner.invoke(app, ["summarize", "--config-path", "unused.yaml"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert len(ai.calls) == 1
    current = Archive.open(archive_path).report_for(123, report_date)
    assert current["effective_template"] == "adaptive"
    assert current["input_fingerprint"]
    payload = json.loads(Path(current["json_path"]).read_text(encoding="utf-8"))
    assert payload["overview"] == "今日发布了一项重要更新。"
