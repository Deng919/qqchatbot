from datetime import datetime, timezone

import pytest
from qq_digest.models import GroupConfig

from tests.test_message_browsing import browsing_client, add_message, login


def preview(client, **values):
    return client.post('/api/reports/range/preview', json={
        'group_ids':[11], 'start_date':'2026-10-02', 'end_date':'2026-10-02', **values})


def test_preview_requires_login(browsing_client):
    client, _, _ = browsing_client
    assert preview(client).status_code == 401


def test_preview_counts_exact_local_natural_day_and_does_not_write(browsing_client):
    client, archive, _ = browsing_client
    for msg, stamp in [('before','2026-10-01T09:59:59+00:00'),('first','2026-10-01T10:00:00+00:00'),('last','2026-10-02T09:59:59+00:00'),('after','2026-10-02T10:00:00+00:00')]:
        add_message(archive,msg,datetime.fromisoformat(stamp))
    login(client)
    response = preview(client)
    assert response.status_code == 200
    data=response.json()
    assert data['total_messages']==2
    assert data['groups']==[{'group_id':11,'group_name':'研发群','message_count':2}]
    assert data['timezone']=='Pacific/Kiritimati'
    assert data['source']=='local_archive'
    assert data['start_date']==data['end_date']=='2026-10-02'
    assert archive.connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0


def test_preview_empty_archive_is_zero(browsing_client):
    client, _, _=browsing_client
    login(client)
    assert preview(client).json()['total_messages']==0


def test_preview_preserves_multiple_group_order_and_zero_counts(browsing_client):
    client, archive, _=browsing_client
    archive.upsert_groups([GroupConfig(group_id=22,name='产品群')])
    add_message(archive,'only-11',datetime(2026,10,1,12,tzinfo=timezone.utc))
    login(client)
    data=preview(client,group_ids=[22,11]).json()
    assert data['groups']==[{'group_id':22,'group_name':'产品群','message_count':0},
                            {'group_id':11,'group_name':'研发群','message_count':1}]
    assert data['total_messages']==1


@pytest.mark.parametrize('values',[
    {'group_ids':[]},{'group_ids':[11,11]},{'group_ids':[22]}, {'group_ids':[999]},
    {'start_date':'bad'}, {'start_date':'2026-10-03'}, {'start_date':'2026-09-24'},
    {'end_date':'9999-12-31','start_date':'9999-12-30'},
    {'group_ids':[2**63]}, {'group_ids':list(range(101))},
])
def test_preview_rejects_invalid_scope(browsing_client, values):
    client, _, _=browsing_client
    login(client)
    assert preview(client,**values).status_code==422


def test_preview_rejects_utc_underflow_in_positive_offset_timezone(browsing_client):
    client, _, config=browsing_client
    config.summary.timezone='Asia/Shanghai'
    login(client)
    assert preview(client,start_date='0001-01-01',end_date='0001-01-01').status_code==422
