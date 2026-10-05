from datetime import datetime, timedelta
import json
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


def test_feature_switches_auth_validation_routes_and_reopen(web_client):
    client, candidate_id, _ = web_client
    assert client.get('/api/features').status_code == 401
    assert client.put('/api/features', json={}).status_code == 401
    client.post('/login', data={'password': 'password123'})
    state = client.get('/api/features').json()
    patch = {'values': {'catchup': False, 'tasks': False, 'failures': False, 'review': False,
                        'report_qa': False, 'report_revisions': False, 'history_inspection': False},
             'expected_revision': state['revision']}
    result = client.put('/api/features', json=patch)
    assert result.status_code == 200
    for page in ('catchup', 'tasks', 'failures', 'candidates'):
        response = client.get('/'+page, follow_redirects=False)
        assert response.status_code == 303 and response.headers['location'].startswith('/settings')
        assert client.get('/api/'+page).status_code == 403
    for path in ('/api/tasks/from-message', '/api/reports/daily/1/ask',
                 '/api/reports/daily/1/regenerate', '/api/history-inspection/run',
                 f'/candidates/{candidate_id}/confirm'):
        assert client.post(path, json={}).status_code == 403
    assert client.get('/api/reports/daily/1/versions').status_code == 403
    for alternative_id in ('+1', '1.0', '01'):
        assert client.get(f'/api/reports/daily/{alternative_id}/versions').status_code == 403
        assert client.post(f'/api/reports/daily/{alternative_id}/corrections', json={}).status_code == 403
        assert client.post(f'/api/reports/daily/{alternative_id}/ask', json={}).status_code == 403
    assert client.put('/api/features', json=patch).status_code == 409
    for values in ({'unknown': False}, {'tasks': 'false'}, {}):
        assert client.put('/api/features', json={'values': values, 'expected_revision': 1}).status_code == 422
    page = client.get('/').text
    assert 'id="nav-tasks"' not in page and 'href="/catchup"' not in page
    assert 'href="/settings"' in page and 'href="/reports"' in page
    scheduler = client.get('/api/scheduler').json()
    assert scheduler['enabled'] is True
    result = client.put('/api/features', json={'values': {'review': True, 'tasks': True, 'auto_daily': False}, 'expected_revision': 1})
    assert result.status_code == 200
    assert client.get('/api/tasks').status_code == 200
    assert client.get(f'/api/candidates/{candidate_id}').status_code == 200
    assert client.get('/api/scheduler').json()['enabled'] is False


@pytest.mark.parametrize('enabled', [False, True])
def test_live_auto_switches_pause_schedulers_before_work(web_client, monkeypatch, enabled):
    import asyncio
    client, _, _ = web_client
    app = client.app
    service = app.state.features
    service.update({'auto_daily': enabled, 'auto_collection': enabled, 'history_inspection': False}, 0)
    app.state.config.ntqq.enabled = True
    app.state.config.ntqq.db_dir = 'synthetic-unused'
    ticks = []
    original_sleep = asyncio.sleep
    async def tick(delay):
        ticks.append(delay)
        # Let each loop evaluate at least once, then wait for cancellation.
        if ticks.count(delay) > 1:
            await original_sleep(3600)
        else:
            await original_sleep(0)
    work_calls = []
    def forbidden(*args, **kwargs):
        work_calls.append('worker')
        raise AssertionError('disabled scheduler reached a worker')
    monkeypatch.setattr('qq_digest.web.app.asyncio.sleep', tick)
    monkeypatch.setattr('qq_digest.web.app.pending_catchup_date', forbidden)
    monkeypatch.setattr('qq_digest.sync.run_message_sync', forbidden)
    monkeypatch.setattr('qq_digest.web.history_inspection.run_history_inspection', forbidden)
    async def check():
        async with app.router.lifespan_context(app):
            for _ in range(6):
                await original_sleep(0)
    asyncio.run(check())
    assert 60 in ticks and app.state.config.collection.startup_delay_seconds in ticks
    assert bool(work_calls) is enabled


def test_revision_api_auth_validation_notes_and_conflict(web_client, tmp_path, monkeypatch):
    client, _, _ = web_client
    ar = client.app.state.archive
    md, js = tmp_path/'legacy.md', tmp_path/'legacy.json'
    md.write_text('old',encoding='utf-8'); js.write_text('{}',encoding='utf-8')
    rid = ar.record_report(group_id=123,report_date='2026-10-01',markdown_path=md,json_path=js,candidate_ids=[])
    path = f'/api/reports/daily/{rid}'
    assert client.get(path+'/versions').status_code==401
    assert client.post(path+'/corrections',json={}).status_code==401
    assert client.post(path+'/regenerate',json={}).status_code==401
    client.post('/login',data={'password':'password123'})
    assert client.get(path+'/versions').json()['current_version']==0
    assert client.get(path+'/versions/0').json()['markdown']=='old'
    payload = dict(expected_version=0,category='error',excerpt='old',correction='new',reason='依据原消息')
    note = client.post(path+'/corrections',json=payload)
    assert note.status_code==200
    assert client.get(path+'/corrections').json()['corrections'][0]['correction']=='new'
    assert client.post(path+'/corrections',json=payload).status_code==409
    note_id = note.json()['correction_id']
    assert client.put(path+f'/corrections/{note_id}',json={'status':'resolved'}).status_code==200
    assert client.post(path+'/corrections',json={**payload,'reason':' '}).status_code==422
    assert client.post(path+'/regenerate',json={'expected_version':1,'reason':' '}).status_code==422
    assert client.get(path+'/versions/99').status_code==404
    assert client.get('/api/reports/other/1/versions').status_code==404
    with client.app.state.operations.claim('collect'):
        assert client.post(path+'/corrections',json={**payload,'expected_version':1}).status_code==409
        assert client.post(path+'/regenerate',json={'expected_version':1,'reason':'重试'}).status_code==409
    calls=[]
    def regen(**kwargs):
        calls.append(kwargs); return {'version':2}
    monkeypatch.setattr('qq_digest.web.report_revisions.regenerate_report',regen)
    assert client.post(path+'/regenerate',json={'expected_version':1,'reason':'重试'}).status_code==200
    assert calls[0]['kind']=='daily' and calls[0]['reason']=='重试'


def test_report_detail_binds_content_to_immutable_revision(web_client, tmp_path):
    client,_,_=web_client
    ar=client.app.state.archive
    md,js=tmp_path/'version.md',tmp_path/'version.json'
    md.write_text('mutable file',encoding='utf-8'); js.write_text('{}',encoding='utf-8')
    rid=ar.record_report(group_id=123,report_date='2026-10-01',markdown_path=md,json_path=js,
        candidate_ids=[],revision_markdown='snapshot content',revision_payload={'group_id':123})
    md.write_bytes(b'\xff')
    client.post('/login',data={'password':'password123'})
    detail=client.get(f'/api/reports/daily/{rid}').json()
    assert detail['current_version']==1
    assert detail['markdown']=='snapshot content'
    assert client.get(f'/api/reports/daily/{rid}/sources/test?version=0').status_code==409
    assert client.post(f'/api/reports/daily/{rid}/ask',json={'question':'解释','expected_version':0}).status_code==409


def test_history_inspection_requires_login_and_persists_settings(web_client):
    client, _, _ = web_client
    assert client.get("/api/history-inspection").status_code == 401
    assert client.put("/api/history-inspection/settings", json={}).status_code == 401
    assert client.post("/api/history-inspection/run", json={}).status_code == 401
    client.post("/login", data={"password": "password123"})
    response = client.put("/api/history-inspection/settings", json={"enabled": False,
        "lookback_days": 14, "interval_hours": 48})
    assert response.status_code == 200
    assert client.get("/api/history-inspection").json()["settings"] == response.json()
    assert client.get("/api/history-inspection").json()["groups"][0]["status"] == "unchecked"
    assert client.put("/api/history-inspection/settings", json={"lookback_days": 32}).status_code == 422
    assert client.put("/api/history-inspection/settings", json={"enabled": "false"}).status_code == 422
    assert client.get("/api/history-inspection?page=0").status_code == 422
    assert client.post("/api/history-inspection/run", json={}).status_code == 400


def test_manual_history_inspection_and_conflict_do_not_import_messages(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled, config.ntqq.db_dir = True, "test-source"
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=3)
    class Source:
        def __init__(self, **kwargs):
            pass
        def collect(self, group_id, start, end):
            return [NormalizedMessage(msg_id="history-gap", group_id=group_id,
                timestamp=stamp, collected_at=stamp, text="历史缺口")]
    monkeypatch.setattr("qq_digest.history_inspection.NTQQCollector", Source)
    client.post("/login", data={"password": "password123"})
    result = client.post("/api/history-inspection/run", json={"refresh": False})
    assert result.status_code == 200
    assert result.json()["missing_count"] == 1
    data = client.get("/api/history-inspection").json()
    assert data["groups"][0]["missing_days"] == [{"date": stamp.date().isoformat(), "count": 1}]
    assert data["running"] is False
    assert client.app.state.archive.count_messages(123) == 0
    with client.app.state.operations.claim("collect"):
        assert client.post("/api/history-inspection/run", json={}).status_code == 409
        assert client.put("/api/history-inspection/settings", json={}).status_code == 409
    assert client.post("/api/history-inspection/run", json={"refresh": "yes"}).status_code == 422
    assert "history-inspection" in client.get("/collect").text
    assert "params.get('end')" in client.get("/collect").text


def test_report_completeness_is_exposed_in_list_detail_and_health(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    md, js = tmp_path / "coverage.md", tmp_path / "coverage.json"
    md.write_text("# 简报", encoding="utf-8")
    js.write_text(json.dumps({"diagnostics": {"source_messages": 30,
        "included_messages": 3, "context_truncated": True}}), encoding="utf-8")
    report_id = archive.record_report(group_id=123, report_date="2026-09-29",
        markdown_path=md, json_path=js, candidate_ids=[])
    job_id = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job_id, "success", group_outcomes={123: "success"})
    assert client.get("/api/health").status_code == 401
    assert client.get(f"/api/reports/daily/{report_id}").status_code == 401
    client.post("/login", data={"password": "password123"})
    item = client.get("/api/reports").json()["reports"][0]
    assert item["completeness"]["status"] == "limited"
    assert item["completeness"]["included_messages"] == 3
    detail = client.get(f"/api/reports/daily/{report_id}").json()
    assert detail["completeness"] == item["completeness"]
    assert client.get(f"/api/reports/{report_id}").json()["completeness"] == item["completeness"]
    day = client.get("/api/health").json()["daily_coverage"]
    assert day["status"] == "limited"
    assert day["limited_input_groups"] == 1
    assert "report-completeness" in client.get("/reports").text
    assert "daily-coverage" in client.get("/overview").text


@pytest.mark.parametrize("contents", ["broken", "[]", "{}"])
def test_broken_or_legacy_json_is_readable_with_unknown_scope(web_client, tmp_path, contents):
    client, _, _ = web_client
    archive = client.app.state.archive
    md, js = tmp_path / "legacy.md", tmp_path / "legacy.json"
    md.write_text("# 旧摘要", encoding="utf-8")
    js.write_text(contents, encoding="utf-8")
    report_id = archive.record_report(group_id=123, report_date="2026-09-29",
        markdown_path=md, json_path=js, candidate_ids=[])
    client.post("/login", data={"password": "password123"})
    assert client.get("/api/reports").json()["reports"][0]["completeness"]["status"] == "unknown"
    detail = client.get(f"/api/reports/daily/{report_id}").json()
    assert detail["markdown"] == "# 旧摘要"
    assert detail["completeness"]["status"] == "unknown"


def test_ai_settings_requires_login(web_client):
    client, _, _ = web_client

    assert client.get("/ai-settings", follow_redirects=False).status_code == 303
    assert client.get("/api/ai-settings").status_code == 401
    assert client.put("/api/ai-settings/key", json={"api_key": "example-key"}).status_code == 401
    assert client.post("/api/ai-settings/test").status_code == 401


def test_failure_center_requires_login_and_shows_persisted_error(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    job_id = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job_id, "failed", "AI 请求失败")
    assert client.get("/failures", follow_redirects=False).status_code == 303
    assert client.get("/api/failures").status_code == 401
    assert client.post(f"/api/failures/jobs/{job_id}/retry").status_code == 401
    assert client.post("/api/failures/notifications/1/retry").status_code == 401
    client.post("/login", data={"password": "password123"})
    assert "异常处理" in client.get("/failures").text
    item = client.get("/api/failures").json()["items"][0]
    assert item["job_id"] == job_id
    assert item["error"] == "AI 请求失败"


def test_failure_center_rejects_invalid_retry_and_requeues_notification(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    client.post("/login", data={"password": "password123"})
    assert client.post("/api/failures/jobs/999/retry").status_code == 404
    assert client.post("/api/failures/notifications/999/retry").status_code == 404
    send_id = archive.enqueue_notification(
        report_id=None, channel="qq_bot_private", recipient="openid-1", payload={}
    )
    archive.mark_notification_retry(
        send_id, error="429", next_attempt_at=datetime.now(ZoneInfo("UTC")),
        max_attempts=1,
    )
    # A disabled bot must not imply that a queued notification can be sent.
    assert client.post(f"/api/failures/notifications/{send_id}/retry").status_code == 503

    client.app.state.config.qq_bot.enabled = True
    client.app.state.config.qq_bot.app_id = "test-app"
    response = client.post(f"/api/failures/notifications/{send_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "pending_send"
    assert client.post(f"/api/failures/notifications/{send_id}/retry").status_code == 409
    assert archive.connection.execute(
        "SELECT manual_retry_count FROM send_log WHERE send_id=?", (send_id,)
    ).fetchone()[0] == 1


def test_failure_center_daily_retry_uses_original_date_and_links_result(web_client, monkeypatch):
    client, _, _ = web_client
    archive = client.app.state.archive
    job_id = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job_id, "failed", "AI 请求失败")
    class QuietAI:
        def close(self):
            pass
    monkeypatch.setattr("qq_digest.ai.factory.build_ai_client", lambda cfg: QuietAI())
    client.post("/login", data={"password": "password123"})
    response = client.post(f"/api/failures/jobs/{job_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    retry_id = response.json()["retry_job_id"]
    row = archive.connection.execute(
        "SELECT target_date,retry_of_job_id FROM jobs WHERE job_id=?", (retry_id,)
    ).fetchone()
    assert row["target_date"] == "2026-09-29"
    assert row["retry_of_job_id"] == job_id
    original = next(item for item in client.get("/api/failures").json()["items"]
                    if item["job_id"] == job_id)
    assert original["status"] == "recovered"
    assert original["retry_job_id"] == retry_id


def test_failure_center_rejects_retry_of_already_recovered_group(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    original = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(original, "partial_success", json.dumps([{
        "group_id": 123, "group_name": "测试群", "stage": "summary", "error": "超时",
    }], ensure_ascii=False))
    later = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(later, "success", group_outcomes={123: "success"})
    client.post("/login", data={"password": "password123"})
    assert client.post(f"/api/failures/jobs/{original}/retry").status_code == 409


def test_failure_center_rejects_duplicate_notification_retry(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    old = archive.enqueue_notification(
        report_id=None, channel="qq_bot_private", recipient="same-recipient", payload={}
    )
    archive.mark_notification_retry(
        old, error="429", next_attempt_at=datetime.now(ZoneInfo("UTC")), max_attempts=1,
    )
    archive.enqueue_notification(
        report_id=None, channel="qq_bot_private", recipient="same-recipient", payload={}
    )
    client.app.state.config.qq_bot.enabled = True
    client.app.state.config.qq_bot.app_id = "test-app"
    client.post("/login", data={"password": "password123"})
    assert client.post(f"/api/failures/notifications/{old}/retry").status_code == 409


def test_desktop_settings_page_requires_login_and_renders(web_client):
    client, _, _ = web_client
    assert client.get("/settings", follow_redirects=False).status_code == 303
    client.post("/login", data={"password": "password123"})
    response = client.get("/settings")
    assert response.status_code == 200
    assert "消息摘要导出" in response.text
    assert "开机自启" in response.text
    assert "数据备份" in response.text
    assert "恢复数据" in response.text


def test_catchup_requires_login_and_persists_read_state(web_client, tmp_path):
    client, _, _ = web_client
    assert client.get("/catchup", follow_redirects=False).status_code == 303
    assert client.get("/api/catchup?scope=today").status_code == 401
    assert client.post("/api/catchup/visit").status_code == 401
    assert client.post("/api/catchup/read", json={"key": "a" * 64, "read": True}).status_code == 401

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    payload_path = tmp_path / "catchup.json"
    payload_path.write_text(json.dumps({
        "evidence_version": 1, "overview": "概览", "main_topics": [{
            "topic": "发布", "summary": "今天发布", "message_ids": ["catchup-source"],
        }], "conclusions": [], "resources": [], "tasks": [], "open_questions": [],
    }, ensure_ascii=False), encoding="utf-8")
    archive = client.app.state.archive
    report_id = archive.record_report(
        group_id=123, report_date=today,
        markdown_path=tmp_path / "catchup.md", json_path=payload_path,
        candidate_ids=[],
    )
    local_time = datetime.now(ZoneInfo("Asia/Shanghai"))
    archive.ingest([NormalizedMessage(
        msg_id="catchup-source", group_id=123, sender_qq=1001,
        timestamp=local_time, collected_at=local_time, text="今天发布",
    )])
    client.post("/login", data={"password": "password123"})
    assert "跨群补看" in client.get("/catchup").text
    assert client.post("/api/catchup/visit").json()["previous_viewed_at"] is None
    assert client.post("/api/catchup/visit").json()["previous_viewed_at"] is not None
    result = client.get("/api/catchup?scope=today").json()
    assert result["total"] == 1
    assert result["unread"] == 1
    item = result["items"][0]
    assert item["source_ids"] == ["catchup-source"]
    assert item["report_url"] == f"/reports?kind=daily&id={report_id}"
    assert client.get(f"/api/reports/daily/{report_id}/sources/catchup-source").status_code == 200
    assert client.post("/api/catchup/read", json={"key": item["key"], "read": True}).status_code == 200
    assert client.get("/api/catchup?scope=today").json()["unread"] == 0
    assert client.post("/api/catchup/read", json={"key": item["key"], "read": False}).status_code == 200
    assert client.get("/api/catchup?scope=today").json()["unread"] == 1
    assert client.get("/api/catchup?scope=since&since=invalid").status_code == 400
    assert client.get("/api/catchup?scope=today&page=0").status_code == 422
    assert client.post("/api/catchup/read", json={"key": "bad", "read": True}).status_code == 422


def test_task_inbox_requires_login_and_supports_confirmation_and_edit(web_client, tmp_path):
    client, _, _ = web_client
    assert client.get("/tasks", follow_redirects=False).status_code == 303
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks/suggestions").status_code == 401
    assert client.post("/api/tasks/from-message", json={
        "group_id": 123, "msg_id": "m1", "title": "任务", "owner": "",
    }).status_code == 401
    assert client.patch("/api/tasks/1", json={
        "title": "任务", "owner": "", "status": "open",
    }).status_code == 401

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    source_time = datetime.now(ZoneInfo("Asia/Shanghai"))
    archive = client.app.state.archive
    archive.ingest([NormalizedMessage(
        msg_id="task-source", group_id=123, sender_qq=1001,
        timestamp=source_time, collected_at=source_time,
        text="小王确认本周提交文档",
    )])
    payload_path = tmp_path / "task-report.json"
    payload_path.write_text(json.dumps({
        "evidence_version": 1, "overview": "", "main_topics": [],
        "conclusions": [], "resources": [],
        "tasks": [{"owner": "小王", "description": "提交文档", "deadline": "本周",
                   "message_ids": ["task-source"]}],
        "open_questions": [],
    }, ensure_ascii=False), encoding="utf-8")
    archive.record_report(group_id=123, report_date=today,
                          markdown_path=tmp_path / "task-report.md",
                          json_path=payload_path, candidate_ids=[])

    client.post("/login", data={"password": "password123"})
    assert "待办收件箱" in client.get("/tasks").text
    suggested = client.get("/api/tasks/suggestions").json()["items"]
    assert len(suggested) == 1
    assert suggested[0]["source_ids"] == ["task-source"]
    key = suggested[0]["key"]
    confirmed = client.post(f"/api/tasks/suggestions/{key}/decision", json={
        "action": "confirm", "title": "提交最终文档", "owner": "小王", "due_date": today,
    })
    assert confirmed.status_code == 200
    task = confirmed.json()["task"]
    assert task["source_ids"] == ["task-source"]
    assert task["due_bucket"] == "today"
    assert client.get("/api/tasks/suggestions").json()["items"] == []
    assert client.get("/api/tasks").json()["counts"]["today"] == 1
    edited = client.patch(f"/api/tasks/{task['task_id']}", json={
        "title": "已提交", "owner": "小王", "due_date": today, "status": "completed",
    })
    assert edited.status_code == 200
    assert edited.json()["task"]["status"] == "completed"
    assert edited.json()["task"]["source_ids"] == ["task-source"]
    assert client.get("/api/tasks?status=completed").json()["items"][0]["title"] == "已提交"
    assert client.post("/api/tasks/from-message", json={
        "group_id": 123, "msg_id": "missing", "title": "伪造", "owner": "",
    }).status_code == 400
    created = client.post("/api/tasks/from-message", json={
        "group_id": 123, "msg_id": "task-source", "title": "从消息创建", "owner": "",
    })
    assert created.status_code == 200
    assert created.json()["task"]["source_ids"] == ["task-source"]


def test_desktop_settings_api_uses_local_bridge_after_login(web_client):
    client, _, _ = web_client

    class FakeBridge:
        def get_settings(self):
            return {"storage_path": "D:/data", "export_path": "D:/exports", "backup_path": "D:/backups", "auto_start": False, "packaged": True}

        def choose_folder(self, kind):
            return "D:/picked" if kind == "storage" else "D:/exports"

        def migrate_storage(self, destination):
            return {"path": destination, "restart_required": True}

        def export_reports(self, destination, format):
            return {"path": destination, "format": format, "reports": 1, "files": 2, "missing": 0}

        def set_auto_start(self, enabled):
            return {"auto_start": enabled}

        def backup_data(self):
            return {"path": "D:/backups/backup.zip"}

        def set_backup_schedule(self, schedule):
            return {"backup_schedule": schedule}

        def choose_backup_file(self):
            return "D:/backups/backup.zip"

        def preview_restore(self, path):
            return {"valid": True, "path": path, "sha256": "a" * 64, "messages": 3,
                    "reports": 1, "knowledge_items": 2}

        def restore_backup(self, path, destination, expected_sha256):
            assert expected_sha256 == "a" * 64
            return {"path": destination, "safety_backup": "D:/backups/safety.zip",
                    "restart_required": True}

        def open_folder(self, kind):
            return {"path": "D:/" + kind}

    assert client.get("/api/desktop-settings").status_code == 401
    client.post("/login", data={"password": "password123"})
    assert client.get("/api/desktop-settings").status_code == 503
    client.app.state.desktop_bridge = FakeBridge()
    assert client.get("/api/desktop-settings").json()["storage_path"] == "D:/data"
    assert client.post("/api/desktop-settings/choose-folder", json={"kind": "storage"}).json() == {"path": "D:/picked"}
    assert client.post("/api/desktop-settings/migrate", json={"destination": "D:/new"}).json()["restart_required"]
    assert client.post("/api/desktop-settings/export", json={"destination": "D:/exports", "format": "both"}).json()["files"] == 2
    assert client.post("/api/desktop-settings/auto-start", json={"enabled": True}).json()["auto_start"]
    assert client.post("/api/desktop-settings/backup", json={}).json()["path"].endswith("backup.zip")
    assert client.post("/api/desktop-settings/backup-schedule", json={"schedule": "weekly"}).json()["backup_schedule"] == "weekly"
    assert client.post("/api/desktop-settings/choose-backup", json={}).json()["path"].endswith("backup.zip")
    assert client.post("/api/desktop-settings/restore-preview", json={"path": "D:/backups/backup.zip"}).json()["messages"] == 3
    assert client.post("/api/desktop-settings/restore", json={"path": "D:/backups/backup.zip", "destination": "D:/restored", "sha256": "a" * 64}).json()["restart_required"]
    assert client.post("/api/desktop-settings/open-folder", json={"kind": "storage"}).json()["path"] == "D:/storage"


def test_desktop_backup_returns_actionable_validation_error(web_client):
    client, _, _ = web_client
    class Bridge:
        def backup_data(self):
            raise ValueError("尚无消息数据库，无法生成可恢复的备份")
    client.app.state.desktop_bridge = Bridge()
    client.post("/login", data={"password": "password123"})
    response = client.post("/api/desktop-settings/backup", json={})
    assert response.status_code == 400
    assert "消息数据库" in response.json()["detail"]


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
    assert "聊天片段会发送到 DeepSeek" in page
    assert "qaHistory = []" in page


def test_report_selection_ignores_stale_detail_responses(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"})
    page = client.get("/reports").text

    assert "reportSelectionToken" in page
    assert "selectionToken !== reportSelectionToken" in page


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
        "provider_priority": ["chatgpt_bridge", "compatible"],
        "bridge_model": "",
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


def test_health_flags_failed_daily_summary_after_successful_message_sync(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    daily = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(daily, "failed", "API key temporarily unavailable")
    sync = archive.start_job("message_sync")
    archive.finish_job(sync, "success")
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    health = client.get("/api/health").json()
    jobs = client.get("/api/jobs").json()["jobs"]

    assert health["latest_job_status"] == "success"
    assert health["latest_daily_status"] == "failed"
    assert health["latest_daily_date"] == "2026-09-27"
    assert jobs[1]["target_date"] == "2026-09-27"


def test_health_shows_latest_report_date_after_older_catchup_finishes(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    recent = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(recent, "failed", "newer report failed")
    older = archive.start_job("daily_digest", target_date="2026-09-26")
    archive.finish_job(older, "success")
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    health = client.get("/api/health").json()

    assert health["latest_daily_date"] == "2026-09-27"
    assert health["latest_daily_status"] == "failed"


def test_scheduler_status_after_restart_uses_latest_report_date(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    recent = archive.start_job("daily_digest", target_date="2026-09-27")
    archive.finish_job(recent, "failed", "newer report failed")
    older = archive.start_job("daily_digest", target_date="2026-09-26")
    archive.finish_job(older, "success")
    app = create_app(
        archive=archive,
        candidates=CandidateService(archive),
        knowledge=KnowledgeWriter(tmp_path / "knowledge"),
        password_hash=PasswordHasher.hash("password123"),
        session_secret="test-secret",
        config=client.app.state.config,
    )
    restarted = TestClient(app)
    restarted.post("/login", data={"password": "password123"}, follow_redirects=False)

    assert restarted.get("/api/scheduler").json()["last_result"]["status"] == "failed"


def test_dashboard_renders_job_errors_with_html_escaping(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"}, follow_redirects=False)

    response = client.get("/overview")

    assert response.status_code == 200
    assert "job-error" in response.text
    assert "escapeHtml(job.error" in response.text
    assert 'id="health-daily"' in response.text


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


def test_groups_api_exposes_sync_failure_for_repair(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    archive.mark_sync_failure(group_id=123, status="adapter_incompatible", error="消息库读取失败")
    client.post("/login", data={"password": "password123"})

    group = client.get("/api/groups").json()["groups"][0]

    assert group["sync_status"] == "adapter_incompatible"
    assert group["sync_error"] == "消息库读取失败"
    assert group["last_sync_attempt"]
    assert group["last_success_at"] is None


def test_discovery_flags_source_message_missing_after_successful_sync(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    source_time = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive = client.app.state.archive
    archive.ingest([NormalizedMessage(
        msg_id="older", group_id=123, sender_qq=1,
        timestamp=source_time - timedelta(days=1), collected_at=source_time,
        text="旧消息",
    )])
    archive.mark_sync(group_id=123, last_timestamp=source_time + timedelta(minutes=1))

    class SourceCollector:
        def __init__(self, **kwargs):
            pass

        def discover_groups(self):
            return [{"group_id": 123, "name": "测试群", "message_count_30d": 2,
                     "latest_message_at": source_time}]

    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", SourceCollector)
    client.post("/login", data={"password": "password123"})

    source = client.get("/api/discover-groups").json()["groups"][0]

    assert source["suspected_gap"] is True
    assert source["source_has_newer_messages"] is True
    assert source["archive_latest_message_at"] is not None


def test_manual_collect_failure_is_reported_and_keeps_previous_cursor(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    archive = client.app.state.archive
    prior = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("UTC"))
    archive.mark_sync(group_id=123, last_timestamp=prior)

    class BrokenCollector:
        def __init__(self, **kwargs):
            pass

        def collect(self, group_id, start, end):
            raise RuntimeError("消息表无法读取")

    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", BrokenCollector)
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21",
    })

    assert response.status_code == 503
    assert "消息表无法读取" in response.json()["detail"]
    state = archive.connection.execute(
        "SELECT last_timestamp, last_success_at, status, error FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert state["last_timestamp"] == prior.isoformat()
    assert state["status"] == "manual_collect_failed"
    assert state["error"] == "消息表无法读取"


def test_repair_refreshes_source_before_collecting_old_range(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    archive = client.app.state.archive
    prior = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("UTC"))
    archive.mark_sync(group_id=123, last_timestamp=prior)
    archive.mark_sync_failure(group_id=123, status="manual_collect_failed", error="此前采集失败")
    calls = []

    def refresh_database(**kwargs):
        calls.append("refresh")
        return SimpleNamespace(success=True, message="已刷新")

    class SourceCollector:
        def __init__(self, **kwargs):
            pass

        def collect(self, group_id, start, end):
            calls.append("collect")
            return [NormalizedMessage(
                msg_id="missed", group_id=group_id, sender_qq=1,
                timestamp=datetime(2026, 9, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
                collected_at=prior, text="遗漏消息",
            )]

    monkeypatch.setattr("qq_digest.refresh.refresh_database", refresh_database)
    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", SourceCollector)
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21", "refresh": True,
    })

    assert response.status_code == 200
    assert response.json()["inserted"] == 1
    assert response.json()["missing_days"] == [{"date": "2026-09-20", "count": 1}]
    assert calls == ["refresh", "collect"]
    assert archive.count_messages(123) == 1
    state = archive.connection.execute(
        "SELECT last_timestamp, last_success_at, status, error FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert state["last_timestamp"] == prior.isoformat()
    assert state["status"] == "manual_repair_completed"
    assert state["error"] == ""
    assert state["last_success_at"]

    repeated = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21", "refresh": True,
    })
    assert repeated.status_code == 200
    assert repeated.json()["inserted"] == 0
    assert repeated.json()["missing_days"] == []


def test_failed_repair_refresh_does_not_collect_stale_source(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    called = []

    def refresh_database(**kwargs):
        return SimpleNamespace(success=False, message="QQ 未运行")

    class SourceCollector:
        def __init__(self, **kwargs):
            pass

        def collect(self, group_id, start, end):
            called.append(True)
            return []

    monkeypatch.setattr("qq_digest.refresh.refresh_database", refresh_database)
    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", SourceCollector)
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21", "refresh": True,
    })

    assert response.status_code == 503
    assert "QQ 未运行" in response.json()["detail"]
    assert called == []


def test_gap_check_reports_missing_days_without_changing_archive(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    stamp = datetime(2026, 9, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai"))

    def refresh_database(**kwargs):
        return SimpleNamespace(success=True, message="已刷新")

    class SourceCollector:
        def __init__(self, **kwargs):
            pass

        def collect(self, group_id, start, end):
            return [NormalizedMessage(
                msg_id="source-only", group_id=group_id, sender_qq=1,
                timestamp=stamp, collected_at=stamp, text="尚未归档",
            )]

    monkeypatch.setattr("qq_digest.refresh.refresh_database", refresh_database)
    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", SourceCollector)
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21",
        "refresh": True, "dry_run": True,
    })

    assert response.status_code == 200
    assert response.json()["missing_days"] == [{"date": "2026-09-20", "count": 1}]
    assert response.json()["inserted"] == 0
    assert client.app.state.archive.count_messages(123) == 0
    assert client.app.state.archive.connection.execute(
        "SELECT 1 FROM sync_state WHERE group_id=123"
    ).fetchone() is None


def test_failed_gap_check_does_not_change_sync_state(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    archive = client.app.state.archive
    prior = datetime(2026, 9, 27, 12, tzinfo=ZoneInfo("UTC"))
    archive.mark_sync(group_id=123, last_timestamp=prior)
    before = dict(archive.connection.execute(
        "SELECT * FROM sync_state WHERE group_id=123"
    ).fetchone())
    monkeypatch.setattr(
        "qq_digest.refresh.refresh_database",
        lambda **kwargs: SimpleNamespace(success=False, message="QQ 未运行"),
    )
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21",
        "refresh": True, "dry_run": True,
    })

    assert response.status_code == 503
    assert dict(archive.connection.execute(
        "SELECT * FROM sync_state WHERE group_id=123"
    ).fetchone()) == before


def test_first_manual_repair_records_success_without_creating_auto_cursor(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"

    class EmptyCollector:
        def __init__(self, **kwargs):
            pass

        def collect(self, group_id, start, end):
            return []

    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", EmptyCollector)
    client.post("/login", data={"password": "password123"})

    response = client.post("/api/collect", json={
        "group_id": 123, "start": "2026-09-20", "end": "2026-09-21",
    })

    assert response.status_code == 200
    archive = client.app.state.archive
    state = archive.connection.execute(
        "SELECT last_timestamp, last_success_at, status FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert state["last_timestamp"] is None
    assert state["last_success_at"]
    assert state["status"] == "manual_repair_completed"


def test_manual_collect_does_not_advance_periodic_sync_cursor(web_client, monkeypatch):
    client, _, _ = web_client
    config = client.app.state.config
    config.ntqq.enabled = True
    config.ntqq.db_dir = "unused-in-test"
    archive = client.app.state.archive
    prior = datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=2)
    archive.mark_sync(group_id=123, last_timestamp=prior)

    class EmptyCollector:
        def __init__(self, **kwargs):
            pass

        def collect(self, group_id, start, end):
            return []

    monkeypatch.setattr("qq_digest.web.app.NTQQCollector", EmptyCollector)
    client.post("/login", data={"password": "password123"})
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    response = client.post("/api/collect", json={
        "group_id": 123, "start": today, "end": today,
    })

    assert response.status_code == 200
    state = archive.connection.execute(
        "SELECT last_timestamp FROM sync_state WHERE group_id=123"
    ).fetchone()
    assert state["last_timestamp"] == prior.astimezone(ZoneInfo("UTC")).isoformat()


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
    assert "测试群摘要" in detail.json()["markdown"]
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


def test_report_claim_sources_open_only_cited_messages_in_report_scope(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    local_tz = ZoneInfo("Asia/Shanghai")
    messages = [
        NormalizedMessage(msg_id=msg_id, group_id=123, sender_qq=sender,
                          timestamp=datetime(2026, 9, day, hour, minute, tzinfo=local_tz),
                          collected_at=datetime(2026, 9, day, hour, minute, tzinfo=local_tz),
                          text=text)
        for msg_id, day, hour, minute, sender, text in [
            ("before", 2, 11, 59, 1, "开始讨论"),
            ("cited", 2, 12, 0, 2, "决定采用方案 A"),
            ("after", 2, 12, 1, 3, "明天复查"),
            ("other-day", 3, 12, 0, 4, "另一日内容"),
        ]
    ]
    archive.ingest(messages)
    markdown_path = tmp_path / "cited.md"
    json_path = tmp_path / "cited.json"
    markdown_path.write_text("# 测试群日报", encoding="utf-8")
    json_path.write_text(json.dumps({
        "evidence_version": 1,
        "conclusions": [
            {"text": "采用方案 A", "message_ids": ["cited"]},
            {"text": "跨日伪引用", "message_ids": ["other-day"]},
        ],
    }), encoding="utf-8")
    report_id = archive.record_report(
        group_id=123, report_date="2026-09-02", markdown_path=markdown_path,
        json_path=json_path, candidate_ids=[],
    )
    url = f"/api/reports/daily/{report_id}/sources/cited"
    assert client.get(url).status_code == 401
    client.post("/login", data={"password": "password123"})

    detail = client.get(f"/api/reports/daily/{report_id}").json()
    assert detail["evidence_status"] == "available"
    assert detail["evidence_items"][0]["source_ids"] == ["cited"]
    assert detail["evidence_items"][1]["source_ids"] == []
    assert detail["evidence_items"][1]["status"] == "unverified"
    context = client.get(url)
    assert context.status_code == 200
    assert [row["msg_id"] for row in context.json()["messages"]][:3] == [
        "before", "cited", "after"
    ]
    assert client.get(f"/api/reports/daily/{report_id}/sources/before").status_code == 404
    assert client.get(f"/api/reports/daily/{report_id}/sources/other-day").status_code == 404


def test_legacy_report_detail_explains_missing_citations(web_client, tmp_path):
    client, _, _ = web_client
    markdown_path = tmp_path / "legacy.md"
    markdown_path.write_text("# 旧日报", encoding="utf-8")
    report_id = client.app.state.archive.record_report(
        group_id=123, report_date="2026-09-02", markdown_path=markdown_path,
        json_path=tmp_path / "missing.json", candidate_ids=[],
    )
    client.post("/login", data={"password": "password123"})

    detail = client.get(f"/api/reports/daily/{report_id}").json()

    assert detail["evidence_status"] == "legacy"
    assert detail["evidence_items"] == []


def test_report_page_has_per_claim_source_controls(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"})

    page = client.get("/reports").text

    assert 'id="report-evidence"' in page
    assert 'id="report-source-context"' in page
    assert "function openReportSource(" in page
    assert "/sources/" in page


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

    dashboard = client.get("/overview")
    groups = client.get("/groups")

    assert dashboard.status_code == 200
    assert "运行记录" in dashboard.text
    assert "function escapeHtml" in dashboard.text
    assert "function dailyResultMessage" in dashboard.text
    assert "data.detail.message" in dashboard.text
    assert "partial_success: '部分成功'" in dashboard.text
    assert groups.status_code == 200
    assert 'id="group-search"' in groups.text
    assert "扫描本地群聊" in groups.text
    assert 'id="group-keywords"' in groups.text
    assert 'id="group-window"' in groups.text


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

    dashboard = client.get("/overview")

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

    assert 'class="table responsive-table group-reading-table"' in groups.text
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
    assert 'id="range-open-btn"' in reports.text and 'id="report-run-btn"' not in reports.text
    assert 'id="range-time-mode"' in reports.text
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


def test_candidate_source_context_api_is_group_scoped_and_detail_only(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    archive.upsert_groups([GroupConfig(group_id=999, name="其他群")])
    stamp = datetime(2026, 9, 1, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    archive.ingest([
        NormalizedMessage(msg_id=msg_id, group_id=group_id, sender_qq=1001,
                          timestamp=stamp + timedelta(minutes=index),
                          collected_at=stamp + timedelta(minutes=index), text=body)
        for index, (msg_id, group_id, body) in enumerate([
            ("m1", 123, "前文"), ("m2", 123, "引用原文"),
            ("m3", 123, "后文"), ("m2", 999, "其他群秘密"),
        ])
    ])
    candidate_id = client.app.state.candidates.create(
        group_id=123, created_date="2026-09-01", candidate_type="experience",
        title="上下文候选", reason="测试", excerpt="旧摘录", message_ids=["m2"],
    )

    assert client.get(f"/api/candidates/{candidate_id}").status_code == 401
    client.post("/login", data={"password": "password123"})
    detail = client.get(f"/api/candidates/{candidate_id}")
    listed = client.get("/api/candidates").json()["candidates"]

    assert detail.status_code == 200
    data = detail.json()
    assert [item["msg_id"] for item in data["source_context"]] == ["m1", "m2", "m3"]
    assert [item["msg_id"] for item in data["source_context"] if item["is_cited"]] == ["m2"]
    assert "其他群秘密" not in detail.text
    assert data["missing_source_count"] == 0
    assert data["excerpt"] == "旧摘录"
    assert data["message_ids"] == ["m2"]
    assert all("source_context" not in item for item in listed)


def test_candidate_source_context_api_retains_excerpt_when_message_missing(web_client):
    client, _, _ = web_client
    candidate_id = client.app.state.candidates.create(
        group_id=123, created_date="2026-09-01", candidate_type="experience",
        title="历史候选", reason="测试", excerpt="保存的原摘录", message_ids=["gone"],
    )
    client.post("/login", data={"password": "password123"})

    detail = client.get(f"/api/candidates/{candidate_id}").json()

    assert detail["source_context"] == []
    assert detail["missing_source_count"] == 1
    assert detail["excerpt"] == "保存的原摘录"


def test_candidate_context_drawer_renders_plain_text_and_fallback(web_client):
    client, _, _ = web_client
    client.post("/login", data={"password": "password123"})

    page = client.get("/candidates").text

    assert 'class="candidate-source-context"' in page
    assert "body.textContent = item.text" in page
    assert "item.is_cited" in page
    assert "c.missing_source_count" in page
    assert "c.source_context_truncated" in page
    assert "c.excerpt || '没有可用的归档原消息'" in page
    assert "candidateViewToken" in page


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
