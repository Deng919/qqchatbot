import json

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.mycortex_export import export_confirmed_candidates, write_bundle


def _message(msg_id: str, text: str = "原消息") -> NormalizedMessage:
    from datetime import datetime, timezone

    stamp = datetime(2026, 11, 15, 8, 30, tzinfo=timezone.utc)
    return NormalizedMessage(
        msg_id=msg_id,
        group_id=123,
        sender_qq=10001,
        timestamp=stamp,
        collected_at=stamp,
        text=text,
    )


def test_export_only_published_confirmed_candidates_and_keeps_context(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="学习群")])
    archive.ingest([_message("m1", "请看这个教程"), _message("m2", "上下文")])
    service = CandidateService(archive)
    confirmed = service.create(
        group_id=123,
        created_date="2026-11-15",
        candidate_type="resource",
        title="教程",
        link="https://example.com",
        content="教程正文",
        reason="与我的学习方向有关",
        excerpt="教程摘录",
        message_ids=["m1"],
    )
    pending = service.create(
        group_id=123,
        created_date="2026-11-15",
        candidate_type="experience",
        title="待审核",
        content="不应导出",
        reason="待确认",
        message_ids=["m2"],
    )
    service.confirm(confirmed)
    service.confirm(pending)
    archive.record_knowledge_item(
        item_id=str(confirmed), candidate_id=confirmed, markdown_path="knowledge/resources.md"
    )
    bundle = export_confirmed_candidates(archive)
    assert [item["candidateId"] for item in bundle["candidates"]] == [confirmed]
    item = bundle["candidates"][0]
    assert item["requestKey"] == f"qqdigest:candidate:{confirmed}"
    assert item["sourceUri"] == "qqdigest://group/123/message/m1"
    assert "教程正文" in item["body"]
    assert "请看这个教程" in item["body"]
    assert item["context"]["source_context"][0]["msg_id"] == "m1"
    archive.close()


def test_write_bundle_does_not_overwrite(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="群")])
    output = tmp_path / "bundle.json"
    write_bundle(archive, output)
    assert json.loads(output.read_text("utf-8"))["format"] == "mycortex.qqdigest"
    try:
        write_bundle(archive, output)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing bundle must not be overwritten")
    archive.close()
