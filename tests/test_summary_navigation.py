import runpy
from pathlib import Path

helpers=runpy.run_path(str(Path(__file__).with_name('test_summary_reading.py')))
reading_client,login,add_report=helpers['reading_client'],helpers['login'],helpers['add_report']
generation=runpy.run_path(str(Path(__file__).with_name('test_unified_summary.py')))


def test_generation_preserves_prior_dates_and_other_group_reports(reading_client,monkeypatch):
    client,archive,config=reading_client
    config.summary.timezone='Asia/Shanghai'
    config.report_dir.mkdir(parents=True)
    old=[]
    for group,day in [(11,'2026-09-01'),(22,'2026-09-01'),(22,'2026-09-02')]:
        report_id,path=add_report(archive,config.report_dir,group_id=group,report_date=day)
        text=f'旧摘要 {group} {day}'
        path.with_suffix('.md').write_text(text,encoding='utf-8')
        old.append((report_id,text))
    generation['add_message'](archive,11,'new')
    ai=generation['manual_helpers']['RecordingAI']();ai.close=lambda:None
    monkeypatch.setattr('qq_digest.ai.factory.build_ai_client',lambda _:ai)
    login(client)
    state=client.get('/api/features').json()
    client.put('/api/features',json={'values':{'report_revisions':False},'expected_revision':state['revision']})
    response=client.post('/api/summaries',json={'group_ids':[11],'start_date':'2026-09-02','end_date':'2026-09-02'})
    assert response.status_code==200,response.text
    assert len(response.json()['created_reports'])==1
    # Same numeric ID may exist in another report kind; typed keys keep them separate.
    assert client.get('/api/reports').json()['total']==4
    assert client.get('/api/reports',params={'date_from':'2026-09-01','date_to':'2026-09-01'}).json()['total']==2
    for report_id,text in old:
        detail=client.get(f'/api/reports/daily/{report_id}')
        assert detail.status_code==200
        assert detail.json()['markdown']==text
    points=client.get('/api/summary-reading',params={'date_from':'2026-09-01','date_to':'2026-09-01'}).json()
    assert {item['group_id'] for item in points['items']}=={11,22}
