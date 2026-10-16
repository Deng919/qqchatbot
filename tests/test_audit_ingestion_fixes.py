"""Synthetic regressions for notification recovery, SQLite URIs and model limits."""

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from qq_digest.ai.client import AIClient, AIError
from qq_digest.ai_usage import UsageLimitError, UsageService, normalize_rates
from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
from qq_digest.collector.ntqq import NTQQCollector
from qq_digest.config import AIConfig, AIConnectionConfig
from qq_digest.desktop_settings import export_reports, migrate_storage
from qq_digest.notify.notifier import SendResult
from tests.test_backup_restore import _data
from tests.test_ntqq_collector import ntqq_dbs, ntqq_fts_dbs
from tests.test_pipeline import SuccessfulNotifier, make_pipeline
from tests.test_web import web_client


@pytest.fixture
def notification_pipeline(tmp_path):
    now = datetime(2026, 10, 17, 22, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline, archive = make_pipeline(tmp_path, now)
    pipeline.notifier = SuccessfulNotifier()
    yield pipeline, archive, now
    archive.connection.close()


def test_reused_report_recovers_enqueue_failure_without_another_ai_call(
    notification_pipeline, monkeypatch
):
    pipeline, archive, now = notification_pipeline
    enqueue = archive.enqueue_notification

    def unavailable(**kwargs):
        raise sqlite3.OperationalError("synthetic transient enqueue failure")

    monkeypatch.setattr(archive, "enqueue_notification", unavailable)
    first = pipeline.run_daily(now)
    assert first.status == "partial_success"
    assert archive.report_for(123, now.date().isoformat()) is not None
    assert pipeline.run_daily(now).status == "partial_success"
    assert archive.connection.execute("SELECT count(*) FROM send_log").fetchone()[0] == 0

    monkeypatch.setattr(archive, "enqueue_notification", enqueue)
    assert pipeline.run_daily(now).status == "success"
    assert len(pipeline.notifier.calls) == 1
    assert archive.connection.execute("SELECT status FROM send_log").fetchone()[0] == "success"
    assert len(pipeline.summarizer.ai.calls) == 1
    assert pipeline.run_daily(now).status == "success"
    assert len(pipeline.notifier.calls) == 1
    assert [row[0] for row in archive.connection.execute("SELECT status FROM jobs ORDER BY job_id")] == [
        "partial_success", "partial_success", "success", "success"
    ]


def test_reused_report_completes_partial_recipient_enqueue(notification_pipeline, monkeypatch):
    pipeline, archive, now = notification_pipeline
    pipeline.notifier.config.allowed_openids = ["synthetic-one", "synthetic-two", "synthetic-three"]
    enqueue = archive.enqueue_notification

    def partial(**kwargs):
        if kwargs["recipient"] == "synthetic-two":
            raise sqlite3.OperationalError("synthetic second recipient failure")
        return enqueue(**kwargs)

    monkeypatch.setattr(archive, "enqueue_notification", partial)
    assert pipeline.run_daily(now).status == "partial_success"
    assert archive.connection.execute("SELECT count(*) FROM send_log").fetchone()[0] == 1
    monkeypatch.setattr(archive, "enqueue_notification", enqueue)
    assert pipeline.run_daily(now).status == "success"
    rows = archive.connection.execute("SELECT recipient,status FROM send_log ORDER BY send_id").fetchall()
    assert [tuple(row) for row in rows] == [(recipient, "success") for recipient in pipeline.notifier.config.allowed_openids]
    assert len(pipeline.notifier.calls) == 3
    assert len(pipeline.summarizer.ai.calls) == 1


def test_notifications_disabled_does_not_fill_reused_report_intents(notification_pipeline):
    pipeline, archive, now = notification_pipeline
    for _ in range(2):
        assert pipeline._run_without_job_tracking(now, send_notifications=False).status == "success"
    assert archive.connection.execute("SELECT count(*) FROM send_log").fetchone()[0] == 0
    assert pipeline.notifier.calls == []
    assert len(pipeline.summarizer.ai.calls) == 1
    pipeline.run_daily(now)
    assert len(pipeline.notifier.calls) == 1


@pytest.mark.parametrize("status", ["failed", "pending_send", "sending", "success"])
def test_reused_report_preserves_existing_notification_state(notification_pipeline, status):
    pipeline, archive, now = notification_pipeline
    pipeline._run_without_job_tracking(now, send_notifications=False)
    report = archive.report_for(123, now.date().isoformat())
    future = datetime.now(timezone.utc) + timedelta(days=1)
    send_id = archive.enqueue_notification(
        report_id=report["report_id"], channel="qq_bot_private", recipient="openid-1",
        payload={"group_name": "synthetic preserved payload"}, now=future,
    )
    with archive.transaction():
        archive.connection.execute(
            "UPDATE send_log SET status=?,retry_count=3,error='synthetic previous failure' WHERE send_id=?",
            (status, send_id),
        )
    before = dict(archive.connection.execute("SELECT * FROM send_log").fetchone())
    pipeline.run_daily(now)
    after = [dict(row) for row in archive.connection.execute("SELECT * FROM send_log")]
    assert after == [before]
    assert pipeline.notifier.calls == []
    assert len(pipeline.summarizer.ai.calls) == 1


def test_report_reuse_keeps_terminal_notification_retry_budget(notification_pipeline, monkeypatch):
    pipeline, archive, now = notification_pipeline
    pipeline.notifier.config.max_retries = 1
    calls = []

    def reject(**kwargs):
        calls.append(kwargs)
        return SendResult(False, "synthetic permanent rejection")

    monkeypatch.setattr(pipeline.notifier, "send_report_notification", reject)
    pipeline.run_daily(now)
    before = dict(archive.connection.execute("SELECT * FROM send_log").fetchone())
    assert before["status"] == "failed" and before["retry_count"] == 1
    pipeline.run_daily(now)
    assert [dict(row) for row in archive.connection.execute("SELECT * FROM send_log")] == [before]
    assert len(calls) == 1
    assert len(pipeline.summarizer.ai.calls) == 1


@pytest.mark.parametrize("name", ["source#literal", "source%23literal", "source space"])
@pytest.mark.parametrize("searchable", [False, True])
def test_collector_reads_exact_special_character_paths(ntqq_fts_dbs, tmp_path, name, searchable):
    target = tmp_path / name
    shutil.copytree(ntqq_fts_dbs, target)
    if not searchable:
        (target / "group_msg_fts.db").unlink()
    collector = NTQQCollector(target)
    assert {group["group_id"] for group in collector.discover_groups()} == {100, 200}
    start = datetime(2025, 10, 7, tzinfo=ZoneInfo("Asia/Shanghai"))
    messages = list(collector.collect(100, start, start + timedelta(days=1)))
    assert len(messages) == 6
    assert any(message.text == ("普通正文" if searchable else "消息0") for message in messages)
    with collector._open_msg_db() as db:
        assert db.execute("SELECT count(*) FROM group_msg_table").fetchone()[0] == 9
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("CREATE TABLE forbidden_write (id INTEGER)")
    assert not (tmp_path / "source").exists()
    assert not (tmp_path / "source#literal").is_file()


@pytest.mark.parametrize("method,table,count", [
    ("_open_msg_db", "group_msg_table", 9), ("_open_fts_db", "group_msg_fts", 6),
])
@pytest.mark.parametrize("name", ["source#literal", "source%23literal", "source space"])
def test_collector_message_connections_encode_uri_independently(
    ntqq_fts_dbs, tmp_path, name, method, table, count
):
    target = tmp_path / name
    shutil.copytree(ntqq_fts_dbs, target)
    connection = getattr(NTQQCollector(target), method)()
    try:
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == count
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden_write (id INTEGER)")
    finally:
        connection.close()
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("method", ["_load_group_names", "_open_msg_db", "_open_fts_db"])
def test_missing_collector_source_creates_no_prefix_file(tmp_path, method):
    directory = tmp_path / "missing#literal%23 space"
    directory.mkdir()
    with pytest.raises(FileNotFoundError):
        getattr(NTQQCollector(directory), method)()
    assert list(directory.iterdir()) == []
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("name", ["data#literal", "data%23literal", "data space"])
def test_backup_special_paths_preserves_actual_data_and_restores(tmp_path, name):
    original, config = _data(tmp_path, name)
    _, current_config = _data(tmp_path, "current")
    cache = tmp_path / "cache"
    backups = tmp_path / "backups"
    backup = Path(backup_data(config, backups, temp_root=cache)["path"])
    preview = inspect_backup(backup, temp_root=cache)
    assert preview["messages"] == 1 and preview["reports"] == 1
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(current_config)}), encoding="utf-8")
    destination = tmp_path / "restored#literal%23 space"
    restore_backup(backup, destination, current_config, install, backup_root=backups,
                   temp_root=cache, expected_sha256=preview["sha256"])
    with sqlite3.connect(destination / "archive" / "archive.sqlite") as db:
        assert db.execute("SELECT msg_id,text FROM messages").fetchall() == [("m1", "hello")]
        assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    assert not (tmp_path / "data").exists()
    assert original.is_dir()


@pytest.mark.parametrize("name", ["cache#literal", "cache%23literal", "cache space"])
def test_backup_preview_reads_exact_special_character_cache_path(tmp_path, name):
    _, config = _data(tmp_path, "source")
    backup = Path(backup_data(config, tmp_path / "backups", temp_root=tmp_path / "ordinary-cache")["path"])
    preview = inspect_backup(backup, temp_root=tmp_path / name)
    assert preview["messages"] == 1 and preview["reports"] == 1
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("name", ["data#literal", "data%23literal", "data space"])
def test_export_uses_exact_special_character_archive(tmp_path, name):
    source, config = _data(tmp_path, name)
    export = export_reports(source / "archive" / "archive.sqlite", tmp_path / "exports")
    assert export["reports"] == 1 and export["files"] == 2 and export["missing"] == 0
    assert (Path(export["path"]) / "daily-1.md").read_text(encoding="utf-8") == "# 摘要"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("name", ["data#literal", "data%23literal", "data space"])
def test_migration_uses_exact_special_character_archive(tmp_path, name):
    source, config = _data(tmp_path, name)
    install = tmp_path / "install"
    install.mkdir()
    (install / "launcher.json").write_text(json.dumps({"config_path": str(config)}), encoding="utf-8")
    destination = tmp_path / "migrated#literal%23 space"
    migrate_storage(config, install, destination)
    with sqlite3.connect(destination / "archive" / "archive.sqlite") as db:
        assert db.execute("SELECT msg_id,text FROM messages").fetchall() == [("m1", "hello")]
        assert db.execute("SELECT markdown_path FROM reports").fetchone()[0] == str(destination / "reports" / "daily.md")
    assert not (tmp_path / "data").exists()


def _rate(model):
    return dict(provider="https://api.example.com", model=model,
                input_hit="1.250000", input_miss="1.250000", output="2.500000")


@pytest.mark.parametrize("length", [100, 101, 200])
def test_accepted_model_lengths_reach_http_and_account_usage(tmp_path, length):
    model = "m" * length
    profile = AIConnectionConfig(id="synthetic", name="Synthetic", base_url="https://api.example.com", model=model)
    AIConfig(base_url=profile.base_url, model=model, api_key_env="SYNTHETIC")
    usage = UsageService(tmp_path / "usage.sqlite")
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 4}})

    client = AIClient(base_url=profile.base_url, api_key="synthetic", model=profile.model,
                      usage_service=usage, transport=httpx.MockTransport(respond))
    try:
        assert client.chat([]) == {"ok": True}
    finally:
        client.close()
    assert sent[0]["model"] == model
    snapshot = usage.snapshot()
    assert snapshot["calls"] == snapshot["successful"] == 1
    assert snapshot["models"][0]["model"] == model
    assert snapshot["input_tokens"] == 10 and snapshot["output_tokens"] == 4


@pytest.mark.parametrize("length", [100, 101, 200])
def test_rate_and_attempt_model_limits_match_profiles(tmp_path, length):
    model = "m" * length
    usage = UsageService(tmp_path / "usage.sqlite")
    settings = usage.update(1, [_rate(model)], 0)
    assert settings["rates"] == [_rate(model)]
    attempt = usage.reserve("https://api.example.com", model, "summary", 1)
    usage.finish(attempt, "success", {"prompt_tokens": 10, "completion_tokens": 4})
    snapshot = usage.snapshot()
    assert Decimal(snapshot["estimated_cost"]) == Decimal("0.0000225")
    with pytest.raises(UsageLimitError):
        usage.reserve("https://api.example.com", model, "summary", 1)


def test_model_length_201_rejected_at_config_rates_and_attempt_boundaries(tmp_path):
    model = "m" * 201
    with pytest.raises(ValidationError):
        AIConnectionConfig(id="synthetic", name="Synthetic", base_url="https://api.example.com", model=model)
    with pytest.raises(ValidationError):
        AIConfig(base_url="https://api.example.com", model=model, api_key_env="SYNTHETIC")
    with pytest.raises(ValueError):
        normalize_rates([_rate(model)])
    usage = UsageService(tmp_path / "usage.sqlite")
    with pytest.raises(ValueError):
        usage.reserve("https://api.example.com", model, "summary", 1)
    sent = []
    client = AIClient(base_url="https://api.example.com", api_key="synthetic", model=model,
                      usage_service=usage, transport=httpx.MockTransport(lambda request: sent.append(request)))
    try:
        with pytest.raises(AIError):
            client.chat([])
    finally:
        client.close()
    assert sent == [] and usage.snapshot()["calls"] == 0


@pytest.mark.parametrize("model", ["", " ", " padded "])
def test_rate_model_whitespace_validation_preserved(model):
    with pytest.raises(ValueError):
        normalize_rates([_rate(model)])


@pytest.mark.parametrize("length,status", [(100, 200), (101, 200), (200, 200), (201, 422)])
def test_usage_settings_api_model_length_boundaries(web_client, length, status):
    client, _, _ = web_client
    assert client.post("/login", data={"password": "password123"}, follow_redirects=False).status_code == 303
    before = client.get("/api/ai-usage").json()["settings"]
    rate = _rate("m" * length)
    response = client.put("/api/ai-usage/settings", json={
        "monthly_limit": 7, "rates": [rate], "expected_revision": before["revision"],
    })
    assert response.status_code == status, response.text
    saved = client.get("/api/ai-usage").json()["settings"]
    if status == 200:
        assert saved["revision"] == before["revision"] + 1
        assert saved["monthly_limit"] == 7 and saved["rates"] == [rate]
    else:
        assert saved == before
