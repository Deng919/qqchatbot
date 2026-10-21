from tests.test_summary_reading import reading_client,add_report,login


def test_dashboard_paginates_groups_without_splitting_points(reading_client,tmp_path):
    client,archive,_=reading_client
    first,_=add_report(archive,tmp_path,group_id=11,report_date='2026-10-19')
    add_report(archive,tmp_path,group_id=11,report_date='2026-10-20')
    add_report(archive,tmp_path,group_id=22,report_date='2026-10-19')
    login(client)
    params={'date_from':'2026-10-19','date_to':'2026-10-20','grouped':True,'page_size':1}
    response=client.get('/api/summary-reading',params=params).json()
    assert response['grouped'] is True and response['total']==2 and response['point_total']==12
    group=response['items'][0]
    assert group['group_id']==11 and group['total']==8 and group['report_count']==2
    assert len(group['preview'])<=3 and group['unread']==8
    second=client.get('/api/summary-reading',params={**params,'page':2}).json()
    assert second['items'][0]['group_id']==22
    raw=client.get('/api/summary-reading',params={'date_from':'2026-10-19','date_to':'2026-10-20','group_id':11}).json()
    assert len(raw['items'])==8 and raw['total']==8
    for item in raw['items']:client.post('/api/summary-reading/read',json={'key':item['key'],'read':True})
    unread=client.get('/api/summary-reading',params={**params,'read_filter':'unread'}).json()
    assert unread['total']==1 and unread['items'][0]['group_id']==22
    selected=client.get('/api/summary-reading',params={**params,'report_keys':f'daily:{first}'}).json()
    assert selected['total']==1 and selected['point_total']==4 and selected['items'][0]['group_id']==11
