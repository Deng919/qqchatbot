from datetime import datetime,timezone,timedelta
import json

from tests.test_message_browsing import browsing_client,add_message,login
from qq_digest.report_sources import extract_report_sources
from tests.test_summary import valid_response,FakeAI,message
from qq_digest.summary import Summarizer
from qq_digest.catchup import report_item_key
from qq_digest.report_revisions import ReportRevisionService


def test_all_evidence_ids_survive_extraction_and_generation():
    ids=[f'm{i}' for i in range(8)]
    payload={'evidence_version':1,'conclusions':[{'text':'结论','message_ids':ids+[ids[0]]}]}
    assert extract_report_sources(payload)[0]['source_ids']==ids
    response=valid_response();response['conclusions']=[{'text':'结论','message_ids':ids+['foreign']}]
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    result=Summarizer(ai=FakeAI(response),max_context_chars=10000).summarize(group_id=123,group_name='合成群',window_start=now,window_end=now+timedelta(days=1),messages=[message(mid,now,'合成正文') for mid in ids],timezone=timezone.utc)
    assert result.response.conclusions[0].message_ids==ids


def test_existing_read_and_bookmark_keys_survive_expanded_references():
    assert report_item_key('daily',1,'结论','正文',['a','b','c','d','e'],0)==report_item_key('daily',1,'结论','正文',['a','b','c'],0)


def related_report(archive,tmp_path):
    now=datetime(2026,10,7,tzinfo=timezone.utc)
    ids=['m00','m10','m30','m60','m90']
    for i in range(100):add_message(archive,f'm{i:02}',now+timedelta(minutes=i))
    add_message(archive,'foreign',now,group_id=22)
    add_message(archive,'outside',now-timedelta(days=2))
    md=tmp_path/'related.md';jp=tmp_path/'related.json';md.write_text('合成结论',encoding='utf-8')
    jp.write_text(json.dumps({'evidence_version':1,'conclusions':[{'text':'结论','message_ids':ids+['foreign','outside','missing']},{'text':'其他结论','message_ids':['m99']}]}),encoding='utf-8')
    rid=archive.record_report(group_id=11,report_date='2026-10-07',markdown_path=md,json_path=jp,candidate_ids=[])
    return rid,ids,jp


def test_related_list_all_and_context_marks_every_valid_reference(browsing_client,tmp_path):
    client,archive,_=browsing_client;rid,ids,jp=related_report(archive,tmp_path)
    url=f'/api/reports/daily/{rid}/related-messages'
    assert client.post(url,json={'source_ids':ids}).status_code==401
    login(client)
    data=client.post(url,json={'source_ids':ids,'expected_version':0}).json()
    assert [r['msg_id'] for r in data['messages']]==ids
    assert data['related_ids']==ids and 'content_json' not in data['messages'][0]
    context=client.post(url,json={'source_ids':ids,'anchor_id':'m30','expected_version':0}).json()
    assert [r['msg_id'] for r in context['messages']]==['m28','m29','m30','m31','m32']
    assert context['related_ids']==ids
    later=client.post(url,json={'source_ids':ids,'anchor_id':'m30','direction':'after','cursor':'m32','expected_version':0})
    assert later.status_code==200
    for bad in (ids[:3],ids+['m99'],ids+['foreign'],['outside']):
        assert client.post(url,json={'source_ids':bad}).status_code==404
    assert client.post(url,json={'source_ids':ids,'anchor_id':'foreign'}).status_code==404
    assert client.post(url,json={'source_ids':ids,'anchor_id':'m30','direction':'after','cursor':'foreign'}).status_code==404
    assert client.post(url,json={'source_ids':ids,'expected_version':1}).status_code==409
    archive.connection.execute("DELETE FROM messages WHERE msg_id='m10'");archive.connection.commit()
    assert client.post(url,json={'source_ids':ids}).status_code==404


def test_related_batch_returns_more_than_five_hundred_messages(browsing_client,tmp_path):
    client,archive,_=browsing_client
    now=datetime(2026,10,7,tzinfo=timezone.utc);ids=[f'large{i:04}' for i in range(505)]
    for i,mid in enumerate(ids):add_message(archive,mid,now+timedelta(seconds=i))
    md=tmp_path/'large.md';jp=tmp_path/'large.json';md.write_text('合成正文',encoding='utf-8')
    jp.write_text(json.dumps({'evidence_version':1,'conclusions':[{'text':'结论','message_ids':ids}]}),encoding='utf-8')
    rid=archive.record_report(group_id=11,report_date='2026-10-07',markdown_path=md,json_path=jp,candidate_ids=[])
    login(client)
    response=client.post(f'/api/reports/daily/{rid}/related-messages',json={'source_ids':ids})
    assert response.status_code==200 and [row['msg_id'] for row in response.json()['messages']]==ids


def test_related_revalidates_version_and_reads_current_snapshot(browsing_client,tmp_path):
    client,archive,_=browsing_client;rid,ids,jp=related_report(archive,tmp_path)
    service=ReportRevisionService(archive)
    with archive.transaction():service.capture_legacy_in_transaction('daily',rid)
    login(client);url=f'/api/reports/daily/{rid}/related-messages'
    assert client.post(url,json={'source_ids':ids,'expected_version':0}).status_code==409
    current=service.current_version('daily',rid)
    jp.write_text('{}',encoding='utf-8')
    assert client.post(url,json={'source_ids':ids,'expected_version':current}).status_code==200
    assert client.post(url,json={'source_ids':ids,'expected_version':True}).status_code==422


def test_range_report_uses_same_complete_related_list(browsing_client,tmp_path):
    client,archive,_=browsing_client;_,ids,jp=related_report(archive,tmp_path)
    rid=archive.record_manual_report(group_id=11,start_date='2026-10-07',end_date='2026-10-07',detail_mode='group',
        effective_template='concise',markdown_path=tmp_path/'related.md',json_path=jp,candidate_ids=[],input_fingerprint='synthetic',source_message_count=100)
    login(client)
    result=client.post(f'/api/reports/range/{rid}/related-messages',json={'source_ids':ids})
    assert result.status_code==200 and [row['msg_id'] for row in result.json()['messages']]==ids


def test_reading_points_bind_content_and_sources_to_displayed_version(browsing_client,tmp_path):
    from qq_digest.catchup import CatchupService
    client,archive,_=browsing_client;rid,ids,jp=related_report(archive,tmp_path)
    revisions=ReportRevisionService(archive)
    with archive.transaction():revisions.capture_legacy_in_transaction('daily',rid)
    feed=CatchupService(archive,timezone_name='UTC')
    old=feed.list_items('today',now=datetime(2026,10,7,tzinfo=timezone.utc))['items'][0]
    assert old['current_version']==1 and old['text']=='结论'
    payload=json.loads(jp.read_text(encoding='utf-8'))
    payload['conclusions'][0]['text']='更新后的结论'
    with archive.transaction():revisions.record_generated_in_transaction('daily',rid,'更新正文',payload,'合成验证')
    jp.write_text('{}',encoding='utf-8')
    new=feed.list_items('today',now=datetime(2026,10,7,tzinfo=timezone.utc))['items'][0]
    from qq_digest.bookmarks import BookmarkService
    bookmark=BookmarkService(archive,timezone_name='UTC').resolve({'kind':'report','report_kind':'daily','report_id':rid,'point_key':new['key']})
    assert bookmark['text']=='更新后的结论' and bookmark['source_ids']==ids
    assert new['text']=='更新后的结论' and new['current_version']==2 and new['source_ids']==ids
    login(client)
    assert client.post(f'/api/reports/daily/{rid}/related-messages',json={'source_ids':ids,'expected_version':old['current_version']}).status_code==409
