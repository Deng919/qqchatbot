"""Regenerate exactly one report from its original local archive window."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import Archive
from .candidates import CandidateService
from .group_summary import GroupSummaryBuilder, summary_input_fingerprint
from .report_revisions import ReportRevisionService
from .reports import ReportWriter
from .summary import Summarizer


def report_window(row, kind, payload, tz):
    first = date.fromisoformat(row['report_date'] if kind=='daily' else row['start_date'])
    last = date.fromisoformat(row['report_date'] if kind=='daily' else row['end_date'])
    lower = datetime.combine(first,time.min,tz)
    upper = datetime.combine(last+timedelta(days=1),time.min,tz)
    start, end = lower, upper
    if payload and ('window_start' in payload or 'window_end' in payload):
        try:
            start = datetime.fromisoformat(payload['window_start'])
            end = datetime.fromisoformat(payload['window_end'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('报告时间窗损坏，无法安全重生成') from exc
        if start.tzinfo is None or end.tzinfo is None or not lower<=start<end<=upper:
            raise ValueError('报告时间窗超出原报告日期，无法安全重生成')
    return start, end


def regenerate_report(*, config, kind, report_id, expected_version, reason, ai_client=None):
    if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
        raise ValueError('请填写重新生成的原因，最多 2000 字')
    archive = Archive.open(config.archive_path)
    owned_ai = ai_client is None
    job_id = None
    try:
        revisions = ReportRevisionService(archive)
        revisions.check_version(kind,report_id,expected_version)
        row = revisions.report(kind,report_id)
        groups = {g.group_id:g for g in archive.enabled_groups()}
        group = groups.get(row['group_id'])
        if group is None:
            raise ValueError('该群未启用，请先启用后再生成')
        if expected_version:
            payload = revisions.version(kind,report_id,expected_version)['payload']
        else:
            try:
                payload = json.loads(Path(row['json_path']).read_text(encoding='utf-8'))
                if not isinstance(payload,dict):
                    payload = None
            except (OSError,UnicodeError,json.JSONDecodeError):
                payload = None
        tz = ZoneInfo(config.summary.timezone)
        start,end = report_window(row,kind,payload,tz)
        job_id = archive.start_job('report_regeneration')
        # Daily pipeline/CLI include the exact cutoff; range summaries exclude
        # the next day's midnight. Keep those original boundary semantics.
        inclusive = kind=='daily' and payload is not None and 'window_end' in payload
        if payload and 'window_end_inclusive' in payload:
            inclusive = payload['window_end_inclusive'] is True
        messages = (archive.messages_between(group.group_id,start,end) if inclusive
                    else archive.messages_in_window(group.group_id,start,end))
        if not messages:
            raise ValueError('原报告范围没有归档消息，保留当前报告')
        if ai_client is None:
            from .ai.factory import build_ai_client
            ai_client = build_ai_client(config)
        knowledge = '\n\n'.join(path.read_text(encoding='utf-8') for path in
                                  config.resolve_knowledge_paths().values() if path.exists())
        first = row['report_date'] if kind=='daily' else row['start_date']
        last = row['report_date'] if kind=='daily' else row['end_date']
        artifact = GroupSummaryBuilder(Summarizer(ai=ai_client,max_context_chars=config.ai.max_context_chars)).build(
            group=group,window_start=start,window_end=end,
            report_date=first if kind=='daily' else f'{first} 至 {last}',candidate_date=last,
            messages=messages,timezone=tz,knowledge_base=knowledge,report_kind=kind)
        output = {**artifact.payload}
        output['window_end_inclusive'] = inclusive
        if kind=='range':
            output.update(report_kind='range',start_date=first,end_date=last)
        fingerprint = summary_input_fingerprint(group=group,report_kind=kind,messages=messages,
            timezone=tz,knowledge_base=knowledge,max_context_chars=config.ai.max_context_chars)
        prepared = ReportWriter(config.report_dir).prepare_named(Path(row['markdown_path']).stem,artifact.markdown,output)
        old_candidate_ids = archive.candidates_for_report(row['candidate_ids'])
        try:
            with archive.transaction():
                revisions.check_version(kind,report_id,expected_version)
                candidates = CandidateService(archive)
                ids = [candidates.create_in_transaction(**kwargs) for kwargs in artifact.candidate_kwargs]
                common = dict(group_id=group.group_id,markdown_path=prepared.paths.markdown,
                    json_path=prepared.paths.json,candidate_ids=ids,effective_template='adaptive',
                    input_fingerprint=fingerprint,source_message_count=artifact.source_message_count,
                    revision_markdown=artifact.markdown,revision_payload=output,revision_reason=reason.strip())
                if kind=='daily':
                    archive.record_report_in_transaction(report_date=first,**common)
                else:
                    archive.record_manual_report_in_transaction(start_date=first,end_date=last,
                        detail_mode=row['detail_mode'],**common)
                archive.delete_unreferenced_pending_candidates_in_transaction(old_candidate_ids)
                prepared.install()
        except Exception:
            prepared.rollback()
            raise
        prepared.finalize()
        archive.finish_job(job_id,'success')
        return {'version':revisions.current_version(kind,report_id),'report_id':report_id,'report_kind':kind}
    except Exception as exc:
        if job_id is not None:
            archive.finish_job(job_id,'failed',' '.join(str(exc).split())[:300])
        raise
    finally:
        if owned_ai and ai_client is not None:
            ai_client.close()
        archive.close()
