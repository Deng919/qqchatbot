from datetime import datetime, timedelta, timezone

import pytest

from qq_digest.archive import Archive
from qq_digest.candidate_context import candidate_source_context
from qq_digest.models import GroupConfig, NormalizedMessage, SummaryCandidate


BASE = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


@pytest.fixture
def archive(tmp_path):
    result = Archive.open(tmp_path / "archive.sqlite")
    result.upsert_groups([
        GroupConfig(group_id=1, name="甲群"),
        GroupConfig(group_id=2, name="乙群"),
    ])
    yield result
    result.connection.close()


def message(msg_id, minute, *, group_id=1, text=None, timestamp=None):
    stamp = timestamp or BASE + timedelta(minutes=minute)
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=group_id,
        sender_qq=1000 + group_id,
        timestamp=stamp,
        collected_at=stamp,
        text=text if text is not None else f"原消息 {msg_id}",
    )


def candidate(*message_ids):
    return SummaryCandidate(
        candidate_id=10,
        group_id=1,
        message_ids=list(message_ids),
        created_date="2026-09-01",
        candidate_type="experience",
        title="测试候选",
        reason="待审核",
    )


def test_context_has_two_neighbors_and_deduplicates_overlapping_references(archive):
    archive.ingest([message(f"m{i}", i) for i in range(6)] + [
        message("m2", 2, group_id=2, text="其他群不应显示"),
    ])

    result = candidate_source_context(archive, candidate("m2", "m3"))

    assert [item["msg_id"] for item in result["source_context"]] == [
        "m0", "m1", "m2", "m3", "m4", "m5",
    ]
    assert [item["msg_id"] for item in result["source_context"] if item["is_cited"]] == ["m2", "m3"]
    assert all("其他群" not in item["text"] for item in result["source_context"])
    assert result["missing_source_count"] == 0
    assert result["source_context_truncated"] is False


def test_context_stops_at_group_boundary_and_counts_missing_source(archive):
    archive.ingest([message("m0", 0), message("m1", 1), message("m2", 2)])

    result = candidate_source_context(archive, candidate("m0", "absent"))

    assert [item["msg_id"] for item in result["source_context"]] == ["m0", "m1", "m2"]
    assert result["missing_source_count"] == 1


def test_context_uses_message_id_to_break_timestamp_ties(archive):
    archive.ingest([message(msg_id, 0, timestamp=BASE) for msg_id in ("c", "a", "b")])

    result = candidate_source_context(archive, candidate("b"))

    assert [item["msg_id"] for item in result["source_context"]] == ["a", "b", "c"]


def test_context_prioritizes_citations_at_forty_message_limit(archive):
    archive.ingest([message(f"m{i:02}", i) for i in range(45)])

    result = candidate_source_context(archive, candidate(*(f"m{i:02}" for i in range(45))))

    assert len(result["source_context"]) == 40
    assert [item["msg_id"] for item in result["source_context"]] == [f"m{i:02}" for i in range(40)]
    assert all(item["is_cited"] for item in result["source_context"])
    assert result["source_context_truncated"] is True


def test_context_bounds_long_text_and_handles_all_missing(archive):
    archive.ingest([message("m0", 0, text="长" * 2001)])

    result = candidate_source_context(archive, candidate("m0"))
    missing = candidate_source_context(archive, candidate("other"))

    assert len(result["source_context"][0]["text"]) == 2000
    assert result["source_context"][0]["text_truncated"] is True
    assert missing["source_context"] == []
    assert missing["missing_source_count"] == 1
