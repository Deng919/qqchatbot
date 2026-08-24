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


def test_knowledge_writer_routes_by_category(tmp_path):
    writer = KnowledgeWriter(tmp_path)
    writer.write(item(), "resource")
    assert (tmp_path / "resources.md").exists()
