from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.config import AIConfig, Config, SecurityConfig, SummaryConfig
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher


@pytest.fixture
def web_client(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    candidates = CandidateService(archive)
    candidate_id = candidates.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type="resource",
        title="站点",
        link="https://example.com",
        reason="高质量教程",
        excerpt="看这个站点",
        message_ids=["m1"],
    )
    app = create_app(
        archive=archive,
        candidates=candidates,
        knowledge=KnowledgeWriter(tmp_path / "knowledge"),
        password_hash=PasswordHasher.hash("password123"),
        session_secret="test-secret",
        config=Config(
            data_dir=tmp_path,
            archive_path=tmp_path / "archive.sqlite",
            report_dir=tmp_path / "reports",
            knowledge_dir=tmp_path / "knowledge",
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
            security=SecurityConfig(web_password_hash="x" * 32),
            ai=AIConfig(
                base_url="https://api.example.com/v1",
                model="gpt-test",
                api_key_env="QQ_DIGEST_AI_API_KEY",
            ),
            summary=SummaryConfig(hour=9, minute=30, timezone="Asia/Shanghai"),
        ),
    )
    client = TestClient(app)
    return client, candidate_id, tmp_path / "knowledge"


def test_candidates_requires_login(web_client):
    client, _, _ = web_client
    response = client.get("/candidates", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_ai_settings_requires_login(web_client):
    client, _, _ = web_client

    assert client.get("/ai-settings", follow_redirects=False).status_code == 303
    assert client.get("/api/ai-settings").status_code == 401
    assert client.put("/api/ai-settings/key", json={"api_key": "example-key"}).status_code == 401
    assert client.post("/api/ai-settings/test").status_code == 401


def test_ask_report_requires_login_and_validates_input(web_client, tmp_path):
    client, _, _ = web_client
    path = tmp_path / "daily.md"
    path.write_text("测试报告", encoding="utf-8")
    report_id = client.app.state.archive.record_report(
        group_id=123, report_date="2026-09-02", markdown_path=path,
        json_path=tmp_path / "daily.json", candidate_ids=[],
    )
    url = f"/api/reports/daily/{report_id}/ask"

    assert client.post(url, json={"question": "为什么？", "history": []}).status_code == 401
    client.post("/login", data={"password": "password123"})
    assert client.post(url, json={"question": " ", "history": []}).status_code == 422
    assert client.post(url, json={"question": "x" * 2001, "history": []}).status_code == 422
    assert client.post(url, json={"question": "为什么？", "history": []}).status_code == 422
    assert client.post("/api/reports/daily/999/ask", json={"question": "为什么？", "history": []}).status_code == 404


def test_ask_report_returns_checked_sources_without_leaking_key(web_client, tmp_path, monkeypatch):
    client, _, _ = web_client
    path = tmp_path / "daily.md"
    path.write_text("系统错误摘要", encoding="utf-8")
    report_id = client.app.state.archive.record_report(
        group_id=123, report_date="2026-09-02", markdown_path=path,
        json_path=tmp_path / "daily.json", candidate_ids=[],
    )
    timestamp = datetime(2026, 9, 2, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    client.app.state.archive.ingest([NormalizedMessage(
        msg_id="evidence-1", group_id=123, sender_qq=1001,
        timestamp=timestamp, collected_at=timestamp, text="接口返回 503",
    )])
    client.app.state.config.ai.ui_api_key_file = str(tmp_path / "deepseek-key.txt")
    client.app.state.config.ai.base_url = "https://api.deepseek.com"
    client.post("/login", data={"password": "password123"})
    client.put("/api/ai-settings/key", json={"api_key": "synthetic-private-key"})

    class FakeAI:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "synthetic-private-key"

        def chat(self, prompts):
            assert "接口返回 503" in str(prompts)
            return {"answer": "只能确认发生 503，原因未说明。", "source_ids": ["evidence-1", "made-up"]}

        def close(self):
            pass

    monkeypatch.setattr("qq_digest.web.app.AIClient", FakeAI)
    response = client.post(f"/api/reports/daily/{report_id}/ask", json={
        "question": "原因是什么？", "history": [{"role": "user", "content": "先看报错"}],
    })

    assert response.status_code == 200
    assert response.json()["context_message_count"] == 1
    assert [item["msg_id"] for item in response.json()["sources"]] == ["evidence-1"]
    assert "synthetic-private-key" not in response.text


def test_report_qa_dialog_has_ephemeral_controls(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"})

    page = client.get("/reports").text

    assert 'id="report-qa-open"' in page
    assert 'id="report-qa-dialog"' in page
    assert 'role="dialog"' in page
    assert 'id="report-qa-question"' in page
    assert 'id="report-qa-sources"' in page
    assert "选中的聊天片段发送到 DeepSeek" in page
    assert "qaHistory = []" in page


def test_ai_settings_page_saves_key_without_echoing_it(web_client, tmp_path):
    client, _, _ = web_client
    key_path = tmp_path / "secrets" / "deepseek-api-key.txt"
    client.app.state.config.ai.ui_api_key_file = str(key_path)
    client.post("/login", data={"password": "password123"})

    page = client.get("/ai-settings")
    before = client.get("/api/ai-settings").json()
    saved = client.put("/api/ai-settings/key", json={"api_key": "synthetic-example-key"})
    after = client.get("/api/ai-settings").json()

    assert page.status_code == 200
    assert 'id="deepseek-api-key"' in page.text
    assert 'type="password"' in page.text
    assert "synthetic-example-key" not in page.text
    assert before == {
        "base_url": "https://api.example.com/v1",
        "model": "gpt-test",
        "key_configured": False,
    }
    assert saved.status_code == 200
    assert "synthetic-example-key" not in saved.text
    assert key_path.read_text(encoding="utf-8").strip() == "synthetic-example-key"
    assert after["key_configured"] is True
    assert "synthetic-example-key" not in str(after)


def test_ai_settings_rejects_invalid_key(web_client, tmp_path):
    client, _, _ = web_client
    key_path = tmp_path / "secrets" / "deepseek-api-key.txt"
    client.app.state.config.ai.ui_api_key_file = str(key_path)
    client.post("/login", data={"password": "password123"})

    response = client.put("/api/ai-settings/key", json={"api_key": "two words"})

    assert response.status_code == 422
    assert not key_path.exists()


def test_ai_settings_connection_test_requires_positive_json_result(web_client, tmp_path, monkeypatch):
    client, _, _ = web_client
    client.app.state.config.ai.ui_api_key_file = str(tmp_path / "deepseek-api-key.txt")
    client.app.state.config.ai.base_url = "https://api.deepseek.com"
    client.post("/login", data={"password": "password123"})
    client.put("/api/ai-settings/key", json={"api_key": "synthetic-example-key"})

    class FakeAI:
        def __init__(self, **kwargs):
            self.closed = False

        def chat(self, messages):
            return {"ok": False}

        def close(self):
            self.closed = True

    monkeypatch.setattr("qq_digest.web.app.AIClient", FakeAI)

    response = client.post("/api/ai-settings/test")

    assert response.status_code == 503
    assert "DeepSeek 连接失败" in response.text


def test_ai_settings_connection_failure_does_not_echo_key(web_client, tmp_path, monkeypatch):
    from qq_digest.ai.client import AIError

    client, _, _ = web_client
    key_path = tmp_path / "secrets" / "deepseek-api-key.txt"
    client.app.state.config.ai.ui_api_key_file = str(key_path)
    client.post("/login", data={"password": "password123"})
    client.put("/api/ai-settings/key", json={"api_key": "synthetic-example-key"})

    class FailingAI:
        def __init__(self, **kwargs):
            pass

        def chat(self, messages):
            raise AIError("synthetic-example-key was rejected")

        def close(self):
            pass

    monkeypatch.setattr("qq_digest.web.app.AIClient", FailingAI)

    response = client.post("/api/ai-settings/test")

    assert response.status_code == 503
    assert "synthetic-example-key" not in response.text


def test_login_and_confirm_candidate(web_client):
    client, candidate_id, knowledge_dir = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)
    response = client.post(f"/candidates/{candidate_id}/confirm", follow_redirects=False)

    assert response.status_code == 303
    assert client.get("/candidates").status_code == 200
    markdown = (knowledge_dir / "resources.md").read_text(encoding="utf-8")
    assert "站点" in markdown
    assert f"条目 ID：{candidate_id}" in markdown
    candidate = client.app.state.candidates.get(candidate_id)
    assert candidate.status == "confirmed"
    knowledge_count = client.app.state.archive.connection.execute(
        "SELECT COUNT(*) AS total FROM knowledge_items"
    ).fetchone()["total"]
    assert knowledge_count == 1


def test_scheduler_api_returns_status(web_client):
    client, _, _ = web_client

    assert client.get("/api/scheduler").status_code == 401

    client.post("/login", data={"password": "password123"}, follow_redirects=False)
    response = client.get("/api/scheduler")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "schedule": "09:30",
        "timezone": "Asia/Shanghai",
        "running": False,
        "last_run_date": "",
        "last_result": None,
        "next_check": "within 60s",
        "collection": {
            "enabled": False,
            "interval_minutes": 10,
            "running": False,
            "last_run_at": "",
            "last_result": None,
        },
    }


def test_jobs_api_returns_bounded_failure_reason(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    job_id = archive.start_job("daily_digest")
    archive.finish_job(job_id, "failed", "上游失败 <script>" + "x" * 1000)
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    job = response.json()["jobs"][0]
    assert job["status"] == "failed"
    assert job["error"].startswith("上游失败 <script>")
    assert len(job["error"]) == 500


def test_dashboard_renders_job_errors_with_html_escaping(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get("/")

    assert response.status_code == 200
    assert "job-error" in response.text
    assert "escapeHtml(job.error" in response.text


def test_stats_counts_daily_and_range_reports(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    archive.record_report(
        group_id=123,
        report_date="2026-09-01",
        effective_template="concise",
        markdown_path=tmp_path / "daily.md",
        json_path=tmp_path / "daily.json",
        candidate_ids=[],
    )
    archive.record_manual_report(
        group_id=123,
        start_date="2026-09-01",
        end_date="2026-09-07",
        detail_mode="group",
        effective_template="detailed",
        markdown_path=tmp_path / "range.md",
        json_path=tmp_path / "range.json",
        candidate_ids=[],
        input_fingerprint="fingerprint",
        source_message_count=1,
    )
    client.post("/login", data={"password": "password123"})

    response = client.get("/api/stats")

    assert response.status_code == 200
    assert response.json()["total_reports"] == 2


def test_groups_api_includes_archive_stats(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get("/api/groups")

    assert response.status_code == 200
    group = next(g for g in response.json()["groups"] if g["group_id"] == 123)
    assert group["message_count"] == 0
    assert group["latest_message_at"] is None
    assert group["last_sync"] == "未同步"
    assert "template" not in group
    assert group["keywords"] == []
    assert group["collection_window_days"] == 30


def test_summary_pages_do_not_expose_template_controls(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"})

    groups_page = client.get("/groups").text
    reports_page = client.get("/reports").text
    dashboard_page = client.get("/").text

    assert "摘要模板" not in groups_page
    assert "摘要详细程度" not in reports_page
    assert "data-label=\"模板\"" not in reports_page
    assert "data-label=\"模板\"" not in dashboard_page


def test_group_api_updates_keywords_and_collection_window(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.patch(
        "/api/groups/123",
        json={"keywords": ["网站", "经验"], "collection_window_days": 45},
    )

    assert response.status_code == 200
    group = client.get("/api/groups").json()["groups"][0]
    assert group["keywords"] == ["网站", "经验"]
    assert group["collection_window_days"] == 45
    stored = client.app.state.archive.all_groups()[0]
    assert stored.keywords == ["网站", "经验"]
    assert stored.collection_window_days == 45


@pytest.mark.parametrize(
    "payload",
    [
        {"keywords": "网站"},
        {"keywords": ["有效", ""]},
        {"collection_window_days": 0},
        {"collection_window_days": 366},
        {"collection_window_days": "30"},
    ],
)
def test_group_api_rejects_invalid_keywords_and_collection_window(
    web_client, payload
):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.patch("/api/groups/123", json=payload)

    assert response.status_code == 400


def test_delete_group_api_purges_related_messages(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)
    archive = client.app.state.archive
    from tests.factories import make_message

    archive.ingest([make_message(msg_id="delete-me")])

    response = client.delete("/api/groups/123")

    assert response.status_code == 200
    assert response.json()["deleted"]["messages"] == 1
    assert archive.connection.execute(
        "SELECT COUNT(*) FROM groups WHERE group_id=123"
    ).fetchone()[0] == 0
    assert archive.count_messages(123) == 0


def test_run_daily_rejects_overlapping_execution(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    with client.app.state.operations.claim("sync"):
        response = client.post("/api/run-daily")

    assert response.status_code == 409
    assert response.json()["detail"]["active"] == ["sync"]


def test_collect_rejects_refresh_conflict(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)
    client.app.state.config.ntqq.enabled = True
    client.app.state.config.ntqq.db_dir = "unused-during-conflict"

    with client.app.state.operations.claim("refresh"):
        response = client.post(
            "/api/collect",
            json={
                "group_id": 123,
                "start": "2026-08-24",
                "end": "2026-08-24",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["active"] == ["refresh"]


def test_refresh_rejects_collect_conflict(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)
    client.app.state.config.ntqq.enabled = True
    client.app.state.config.ntqq.db_dir = "unused-during-conflict"

    with client.app.state.operations.claim("collect"):
        response = client.post("/api/refresh")

    assert response.status_code == 409
    assert response.json()["detail"]["active"] == ["collect"]


class FailingWebAI:
    def chat(self, messages):
        raise RuntimeError("temporary AI outage")

    def close(self):
        pass


class SuccessfulRangeWebAI:
    def __init__(self):
        self.closed = False

    def chat(self, messages):
        prompt = messages[-1]["content"]
        group_id = int(prompt.split("群ID: ", 1)[1].splitlines()[0])
        return {
            "group_id": group_id,
            "main_topics": [],
            "conclusions": [],
            "resources": [],
            "tasks": [],
            "open_questions": [],
            "candidates": [],
        }

    def close(self):
        self.closed = True


def test_manual_range_summary_requires_login(web_client):
    client, _, _ = web_client

    response = client.post(
        "/api/reports/range",
        json={
            "group_ids": [123],
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
        },
    )

    assert response.status_code == 401


def test_manual_range_summary_creates_listed_report(web_client, monkeypatch):
    client, _, _ = web_client
    archive = client.app.state.archive
    timestamp = datetime(2026, 9, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive.ingest(
        [
            NormalizedMessage(
                msg_id="range-web",
                group_id=123,
                sender_qq=1,
                timestamp=timestamp,
                text="范围摘要消息",
                collected_at=timestamp,
            )
        ]
    )
    ai = SuccessfulRangeWebAI()
    monkeypatch.setattr("qq_digest.ai.factory.build_ai_client", lambda cfg: ai)
    client.post("/login", data={"password": "password123"})

    response = client.post(
        "/api/reports/range",
        json={
            "group_ids": [123],
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["created_reports"][0]["report_key"].startswith("range:")
    assert ai.closed is True
    reports = client.get("/api/reports").json()["reports"]
    range_report = next(item for item in reports if item["report_kind"] == "range")
    assert range_report["window_start_date"] == "2026-09-01"
    assert range_report["window_end_date"] == "2026-09-01"
    assert "effective_template" not in range_report
    detail = client.get(
        f"/api/reports/range/{range_report['report_id']}"
    )
    assert detail.status_code == 200
    assert "测试群范围摘要" in detail.json()["markdown"]
    assert detail.json()["group_name"] == "测试群"
    assert "effective_template" not in detail.json()


def test_daily_report_list_omits_legacy_template_metadata(web_client, tmp_path):
    client, _, _ = web_client
    client.app.state.archive.record_report(
        group_id=123,
        report_date="2026-09-01",
        effective_template="detailed",
        markdown_path=tmp_path / "daily.md",
        json_path=tmp_path / "daily.json",
        candidate_ids=[],
    )
    client.post("/login", data={"password": "password123"})

    reports = client.get("/api/reports").json()["reports"]
    daily = next(item for item in reports if item["report_kind"] == "daily")

    assert "effective_template" not in daily


def test_report_list_shows_messages_actually_used_for_daily_and_range_reports(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    daily_json = tmp_path / "daily.json"
    range_json = tmp_path / "range.json"
    daily_json.write_text('{"diagnostics": {"included_messages": 7}}', encoding="utf-8")
    range_json.write_text('{"diagnostics": {"included_messages": 12}}', encoding="utf-8")
    archive.record_report(
        group_id=123,
        report_date="2026-09-01",
        markdown_path=tmp_path / "daily.md",
        json_path=daily_json,
        candidate_ids=[],
        source_message_count=20,
    )
    archive.record_manual_report(
        group_id=123,
        start_date="2026-08-30",
        end_date="2026-09-01",
        detail_mode="group",
        effective_template="detailed",
        markdown_path=tmp_path / "range.md",
        json_path=range_json,
        candidate_ids=[],
        input_fingerprint="range-input",
        source_message_count=30,
    )
    client.post("/login", data={"password": "password123"})

    reports = client.get("/api/reports").json()["reports"]

    assert next(r for r in reports if r["report_kind"] == "daily")["reference_message_count"] == 7
    assert next(r for r in reports if r["report_kind"] == "range")["reference_message_count"] == 12
    assert all("_json_path" not in r for r in reports)


def test_report_list_uses_unknown_count_for_legacy_or_invalid_report_json(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    legacy_json = tmp_path / "legacy.json"
    legacy_json.write_text('{"overview": "旧报告"}', encoding="utf-8")
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("not json", encoding="utf-8")
    archive.record_report(
        group_id=123,
        report_date="2026-09-01",
        markdown_path=tmp_path / "daily.md",
        json_path=legacy_json,
        candidate_ids=[],
    )
    archive.record_report(
        group_id=123,
        report_date="2026-08-31",
        markdown_path=tmp_path / "missing.md",
        json_path=tmp_path / "missing.json",
        candidate_ids=[],
    )
    archive.record_manual_report(
        group_id=123,
        start_date="2026-08-30",
        end_date="2026-09-01",
        detail_mode="group",
        effective_template="detailed",
        markdown_path=tmp_path / "range.md",
        json_path=invalid_json,
        candidate_ids=[],
        input_fingerprint="range-input",
        source_message_count=30,
    )
    client.post("/login", data={"password": "password123"})

    reports = client.get("/api/reports").json()["reports"]

    assert all(r["reference_message_count"] is None for r in reports)


def test_manual_range_summary_accepts_legacy_detail_mode(web_client, monkeypatch):
    client, _, _ = web_client
    timestamp = datetime(2026, 9, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    client.app.state.archive.ingest(
        [
            NormalizedMessage(
                msg_id="legacy-range-web",
                group_id=123,
                sender_qq=1,
                timestamp=timestamp,
                text="兼容旧请求",
                collected_at=timestamp,
            )
        ]
    )
    monkeypatch.setattr(
        "qq_digest.ai.factory.build_ai_client", lambda cfg: SuccessfulRangeWebAI()
    )
    client.post("/login", data={"password": "password123"})

    response = client.post(
        "/api/reports/range",
        json={
            "group_ids": [123],
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
            "detail_mode": "detailed",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "group_ids": [],
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
            "detail_mode": "group",
        },
        {
            "group_ids": [123],
            "start_date": "2026-09-01",
            "end_date": "2026-09-08",
            "detail_mode": "group",
        },
        {
            "group_ids": [999],
            "start_date": "2026-09-01",
            "end_date": "2026-09-01",
            "detail_mode": "group",
        },
    ],
)
def test_manual_range_summary_rejects_invalid_requests(
    web_client, monkeypatch, payload
):
    client, _, _ = web_client
    monkeypatch.setattr(
        "qq_digest.ai.factory.build_ai_client", lambda cfg: SuccessfulRangeWebAI()
    )
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/reports/range", json=payload)

    assert response.status_code == 422


def test_manual_range_summary_rejects_operation_conflict(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"})

    with client.app.state.operations.claim("sync"):
        response = client.post(
            "/api/reports/range",
            json={
                "group_ids": [123],
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
                "detail_mode": "group",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["active"] == ["sync"]


def test_run_daily_returns_partial_group_details(web_client, monkeypatch):
    client, _, _ = web_client
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    client.app.state.archive.ingest(
        [
            NormalizedMessage(
                msg_id="web-partial",
                group_id=123,
                sender_qq=1,
                timestamp=now - timedelta(minutes=1),
                text="需要生成摘要的消息",
                collected_at=now,
            )
        ]
    )
    monkeypatch.setattr(
        "qq_digest.ai.factory.build_ai_client", lambda config: FailingWebAI()
    )
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/run-daily")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial_success"
    assert data["reports"] == 0
    assert data["succeeded_groups"] == []
    assert data["failed_groups"][0]["group_id"] == 123
    assert data["failed_groups"][0]["stage"] == "summary"
    assert "temporary AI outage" in data["failed_groups"][0]["error"]


def test_run_daily_preflight_failure_remains_server_error(
    web_client, monkeypatch, tmp_path
):
    client, _, _ = web_client
    cfg = client.app.state.config
    cfg.ntqq.enabled = True
    cfg.ntqq.db_dir = str(tmp_path / "decrypted")
    monkeypatch.setattr(
        "qq_digest.refresh.refresh_database",
        lambda **kwargs: SimpleNamespace(success=False, message="snapshot failed"),
    )
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/run-daily")

    assert response.status_code == 500
    assert "snapshot failed" in response.json()["detail"]


def test_operational_ui_contains_group_search_and_safe_render_helpers(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    dashboard = client.get("/")
    groups = client.get("/groups")

    assert dashboard.status_code == 200
    assert "运行总览" in dashboard.text
    assert "function escapeHtml" in dashboard.text
    assert "function dailyResultMessage" in dashboard.text
    assert "data.detail.message" in dashboard.text
    assert "partial_success: '部分成功'" in dashboard.text
    assert groups.status_code == 200
    assert 'id="group-search"' in groups.text
    assert "扫描本地群聊" in groups.text
    assert "group-keywords" in groups.text
    assert "collection-window" in groups.text


def test_pages_use_top_navigation_shell(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    dashboard = client.get("/")

    assert dashboard.status_code == 200
    assert 'class="global-header"' in dashboard.text
    assert 'class="global-nav"' in dashboard.text
    assert 'class="page-toolbar"' in dashboard.text
    assert 'class="sidebar"' not in dashboard.text


def test_dashboard_has_responsive_job_table(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    dashboard = client.get("/")

    assert 'class="table responsive-table job-table"' in dashboard.text
    assert 'class="dashboard-grid"' in dashboard.text
    assert 'class="mobile-job-note"' in dashboard.text
    assert 'data-label="任务"' in dashboard.text
    assert 'data-label="错误"' in dashboard.text


def test_operational_pages_use_responsive_layout_classes(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    groups = client.get("/groups")
    collect = client.get("/collect")
    reports = client.get("/reports")

    assert 'class="table group-config-table"' in groups.text
    assert 'class="filter-toolbar collect-toolbar"' in collect.text
    assert 'class="report-layout"' in reports.text
    assert 'class="table responsive-table report-table"' in reports.text
    assert '<th title="实际纳入摘要的消息条数">参考消息数</th>' in reports.text
    assert 'data-label="参考消息数"' in reports.text
    assert 'colspan="7"' in reports.text
    assert 'data-label="操作"' in reports.text
    assert 'style="width:150px"' not in groups.text


def test_reports_page_contains_manual_range_summary_drawer(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    reports = client.get("/reports")

    assert reports.status_code == 200
    assert "摘要报告" in reports.text
    assert "生成今日日报" in reports.text
    assert 'id="range-summary-panel"' in reports.text
    assert 'id="range-summary-backdrop"' in reports.text
    assert 'id="range-group-search"' in reports.text
    assert 'id="range-group-list"' in reports.text
    assert 'id="range-start-date"' in reports.text
    assert 'id="range-end-date"' in reports.text
    assert 'id="range-selection-summary"' in reports.text
    assert 'id="range-submit-btn"' in reports.text
    assert "selectedRangeGroupIds" in reports.text
    assert ".report-layout.has-detail .report-table { min-width: 0; }" in reports.text


def test_candidate_drawer_and_login_use_redesigned_structure(web_client):
    client, _, _ = web_client
    login = client.get("/login")
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    candidates = client.get("/candidates")

    assert 'class="login-shell"' in login.text
    assert 'rel="icon"' in login.text
    assert 'rel="icon"' in candidates.text
    assert 'class="candidate-detail-backdrop"' in candidates.text
    assert 'aria-label="关闭候选详情"' in candidates.text


def test_candidate_unsafe_link_is_not_clickable(web_client):
    client, _, _ = web_client
    client.app.state.candidates.create(
        group_id=123,
        created_date="2026-08-30",
        candidate_type="resource",
        title="不安全链接",
        link="javascript:alert(1)",
        reason="测试协议过滤",
        excerpt="",
        message_ids=["m-unsafe"],
    )
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get("/candidates")

    assert response.status_code == 200
    assert "javascript:alert(1)" in response.text
    assert 'href="javascript:alert(1)"' not in response.text


def test_candidate_api_filters_history_and_returns_details(web_client):
    client, candidate_id, _ = web_client
    candidates = client.app.state.candidates
    later_id = candidates.create(
        group_id=123,
        created_date="2026-08-30",
        candidate_type="experience",
        title="排障经验",
        content="先检查日志",
        reason="可以复用",
        excerpt="检查日志后恢复",
        message_ids=["m2", "m3"],
    )
    candidates.update_status(later_id, "later")
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get(
        "/api/candidates",
        params={
            "status": "later",
            "group_id": 123,
            "candidate_type": "experience",
            "date_from": "2026-08-29",
            "date_to": "2026-08-31",
            "q": "日志",
        },
    )

    assert response.status_code == 200
    assert [item["candidate_id"] for item in response.json()["candidates"]] == [later_id]
    assert response.json()["candidates"][0]["group_name"] == "测试群"

    detail = client.get(f"/api/candidates/{later_id}")
    assert detail.status_code == 200
    assert detail.json()["message_ids"] == ["m2", "m3"]
    assert detail.json()["content"] == "先检查日志"
    assert detail.json()["updated_at"]

    pending = client.get("/api/candidates", params={"status": "pending"}).json()
    assert [item["candidate_id"] for item in pending["candidates"]] == [candidate_id]


def test_later_candidate_can_be_restored_to_pending(web_client):
    client, candidate_id, _ = web_client
    candidates = client.app.state.candidates
    candidates.update_status(candidate_id, "later")
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.post(
        f"/candidates/{candidate_id}/restore", follow_redirects=False
    )

    assert response.status_code == 303
    assert candidates.get(candidate_id).status == "pending"


def test_candidate_page_contains_history_filters_and_detail_panel(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get("/candidates")

    assert response.status_code == 200
    assert 'id="candidate-status"' in response.text
    assert 'id="candidate-group"' in response.text
    assert 'id="candidate-type"' in response.text
    assert 'id="candidate-detail"' in response.text
    assert "稍后处理" in response.text
