from datetime import datetime, time, timedelta, timezone
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
def browsing_client(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=11, name="研发群"),
                           GroupConfig(group_id=22, name="产品群", enabled=False)])
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
    # Do not enter lifespan: these requests only read synthetic archive data.
    client = TestClient(app)
    yield client, archive, config
    client.close()
    archive.close()


def add_message(archive, msg_id, timestamp, *, group_id=11, text="归档消息", sender=123):
    archive.ingest([NormalizedMessage(
        msg_id=msg_id, group_id=group_id, timestamp=timestamp, text=text,
        sender_qq=sender, collected_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )])


def login(client):
    assert client.post("/login", data={"password": "password123"}).status_code == 200


def test_messages_require_login_even_for_invalid_query(browsing_client):
    client, _, _ = browsing_client
    assert client.get("/api/messages").status_code == 401
    assert client.get("/api/messages?page=0&date_from=bad").status_code == 401


def test_messages_have_search_compatible_fields_and_full_text(browsing_client):
    client, archive, _ = browsing_client
    body = "多行  内容\n" + "x" * 240 + " %_' OR 1=1 --"
    stamp = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
    add_message(archive, "quoted'_%", stamp, text=body, sender=None)
    login(client)
    response = client.get("/api/messages?date_from=2026-10-02&date_to=2026-10-02")
    assert response.status_code == 200
    result = response.json()
    assert (result["total"], result["page"], result["page_size"]) == (1, 1, 20)
    item = result["results"][0]
    assert item == {
        "kind": "message", "id": "quoted'_%", "group_id": 11,
        "group_name": "研发群", "date": "2026-10-02", "sort_at": stamp.isoformat(),
        "title": "研发群 · 未知发送者", "snippet": "多行 内容 " + "x" * 204 + "…",
        "text": body, "url": "", "timestamp": stamp.isoformat(),
        "sender_qq": None, "message_type": "text",
    }


def test_messages_filter_before_stable_pagination_including_duplicate_ids(browsing_client):
    client, archive, _ = browsing_client
    stamp = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    for group_id in (22, 11):
        for msg_id in ("b", "a"):
            add_message(archive, msg_id, stamp, group_id=group_id)
    add_message(archive, "newest", stamp + timedelta(hours=1), group_id=22)
    add_message(archive, "too-old", stamp - timedelta(days=10))
    login(client)
    params = {"date_from": "2026-10-02", "date_to": "2026-10-02", "page_size": 2}
    pages = [client.get("/api/messages", params={**params, "page": page}).json()
             for page in (1, 2, 3, 4)]
    assert [page["total"] for page in pages] == [5] * 4
    assert [(row["group_id"], row["id"]) for page in pages for row in page["results"]] == [
        (22, "newest"), (11, "a"), (11, "b"), (22, "a"), (22, "b"),
    ]
    filtered = client.get("/api/messages", params={**params, "group_id": 22, "page": 2}).json()
    assert filtered["total"] == 3
    assert [row["id"] for row in filtered["results"]] == ["b"]
    unknown = client.get("/api/messages", params={**params, "group_id": 999}).json()
    assert unknown["total"] == 0 and unknown["results"] == []


def test_messages_same_day_uses_timezone_half_open_boundaries(browsing_client):
    client, archive, config = browsing_client
    start = datetime(2026, 10, 2, tzinfo=ZoneInfo(config.summary.timezone))
    for msg_id, stamp in [("before", start - timedelta(microseconds=1)),
                          ("first", start),
                          ("last", start + timedelta(days=1, microseconds=-1)),
                          ("after", start + timedelta(days=1))]:
        add_message(archive, msg_id, stamp)
    login(client)
    result = client.get("/api/messages?date_from=2026-10-02&date_to=2026-10-02").json()
    assert result["total"] == 2
    assert [row["id"] for row in result["results"]] == ["last", "first"]
    assert all(row["date"] == "2026-10-02" for row in result["results"])


def test_messages_default_seven_days_and_one_sided_dates(browsing_client):
    client, archive, config = browsing_client
    zone = ZoneInfo(config.summary.timezone)
    today = datetime.now(zone).date()
    for offset in range(-10, 3):
        stamp = datetime.combine(today + timedelta(days=offset), time(12), zone)
        add_message(archive, str(offset), stamp)
    login(client)
    defaults = client.get("/api/messages").json()
    assert defaults["total"] == 7
    assert [row["id"] for row in defaults["results"]] == [str(n) for n in range(0, -7, -1)]
    to_only = client.get("/api/messages", params={"date_to": (today - timedelta(days=2)).isoformat()}).json()
    assert [row["id"] for row in to_only["results"]] == [str(n) for n in range(-2, -9, -1)]
    from_only = client.get("/api/messages", params={"date_from": (today - timedelta(days=9)).isoformat()}).json()
    assert from_only["total"] == 10
    future = client.get("/api/messages", params={"date_from": (today + timedelta(days=2)).isoformat()}).json()
    assert [row["id"] for row in future["results"]] == ["2"]


@pytest.mark.parametrize("params", [
    {"date_from": "2026-10-02", "date_to": "2026-10-01"},
    {"date_from": "2026-1-1"}, {"date_to": "2026-02-30"},
    {"date_to": "20261002"}, {"date_to": "2026-10-02T00:00:00"},
    {"date_from": "' OR 1=1 --"}, {"group_id": "11 OR 1=1"},
    {"page": "0"}, {"page": "no"}, {"page_size": "0"}, {"page_size": "101"},
    {"date_to": "9999-12-31"},
])
def test_messages_reject_invalid_parameters(browsing_client, params):
    client, _, _ = browsing_client
    login(client)
    assert client.get("/api/messages", params=params).status_code == 422


def test_messages_are_read_only_and_do_not_collect_or_call_ai(browsing_client, monkeypatch):
    import qq_digest.web.app as web_module

    client, archive, _ = browsing_client

    def forbidden(*args, **kwargs):
        pytest.fail("message browsing must not collect, generate, or open another archive")

    monkeypatch.setattr(web_module, "NTQQCollector", forbidden)
    monkeypatch.setattr(web_module, "AIClient", forbidden)
    monkeypatch.setattr(Archive, "open", forbidden)
    before = archive.connection.total_changes
    login(client)
    response = client.get("/api/messages")
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert archive.connection.total_changes == before
