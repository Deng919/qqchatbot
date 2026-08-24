from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from qq_digest.archive import Archive
from qq_digest.collector.fixture import FixtureCollector
from qq_digest.models import GroupConfig
from qq_digest.pipeline import DailyPipeline


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
                    "content": "",
                    "reason": "高质量教程",
                    "message_ids": ["fixture-123"],
                }
            ],
        }


def make_pipeline(tmp_path: Path, now: datetime) -> tuple[DailyPipeline, Archive]:
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=123, name="测试群")])
    return (
        DailyPipeline(
            archive=archive,
            collector=FixtureCollector(now=now),
            ai_client=FakeAI(),
            report_dir=tmp_path / "reports",
            max_context_chars=1000,
            timezone_name="Asia/Shanghai",
            window_mode="today",
        ),
        archive,
    )


def test_run_daily_creates_report_and_candidate(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline, archive = make_pipeline(tmp_path, now)

    result = pipeline.run_daily(now)

    assert result.groups_processed == 1
    assert result.report_paths[0].markdown.exists()
    assert result.candidate_ids == [1]
    assert result.messages_inserted == 1
    assert len(pipeline.candidates.pending()) == 1
    job = archive.connection.execute("SELECT * FROM jobs").fetchone()
    assert job["status"] == "success"


def test_run_daily_is_idempotent(tmp_path):
    now = datetime(2026, 8, 24, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    pipeline, archive = make_pipeline(tmp_path, now)

    first = pipeline.run_daily(now)
    second = pipeline.run_daily(now)

    assert second.candidate_ids == first.candidate_ids
    assert len(pipeline.candidates.pending()) == 1
    reports = archive.connection.execute("SELECT * FROM reports").fetchall()
    assert len(reports) == 1
