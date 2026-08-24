from datetime import datetime
from zoneinfo import ZoneInfo

from qq_digest.archive import Archive
from qq_digest.candidates import CandidateService
from qq_digest.collector.fixture import FixtureCollector
from qq_digest.models import GroupConfig
from qq_digest.reports import ReportWriter, render_markdown
from qq_digest.summary import Summarizer


class FakeAI:
    def chat(self, messages):
        return {
            "group_id": 123,
            "main_topics": [{"topic": "资源分享", "summary": "分享了教程站点"}],
            "conclusions": [],
            "resources": [{"title": "站点", "link": "https://example.com"}],
            "tasks": [],
            "open_questions": [],
            "candidates": [
                {
                    "type": "resource",
                    "title": "站点",
                    "link": "https://example.com",
                    "reason": "高质量教程",
                    "message_ids": ["fixture-123"],
                }
            ],
        }


def test_collect_summary_report_candidate(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    group = GroupConfig(group_id=123, name="测试群")
    archive.upsert_groups([group])
    start = datetime(2026, 8, 24, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = datetime(2026, 8, 24, 22, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = archive.ingest(FixtureCollector().collect(123, start, end))
    messages = archive.messages_between(123, start, end)
    summary = Summarizer(ai=FakeAI(), max_context_chars=1000).summarize(
        group_id=123,
        group_name="测试群",
        window_start=start,
        window_end=end,
        messages=messages,
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    candidates = CandidateService(archive)
    candidate_id = candidates.create(
        group_id=123,
        created_date="2026-08-24",
        candidate_type=summary.response.candidates[0].type,
        title=summary.response.candidates[0].title,
        link=summary.response.candidates[0].link,
        content=summary.response.candidates[0].content,
        reason=summary.response.candidates[0].reason,
        excerpt=messages[0].text[:100],
        message_ids=summary.response.candidates[0].message_ids,
    )
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-08-24",
        window="2026-08-24 00:00 到 22:00",
        topics=[item.topic for item in summary.response.main_topics],
        conclusions=summary.response.conclusions,
        resources=[item.title for item in summary.response.resources],
        tasks=summary.response.tasks,
        open_questions=summary.response.open_questions,
        deterministic=summary.deterministic,
        quality_note="",
    )
    paths = ReportWriter(tmp_path / "reports").write(
        group_id=123,
        group_name="测试群",
        report_date="2026-08-24",
        markdown=markdown,
        payload=summary.response.model_dump(),
    )

    assert result.inserted == 1
    assert paths.markdown.exists()
    assert candidates.get(candidate_id).status == "pending"
