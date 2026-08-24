from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig


def make_service(tmp_path) -> CandidateService:
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    return CandidateService(archive)


def candidate_kwargs() -> dict:
    return {
        "group_id": 123,
        "created_date": "2026-08-24",
        "candidate_type": "resource",
        "title": "站点",
        "link": "https://example.com",
        "content": "",
        "reason": "高质量教程",
        "excerpt": "看这个站点",
        "message_ids": ["m1"],
    }


def test_candidate_lifecycle(tmp_path):
    service = make_service(tmp_path)
    candidate_id = service.create(**candidate_kwargs())

    assert service.pending(group_id=123)[0].candidate_id == candidate_id
    service.confirm(candidate_id)
    assert service.get(candidate_id).status == "confirmed"
    service.update_status(candidate_id, "pending", ignore_reason="")
    service.ignore(candidate_id, "重复")
    assert service.get(candidate_id).ignore_reason == "重复"


def test_candidate_create_is_idempotent(tmp_path):
    service = make_service(tmp_path)

    first = service.create(**candidate_kwargs())
    second = service.create(**candidate_kwargs())

    assert first == second
    count = service.archive.connection.execute(
        "SELECT COUNT(*) AS total FROM candidates"
    ).fetchone()["total"]
    assert count == 1
