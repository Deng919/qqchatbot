import json

from qq_digest.reports import ReportWriter, render_markdown


def test_render_markdown_contains_required_sections():
    markdown = render_markdown(
        group_name="测试群",
        report_date="2026-08-24",
        window="2026-08-24 00:00 到 22:00",
        topics=["工具推荐"],
        conclusions=["站点适合归档"],
        resources=["[站点](https://example.com)"],
        tasks=[],
        open_questions=[],
        deterministic={"links": ["https://example.com"], "files": [], "todos": []},
        quality_note="",
    )

    assert "# 测试群日报" in markdown
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
