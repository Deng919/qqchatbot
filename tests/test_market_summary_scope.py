"""Marketplace membership excludes ordinary summaries without affecting collection."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig
from qq_digest.secondhand import SecondhandService
from qq_digest.manual_summary import ManualSummaryRequest
from qq_digest.summary_generation import preview_summary
from tests.test_manual_summary import make_service, add_message
from tests.test_pipeline import make_pipeline
from tests.test_report_revisions import publish
from tests.test_web import web_client


def test_summary_groups_use_membership_and_preserve_collection(tmp_path):
    archive = Archive.open(tmp_path / 'archive.sqlite')
    try:
        archive.upsert_groups([
            GroupConfig(group_id=1, name='二手群但未注册'),
            GroupConfig(group_id=2, name='已改名', daily_summary=True),
            GroupConfig(group_id=3, name='停用普通群', enabled=False),
            GroupConfig(group_id=4, name='关闭日报的普通群', daily_summary=False),
            GroupConfig(group_id=5, name='停用市场群', enabled=False),
        ])
        SecondhandService(archive).add_groups([2, 5])
        assert [g.group_id for g in archive.summary_groups()] == [1, 3, 4]
        assert [g.group_id for g in archive.summary_groups(enabled_only=True)] == [1, 4]
        assert [g.group_id for g in archive.summary_groups(enabled_only=True, daily_summary=True)] == [1]
        assert [g.group_id for g in archive.all_groups()] == [1, 2, 3, 4, 5]
        assert [g.group_id for g in archive.enabled_groups()] == [1, 2, 4]
    finally:
        archive.close()


@pytest.mark.parametrize('preview', [False, True])
def test_manual_summary_rejects_mixed_stale_market_selection_before_ai(tmp_path, preview):
    service, archive, ai = make_service(tmp_path, [
        GroupConfig(group_id=123, name='普通群'), GroupConfig(group_id=456, name='已改名')])
    try:
        SecondhandService(archive).add_groups([456])
        add_message(archive, 123, 'm1')
        add_message(archive, 456, 'm2')
        request = ManualSummaryRequest((123, 456), date(2026, 9, 2), date(2026, 9, 2))
        with pytest.raises(ValueError, match='二手'):
            if preview:
                preview_summary(archive, request, 'Asia/Shanghai')
            else:
                service.run(request)
        assert ai.calls == []
        assert archive.connection.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0] == 0
    finally:
        archive.close()


@pytest.mark.parametrize('summary_only', [False, True])
def test_daily_generation_skips_market_even_when_daily_flag_enabled(tmp_path, summary_only):
    now = datetime(2026, 8, 24, 22, tzinfo=ZoneInfo('Asia/Shanghai'))
    pipeline, archive = make_pipeline(tmp_path, now)
    try:
        SecondhandService(archive).add_groups([123])
        result = pipeline.run_summary_only(now) if summary_only else pipeline.run_daily(now)
        assert result.groups_processed == 0
        assert result.report_paths == []
        assert pipeline.summarizer.ai.calls == []
    finally:
        archive.close()


def test_groups_summary_scope_excludes_market_but_default_keeps_it(web_client):
    client, _, _ = web_client
    archive = client.app.state.archive
    archive.upsert_groups([GroupConfig(group_id=456, name='二手未注册')])
    SecondhandService(archive).add_groups([123])
    client.post('/login', data={'password': 'password123'})
    assert [g['group_id'] for g in client.get('/api/groups').json()['groups']] == [123, 456]
    assert [g['group_id'] for g in client.get('/api/groups?scope=summary').json()['groups']] == [456]
    assert client.get('/api/groups?scope=unknown').status_code == 422


def test_report_listing_and_summary_counts_hide_market_history_without_deleting(web_client, tmp_path):
    client, _, _ = web_client
    archive = client.app.state.archive
    publish(archive, tmp_path, 'market daily')
    publish(archive, tmp_path, 'market range', kind='range')
    SecondhandService(archive).add_groups([123])
    client.post('/login', data={'password': 'password123'})
    for query in ['', '?group_id=123', '?group_ids=123', '?report_keys=daily:1']:
        data = client.get('/api/reports' + query).json()
        assert data['total'] == 0
        assert data['reports'] == []
    assert client.get('/api/stats').json()['total_reports'] == 0
    assert archive.connection.execute('SELECT COUNT(*) FROM reports').fetchone()[0] == 1
    assert archive.connection.execute('SELECT COUNT(*) FROM manual_reports').fetchone()[0] == 1


@pytest.mark.parametrize('endpoint', ['/api/summaries', '/api/reports/range'])
def test_generation_http_rejects_market_before_building_ai(web_client, monkeypatch, endpoint):
    client, _, _ = web_client
    SecondhandService(client.app.state.archive).add_groups([123])
    def forbidden(*args, **kwargs):
        pytest.fail('market request must be rejected before creating an AI client')
    monkeypatch.setattr('qq_digest.ai.factory.build_ai_client', forbidden)
    client.post('/login', data={'password': 'password123'})
    response = client.post(endpoint, json={
        'group_ids': [123], 'start_date': '2026-10-01', 'end_date': '2026-10-01'})
    assert response.status_code == 422
    assert '二手' in response.json()['detail']


@pytest.mark.parametrize('kind', ['daily', 'range'])
def test_report_regeneration_rejects_market_before_ai(web_client, tmp_path, kind):
    from qq_digest.report_regeneration import regenerate_report
    client, _, _ = web_client
    archive = client.app.state.archive
    report_id = publish(archive, tmp_path, 'stored report', kind=kind)
    SecondhandService(archive).add_groups([123])
    with pytest.raises(ValueError, match='二手'):
        regenerate_report(config=client.app.state.config, kind=kind,
            report_id=report_id, expected_version=1, reason='复核', ai_client=object())


def test_daily_summary_coverage_excludes_historical_market_participants(tmp_path):
    from qq_digest.report_completeness import ReportCompletenessService
    from tests.test_report_completeness import write_report
    archive = Archive.open(tmp_path / 'archive.sqlite')
    try:
        archive.upsert_groups([GroupConfig(group_id=i, name=f'群{i}') for i in [1, 2]])
        write_report(archive, tmp_path, 1)
        write_report(archive, tmp_path, 2)
        job = archive.start_job('daily_digest', target_date='2026-09-29')
        archive.finish_job(job, 'success', group_outcomes={1: 'success', 2: 'success'})
        SecondhandService(archive).add_groups([2])
        result = ReportCompletenessService(archive).daily('2026-09-29')
        assert result['participating_groups'] == 1
        assert result['report_groups'] == 1
        assert result['status'] == 'normal'
    finally:
        archive.close()
