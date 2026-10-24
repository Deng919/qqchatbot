import json

import pytest

from qq_digest.catchup import CatchupService
from tests.test_summary_reading import reading_client, add_report, login


def test_all_date_dashboard_includes_old_daily_and_range_reports(reading_client, tmp_path):
    client, archive, _ = reading_client
    add_report(archive, tmp_path, report_date='2020-01-01')
    add_report(archive, tmp_path, group_id=22, report_date='2099-01-01')
    path = tmp_path / 'historical-range.json'
    path.write_text(json.dumps({'main_topics': [{'topic': '历史范围', 'summary': '原始内容'}]}), encoding='utf-8')
    archive.record_manual_report(group_id=11, start_date='2019-01-01', end_date='2019-01-31',
        detail_mode='adaptive', effective_template='adaptive', input_fingerprint='history',
        source_message_count=1, markdown_path=path.with_suffix('.md'), json_path=path, candidate_ids=[])
    login(client)
    params = {'all_dates': True, 'grouped': True, 'page_size': 1}
    result = client.get('/api/summary-reading', params=params).json()
    assert result['total'] == 2 and result['point_total'] == 9
    assert result['items'][0]['group_id'] == 22
    second = client.get('/api/summary-reading', params={**params, 'page': 2}).json()
    assert second['items'][0]['group_id'] == 11
    assert second['items'][0]['total'] == 5 and second['items'][0]['report_count'] == 2
    # Expansion pages keep both the old daily report and the earlier range report.
    params = {'all_dates': True, 'group_id': 11, 'page_size': 4}
    first = client.get('/api/summary-reading', params=params).json()
    last = client.get('/api/summary-reading', params={**params, 'page': 2}).json()
    assert first['total'] == last['total'] == 5
    assert [item['report_kind'] for item in first['items']] == ['daily'] * 4
    assert last['items'][0]['report_kind'] == 'range'
    assert last['items'][0]['date_label'] == '2019-01-01 至 2019-01-31'


def test_history_preserves_read_keys_and_report_selection(reading_client, tmp_path):
    client, archive, _ = reading_client
    report_id, _ = add_report(archive, tmp_path, report_date='2020-01-01')
    login(client)
    bounded = client.get('/api/summary-reading', params={
        'date_from': '2020-01-01', 'date_to': '2020-01-01'}).json()['items'][0]
    client.post('/api/summary-reading/read', json={'key': bounded['key'], 'read': True})
    selected = client.get('/api/summary-reading', params={
        'all_dates': True, 'report_keys': f'daily:{report_id}'}).json()
    assert selected['total'] == 4 and selected['unread'] == 3
    assert selected['items'][0] == {**bounded, 'read': True}
    assert client.get('/api/summary-reading', params={
        'all_dates': True, 'read_filter': 'unread'}).json()['total'] == 3
    assert client.get('/api/summary-reading', params={
        'all_dates': True, 'group_id': 22}).json()['total'] == 0


@pytest.mark.parametrize('dates', [
    {'date_from': '2020-01-01'}, {'date_to': '2020-01-01'},
    {'date_from': '2020-01-01', 'date_to': '2020-01-02'}, {'date_from': ''},
])
def test_all_dates_rejects_conflicting_explicit_bounds(reading_client, dates):
    client, _, _ = reading_client
    login(client)
    response = client.get('/api/summary-reading', params={'all_dates': True, **dates})
    assert response.status_code == 422


def test_regular_reading_excludes_marketplace_members_even_when_explicit(reading_client, tmp_path):
    client, archive, config = reading_client
    add_report(archive, tmp_path, report_date='2020-01-01')
    add_report(archive, tmp_path, group_id=22, report_date='2020-01-01')
    with archive.transaction():
        archive.connection.execute('INSERT INTO market_groups(group_id) VALUES(22)')
        archive.connection.execute("UPDATE groups SET enabled=0,name='已改名的旧群' WHERE group_id=22")
    login(client)
    bounded = client.get('/api/summary-reading', params={
        'date_from': '2020-01-01', 'date_to': '2020-01-01'}).json()
    assert bounded['total'] == 4
    assert client.get('/api/summary-reading', params={'all_dates': True}).json()['total'] == 4
    assert client.get('/api/summary-reading', params={
        'all_dates': True, 'group_ids': [11, 22], 'grouped': True}).json()['total'] == 1
    assert client.get('/api/summary-reading', params={
        'all_dates': True, 'group_id': 22}).json()['total'] == 0
    # The compatibility catch-up feed shares ordinary summary classification.
    result = client.get('/api/catchup', params={
        'scope': 'since', 'since': '2000-01-01T00:00:00Z'}).json()
    assert result['total'] == 4 and {item['group_id'] for item in result['items']} == {11}
    service = CatchupService(archive, timezone_name=config.summary.timezone)
    assert service.list_items('custom', date_from='2020-01-01', date_to='2020-01-01')['total'] == 4
