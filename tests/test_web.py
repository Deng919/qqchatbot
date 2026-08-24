from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.models import GroupConfig
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
    )
    client = TestClient(app)
    return client, candidate_id, tmp_path / "knowledge"


def test_candidates_requires_login(web_client):
    client, _, _ = web_client
    response = client.get("/candidates", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


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
