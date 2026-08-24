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
