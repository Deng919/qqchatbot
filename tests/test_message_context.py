from datetime import datetime, timedelta, timezone
import json

import pytest

from tests.test_message_browsing import browsing_client, add_message, login


def seed(archive):
    stamp = datetime(2026, 10, 4, 3, tzinfo=timezone.utc)
    for i in range(61):
        add_message(archive, f'm{i:02}', stamp, text=f'上下文 {i}')
    add_message(archive, 'other-group', stamp, group_id=22)


def test_context_has_stable_directional_pages(browsing_client):
    client, archive, _ = browsing_client
    seed(archive)
    login(client)
    url = '/api/search/messages/11/m30/context'
    initial = client.get(url).json()
    assert [row['msg_id'] for row in initial['messages']] == ['m28','m29','m30','m31','m32']
    assert initial['has_before'] and initial['has_after']
    earlier = client.get(url, params={'direction':'before','cursor':initial['first_id']}).json()
    later = client.get(url, params={'direction':'after','cursor':initial['last_id']}).json()
    assert [row['msg_id'] for row in earlier['messages']] == [f'm{i:02}' for i in range(8,28)]
    assert [row['msg_id'] for row in later['messages']] == [f'm{i:02}' for i in range(33,53)]
    first = client.get(url, params={'direction':'before','cursor':earlier['first_id']}).json()
    last = client.get(url, params={'direction':'after','cursor':later['last_id']}).json()
    assert not first['has_before'] and not last['has_after']
    assert [row['msg_id'] for row in first['messages']] == [f'm{i:02}' for i in range(8)]
    assert [row['msg_id'] for row in last['messages']] == [f'm{i:02}' for i in range(53,61)]


@pytest.mark.parametrize('params,status', [
    ({'direction':'before','cursor':'other-group'},404),
    ({'direction':'after','cursor':'missing'},404),
    ({'direction':'before'},422),
    ({'direction':'bad','cursor':'m30'},422),
])
def test_context_rejects_foreign_or_invalid_cursor(browsing_client, params, status):
    client, archive, _ = browsing_client
    seed(archive)
    login(client)
    assert client.get('/api/search/messages/11/m30/context',params=params).status_code == status


def test_context_direction_keeps_chronology_across_dates(browsing_client):
    client, archive, _ = browsing_client
    stamp = datetime(2026,10,4,tzinfo=timezone.utc)
    for i in range(8):
        add_message(archive, str(i), stamp+timedelta(days=i))
    login(client)
    data=client.get('/api/search/messages/11/4/context?direction=before&cursor=4').json()
    assert [row['msg_id'] for row in data['messages']]==['0','1','2','3']


def test_report_context_revalidates_citation_on_every_page(browsing_client, tmp_path):
    client, archive, _ = browsing_client
    seed(archive)
    markdown=tmp_path/'cited.md';payload=tmp_path/'cited.json'
    markdown.write_text('合成摘要',encoding='utf-8')
    def save_citation(msg_id):
        payload.write_text(json.dumps({'evidence_version':1,'conclusions':[{'text':'合成结论','message_ids':[msg_id]}]}),encoding='utf-8')
    save_citation('m30')
    report=archive.record_report(group_id=11,report_date='2026-10-04',markdown_path=markdown,json_path=payload,candidate_ids=[])
    login(client)
    url=f'/api/reports/daily/{report}/sources/m30'
    first=client.get(url).json()
    page={'direction':'before','cursor':first['first_id']}
    assert len(client.get(url,params=page).json()['messages'])==20
    assert client.get(url,params={'direction':'after','cursor':'other-group'}).status_code==404
    save_citation('m29')
    assert client.get(url,params=page).status_code==404
    client.cookies.clear()
    assert client.get(url,params=page).status_code==401
