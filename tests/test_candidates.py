from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.models import GroupConfig
import pytest


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


def test_pending_candidate_dedup_refreshes_mutable_content(tmp_path):
    service = make_service(tmp_path)
    first = service.create(**candidate_kwargs())
    changed = candidate_kwargs() | {
        "content": "新的完整正文",
        "reason": "新的价值说明",
        "excerpt": "新的原文摘录",
    }

    second = service.create(**changed)

    assert second == first
    candidate = service.get(first)
    assert candidate.content == "新的完整正文"
    assert candidate.reason == "新的价值说明"
    assert candidate.excerpt == "新的原文摘录"


def test_reviewed_candidate_dedup_does_not_rewrite_reviewed_content(tmp_path):
    service = make_service(tmp_path)
    candidate_id = service.create(**candidate_kwargs())
    service.confirm(candidate_id)

    duplicate_id = service.create(
        **(
            candidate_kwargs()
            | {
                "content": "不应覆盖",
                "reason": "不应覆盖",
                "excerpt": "不应覆盖",
            }
        )
    )

    candidate = service.get(candidate_id)
    assert duplicate_id == candidate_id
    assert candidate.status == "confirmed"
    assert candidate.content == ""
    assert candidate.reason == "高质量教程"
    assert candidate.excerpt == "看这个站点"


def test_candidate_create_in_transaction_rolls_back_with_outer_transaction(tmp_path):
    service = make_service(tmp_path)

    with pytest.raises(RuntimeError, match="rollback"):
        with service.archive.transaction():
            service.create_in_transaction(**candidate_kwargs())
            raise RuntimeError("rollback")

    count = service.archive.connection.execute(
        "SELECT COUNT(*) AS total FROM candidates"
    ).fetchone()["total"]
    assert count == 0
