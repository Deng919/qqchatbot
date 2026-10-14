from tests.test_summary_reading import reading_client, add_report, login
import pytest


def test_generated_report_keys_exclude_old_and_failed_group_reports(reading_client,tmp_path):
    client,ar,_=reading_client
    wanted,_=add_report(ar,tmp_path,group_id=11,report_date='2026-10-14')
    add_report(ar,tmp_path,group_id=22,report_date='2026-10-14')
    add_report(ar,tmp_path,group_id=11,report_date='2026-10-13')
    login(client)
    params={'report_keys':f'daily:{wanted}','date_from':'2026-10-13','date_to':'2026-10-14'}
    reports=client.get('/api/reports',params=params).json()
    assert reports['total']==1
    assert [r['report_id'] for r in reports['reports']]==[wanted]
    points=client.get('/api/summary-reading',params=params).json()
    assert points['items'] and {i['report_id'] for i in points['items']}=={wanted}


@pytest.mark.parametrize('key',['garbage','daily:0','range:-1','daily:99999999999999999999999'])
def test_generated_reading_keys_reject_invalid_scope(reading_client,key):
    client,_,_=reading_client;login(client)
    assert client.get('/api/reports',params={'report_keys':key}).status_code==422
    assert client.get('/api/summary-reading',params={'report_keys':key}).status_code==422


def test_legacy_source_endpoint_uses_immutable_current_snapshot(reading_client,tmp_path):
    from datetime import datetime,timezone
    from qq_digest.models import NormalizedMessage
    client,ar,_=reading_client
    now=datetime(2026,10,14,0,tzinfo=timezone.utc)
    ar.ingest([NormalizedMessage(group_id=11,msg_id='m1',timestamp=now,collected_at=now,text='source')])
    payload={'evidence_version':1,'overview':'snapshot source','overview_message_ids':['m1'],
        'window_start':'2026-10-14T00:00:00+00:00','window_end':'2026-10-15T00:00:00+00:00'}
    rid,path=add_report(ar,tmp_path,report_date='2026-10-14',payload=payload)
    from qq_digest.report_revisions import ReportRevisionService
    with ar.transaction():ReportRevisionService(ar).record_generated_in_transaction('daily',rid,'snapshot',payload,'test')
    path.write_text('{"evidence_version":1,"overview":"changed","overview_message_ids":[]}',encoding='utf-8')
    login(client)
    assert client.get(f'/api/reports/daily/{rid}/sources/m1').status_code==200


def test_message_browse_keeps_explicit_multiple_group_selection(reading_client):
    from datetime import datetime,timezone
    from qq_digest.models import NormalizedMessage
    client,ar,_=reading_client;now=datetime.now(timezone.utc)
    ar.ingest([NormalizedMessage(group_id=gid,msg_id=str(gid),sender_qq=gid,message_type='text' if gid==11 else 'link',timestamp=now,collected_at=now,text='message') for gid in (11,22)])
    login(client)
    result=client.get('/api/messages',params={'group_ids':11}).json()
    assert result['total']==1 and result['results'][0]['group_id']==11
    assert client.get('/api/messages',params=[('group_ids',11),('group_ids',22)]).json()['total']==2
    assert client.get('/api/messages',params={'sender_qq':11}).json()['total']==1
    assert client.get('/api/messages',params={'message_type':'link'}).json()['results'][0]['group_id']==22
    old=datetime(2020,1,1,tzinfo=timezone.utc)
    ar.ingest([NormalizedMessage(group_id=11,msg_id='old',timestamp=old,collected_at=now,text='old source')])
    assert client.get('/api/messages',params={'group_ids':11,'custom_dates':True}).json()['total']==2
    assert client.get('/api/messages',params={'group_ids':11,'date_to':'2026-10-15','custom_dates':True}).json()['total']==2
