from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from qq_digest.models import NormalizedMessage
from tests.test_web import web_client  # noqa: F401 - shared isolated web fixture


def _login(client):
    assert client.post("/login", data={"password": "password123"}).status_code == 200


def test_health_endpoint_and_authenticated_operational_status(web_client):
    client, _, _ = web_client
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/health").status_code == 401
    _login(client)
    status = client.get("/api/health")
    assert status.status_code == 200
    assert status.json()["service"] == "running"
    assert status.json()["archive"] == "ready"


def test_global_search_finds_messages_reports_and_confirmed_knowledge(web_client, tmp_path):
    client, candidate_id, _ = web_client
    assert client.get("/api/search", params={"q": "向日葵"}).status_code == 401
    timestamp = datetime(2026, 9, 21, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    client.app.state.archive.ingest([NormalizedMessage(
        msg_id="search-message", group_id=123, sender_qq=1001,
        timestamp=timestamp, collected_at=timestamp, text="向日葵部署说明",
    )])
    report_path = tmp_path / "report.md"
    report_path.write_text("# 日报\n向日葵项目已经上线", encoding="utf-8")
    client.app.state.archive.record_report(
        group_id=123, report_date="2026-09-21", markdown_path=report_path,
        json_path=tmp_path / "report.json", candidate_ids=[],
    )
    _login(client)
    assert client.patch(f"/api/candidates/{candidate_id}", json={
        "title": "向日葵资料", "link": "https://example.com", "content": "部署资料",
        "reason": "可供复用", "excerpt": "看这个站点", "candidate_type": "resource",
    }).status_code == 200
    assert client.post(f"/candidates/{candidate_id}/confirm").status_code == 200

    response = client.get("/api/search", params={"q": "向日葵"})
    assert response.status_code == 200
    assert {item["kind"] for item in response.json()["results"]} == {
        "message", "report", "knowledge",
    }
    filtered = client.get("/api/search", params={"q": "向日葵", "kind": "message", "group_id": 123})
    assert [item["kind"] for item in filtered.json()["results"]] == ["message"]
    assert client.get("/api/search", params={"q": " "}).status_code == 422
    context = client.get("/api/search/messages/123/search-message/context")
    assert context.status_code == 200
    assert any(row["text"] == "向日葵部署说明" for row in context.json()["messages"])
    assert client.get("/api/search/messages/123/unknown/context").status_code == 404
    assert client.get("/api/search", params={"q": "向日葵", "date_from": "2027-01-01"}).json()["total"] == 0


def test_candidate_can_be_edited_then_undone_after_confirmation(web_client):
    client, candidate_id, knowledge_dir = web_client
    _login(client)
    payload = {
        "title": "修订后的标题", "link": "https://example.com/new", "content": "新的正文",
        "reason": "更准确的理由", "excerpt": "对应原文", "candidate_type": "resource",
    }
    assert client.patch(f"/api/candidates/{candidate_id}", json=payload).status_code == 200
    assert client.get(f"/api/candidates/{candidate_id}").json()["title"] == payload["title"]
    assert client.post(f"/candidates/{candidate_id}/confirm").status_code == 200
    knowledge_path = knowledge_dir / "resources.md"
    assert payload["title"] in knowledge_path.read_text(encoding="utf-8")
    assert client.patch(f"/api/candidates/{candidate_id}", json=payload).status_code == 409

    assert client.post(f"/api/candidates/{candidate_id}/undo").status_code == 200
    assert client.get(f"/api/candidates/{candidate_id}").json()["status"] == "pending"
    assert payload["title"] not in knowledge_path.read_text(encoding="utf-8")
    assert client.post(f"/api/candidates/{candidate_id}/undo").status_code == 409


def test_reports_support_filters_and_pagination(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    for day in range(1, 5):
        path = tmp_path / f"report-{day}.md"
        path.write_text(f"报告 {day}", encoding="utf-8")
        archive.record_report(
            group_id=123, report_date=f"2026-09-{day:02}",
            markdown_path=path, json_path=tmp_path / f"report-{day}.json", candidate_ids=[],
        )
    _login(client)
    first = client.get("/api/reports", params={"page": 1, "page_size": 2}).json()
    second = client.get("/api/reports", params={"page": 2, "page_size": 2}).json()
    assert first["total"] == 4
    assert len(first["reports"]) == len(second["reports"]) == 2
    assert {r["report_id"] for r in first["reports"]}.isdisjoint(
        {r["report_id"] for r in second["reports"]}
    )
    filtered = client.get("/api/reports", params={
        "group_id": 123, "kind": "daily", "date_from": "2026-09-02", "date_to": "2026-09-03",
    }).json()
    assert filtered["total"] == 2
    assert client.get("/api/reports", params={"page": 0}).status_code == 422
