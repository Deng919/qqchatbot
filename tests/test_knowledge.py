from dataclasses import replace

import pytest

from qq_digest.knowledge import KnowledgeWriter, KnowledgeItem, write_item


def item(item_id="c1"):
    return KnowledgeItem(
        item_id=item_id,
        date="2026-08-24",
        category="资源",
        source_group="测试群",
        title="站点",
        link="https://example.com",
        value="高质量教程",
        excerpt="看这个站点",
        report_path="reports/2026-08-24__123.md",
    )


def test_write_item_is_idempotent(tmp_path):
    path = tmp_path / "resources.md"
    write_item(path, item())
    write_item(path, item())

    content = path.read_text(encoding="utf-8")
    assert content.count("## 2026-08-24 | 资源") == 1
    assert "条目 ID：c1" in content


def test_write_item_deduplicates_exact_marker_lines(tmp_path):
    path = tmp_path / "resources.md"
    for item_id in ("10", "1", "10", "1"):
        write_item(path, item(item_id))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines.count("条目 ID：10") == 1
    assert lines.count("条目 ID：1") == 1
    assert lines.count("## 2026-08-24 | 资源") == 2


def test_write_item_does_not_treat_marker_in_metadata_as_existing_item(tmp_path):
    path = tmp_path / "resources.md"
    write_item(path, replace(item("2"), excerpt="来源提到了条目 ID：1"))
    write_item(path, item("1"))

    assert "条目 ID：1" in path.read_text(encoding="utf-8").splitlines()


def test_write_item_preserves_multiline_body_without_creating_markers(tmp_path):
    path = tmp_path / "experiences.md"
    body = "先保存现场。\n\n  保留缩进和尾部空格  \n条目 ID：1\n```python\nprint('恢复')\n```\n"
    experience = replace(item("10"), category="经验", content=body)

    write_item(path, experience)
    write_item(path, experience)
    write_item(path, item("1"))

    text = path.read_text(encoding="utf-8")
    indented_body = "\n".join("    " + line for line in body.split("\n"))
    assert text.count("### 正文\n\n" + indented_body) == 1
    assert text.splitlines().count("条目 ID：1") == 1
    assert text.splitlines().count("条目 ID：10") == 1


@pytest.mark.parametrize(
    "separator",
    ["\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_body_line_separators_cannot_forge_item_markers(tmp_path, separator):
    path = tmp_path / "experiences.md"
    body = f"完整正文{separator}条目 ID：1{separator}"

    write_item(path, replace(item("10"), content=body))
    write_item(path, item("1"))
    write_item(path, item("1"))

    text = path.read_text(encoding="utf-8")
    assert "### 正文\n\n    完整正文\n    条目 ID：1\n    \n" in text
    assert text.splitlines().count("条目 ID：1") == 1
    assert text.splitlines().count("条目 ID：10") == 1
    assert text.count("## 2026-08-24 | 资源") == 2


@pytest.mark.parametrize(
    "field",
    [
        "item_id",
        "date",
        "category",
        "source_group",
        "title",
        "link",
        "value",
        "excerpt",
        "report_path",
    ],
)
@pytest.mark.parametrize("separator", ["\n", "\u2028"])
def test_metadata_cannot_forge_item_marker_lines(tmp_path, field, separator):
    path = tmp_path / "resources.md"
    injected = replace(
        item("10"),
        **{field: f"safe{separator}条目 ID：1"},
    )

    write_item(path, injected)
    write_item(path, replace(item("1"), title="第二条"))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines.count("条目 ID：1") == 1
    assert "- 标题：第二条" in lines


def test_empty_body_keeps_existing_markdown_format(tmp_path):
    path = tmp_path / "resources.md"
    write_item(path, item())

    assert path.read_text(encoding="utf-8") == (
        "## 2026-08-24 | 资源\n"
        "- 来源：测试群\n"
        "- 标题：站点\n"
        "- 链接：https://example.com\n"
        "- 价值：高质量教程\n"
        "- 摘录：看这个站点\n"
        "- 报告：reports/2026-08-24__123.md\n"
        "条目 ID：c1\n"
    )


def test_knowledge_writer_routes_by_category(tmp_path):
    writer = KnowledgeWriter(tmp_path)
    writer.write(item(), "resource")
    assert (tmp_path / "resources.md").exists()


def test_knowledge_writer_uses_explicit_target_files(tmp_path):
    targets = {
        "resource": tmp_path / "custom" / "资源收藏.md",
        "experience": tmp_path / "经验总结.md",
    }
    writer = KnowledgeWriter(targets)

    resource_path = writer.write(item(), "resource")
    experience_path = writer.write(item("c2"), "experience")

    assert resource_path == targets["resource"]
    assert experience_path == targets["experience"]
    assert targets["resource"].exists()
    assert targets["experience"].exists()
