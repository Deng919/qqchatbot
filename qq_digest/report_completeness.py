"""Describe persisted report inputs and daily run scope without assuming source completeness."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .archive import Archive
from .reports import published_report_is_valid


def input_coverage(diagnostics: object, *, archive_mismatch: bool = False) -> dict:
    result = {"status": "unknown", "label": "范围无法确认", "source_messages": None,
              "included_messages": None, "archive_mismatch": archive_mismatch,
              "notes": ["未记录有效的输入统计，无法确认报告覆盖了多少消息。"]}
    if not isinstance(diagnostics, dict):
        return result
    source, included = diagnostics.get("source_messages"), diagnostics.get("included_messages")
    truncated = diagnostics.get("context_truncated")
    if (type(source) is not int or type(included) is not int
            or not 0 <= included <= source or type(truncated) is not bool):
        return result
    notes = []
    if included < 5:
        notes.append(f"样本较少：实际纳入 {included} 条消息，结论仅供参考；安静的群不代表采集失败。")
    if truncated:
        notes.append(f"仅总结实际纳入的 {included} 条消息，部分消息因长度限制未包含。")
    elif included < source:
        notes.append(f"预处理排除了 {source - included} 条空白、重复或无有效内容的消息。")
    if archive_mismatch:
        notes.append("采集返回量明显高于本时间窗的归档量，可能存在导入或时间范围异常，请检查采集记录。")
    label = ("采集范围异常" if archive_mismatch else "部分消息未纳入" if truncated
             else "消息较少" if included < 5 else "已过滤无效消息" if included < source else "输入已覆盖")
    result.update(status="limited" if notes else "normal", label=label,
                  source_messages=source, included_messages=included, notes=notes)
    return result


def _read_input(json_path: str) -> dict:
    try:
        payload = json_path if isinstance(json_path, dict) else json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        return input_coverage(None)
    snapshot = payload.get("coverage")
    mismatch = isinstance(snapshot, dict) and snapshot.get("archive_mismatch") is True
    result = input_coverage(payload.get("diagnostics"), archive_mismatch=mismatch)
    review = payload.get("source_review")
    if (mismatch or result["status"] == "unknown" or not isinstance(review, dict)
            or type(review.get("version")) is not int or review["version"] != 1
            or review.get("kind") != "full_window" or review.get("claims_verified") is not True
            or type(review.get("group_id")) is not int or review["group_id"] != payload.get("group_id")
            or type(review.get("archive_message_count")) is not int
            or review["archive_message_count"] < result["source_messages"]
            or not isinstance(review.get("source_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", review["source_sha256"])
            or any(review.get(key) != payload.get(key) for key in
                   ("window_start", "window_end", "window_end_inclusive"))
            or type(review.get("window_end_inclusive")) is not bool):
        return result
    try:
        start = datetime.fromisoformat(review["window_start"])
        end = datetime.fromisoformat(review["window_end"])
        reviewed_at = datetime.fromisoformat(review["reviewed_at"])
        if any(value.tzinfo is None for value in (start, end, reviewed_at)) or start >= end:
            return result
    except (TypeError, ValueError, KeyError):
        return result
    # Keep the original generation diagnostics intact. This describes the later
    # full-window editorial review, not a claim that generation never truncated.
    result.update(status="normal", label="已核对完整归档", notes=[],
                  source_messages=review["archive_message_count"],
                  included_messages=review["archive_message_count"])
    return result


class ReportCompletenessService:
    def __init__(self, archive: Archive):
        self.archive = archive
        self._days: dict[str, dict] = {}

    def daily(self, report_date: str) -> dict:
        """A request-scoped snapshot of the latest attempt for this target day."""
        if report_date in self._days:
            return self._days[report_date]
        job = self.archive.connection.execute(
            """SELECT * FROM jobs WHERE job_type='daily_digest' AND target_date=?
               ORDER BY job_id DESC LIMIT 1""", (report_date,)
        ).fetchone()
        result = {"report_date": report_date, "status": "unknown", "label": "范围无法确认",
                  "job_id": job["job_id"] if job else None, "participating_groups": 0,
                  "report_groups": 0, "failed_groups": 0, "no_message_groups": 0,
                  "missing_report_groups": 0, "limited_input_groups": 0, "unknown_input_groups": 0,
                  "notes": [], "scope": "仅描述本次任务与本地归档，不能证明 QQ 历史消息无遗漏。"}
        outcomes = self.archive.connection.execute(
            "SELECT group_id,status FROM job_group_results r WHERE job_id=? "
            "AND NOT EXISTS (SELECT 1 FROM market_groups m WHERE m.group_id=r.group_id)",
            (job["job_id"],),
        ).fetchall() if job else []
        if not job or not outcomes:
            if job and job["status"] in {"failed", "partial_success"}:
                result.update(status="limited", label="日报存在限制")
                result["notes"].append("本次日报任务失败，未记录有效的逐群覆盖结果，请到失败处理中心查看。")
            else:
                result["notes"].append("尚无有效的逐群结果，无法确认当日覆盖范围。")
        elif job["status"] == "running":
            result["notes"].append("日报任务仍在运行，覆盖范围尚未确定。")
        else:
            result["participating_groups"] = len(outcomes)
            for item in outcomes:
                if item["status"] == "failed":
                    result["failed_groups"] += 1
                report = self.archive.report_for(item["group_id"], report_date)
                if report is None:
                    key = "no_message_groups" if item["status"] == "skipped" else "missing_report_groups"
                    if item["status"] != "failed":
                        result[key] += 1
                    continue
                if not published_report_is_valid(report["markdown_path"], report["json_path"]):
                    result["missing_report_groups"] += 1
                    continue
                result["report_groups"] += 1
                status = _read_input(report["json_path"])["status"]
                if status != "normal":
                    result[f"{status}_input_groups"] += 1
            for count_key, text in (
                ("failed_groups", "个群失败；已有报告可能来自之前的运行"),
                ("no_message_groups", "个群因本时间窗无归档消息而跳过，未生成日报"),
                ("missing_report_groups", "个群缺少可读取的报告文件"),
                ("limited_input_groups", "个群的报告输入存在限制，请查看报告详情"),
                ("unknown_input_groups", "个群的报告缺少有效输入统计，覆盖范围无法确认"),
            ):
                if result[count_key]:
                    result["notes"].append(f"{result[count_key]} {text}。")
            if job["status"] in {"failed", "partial_success"} and not result["failed_groups"]:
                result["notes"].append("本次任务未全部成功，请查看失败处理中心。")
            limited = any(result[key] for key in ("failed_groups",
                "missing_report_groups", "limited_input_groups")) or job["status"] != "success"
            status = "limited" if limited else "unknown" if result["unknown_input_groups"] else "normal"
            result.update(status=status, label={"limited": "日报存在限制", "unknown": "范围无法确认",
                                               "normal": "本次已覆盖"}[status])
            if status == "normal" and result["no_message_groups"]:
                result["label"] = f"生成成功，{result['no_message_groups']} 个群无消息跳过"
        self._days[report_date] = result
        return result

    def report(self, json_path: str, report_kind: str, report_date: str, *, group_id: int | None = None) -> dict:
        result = _read_input(json_path)
        result["scope"] = "仅基于所选群、日期范围内的本地归档消息，不能证明历史消息无遗漏。"
        result["daily"] = None
        if report_kind == "daily":
            day = self.daily(report_date)
            result["daily"] = day
            # Whole-day results are context, never a warning about another group's report.
            own = self.archive.connection.execute(
                '''SELECT j.started_at,j.error,r.created_at FROM job_group_results g
                   JOIN jobs j USING(job_id) JOIN reports r ON r.group_id=g.group_id AND r.report_date=?
                   WHERE g.job_id=? AND g.group_id=? AND g.status='failed' ''',
                (report_date, day['job_id'], group_id),
            ).fetchone() if day['job_id'] is not None and group_id is not None else None
            if own is not None:
                try:older=datetime.fromisoformat(own['created_at']) < datetime.fromisoformat(own['started_at'])
                except (ValueError,TypeError):older=False
                try:failures=json.loads(own['error'])
                except (ValueError,TypeError):failures=[]
                stage=next((item.get('stage') for item in failures if isinstance(item,dict) and item.get('group_id')==group_id),None) if isinstance(failures,list) else None
                if older and stage!='notification':
                    result.update(status='limited',label={'summary':'本次生成失败','collection':'本次采集失败'}.get(stage,'该群任务有异常'))
                    result['notes'].append('该群在当天最新任务中未全部成功，当前显示之前已保存的报告。')
        return result
