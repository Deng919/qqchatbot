"""Read-only product review: failing expectations reproduce defects on disposable data."""
import json
import runpy
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
import pytest
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from qq_digest.candidates import CandidateService
from qq_digest.config import AIConfig, Config, ConfigError, SecurityConfig
from qq_digest.collector.fixture import FixtureCollector
from qq_digest.knowledge import KnowledgeItem, KnowledgeWriter
from qq_digest.manual_summary import ManualSummaryRequest
from qq_digest.models import GroupConfig
from qq_digest.pipeline import DailyPipeline
from qq_digest.summary import build_context_window
from qq_digest.web.app import create_app

helpers = runpy.run_path(str(Path(__file__).with_name("test_manual_summary.py")))
make_service = helpers["make_service"]
add_message = helpers["add_message"]


def setup(tmp_path):
    service, archive, ai = make_service(tmp_path, [GroupConfig(group_id=123, name="Review")])
    add_message(archive, 123, "m1")
    request = ManualSummaryRequest((123,), date(2026, 9, 2), date(2026, 9, 2))
    return service, archive, ai, request


def test_shared_candidate_survives_other_report_regeneration(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    service.run(request)
    original = archive.manual_report_for(
        123, "2026-09-02", "2026-09-02", "adaptive"
    )
    shared_id = json.loads(original["candidate_ids"])[0]
    archive.record_report(
        group_id=123,
        report_date="2026-09-02",
        markdown_path=tmp_path / "daily.md",
        json_path=tmp_path / "daily.json",
        candidate_ids=[shared_id],
    )
    add_message(archive, 123, "new-message", 11)

    original_chat = ai.chat

    def changed_candidate(messages):
        response = original_chat(messages)
        response["candidates"][0]["title"] = "新的资源"
        return response

    ai.chat = changed_candidate
    service.run(request)
    assert archive.connection.execute(
        "SELECT 1 FROM candidates WHERE candidate_id=?", (shared_id,)
    ).fetchone(), "Another report still references a deleted candidate"


def test_daily_regeneration_preserves_current_candidate(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    now = datetime(2026, 9, 2, 22, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline = DailyPipeline(archive=archive, collector=FixtureCollector(), ai_client=ai,
        report_dir=tmp_path / "reports", max_context_chars=1000, timezone_name="Asia/Shanghai")
    pipeline.run_daily(now)
    add_message(archive, 123, "new-message", 11)
    result = pipeline.run_daily(now)
    assert result.status == "success"
    ids = json.loads(archive.report_for(123, "2026-09-02")["candidate_ids"])
    assert all(archive.connection.execute("SELECT 1 FROM candidates WHERE candidate_id=?", (i,)).fetchone() for i in ids), "Daily report points at a deleted candidate"


def test_missing_report_is_recreated(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    first = service.run(request)
    row = archive.manual_report_by_id(first.created_reports[0].report_id)
    path = Path(row["markdown_path"])
    path.unlink()
    second = service.run(request)
    assert path.is_file(), f"Missing Markdown was reused: {len(second.reused_reports)}"


def test_changed_candidate_setting_invalidates_reuse(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="Review", important_candidates=False)])
    service.run(request)
    archive.upsert_groups([GroupConfig(group_id=123, name="Review", important_candidates=True)])
    second = service.run(request)
    assert second.created_reports, "Enabling candidates still reuses a report with no candidates"


def test_knowledge_ids_are_exact_not_prefixes(tmp_path):
    writer = KnowledgeWriter(tmp_path / "knowledge")
    for item_id in ("10", "1"):
        writer.write(KnowledgeItem(item_id=item_id, date="2026-09-02", category="resource",
            source_group="Review", title=f"title-{item_id}"), "resource")
    text = writer.path_for("resource").read_text(encoding="utf-8")
    assert "条目 ID：1" in text.splitlines(), "ID 1 was silently skipped after ID 10"


def test_group_settings_survive_startup_seed(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    yaml_groups = [GroupConfig(group_id=123, name="Review", template="concise")]
    archive.upsert_groups([GroupConfig(group_id=123, name="Review", template="detailed", enabled=False)])
    archive.seed_groups(yaml_groups)  # The non-destructive bootstrap used by serve().
    group = archive.all_groups()[0]
    assert group.template == "detailed" and not group.enabled, "Startup overwrites Web settings"


def test_delete_group_is_blocked_while_summary_is_running(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    app = create_app(archive=archive, candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(tmp_path / "knowledge"), password_hash="unused", session_secret="audit")
    client = TestClient(app)
    client.cookies.set("qq_digest_session", app.state.cookie.issue())
    with app.state.operations.claim("manual_summary"):
        response = client.delete("/api/groups/123")
    assert response.status_code == 409, f"Deletion allowed during summary: HTTP {response.status_code}"


def test_confirm_preserves_experience_body(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    candidates = CandidateService(archive)
    candidate_id = candidates.create(group_id=123, created_date="2026-09-02",
        candidate_type="experience", title="Review", content="The complete actionable procedure",
        reason="Useful", excerpt="Short source excerpt", message_ids=["m1"])
    writer = KnowledgeWriter(tmp_path / "knowledge")
    app = create_app(archive=archive, candidates=candidates, knowledge=writer,
        password_hash="unused", session_secret="audit")
    client = TestClient(app)
    client.cookies.set("qq_digest_session", app.state.cookie.issue())
    response = client.post(f"/candidates/{candidate_id}/confirm", follow_redirects=False)
    assert response.status_code == 303
    assert "The complete actionable procedure" in writer.path_for("experience").read_text(encoding="utf-8"), "Confirmed experience loses its content"


def test_candidate_must_reference_archived_source(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    original_chat = ai.chat
    def invalid_source(messages):
        response = original_chat(messages)
        response["candidates"][0]["message_ids"] = ["nonexistent-message"]
        return response
    ai.chat = invalid_source
    service.run(request)
    count = archive.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert count == 0, "AI invented message ID was persisted as evidence"


def test_large_latest_message_does_not_erase_all_context(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    messages = archive.messages_in_window(123, datetime(2026, 9, 2, tzinfo=ZoneInfo('Asia/Shanghai')),
        datetime(2026, 9, 3, tzinfo=ZoneInfo('Asia/Shanghai')))
    messages.append(messages[0].model_copy(update={"msg_id":"long", "text":"x" * 2000}))
    context = build_context_window(messages, 1000)
    assert context.included_messages > 0, "Oversized newest message yields empty context despite earlier valid messages"


def test_multiday_context_contains_dates(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    messages = archive.messages_in_window(123, datetime(2026, 9, 2, tzinfo=ZoneInfo('Asia/Shanghai')),
        datetime(2026, 9, 3, tzinfo=ZoneInfo('Asia/Shanghai')))
    messages[0] = messages[0].model_copy(update={"timestamp":messages[0].timestamp.astimezone(ZoneInfo('Asia/Shanghai'))})
    messages.append(messages[0].model_copy(update={"msg_id":"next-day", "timestamp":datetime(2026, 9, 3, 10, tzinfo=ZoneInfo('Asia/Shanghai'))}))
    context = build_context_window(messages, 1000)
    assert "2026-09-02" in context.text and "2026-09-03" in context.text, "Messages on different days both carry only 10:00"


def test_unconfigured_fallback_does_not_block_primary(tmp_path):
    from qq_digest.ai.factory import build_ai_client
    cfg = Config(data_dir=tmp_path, archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports", knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="unused"),
        ai=AIConfig(base_url="https://example.com/v1", model="test", api_key_env="AUDIT_UNUSED_KEY"))
    with patch.object(Config, "resolve_api_key", side_effect=ConfigError("Missing optional fallback key")):
        client = build_ai_client(cfg)
        client.close()


def test_api_key_file_is_read_with_environment_precedence(tmp_path, monkeypatch):
    key_file = tmp_path / "test-key.txt"
    key_file.write_text("\ufeffsynthetic-api-key\n", encoding="utf-8")
    cfg = Config(data_dir=tmp_path, archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports", knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="unused"),
        ai=AIConfig(base_url="https://example.com/v1", model="test", api_key_env="AUDIT_TEST_KEY", api_key_file=str(key_file)))
    monkeypatch.delenv("AUDIT_TEST_KEY", raising=False)
    with patch.object(Config, "_discover_codex_api_key", side_effect=AssertionError("Must use explicit credential")):
        assert cfg.resolve_api_key() == "synthetic-api-key"
        monkeypatch.setenv("AUDIT_TEST_KEY", "environment-key")
        assert cfg.resolve_api_key() == "environment-key"


def test_seed_does_not_resurrect_deleted_configured_group(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    groups = [GroupConfig(group_id=123, name="Review")]
    archive.seed_groups(groups)
    archive.delete_group(123)
    archive.seed_groups(groups)
    assert archive.all_groups() == []


def test_empty_context_does_not_call_ai(tmp_path):
    service, archive, ai, request = setup(tmp_path)
    with archive.transaction():
        archive.connection.execute("UPDATE messages SET text=?", ("x" * 2000,))
    result = service.run(request)
    assert result.failed_groups and not result.created_reports
    assert ai.calls == []
