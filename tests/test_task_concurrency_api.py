"""Task PATCH contracts exercised against the real route and synthetic SQLite data."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qq_digest.task_inbox import TaskInboxService
from qq_digest.web.task_routes import add_task_routes
from tests.test_task_inbox import NOW, setup_inbox


@pytest.fixture
def task_client(tmp_path):
    archive, inbox = setup_inbox(tmp_path)
    task = inbox.create_from_message(group_id=123, msg_id="m1", title="原内容",
                                     owner="原负责人", due_date="2026-10-01", now=NOW)
    app = FastAPI()
    add_task_routes(app, archive=archive, config=None, templates=None, require_login=lambda request: None)
    with TestClient(app) as client:
        yield client, task, TaskInboxService(archive, timezone_name="Asia/Shanghai")
    archive.connection.close()


def test_patch_status_only_preserves_a_newer_edit(task_client):
    client, original, inbox = task_client
    path = f"/api/tasks/{original['task_id']}"
    edited = client.patch(path, json={"title": "另一窗口的新内容", "owner": "新负责人",
                                     "due_date": "2026-10-02", "status": "open", "expected_revision": 0})
    assert edited.status_code == 200
    done = client.patch(path, json={"status": "completed"})
    assert done.status_code == 200
    assert done.json()["task"]["title"] == "另一窗口的新内容"
    assert done.json()["task"]["owner"] == "新负责人"
    assert done.json()["task"]["due_date"] == "2026-10-02"
    assert done.json()["task"]["revision"] == 2
    assert inbox.list_tasks(status="all")["items"][0] == done.json()["task"]


def test_stale_full_edit_returns_409_without_mutating_task(task_client):
    client, original, _ = task_client
    path = f"/api/tasks/{original['task_id']}"
    fields = {"title": "当前内容", "status": "open", "expected_revision": 0}
    first = client.patch(path, json=fields)
    assert first.status_code == 200
    stale = client.patch(path, json=dict(fields, title="陈旧窗口草稿"))
    assert stale.status_code == 409
    assert "已被修改" in stale.json()["detail"]
    assert client.get("/api/tasks?status=all").json()["items"] == [first.json()["task"]]


@pytest.mark.parametrize("fields", [
    {"title": "无版本保护", "status": "open"},
    {"title": "坏版本", "status": "open", "expected_revision": True},
    {"title": "坏版本", "status": "open", "expected_revision": "0"},
    {"title": "坏版本", "status": "open", "expected_revision": 0.0},
    {"title": "坏版本", "status": "open", "expected_revision": -1},
    {"status": "completed", "owner": "不完整编辑"},
    {"status": "completed", "due_date": None},
])
def test_patch_cannot_bypass_revision_contract(task_client, fields):
    client, task, _ = task_client
    response = client.patch(f"/api/tasks/{task['task_id']}", json=fields)
    assert response.status_code == 422
    assert client.get("/api/tasks?status=all").json()["items"][0]["title"] == task["title"]


def test_missing_task_status_update_returns_validation_error(task_client):
    client, _, _ = task_client
    assert client.patch("/api/tasks/999", json={"status": "completed"}).status_code == 400
