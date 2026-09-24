from qq_digest.preprocessing import Preprocessor, PreprocessResult


def test_extracts_links_and_files():
    processor = Preprocessor()
    result = processor.process(
        [
            "看这个 https://example.com/a?b=1 很好用",
            "[文件] 白皮书.pdf",
        ]
    )

    assert result.links == ["https://example.com/a?b=1"]
    assert result.files == ["白皮书.pdf"]


def test_filters_system_noise_and_normalizes_whitespace():
    processor = Preprocessor()
    result = processor.process(["有人加入本群", "  hello   world  "])

    assert result.cleaned_lines == ["hello world"]
    assert result.discarded_count == 1


def test_extracts_todo_statements():
    processor = Preprocessor()
    result = processor.process(["明天我整理一下链接", "TODO: 修复配置"])

    assert result.todos == ["明天我整理一下链接", "TODO: 修复配置"]


def test_todo_extraction_does_not_treat_memories_as_future_actions():
    result = Preprocessor().process(
        [
            "我记得服务器最便宜的能用半年",
            "记得去年选的就是这个套餐",
            "记得明天提交报告",
            "TODO: 修复配置",
        ]
    )

    assert result.todos == ["记得明天提交报告", "TODO: 修复配置"]


def test_normalizes_urls_and_keeps_source_indexes():
    result = Preprocessor().process(
        ["忽略", "  文档 https://EXAMPLE.com/a?x=1&amp;y=2。  "]
    )

    assert result.cleaned_lines == [
        "忽略",
        "文档 https://EXAMPLE.com/a?x=1&y=2。",
    ]
    assert result.kept_indexes == [0, 1]
    assert result.links == ["https://example.com/a?x=1&y=2"]


def test_excludes_internal_qq_urls_without_dropping_user_text():
    result = Preprocessor().process(
        [
            "https://tianquan.gtimg.cn/nudgeaction/item/10/expression.jpg",
            "这个入口打不开 https://zb.vip.qq.com/v2/pages/nudgeMall?_wv=2",
        ]
    )

    assert result.links == []
    assert result.cleaned_lines == [
        "这个入口打不开 https://zb.vip.qq.com/v2/pages/nudgeMall?_wv=2"
    ]
    assert result.kept_indexes == [1]
    assert result.discarded_count == 1
