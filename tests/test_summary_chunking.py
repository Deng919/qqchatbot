import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import httpx

from qq_digest.ai.client import AIClient, AILimitExceeded
from qq_digest.ai_usage import UsageService
from qq_digest.models import NormalizedMessage
from qq_digest.summary import Summarizer


TZ = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 10, 29, tzinfo=TZ)
BUDGET = 1800


def source(index, text):
    return NormalizedMessage(
        msg_id=f"m{index}", group_id=123, sender_qq=10001,
        timestamp=START + timedelta(minutes=index), text=text, collected_at=START,
    )


def response(ids=(), title="保留话题"):
    return {
        "group_id": 123, "overview": "", "overview_message_ids": [],
        "main_topics": [{"topic": title, "summary": title, "message_ids": list(ids)}],
        "conclusions": [], "resources": [], "tasks": [], "open_questions": [],
        "candidates": [],
    }


class RecordingAI:
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def chat(self, messages):
        self.calls.append(messages)
        if self.callback:
            return self.callback(messages, len(self.calls))
        user = messages[1]["content"]
        ids = re.findall(r"\[(m\d+)\|", user)
        if not ids:
            payloads = json.loads(user.split("待合并摘要（时间顺序）：\n", 1)[1])
            ids = [mid for payload in payloads for item in payload["main_topics"] for mid in item["message_ids"]]
        return response(dict.fromkeys(ids))


def summarize(ai, sources, budget=BUDGET):
    return Summarizer(ai=ai, max_context_chars=budget).summarize(
        group_id=123, group_name="测试群", window_start=START,
        window_end=START + timedelta(days=1), messages=sources, timezone=TZ,
    )


def raw_contexts(ai):
    return [call[1]["content"].split("今日消息（", 1)[1].split("：\n", 1)[1]
            for call in ai.calls if "今日消息（" in call[1]["content"]]


def test_oversized_sources_all_reach_ordered_bounded_chunks_and_final_evidence():
    ai = RecordingAI()
    sources = [source(i, f"独立话题{i}：" + str(i) * 800) for i in range(1, 5)]
    result = summarize(ai, list(reversed(sources)))

    contexts = raw_contexts(ai)
    assert len(contexts) >= 2
    assert all(len(context) <= BUDGET for context in contexts)
    delivered = [mid for context in contexts for mid in re.findall(r"\[(m\d+)\|", context)]
    assert delivered == ["m1", "m2", "m3", "m4"]
    assert result.included_message_ids == ("m1", "m2", "m3", "m4")
    assert result.included_messages == result.source_messages == 4
    assert result.discarded_messages == 0
    assert result.context_truncated is False
    assert result.response.main_topics[0].message_ids == ["m1", "m2", "m3", "m4"]
    assert len(ai.calls) == 2 * len(contexts) - 1
    for call in ai.calls:
        if "待合并摘要（时间顺序）：\n" in call[1]["content"]:
            evidence = call[1]["content"].split("待合并摘要（时间顺序）：\n", 1)[1]
            assert len(evidence) <= BUDGET


def test_single_huge_source_preserves_every_character_with_original_id():
    ai = RecordingAI()
    text = "开头问题" + "完整正文" * 1300 + "最后回答"
    result = summarize(ai, [source(1, text)])

    contexts = raw_contexts(ai)
    assert len(contexts) > 1
    assert all(len(context) <= BUDGET for context in contexts)
    assert all(context.startswith("[m1|") for context in contexts)
    assert "".join(context.split("] ", 1)[1] for context in contexts) == text
    assert result.included_message_ids == ("m1",)
    assert result.included_messages == result.source_messages == 1
    assert result.context_truncated is False


def test_partial_and_merge_citations_restricted_to_actual_stage_evidence():
    def answer(messages, number):
        user = messages[1]["content"]
        if "今日消息（" in user:
            ids = re.findall(r"\[(m\d+)\|", user)
            # m3 is a real archive ID but belongs to a different chunk.
            return response([ids[0], "m3", "invented"])
        if number == 3:
            assert '"m3"' not in user
        assert '"invented"' not in user
        return response(["m1", "m2", "m3", "invented"])

    ai = RecordingAI(answer)
    result = summarize(ai, [source(i, str(i) * 1100) for i in range(1, 4)])

    # m3 legitimately enters only after its own chunk is processed.
    assert result.response.main_topics[0].message_ids == ["m1", "m2", "m3"]
    assert all("invented" not in call[1]["content"] for call in ai.calls)


def test_merge_cannot_resurrect_id_omitted_from_partial_evidence():
    def answer(messages, number):
        if "今日消息（" in messages[1]["content"]:
            payload = response(["m1"])
            if number != 1:
                payload["main_topics"] = []
            return payload
        return response(["m1", "m2"])

    result = summarize(RecordingAI(answer), [source(i, str(i) * 1100) for i in (1, 2)])
    assert result.response.main_topics[0].message_ids == ["m1"]


def test_merge_instructions_resolve_later_answers_and_preserve_disagreement():
    ai = RecordingAI()
    summarize(ai, [source(1, "最初提问" + "甲" * 1100), source(2, "后续回答" + "乙" * 1100)])
    merge = next(call for call in ai.calls if "今日消息（" not in call[1]["content"])
    instructions = "\n".join(part["content"] for part in merge)
    assert "时间顺序" in instructions
    assert "后续回答" in instructions
    assert "open_questions" in instructions
    assert "分歧" in instructions
    assert "来源" in instructions
    assert "去重" in instructions


def test_intermediate_candidates_survive_until_final_limit():
    candidates = [dict(type="experience", title=f"方法{i}", content=f"步骤{i}", reason="复用",
                       message_ids=["m1"]) for i in (1, 2)]

    def answer(messages, number):
        ids = re.findall(r"\[(m\d+)\|", messages[1]["content"])
        payload = response(ids or ["m1", "m2"])
        if "今日消息（" in messages[1]["content"]:
            payload["candidates"] = candidates if number == 1 else []
            assert "数量上限：1 条" not in messages[0]["content"]
        else:
            assert "方法1" in messages[1]["content"] and "方法2" in messages[1]["content"]
            payload["candidates"] = [candidates[1], candidates[0]]
        return payload

    result = summarize(RecordingAI(answer), [source(i, str(i) * 1100) for i in (1, 2)])
    assert [item.title for item in result.response.candidates] == ["方法2"]


@pytest.mark.parametrize("failure_call", [2, 3])
def test_partial_or_merge_failure_propagates_without_success(failure_call):
    def answer(messages, number):
        if number == failure_call:
            raise AILimitExceeded("调用预算已用尽")
        return response(["m1"])

    ai = RecordingAI(answer)
    with pytest.raises(AILimitExceeded, match="预算"):
        summarize(ai, [source(i, str(i) * 1100) for i in (1, 2, 3)])
    assert len(ai.calls) == failure_call


def test_oversized_merge_evidence_fails_instead_of_truncating_or_retrying():
    ai = RecordingAI(lambda messages, number: response(["m1"], title="重大话题" * 600))
    with pytest.raises(ValueError, match="预算|上限|合并"):
        summarize(ai, [source(i, str(i) * 1100) for i in (1, 2)])
    assert len(ai.calls) <= 2


def test_final_merge_cannot_return_unbounded_digest():
    def answer(messages, number):
        ids = re.findall(r"\[(m\d+)\|", messages[1]["content"])
        return response(ids or ["m1", "m2"], title="重大话题" * 600 if number == 3 else "简短话题")

    ai = RecordingAI(answer)
    with pytest.raises(ValueError, match="预算|上限|合并"):
        summarize(ai, [source(i, str(i) * 1100) for i in (1, 2)])
    assert len(ai.calls) == 3


def test_small_input_retains_single_call_path():
    ai = RecordingAI()
    result = summarize(ai, [source(1, "完整公告")])
    assert len(ai.calls) == 1
    assert result.included_message_ids == ("m1",)
    assert result.context_truncated is False


def test_missing_citation_gets_one_citation_only_repair():
    def answer(messages, number):
        if number == 1:
            return response([])
        assert "仅修复引用" in messages[1]["content"]
        assert "真实完整公告" in messages[1]["content"]
        return response(["m1"])

    ai = RecordingAI(answer)
    result = summarize(ai, [source(1, "真实完整公告")])
    assert result.response.main_topics[0].message_ids == ["m1"]
    assert len(ai.calls) == 2


@pytest.mark.parametrize("bad_repair", ["changed_text", "invalid_id", "still_missing"])
def test_citation_repair_rejects_changed_claim_or_unverified_source(bad_repair):
    def answer(messages, number):
        if number == 1 or bad_repair == "still_missing":
            return response([])
        return response(["made-up"] if bad_repair == "invalid_id" else ["m1"],
                        title="擅自改变正文" if bad_repair == "changed_text" else "保留话题")

    ai = RecordingAI(answer)
    with pytest.raises(ValueError, match="引用|正文"):
        summarize(ai, [source(1, "真实完整公告")])
    assert len(ai.calls) == 2


def test_legacy_generated_string_claim_is_repaired_without_changing_text():
    def answer(messages, number):
        payload = response(["m1"])
        payload["conclusions"] = ["采用方案A"] if number == 1 else [dict(text="采用方案A", message_ids=["m1"])]
        return payload

    ai = RecordingAI(answer)
    result = summarize(ai, [source(1, "讨论后确认采用方案A")])
    assert result.response.conclusions[0].text == "采用方案A"
    assert result.response.conclusions[0].message_ids == ["m1"]
    assert len(ai.calls) == 2


def test_merge_losing_earlier_substantive_topic_fails_explicitly():
    def answer(messages, number):
        ids = re.findall(r"\[(m\d+)\|", messages[1]["content"])
        return response(ids or ["m2"], title="早期主题" if number == 1 else "后期主题")

    ai = RecordingAI(answer)
    with pytest.raises(ValueError, match="遗漏|来源|覆盖"):
        summarize(ai, [source(i, str(i) * 1100) for i in (1, 2)])
    assert len(ai.calls) == 3


def test_citation_repair_respects_provider_budget_error():
    def answer(messages, number):
        if number == 2:
            raise AILimitExceeded("修复调用预算已用尽")
        return response([])

    ai = RecordingAI(answer)
    with pytest.raises(AILimitExceeded, match="修复调用预算"):
        summarize(ai, [source(1, "真实完整公告")])
    assert len(ai.calls) == 2


def test_citation_repair_cannot_move_existing_references_between_claims():
    def answer(messages, number):
        payload = response(["m1"] if number == 1 else ["m2"])
        payload["main_topics"].extend([
            dict(topic="第二主题", summary="第二内容", message_ids=["m2"] if number == 1 else ["m1", "m2"]),
            dict(topic="缺引用主题", summary="待补来源", message_ids=[] if number == 1 else ["m1"]),
        ])
        return payload

    ai = RecordingAI(answer)
    with pytest.raises(ValueError, match="引用|来源"):
        summarize(ai, [source(1, "第一公告"), source(2, "第二公告")])
    assert len(ai.calls) == 2


def test_distinct_early_and_late_topics_survive_merge():
    def answer(messages, number):
        user = messages[1]["content"]
        ids = re.findall(r"\[(m\d+)\|", user)
        if ids:
            return response(ids, title="早期数据库方案" if ids == ["m1"] else "晚期发布安排")
        parts = json.loads(user.split("待合并摘要（时间顺序）：\n", 1)[1])
        payload = response()
        payload["main_topics"] = [topic for part in parts for topic in part["main_topics"]]
        return payload

    result = summarize(RecordingAI(answer), [source(1, "数据库方案" + "甲" * 1100), source(2, "发布安排" + "乙" * 1100)])
    assert [item.topic for item in result.response.main_topics] == ["早期数据库方案", "晚期发布安排"]
    assert [item.message_ids for item in result.response.main_topics] == [["m1"], ["m2"]]


def test_later_answer_can_resolve_question_without_losing_original_source():
    def answer(messages, number):
        if number == 1:
            payload = response(["m1"], title="发布问题")
            payload["open_questions"] = [dict(text="何时发布？", message_ids=["m1"])]
            return payload
        if number == 2:
            return response(["m2"], title="后续回答：周五发布")
        assert "后续回答" in messages[1]["content"]
        return response(["m1", "m2"], title="发布时间已明确为周五")

    result = summarize(RecordingAI(answer), [source(1, "何时发布？" + "甲" * 1100), source(2, "周五发布" + "乙" * 1100)])
    assert result.response.open_questions == []
    assert result.response.main_topics[0].message_ids == ["m1", "m2"]


def test_exact_duplicate_merge_claims_are_combined_with_all_real_sources():
    def answer(messages, number):
        ids = re.findall(r"\[(m\d+)\|", messages[1]["content"])
        if ids:
            return response(ids)
        payload = response(["m1"])
        payload["main_topics"].extend(response(["m2"])["main_topics"])
        return payload

    result = summarize(RecordingAI(answer), [source(i, str(i) * 1100) for i in (1, 2)])
    assert len(result.response.main_topics) == 1
    assert result.response.main_topics[0].message_ids == ["m1", "m2"]


@pytest.mark.parametrize("mode", ["chunks", "repair", "failed_repair"])
def test_every_stage_uses_existing_provider_usage_accounting(tmp_path, mode):
    usage = UsageService(tmp_path / "usage.sqlite", now=lambda: START)
    usage.update(10, [], 0)
    calls = []
    delegate = RecordingAI()

    def transport(request):
        messages = json.loads(request.content)["messages"]
        calls.append(messages)
        if mode == "chunks":
            payload = delegate.chat(messages)
        else:
            payload = response(["m1"] if len(calls) == 2 and mode == "repair" else [])
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        })

    ai = AIClient(base_url="https://api.example.com", api_key="test", model="test",
                  transport=httpx.MockTransport(transport), usage_service=usage)
    try:
        sources = [source(i, str(i) * 1100) for i in (1, 2)] if mode == "chunks" else [source(1, "完整公告")]
        if mode == "failed_repair":
            with pytest.raises(ValueError, match="引用"):
                summarize(ai, sources)
        else:
            summarize(ai, sources)
    finally:
        ai.close()
    snapshot = usage.snapshot()
    assert snapshot["calls"] == len(calls) == (3 if mode == "chunks" else 2)
    assert snapshot["failed"] == {"chunks": 0, "repair": 1, "failed_repair": 2}[mode]
    assert snapshot["successful"] == {"chunks": 3, "repair": 1, "failed_repair": 0}[mode]


@pytest.mark.parametrize("failure_call", [1, 3], ids=["partial", "merge"])
def test_oversized_stage_output_is_rejected_in_usage_ledger(tmp_path, failure_call):
    usage = UsageService(tmp_path / "usage.sqlite", now=lambda: START)
    usage.update(10, [], 0)
    calls = []
    delegate = RecordingAI()

    def transport(request):
        messages = json.loads(request.content)["messages"]
        calls.append(messages)
        payload = delegate.chat(messages)
        if len(calls) == failure_call:
            payload["main_topics"][0]["summary"] = "超预算实质内容" * 600
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        })

    ai = AIClient(base_url="https://api.example.com", api_key="test", model="test",
                  transport=httpx.MockTransport(transport), usage_service=usage)
    try:
        with pytest.raises(ValueError, match="预算"):
            summarize(ai, [source(i, str(i) * 1100) for i in (1, 2)])
    finally:
        ai.close()
    snapshot = usage.snapshot()
    assert snapshot["calls"] == len(calls) == failure_call
    assert snapshot["failed"] == 1
    assert snapshot["successful"] == failure_call - 1
