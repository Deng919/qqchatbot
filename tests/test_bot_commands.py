from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.knowledge import KnowledgeWriter
from qq_digest.models import GroupConfig
from qq_digest.notify.commands import BotCommandHandler


class AuthorizedNotifier:
    @staticmethod
    def is_authorized(openid):
        return openid == "owner"


def build_handler(tmp_path, *, count=1):
    archive = Archive.open(tmp_path / "archive.sqlite")
    candidates = CandidateService(archive)
    candidate_ids = []
    for index in range(count):
        candidate_ids.append(
            candidates.create(
                group_id=123,
                created_date="2026-08-24",
                candidate_type="resource",
                title=f"高质量站点 {index + 1}",
                link=f"https://example.com/{index + 1}",
                content="",
                reason="资料完整",
                excerpt="群友推荐",
                message_ids=[f"m{index + 1}"],
            )
        )
    handler = BotCommandHandler(
        notifier=AuthorizedNotifier(),
        candidates=candidates,
        knowledge=KnowledgeWriter(
            {
                "resource": tmp_path / "notes" / "重点资源.md",
                "experience": tmp_path / "notes" / "经验.md",
            }
        ),
        group_names={123: "开发群"},
    )
    return archive, candidates, candidate_ids, handler


def test_confirm_command_records_configured_knowledge_path(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    candidates = CandidateService(archive)
    candidate_id = candidates.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type="resource",
        title="高质量站点",
        link="https://example.com",
        content="",
        reason="资料完整",
        excerpt="群友推荐",
        message_ids=["m1"],
    )
    target = tmp_path / "notes" / "重点资源.md"
    handler = BotCommandHandler(
        notifier=AuthorizedNotifier(),
        candidates=candidates,
        knowledge=KnowledgeWriter(
            {"resource": target, "experience": tmp_path / "notes" / "经验.md"}
        ),
        group_names={123: "开发群"},
    )

    result = handler.handle_message("owner", f"/confirm {candidate_id}")

    row = archive.connection.execute("SELECT * FROM knowledge_items").fetchone()
    assert result.success is True
    assert candidates.get(candidate_id).status == "confirmed"
    assert row["candidate_id"] == candidate_id
    assert row["markdown_path"] == str(target)
    assert target.exists()


def test_confirm_command_preserves_full_experience_body(tmp_path):
    archive, candidates, _, handler = build_handler(tmp_path)
    body = "第一步：记录故障现象。\n\n第二步：回滚配置并验证恢复。"
    candidate_id = candidates.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type="experience",
        title="故障恢复流程",
        content=body,
        reason="可以复用",
        excerpt="这次是配置的问题",
        message_ids=["experience-source"],
    )

    first = handler.handle_message("owner", f"入库 {candidate_id}")
    repeated = handler.handle_message("owner", f"入库 {candidate_id}")

    text = handler.knowledge.path_for("experience").read_text(encoding="utf-8")
    assert first.success and repeated.success
    assert text.count("第一步：记录故障现象。") == 1
    assert text.count("第二步：回滚配置并验证恢复。") == 1
    assert "这次是配置的问题" in text
    assert candidates.get(candidate_id).status == "confirmed"
    assert archive.connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 1


def test_chinese_list_and_multi_confirm_commands(tmp_path):
    archive, candidates, candidate_ids, handler = build_handler(tmp_path, count=2)

    listed = handler.handle_message("owner", "候选")
    confirmed = handler.handle_message(
        "owner", f"入库 {candidate_ids[0]} {candidate_ids[1]}"
    )

    assert listed.success is True
    assert all(f"#{candidate_id}" in listed.message for candidate_id in candidate_ids)
    assert confirmed.success is True
    assert "已入库 2 条" in confirmed.message
    assert all(candidates.get(candidate_id).status == "confirmed" for candidate_id in candidate_ids)
    assert archive.connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0] == 2


def test_multi_confirm_reports_partial_failure_without_rolling_back_success(tmp_path):
    _, candidates, candidate_ids, handler = build_handler(tmp_path)

    result = handler.handle_message("owner", f"/confirm {candidate_ids[0]} 999999")

    assert result.success is False
    assert candidates.get(candidate_ids[0]).status == "confirmed"
    assert "已入库 1 条" in result.message
    assert "999999" in result.message


def test_chinese_review_commands(tmp_path):
    _, candidates, candidate_ids, handler = build_handler(tmp_path, count=3)
    first, second, third = candidate_ids

    viewed = handler.handle_message("owner", f"查看 {first}")
    ignored = handler.handle_message("owner", f"忽略 {second} 内容过期")
    later = handler.handle_message("owner", f"稍后 {third}")

    assert viewed.success is True
    assert f"候选 #{first}" in viewed.message
    assert ignored.success is True
    assert candidates.get(second).status == "ignored"
    assert candidates.get(second).ignore_reason == "内容过期"
    assert later.success is True
    assert candidates.get(third).status == "later"


def test_bot_uses_latest_group_name_from_archive(tmp_path):
    archive, _, candidate_ids, handler = build_handler(tmp_path)
    archive.upsert_groups([GroupConfig(group_id=123, name="改名后的群")])

    result = handler.handle_message("owner", f"查看 {candidate_ids[0]}")

    assert result.success is True
    assert "来源群：改名后的群" in result.message
