import json
import os
from pathlib import Path

import pytest

from qq_digest.reports import ReportWriter, render_markdown


def test_render_markdown_contains_required_sections():
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-08-24",
        window="2026-08-24 00:00 到 22:00",
        overview="今天集中讨论客户端兼容性。",
        topics=["工具推荐"],
        conclusions=["站点适合归档"],
        resources=["[站点](https://example.com)"],
        tasks=[],
        open_questions=[],
        deterministic={"links": ["https://example.com"], "files": [], "todos": []},
        quality_note="",
    )

    assert "# 测试群日报" in markdown
    assert "## 今日概览\n今天集中讨论客户端兼容性。" in markdown
    assert "## 主要话题" in markdown
    assert "## 数据质量" not in markdown


def test_report_writer_creates_markdown_and_json(tmp_path):
    writer = ReportWriter(tmp_path)
    payload = {"group_id": 123, "group_name": "测试群", "main_topics": []}
    paths = writer.write(
        group_id=123,
        group_name="测试群",
        report_date="2026-08-24",
        markdown="# 测试",
        payload=payload,
    )

    assert paths.markdown.name == "2026-08-24__123.md"
    assert paths.json.name == "2026-08-24__123.json"
    assert json.loads(paths.json.read_text(encoding="utf-8")) == payload


def test_render_markdown_preserves_topic_and_resource_descriptions():
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-09-09",
        window="00:00 到 22:00",
        topics=[{"topic": "工具推荐", "summary": "适合处理日志"}],
        conclusions=[],
        resources=[
            {
                "title": "站点",
                "url": "https://example.com",
                "description": "官方文档",
            }
        ],
        tasks=[],
        open_questions=[],
        deterministic={"links": [], "files": [], "todos": []},
        quality_note="",
    )

    assert "**工具推荐**：适合处理日志" in markdown
    assert "[站点](https://example.com)：官方文档" in markdown


def test_render_markdown_supports_range_heading():
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-09-01 至 2026-09-07",
        window="2026-09-01T00:00:00+08:00 到 2026-09-08T00:00:00+08:00",
        topics=[],
        conclusions=[],
        resources=[],
        tasks=[],
        open_questions=[],
        deterministic={"links": [], "files": [], "todos": []},
        quality_note="",
        title_suffix="范围摘要",
        date_label="日期范围",
    )

    assert "# 测试群范围摘要" in markdown
    assert "- 日期范围：2026-09-01 至 2026-09-07" in markdown


def test_prepared_report_write_can_restore_previous_pair(tmp_path):
    writer = ReportWriter(tmp_path)
    original = writer.prepare_named("range", "old markdown", {"version": "old"})
    original.install()
    original.finalize()

    prepared = writer.prepare_named("range", "new markdown", {"version": "new"})
    prepared.install()
    prepared.rollback()

    assert prepared.paths.markdown.read_text(encoding="utf-8") == "old markdown"
    assert json.loads(prepared.paths.json.read_text(encoding="utf-8")) == {
        "version": "old"
    }
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_prepared_report_write_finalize_keeps_new_pair(tmp_path):
    writer = ReportWriter(tmp_path)

    prepared = writer.prepare_named("range", "new markdown", {"version": "new"})
    prepared.install()
    prepared.finalize()

    assert prepared.paths.markdown.read_text(encoding="utf-8") == "new markdown"
    assert json.loads(prepared.paths.json.read_text(encoding="utf-8")) == {
        "version": "new"
    }
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_report_writer_restores_existing_pair_when_json_replace_fails(
    tmp_path, monkeypatch
):
    writer = ReportWriter(tmp_path)
    original = writer.write(
        group_id=123,
        group_name="群",
        report_date="2026-09-09",
        markdown="old markdown",
        payload={"version": "old"},
    )
    real_replace = os.replace

    def fail_json_install(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if source_path.suffix == ".tmp" and target_path.suffix == ".json":
            raise OSError("simulated json replace failure")
        return real_replace(source, target)

    monkeypatch.setattr("qq_digest.reports.os.replace", fail_json_install)

    with pytest.raises(OSError, match="simulated"):
        writer.write(
            group_id=123,
            group_name="群",
            report_date="2026-09-09",
            markdown="new markdown",
            payload={"version": "new"},
        )

    assert original.markdown.read_text(encoding="utf-8") == "old markdown"
    assert json.loads(original.json.read_text(encoding="utf-8")) == {
        "version": "old"
    }
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.bak"))


def test_report_writer_leaves_no_files_when_pair_preparation_fails(
    tmp_path, monkeypatch
):
    writer = ReportWriter(tmp_path)
    real_prepare = ReportWriter._prepare_text
    calls = 0

    def fail_second_prepare(target, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated preparation failure")
        return real_prepare(target, content)

    monkeypatch.setattr(
        ReportWriter, "_prepare_text", staticmethod(fail_second_prepare)
    )

    with pytest.raises(OSError, match="preparation"):
        writer.write(
            group_id=123,
            group_name="群",
            report_date="2026-09-09",
            markdown="new markdown",
            payload={"version": "new"},
        )

    assert not list(tmp_path.iterdir())


def test_report_writer_removes_partial_first_write_when_json_replace_fails(
    tmp_path, monkeypatch
):
    writer = ReportWriter(tmp_path)
    real_replace = os.replace

    def fail_json_install(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if source_path.suffix == ".tmp" and target_path.suffix == ".json":
            raise OSError("simulated json replace failure")
        return real_replace(source, target)

    monkeypatch.setattr("qq_digest.reports.os.replace", fail_json_install)

    with pytest.raises(OSError, match="simulated"):
        writer.write(
            group_id=123,
            group_name="群",
            report_date="2026-09-09",
            markdown="new markdown",
            payload={"version": "new"},
        )

    assert not list(tmp_path.iterdir())
