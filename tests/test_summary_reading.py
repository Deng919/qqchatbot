from __future__ import annotations

import json
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
from qq_digest.models import GroupConfig
from qq_digest.web.app import create_app
from qq_digest.web.auth import PasswordHasher


@pytest.fixture
def reading_client(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群"),
                           GroupConfig(group_id=22, name="产品群")])
    config = Config(
        data_dir=tmp_path, archive_path=tmp_path / "archive.sqlite",
        report_dir=tmp_path / "reports", knowledge_dir=tmp_path / "knowledge",
        work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
        security=SecurityConfig(web_password_hash="x" * 32),
        ai=AIConfig(base_url="https://invalid.example/v1", model="test", api_key_env="TEST_KEY"),
        summary=SummaryConfig(timezone="Pacific/Kiritimati"),
    )
    app = create_app(archive=archive, candidates=CandidateService(archive),
                     knowledge=KnowledgeWriter(tmp_path / "knowledge"),
                     password_hash=PasswordHasher.hash("password123"),
                     session_secret="test-secret", config=config)
    client = TestClient(app)  # No lifespan: this fixture never starts collectors or schedulers.
    yield client, archive, config
    client.close()
    archive.close()


def add_report(archive, root, *, group_id=11, report_date="2026-09-29", payload=None):
    path = root / f"{group_id}-{report_date}.json"
    if payload is None:
        payload = {"overview": "当天的中文概览", "main_topics": [
            {"topic": f"话题{index}", "summary": f"摘要{index}"} for index in range(4)
        ]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_id = archive.record_report(group_id=group_id, report_date=report_date,
                                      json_path=path, markdown_path=path.with_suffix(".md"),
                                      candidate_ids=[])
    return report_id, path


def login(client):
    client.post("/login", data={"password": "password123"})


def test_reading_endpoints_require_login(reading_client):
    client, _, _ = reading_client
    assert client.get("/api/summary-reading").status_code == 401
    assert client.post("/api/summary-reading/visit").status_code == 401
    assert client.post("/api/summary-reading/read", json={"key": "a" * 64, "read": True}).status_code == 401


def test_core_reading_works_with_optional_features_disabled_and_keeps_unread(reading_client, tmp_path):
    client, archive, config = reading_client
    today = datetime.now(ZoneInfo(config.summary.timezone)).date().isoformat()
    report_id, _ = add_report(archive, tmp_path, report_date=today)
    login(client)
    state = client.get("/api/features").json()
    values = {name: False for name in state["values"]}
    assert client.put("/api/features", json={"values": values, "expected_revision": state["revision"]}).status_code == 200
    result = client.get("/api/summary-reading").json()
    assert result["total"] == result["unread"] == result["new_count"] == 4
    assert result["items"][0]["report_id"] == report_id
    first_visit = client.post("/api/summary-reading/visit").json()
    assert first_visit["previous_viewed_at"] is None
    second_visit = client.post("/api/summary-reading/visit").json()
    assert second_visit["previous_viewed_at"] == first_visit["viewed_at"]
    assert client.get("/api/summary-reading?read_filter=unread").json()["total"] == 4
    key = result["items"][0]["key"]
    assert client.post("/api/summary-reading/read", json={"key": key, "read": True}).json() == {"key": key, "read": True}
    assert client.get("/api/summary-reading?read_filter=unread").json()["total"] == 3
    assert client.get("/api/summary-reading?read_filter=new").json()["total"] == 4
    assert client.post("/api/summary-reading/read", json={"key": key, "read": False}).status_code == 200
    assert client.get("/api/summary-reading").json()["unread"] == 4


@pytest.mark.parametrize("query", [
    "date_from=2026-09-30&date_to=2026-09-29", "date_from=2026-9-29", "date_to=invalid",
    "since=2026-09-29T00:00:00", "read_filter=read", "page=0", "page_size=101",
])
def test_reading_validates_selection(reading_client, query):
    client, _, _ = reading_client
    login(client)
    assert client.get("/api/summary-reading?" + query).status_code == 422


def test_reading_selection_and_legacy_key_compatibility(reading_client, tmp_path):
    client, archive, _ = reading_client
    add_report(archive, tmp_path, report_date="2026-09-29")
    add_report(archive, tmp_path, group_id=22, report_date="2026-09-29")
    broken_id, broken = add_report(archive, tmp_path, report_date="2026-09-28")
    broken.write_text("not json", encoding="utf-8")
    login(client)
    params = {"date_from": "2026-09-28", "date_to": "2026-09-29", "group_id": 11,
              "page_size": 1}
    result = client.get("/api/summary-reading", params=params).json()
    assert result["total"] == 4 and result["skipped_reports"] == 1
    assert result["items"][0]["group_id"] == 11
    # Use the old since API with a cutoff predating all synthetic reports.
    old = client.get("/api/catchup", params={"scope": "since", "since": "2000-01-01T00:00:00Z"}).json()
    assert result["items"][0]["key"] == next(item["key"] for item in old["items"] if item["group_id"] == 11)
    assert client.post("/api/summary-reading/read", json={"key": "bad", "read": True}).status_code == 422
    assert client.post("/api/summary-reading/read", json={"key": "a" * 64, "read": "false"}).status_code == 422


def test_core_reads_multi_day_points_without_id_or_read_collisions(reading_client,tmp_path):
    client,archive,_=reading_client
    daily_id,_=add_report(archive,tmp_path,report_date='2026-09-29')
    path=tmp_path/'range-points.json'
    payload={'overview':'一周概览','main_topics':[{'topic':'发布计划','summary':'按周完成检查'}]}
    path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    range_id=archive.record_manual_report(group_id=11,start_date='2026-09-23',end_date='2026-09-29',
        detail_mode='adaptive',effective_template='adaptive',input_fingerprint='synthetic',source_message_count=2,
        markdown_path=path.with_suffix('.md'),json_path=path,candidate_ids=[])
    assert range_id==daily_id
    login(client)
    scope={'date_from':'2026-09-23','date_to':'2026-09-29','group_id':11}
    items=client.get('/api/summary-reading',params=scope).json()['items']
    range_item=next(item for item in items if item['report_kind']=='range')
    assert range_item['date_label']=='2026-09-23 至 2026-09-29'
    assert range_item['report_url']==f'/reports?kind=range&id={range_id}'
    assert len({item['key'] for item in items})==5
    assert client.post('/api/summary-reading/read',json={'key':range_item['key'],'read':True}).status_code==200
    unread=client.get('/api/summary-reading',params={**scope,'read_filter':'unread'}).json()['items']
    assert len(unread)==4 and all(item['report_kind']=='daily' for item in unread)
    assert client.get('/api/summary-reading',params={**scope,'date_from':'2026-09-29'}).json()['total']==4
    assert client.get('/api/summary-reading',params={**scope,'group_id':22}).json()['total']==0
    # Legacy catch-up does not add multi-day summaries implicitly.
    assert client.get('/api/catchup',params={'scope':'since','since':'2000-01-01T00:00:00Z'}).json()['total']==4


def test_multi_group_selection_is_kept_in_both_reading_views(reading_client,tmp_path):
    client,archive,_=reading_client
    archive.upsert_groups([GroupConfig(group_id=33,name='未选择的群')])
    for group in (11,22,33):add_report(archive,tmp_path,group_id=group)
    login(client)
    for endpoint in ('/api/summary-reading','/api/reports'):
        params=[('date_from','2026-09-29'),('date_to','2026-09-29'),('group_ids',11),('group_ids',22)]
        response=client.get(endpoint,params=params)
        assert response.status_code==200,response.text
        values=response.json().get('items',response.json().get('reports'))
        assert {item['group_id'] for item in values}=={11,22}


def test_multi_day_regeneration_is_new_since_previous_visit(reading_client,tmp_path):
    client,archive,_=reading_client
    path=tmp_path/'range-updated.json'
    path.write_text(json.dumps({'main_topics':[{'topic':'更新','summary':'新版本内容'}]}),encoding='utf-8')
    rid=archive.record_manual_report(group_id=11,start_date='2026-09-23',end_date='2026-09-29',
        detail_mode='adaptive',effective_template='adaptive',input_fingerprint='x',source_message_count=1,
        markdown_path=path.with_suffix('.md'),json_path=path,candidate_ids=[])
    archive.connection.execute('UPDATE manual_reports SET created_at=?,updated_at=? WHERE manual_report_id=?',
        ('2026-09-29T01:00:00+00:00','2026-09-29T03:00:00+00:00',rid));archive.connection.commit()
    login(client)
    result=client.get('/api/summary-reading',params={'date_from':'2026-09-23','date_to':'2026-09-29',
        'since':'2026-09-29T02:00:00Z','read_filter':'new'}).json()
    assert result['total']==1


def test_report_previews_are_extracted_only_for_current_page(reading_client, tmp_path, monkeypatch):
    client, archive, _ = reading_client
    first_id, first_path = add_report(archive, tmp_path, report_date="2026-09-29")
    second_id, second_path = add_report(archive, tmp_path, report_date="2026-09-28")
    second_path.write_text("broken", encoding="utf-8")
    login(client)
    first = client.get("/api/reports?page_size=1").json()
    assert first["reports"][0]["preview"] == {
        "overview": "当天的中文概览", "points": [f"话题{index}：摘要{index}" for index in range(3)],
        "available": True,
    }
    assert first["reports"][0]["report_key"] == f"daily:{first_id}"
    second = client.get("/api/reports?page_size=1&page=2").json()
    assert second["reports"][0]["report_id"] == second_id
    assert second["reports"][0]["preview"] == {"overview": "", "points": [], "available": False}
    from qq_digest.report_previews import load_report_preview
    calls = []
    def tracked(path):
        calls.append(Path(path))
        return load_report_preview(path)
    monkeypatch.setattr("qq_digest.web.app.load_report_preview", tracked)
    client.get("/api/reports?page_size=1&page=2")
    assert calls == [second_path]


@pytest.mark.parametrize("payload,expected", [
    ({}, {"overview": "", "points": [], "available": False}),
    ({"overview": ["不应构造正文"], "main_topics": [{"topic": {}, "summary": 8}]},
     {"overview": "", "points": [], "available": False}),
    ({"overview": "  真实概览  ", "main_topics": None},
     {"overview": "真实概览", "points": [], "available": True}),
    ({"summary": {"overview": "范围概览", "main_topics": [{"topic": "讨论", "summary": "范围话题"}]},
      "diagnostics": {"warnings": ["不能成为正文"]}},
     {"overview": "范围概览", "points": ["讨论：范围话题"], "available": True}),
    ({"main_topics": [{"topic": "原有话题"}, {"summary": "原有摘要"}]},
     {"overview": "", "points": ["原有话题", "原有摘要"], "available": True}),
    ([], {"overview": "", "points": [], "available": False}),
])
def test_preview_does_not_fabricate_text(tmp_path, payload, expected):
    from qq_digest.report_previews import load_report_preview
    path = tmp_path / "preview.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert load_report_preview(path) == expected


def test_reading_rejects_key_with_trailing_newline(reading_client):
    client, _, _ = reading_client
    login(client)
    assert client.post("/api/summary-reading/read", json={
        "key": "a" * 64 + "\n", "read": True,
    }).status_code == 422


@pytest.mark.parametrize("status", ["success", "partial_success"])
@pytest.mark.parametrize("mode,started_at,expected_date", [
    ("previous_day", "2026-10-03T00:01:00+08:00", "2026-10-02"),
    ("today", "2026-10-02T23:59:00+08:00", "2026-10-02"),
    ("today", "2026-10-02T16:01:00+00:00", "2026-10-03"),
])
def test_daily_generation_returns_actual_window_date(
    reading_client, monkeypatch, status, mode, started_at, expected_date,
):
    client, _, config = reading_client
    config.summary.timezone = "Asia/Shanghai"
    config.summary.window_mode = mode
    started = datetime.fromisoformat(started_at)
    pipeline_times = []

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            value = started if cls.calls == 0 else started + timedelta(minutes=2)
            cls.calls += 1
            return value.astimezone(tz) if tz else value

    class FakePipeline:
        def __init__(self, **kwargs):
            pass

        def run_daily(self, scheduled_at, **kwargs):
            pipeline_times.append(scheduled_at)
            return SimpleNamespace(status=status, groups_processed=1, messages_inserted=2,
                                   report_paths=["synthetic-report"], candidate_ids=[],
                                   succeeded_groups=[11], skipped_groups=[], failed_groups=[])

    monkeypatch.setattr("qq_digest.ai.factory.build_ai_client",
                        lambda cfg: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr("qq_digest.pipeline.DailyPipeline", FakePipeline)
    monkeypatch.setattr("qq_digest.web.app.datetime", Clock)
    login(client)
    response = client.post("/api/run-daily")
    assert response.status_code == 200
    result = response.json()
    assert result["report_date"] == expected_date
    assert result["status"] == status
    assert result["succeeded_groups"] == [11]
    assert result["reports"] == 1
    assert pipeline_times == [started]
