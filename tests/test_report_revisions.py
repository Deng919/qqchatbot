import json
from pathlib import Path

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig
from qq_digest.reports import ReportWriter


@pytest.fixture
def ar(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    archive.upsert_groups([GroupConfig(group_id=123, name='测试群')])
    yield archive
    archive.close()


def service(ar):
    from qq_digest.report_revisions import ReportRevisionService
    return ReportRevisionService(ar)


def publish(ar, tmp_path, text, *, kind='daily', versioned=True, fail=False, payload_override=None):
    writer = ReportWriter(tmp_path / 'reports')
    payload = payload_override or {'group_id': 123, 'overview': text}
    prepared = writer.prepare_named(kind, text, payload)
    revision = dict(revision_markdown=text, revision_payload=payload) if versioned else {}
    try:
        with ar.transaction():
            if kind == 'daily':
                report_id = ar.record_report_in_transaction(group_id=123, report_date='2026-10-01',
                    markdown_path=prepared.paths.markdown, json_path=prepared.paths.json,
                    candidate_ids=[], **revision)
            else:
                report_id = ar.record_manual_report_in_transaction(group_id=123,
                    start_date='2026-09-30', end_date='2026-10-01', detail_mode='adaptive',
                    effective_template='adaptive', markdown_path=prepared.paths.markdown,
                    json_path=prepared.paths.json, candidate_ids=[], input_fingerprint='x',
                    source_message_count=1, **revision)
            prepared.install()
            if fail:
                raise RuntimeError('publication failed')
    except Exception:
        prepared.rollback()
        raise
    prepared.finalize()
    return report_id


def test_successful_publications_keep_immutable_versions_and_kind_isolation(ar, tmp_path):
    rid = publish(ar, tmp_path, 'first')
    assert publish(ar, tmp_path, 'second') == rid
    other = publish(ar, tmp_path, 'range', kind='range')
    assert service(ar).versions('daily', rid)['total'] == 2
    assert service(ar).version('daily', rid, 1)['markdown'] == 'first'
    assert service(ar).version('daily', rid, 2)['payload']['overview'] == 'second'
    assert service(ar).version('range', other, 1)['markdown'] == 'range'


def test_rollback_keeps_previous_files_and_revision(ar, tmp_path):
    rid = publish(ar, tmp_path, 'first')
    with pytest.raises(RuntimeError, match='publication failed'):
        publish(ar, tmp_path, 'second', fail=True)
    assert service(ar).versions('daily', rid)['total'] == 1
    assert (tmp_path/'reports/daily.md').read_text(encoding='utf-8') == 'first'


def test_legacy_report_is_snapshotted_before_first_replacement(ar, tmp_path):
    rid = publish(ar, tmp_path, 'legacy', versioned=False)
    assert service(ar).versions('daily', rid)['current_version'] == 0
    assert service(ar).version('daily', rid, 0)['markdown'] == 'legacy'
    publish(ar, tmp_path, 'new')
    assert service(ar).version('daily', rid, 1)['markdown'] == 'legacy'
    assert service(ar).version('daily', rid, 1)['origin'] == 'legacy'
    assert service(ar).version('daily', rid, 2)['markdown'] == 'new'


def test_damaged_legacy_file_is_marked_unavailable_and_can_be_repaired(ar, tmp_path):
    rid = publish(ar, tmp_path, 'legacy', versioned=False)
    (tmp_path/'reports/daily.json').write_text('broken', encoding='utf-8')
    publish(ar, tmp_path, 'repaired')
    assert service(ar).version('daily', rid, 1)['payload'] is None
    assert service(ar).version('daily', rid, 1)['markdown'] == 'legacy'
    assert service(ar).version('daily', rid, 1)['content_available'] is False


def test_corrections_survive_regeneration_reopen_and_resolve(ar, tmp_path):
    rid = publish(ar, tmp_path, 'first')
    note = service(ar).add_correction('daily', rid, expected_version=1,
        category='error', excerpt='原结论', correction='应为方案 B', reason='原消息明确说明')
    publish(ar, tmp_path, 'second')
    reopened = Archive.open(tmp_path/'archive.sqlite')
    try:
        notes = service(reopened).corrections('daily', rid)
        assert notes[0]['correction'] == '应为方案 B'
        assert notes[0]['version'] == 1 and notes[0]['status'] == 'open'
        service(reopened).set_correction_status('daily', rid, note['correction_id'], 'resolved')
        assert service(reopened).corrections('daily', rid)[0]['status'] == 'resolved'
        service(reopened).set_correction_status('daily', rid, note['correction_id'], 'open')
        assert service(reopened).corrections('daily', rid)[0]['status'] == 'open'
    finally:
        reopened.close()


def test_stale_note_and_foreign_report_note_are_rejected(ar, tmp_path):
    from qq_digest.report_revisions import RevisionConflict
    rid = publish(ar, tmp_path, 'first')
    publish(ar, tmp_path, 'second')
    with pytest.raises(RevisionConflict):
        service(ar).add_correction('daily', rid, expected_version=1,
            category='error', excerpt='', correction='修正', reason='原因')
    note = service(ar).add_correction('daily', rid, expected_version=2,
        category='missing', excerpt='', correction='遗漏', reason='原因')
    other = publish(ar, tmp_path, 'other', kind='range')
    with pytest.raises(LookupError):
        service(ar).set_correction_status('range', other, note['correction_id'], 'resolved')


@pytest.mark.parametrize('field,value', [('category','other'), ('correction',' '), ('reason',' '), ('status','other')])
def test_invalid_correction_data_rejected(ar, tmp_path, field, value):
    rid = publish(ar, tmp_path, 'first')
    kwargs = dict(expected_version=1, category='noise', excerpt='', correction='修正', reason='理由')
    if field == 'status':
        note = service(ar).add_correction('daily', rid, **kwargs)
        with pytest.raises(ValueError):
            service(ar).set_correction_status('daily', rid, note['correction_id'], value)
    else:
        kwargs[field] = value
        with pytest.raises(ValueError):
            service(ar).add_correction('daily', rid, **kwargs)


def test_group_deletion_removes_versions_and_corrections(ar, tmp_path):
    rid = publish(ar, tmp_path, 'first')
    service(ar).add_correction('daily', rid, expected_version=1,
        category='noise', excerpt='', correction='修正', reason='原因')
    ar.delete_group(123)
    assert ar.connection.execute('SELECT COUNT(*) FROM report_revisions').fetchone()[0] == 0
    assert ar.connection.execute('SELECT COUNT(*) FROM report_corrections').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['daily','range'])
@pytest.mark.parametrize('damaged_file', [False, True])
def test_regeneration_uses_one_group_and_original_window_and_keeps_reason(ar, tmp_path, kind, damaged_file):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from qq_digest.config import Config, AIConfig, SecurityConfig
    from qq_digest.models import NormalizedMessage
    from qq_digest.report_regeneration import regenerate_report
    payload = {'group_id':123, 'window_start':'2026-10-01T10:00:00+08:00',
               'window_end':'2026-10-01T12:00:00+08:00'}
    rid = publish(ar, tmp_path, 'first', kind=kind,payload_override=payload)
    row = service(ar).report(kind,rid)
    if damaged_file:
        Path(row['json_path']).write_text('broken',encoding='utf-8')
    for hour in (9,11,12,13):
        stamp = datetime(2026,10,1,hour,tzinfo=ZoneInfo('Asia/Shanghai'))
        ar.ingest([NormalizedMessage(group_id=123,msg_id=f'm{hour}',timestamp=stamp,
                                    collected_at=stamp,text=f'时间{hour}的讨论')])
    class AI:
        calls = []
        def chat(self, messages):
            self.calls.append(messages)
            return dict(group_id=123,overview='修订摘要',main_topics=[],conclusions=[],
                        resources=[],tasks=[],open_questions=[],candidates=[])
    ai = AI()
    cfg = Config(data_dir=tmp_path,archive_path=tmp_path/'archive.sqlite',report_dir=tmp_path/'reports',
        work_dir=tmp_path/'work',log_dir=tmp_path/'logs',knowledge_dir=tmp_path/'knowledge',
        security=SecurityConfig(web_password_hash='x'*32),
        ai=AIConfig(base_url='https://example.com',model='test',api_key_env='TEST_KEY'))
    result = regenerate_report(config=cfg,kind=kind,report_id=rid,expected_version=1,
                               reason='修复遗漏',ai_client=ai)
    assert result['version'] == 2
    prompt = ai.calls[0][-1]['content']
    assert '时间11' in prompt and '时间9' not in prompt and '时间13' not in prompt
    assert ('时间12' in prompt) == (kind=='daily')
    assert service(ar).version(kind,rid,2)['reason'] == '修复遗漏'
    assert service(ar).version(kind,rid,1)['markdown'] == 'first'
    assert json.loads(Path(row['json_path']).read_text(encoding='utf-8'))['window_end'] == payload['window_end']


def test_legacy_daily_fallback_stays_exclusive_after_repeated_regeneration(ar, tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from qq_digest.config import Config, AIConfig, SecurityConfig
    from qq_digest.models import NormalizedMessage
    from qq_digest.report_regeneration import regenerate_report
    rid=publish(ar,tmp_path,'legacy',versioned=False)
    for day,hour in ((1,12),(2,0)):
        stamp=datetime(2026,10,day,hour,tzinfo=ZoneInfo('Asia/Shanghai'))
        ar.ingest([NormalizedMessage(group_id=123,msg_id=f'm{day}',timestamp=stamp,
            collected_at=stamp,text='原日期讨论' if day==1 else '下一天午夜讨论')])
    class AI:
        prompts=[]
        def chat(self,messages):
            self.prompts.append(messages[-1]['content'])
            return dict(group_id=123,overview='模拟摘要',main_topics=[],conclusions=[],
                        resources=[],tasks=[],open_questions=[],candidates=[])
    cfg=Config(data_dir=tmp_path,archive_path=tmp_path/'archive.sqlite',report_dir=tmp_path/'reports',
        work_dir=tmp_path/'work',log_dir=tmp_path/'logs',knowledge_dir=tmp_path/'knowledge',
        security=SecurityConfig(web_password_hash='x'*32),
        ai=AIConfig(base_url='https://example.com',model='test',api_key_env='TEST_KEY'))
    ai=AI()
    regenerate_report(config=cfg,kind='daily',report_id=rid,expected_version=0,reason='补存旧版',ai_client=ai)
    regenerate_report(config=cfg,kind='daily',report_id=rid,expected_version=2,reason='复核',ai_client=ai)
    assert all('下一天午夜讨论' not in prompt for prompt in ai.prompts)
    assert service(ar).version('daily',rid,3)['payload']['window_end_inclusive'] is False


def test_regeneration_failure_preserves_current_content(ar, tmp_path):
    from qq_digest.report_regeneration import regenerate_report
    from qq_digest.config import Config,AIConfig,SecurityConfig
    rid = publish(ar,tmp_path,'first')
    cfg = Config(data_dir=tmp_path,archive_path=tmp_path/'archive.sqlite',report_dir=tmp_path/'reports',
        work_dir=tmp_path/'work',log_dir=tmp_path/'logs',knowledge_dir=tmp_path/'knowledge',
        security=SecurityConfig(web_password_hash='x'*32),
        ai=AIConfig(base_url='https://example.com',model='test',api_key_env='TEST_KEY'))
    with pytest.raises(ValueError, match='没有归档消息'):
        regenerate_report(config=cfg,kind='daily',report_id=rid,expected_version=1,reason='重试',ai_client=object())
    assert service(ar).current_version('daily',rid)==1
    assert (tmp_path/'reports/daily.md').read_text(encoding='utf-8')=='first'
