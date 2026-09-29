from qq_digest.report_sources import extract_report_sources


def test_extract_report_sources_returns_claims_and_valid_ids():
    payload = {
        "evidence_version": 1,
        "overview": "群内决定使用 A",
        "overview_message_ids": ["m1"],
        "main_topics": [{"topic": "选型", "summary": "A 更稳定", "message_ids": ["m1", "m2"]}],
        "conclusions": [{"text": "A 已上线", "message_ids": []}],
        "resources": [{"title": "文档", "url": "https://example.com", "description": "说明", "message_ids": ["m2"]}],
        "tasks": [{"owner": "小王", "description": "复查", "deadline": "明天", "message_ids": ["m1"]}],
        "open_questions": [{"text": "是否需要回滚？", "message_ids": ["m2"]}],
    }

    items = extract_report_sources(payload)

    assert [item["section"] for item in items] == [
        "今日概览", "主要话题", "重要结论", "资源与链接", "任务或承诺", "未解决问题或争议"
    ]
    assert items[0]["source_ids"] == ["m1"]
    assert items[2]["status"] == "unverified"
    assert items[4]["text"] == "小王：复查（明天）"


def test_extract_report_sources_does_not_invent_legacy_evidence():
    assert extract_report_sources({"conclusions": ["旧结论"]}) is None
