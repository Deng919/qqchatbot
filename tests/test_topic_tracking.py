import json
from datetime import datetime, timezone

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage


@pytest.fixture
def ar(tmp_path):
    archive = Archive.open(tmp_path / 'topics.sqlite')
    archive.upsert_groups([GroupConfig(group_id=1, name='一群'), GroupConfig(group_id=2, name='二群')])
    yield archive
    archive.close()


def service(ar):
    from qq_digest.topic_tracking import TopicTrackingService
    return TopicTrackingService(ar)


def publish(ar, tmp_path, *, group=1, date='2026-10-01', titles=('部署流水线故障',),
            ids=None, kind='daily', payload=None, versioned=True):
    mids = ids if ids is not None else [f'{date}-{i}' for i in range(len(titles))]
    stamp = datetime.fromisoformat(date + 'T04:00:00+00:00')
    ar.ingest([NormalizedMessage(msg_id=mid, group_id=group, timestamp=stamp,
        text='有效讨论', collected_at=stamp) for mid in mids if mid])
    payload = payload if payload is not None else {'evidence_version': 1,
        'main_topics': [{'topic': title, 'summary': '进展 ' + title,
                        'message_ids': [mids[i]] if mids[i] else []} for i,title in enumerate(titles)]}
    path = tmp_path / f'{kind}-{group}-{date}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    kwargs = dict(group_id=group, markdown_path=path.with_suffix('.md'), json_path=path,
                  candidate_ids=[])
    if versioned:
        kwargs.update(revision_markdown='内容', revision_payload=payload)
    if kind == 'daily':
        rid = ar.record_report(report_date=date, **kwargs)
    else:
        rid = ar.record_manual_report(start_date=date, end_date=date, detail_mode='adaptive',
            effective_template='adaptive', input_fingerprint='x', source_message_count=1, **kwargs)
    return rid, path


def topics(s):
    return s.list_topics()['topics']


def test_exact_specific_titles_group_across_two_groups_three_days(ar, tmp_path):
    for group in (1,2):
        for date in ('2026-10-01','2026-10-02','2026-10-03'):
            publish(ar,tmp_path,group=group,date=date)
    s = service(ar)
    result = s.refresh()
    assert result == dict(reports_processed=6, skipped_reports=0, discussions=6, topics=1)
    topic = topics(s)[0]
    assert topic['discussion_count'] == 6 and len(topic['groups']) == 2
    assert topic['first_date'] == '2026-10-01' and topic['last_date'] == '2026-10-03'
    assert s.refresh()['discussions'] == 6
    assert s.detail(topic['topic_id'])['total'] == 6


def test_normalization_generic_titles_and_kind_identity(ar,tmp_path):
    publish(ar,tmp_path,titles=('API V2：部署故障','日常讨论','选型'),ids=['a','b','c'])
    publish(ar,tmp_path,group=2,titles=('ａｐｉ v2 部署故障','日常讨论','选型'),ids=['a','b','c'])
    publish(ar,tmp_path,kind='range',titles=('API V2：部署故障',),ids=['a'])
    s=service(ar);s.refresh()
    assert len(topics(s)) == 5
    topic=next(t for t in topics(s) if t['discussion_count']==3)
    assert {d['report_kind'] for d in s.detail(topic['topic_id'])['discussions']} == {'daily','range'}


def test_manual_split_rename_archive_reorder_reopen_and_new_identity(ar,tmp_path):
    publish(ar,tmp_path,titles=('部署流水线故障','支付网关故障'),ids=['a','b'])
    s=service(ar);s.refresh()
    t=next(t for t in topics(s) if t['title']=='部署流水线故障')
    d=s.detail(t['topic_id'])['discussions'][0]
    moved=s.move(d['discussion_id'],new_title='人工排查跟踪',expected_revision=d['revision'])
    manual=s.detail(moved['topic_id'])
    s.update(manual['topic_id'],title='已改名',status='archived',expected_revision=manual['revision'])
    publish(ar,tmp_path,titles=('支付网关故障','部署流水线故障'),ids=['b','a'])
    s.refresh()
    manual=s.detail(manual['topic_id'])
    assert manual['title']=='已改名' and manual['status']=='archived'
    assert manual['discussions'][0]['link_mode']=='manual'
    publish(ar,tmp_path,titles=('部署流水线故障',),ids=['c'])
    s.refresh()
    assert not s.detail(manual['topic_id'])['discussions'][0]['is_current']
    reopened=Archive.open(tmp_path/'topics.sqlite')
    try:
        assert service(reopened).detail(manual['topic_id'])['status']=='archived'
    finally:
        reopened.close()


def test_only_verified_shared_claims_and_source_revalidated(ar,tmp_path):
    payload={'evidence_version':1,'main_topics':[{'topic':'部署流水线故障','summary':'已排查',
        'message_ids':['a','wrong']}], 'conclusions':[{'text':'有效结论','message_ids':['a']},
        {'text':'其他话题','message_ids':['b']},{'text':'伪来源','message_ids':['wrong']}],
        'open_questions':[{'text':'待复查','message_ids':['a']}]}
    publish(ar,tmp_path,ids=['a','b'],payload=payload)
    s=service(ar);s.refresh();t=topics(s)[0];d=s.detail(t['topic_id'])['discussions'][0]
    assert [c['text'] for c in t['latest_conclusions']]==['有效结论']
    claim=t['latest_conclusions'][0]
    assert (claim['group_id'],claim['group_name'],claim['date_label']) == (1,'一群','2026-10-01')
    assert d['source_ids']==['a'] and d['evidence_status']=='cited'
    assert s.source(d['discussion_id'],'a')['anchor_id']=='a'
    with pytest.raises(LookupError):s.source(d['discussion_id'],'b')
    ar.connection.execute("DELETE FROM messages WHERE msg_id='a'");ar.connection.commit()
    assert s.detail(t['topic_id'])['discussions'][0]['source_ids']==[]
    assert s.detail(t['topic_id'])['latest_conclusions']==[]
    with pytest.raises(LookupError):s.source(d['discussion_id'],'a')


def test_corruption_and_stale_current_hide_latest_but_preserve_history(ar,tmp_path):
    rid,path=publish(ar,tmp_path)
    s=service(ar);s.refresh();t=topics(s)[0];d=s.detail(t['topic_id'])['discussions'][0]
    path.write_text('broken',encoding='utf-8')
    assert s.detail(t['topic_id'])['latest_progress']==''
    with pytest.raises(LookupError):s.source(d['discussion_id'],d['source_ids'][0])
    assert s.refresh()['skipped_reports']==1
    assert s.detail(t['topic_id'])['discussions'][0]['evidence_status']=='unavailable'
    publish(ar,tmp_path,titles=('新的特定故障',),ids=['new'])
    s.refresh()
    assert s.detail(t['topic_id'])['total']==1


def test_historical_source_uses_episode_not_new_report(ar,tmp_path):
    publish(ar,tmp_path,ids=['old'])
    s=service(ar);s.refresh();t=topics(s)[0];d=s.detail(t['topic_id'])['discussions'][0]
    publish(ar,tmp_path,titles=('全新项目故障',),ids=['new'])
    s.refresh()
    assert s.source(d['discussion_id'],'old')['anchor_id']=='old'
    with pytest.raises(LookupError):s.source(d['discussion_id'],'new')


def test_legacy_has_no_invented_citations(ar,tmp_path):
    publish(ar,tmp_path,payload={'main_topics':[{'topic':'旧版特定讨论','summary':'旧内容',
        'message_ids':['a']}],'conclusions':['旧结论']},ids=['a'],versioned=False)
    s=service(ar);s.refresh();t=topics(s)[0];d=s.detail(t['topic_id'])['discussions'][0]
    assert d['evidence_status']=='legacy' and not d['source_ids']
    assert not t['latest_conclusions']


def test_optimistic_mutations_merge_and_suggestions(ar,tmp_path):
    from qq_digest.topic_tracking import TopicConflict
    publish(ar,tmp_path,titles=('部署流水线故障','部署流水线故障复查'),ids=['a','b'])
    s=service(ar);s.refresh();a,b=topics(s)
    d=s.detail(a['topic_id'])['discussions'][0]
    assert any(t['topic_id']==b['topic_id'] for t in s.suggestions(d['discussion_id'])['suggestions'])
    changed=s.update(a['topic_id'],title='人工标题',expected_revision=a['revision'])
    with pytest.raises(TopicConflict):s.update(a['topic_id'],status='archived',expected_revision=a['revision'])
    target=s.merge(a['topic_id'],b['topic_id'],expected_revision=changed['revision'],target_revision=b['revision'])
    assert target['discussion_count']==2 and len(topics(s))==1
    s.refresh();assert len(topics(s))==1
    with pytest.raises(TopicConflict):s.move(d['discussion_id'],new_title='拆分',expected_revision=d['revision'])


def test_filters_pages_validation_delete_group_and_sqlite_backup(ar,tmp_path):
    publish(ar,tmp_path);publish(ar,tmp_path,group=2,date='2026-10-02')
    s=service(ar);s.refresh()
    assert s.list_topics(group_id=2,date_from='2026-10-02',q='部署')['total']==1
    assert s.list_topics(date_to='2026-09-30')['total']==0
    assert s.list_topics(page=999,page_size=100000)['page_size']<=100
    with pytest.raises(ValueError):s.list_topics(date_from='2026-2-3')
    with pytest.raises(ValueError):s.list_topics(group_id=2**64)
    with pytest.raises(ValueError):s.update(topics(s)[0]['topic_id'],title='x'*201,expected_revision=1)
    backup=Archive.open(tmp_path/'backup.sqlite')
    try:
        ar.connection.backup(backup.connection)
        assert topics(service(backup))[0]['discussion_count']==2
    finally:backup.close()
    ar.delete_group(1)
    assert topics(s)[0]['discussion_count']==1
    ar.delete_group(2);assert topics(s)==[]


def test_refresh_transaction_rolls_back_entire_batch(ar,tmp_path):
    publish(ar,tmp_path)
    s=service(ar);s.refresh()
    original=topics(s)
    publish(ar,tmp_path,group=2,titles=('新的独立具体项目',))
    ar.connection.execute("CREATE TRIGGER reject_new_topic BEFORE INSERT ON tracked_topics BEGIN SELECT RAISE(ABORT,'reject'); END")
    ar.connection.commit()
    with pytest.raises(Exception,match='reject'):s.refresh()
    assert topics(s)==original


def test_merge_original_title_routes_new_episodes_to_surviving_topic(ar,tmp_path):
    publish(ar,tmp_path,titles=('部署流水线故障','支付网关故障'),ids=['a','b'])
    s=service(ar);s.refresh();a,b=topics(s)
    target=s.merge(a['topic_id'],b['topic_id'],expected_revision=a['revision'],target_revision=b['revision'])
    publish(ar,tmp_path,date='2026-10-02',titles=(a['title'],),ids=['new'])
    s.refresh()
    assert len(topics(s))==1
    assert topics(s)[0]['topic_id']==target['topic_id']


def test_wrong_group_date_unverified_and_malformed_reports(ar,tmp_path):
    publish(ar,tmp_path,group=2,ids=['other'])
    publish(ar,tmp_path,date='2026-09-01',ids=['outside'])
    rid,path=publish(ar,tmp_path,payload={'evidence_version':1,'main_topics':[
        {'topic':'安全来源校验话题','summary':'无有效引用','message_ids':['other','outside','missing']}],
        'conclusions':[{'text':'越界结论','message_ids':['outside']} ]})
    s=service(ar);s.refresh()
    t=next(t for t in topics(s) if t['title']=='安全来源校验话题')
    d=s.detail(t['topic_id'])['discussions'][0]
    assert d['evidence_status']=='unverified' and not d['source_ids']
    for source in ('other','outside','missing'):
        with pytest.raises(LookupError):s.source(d['discussion_id'],source)
    path.write_text('{"main_topics": null}',encoding='utf-8')
    assert s.refresh()['skipped_reports']==1


def test_duplicate_titles_with_different_evidence_keep_manual_identity_on_reorder(ar,tmp_path):
    publish(ar,tmp_path,titles=('部署流水线故障','部署流水线故障'),ids=['a','b'])
    s=service(ar);s.refresh();t=topics(s)[0]
    before=s.detail(t['topic_id'])['discussions']
    a=next(d for d in before if d['source_ids']==['a'])
    moved=s.move(a['discussion_id'],new_title='第一处人工拆分',expected_revision=a['revision'])
    publish(ar,tmp_path,titles=('部署流水线故障','部署流水线故障'),ids=['b','a'])
    s.refresh()
    assert s.detail(moved['topic_id'])['total']==1
    assert s.detail(moved['topic_id'])['discussions'][0]['source_ids']==['a']
    assert s.refresh()['discussions']==2


def test_group_deletion_invalidates_topic_revision(ar,tmp_path):
    from qq_digest.topic_tracking import TopicConflict
    publish(ar,tmp_path);publish(ar,tmp_path,group=2)
    s=service(ar);s.refresh();t=topics(s)[0]
    ar.delete_group(1)
    with pytest.raises(TopicConflict):
        s.update(t['topic_id'],title='陈旧修改',expected_revision=t['revision'])


def test_backup_restore_preserves_corrections_and_relocates_report_sources(tmp_path):
    from qq_digest.backup_restore import backup_data, inspect_backup, restore_backup
    root=tmp_path/'original'
    (root/'config').mkdir(parents=True)
    (root/'reports').mkdir()
    config=root/'config'/'config.yaml'
    secret=root/'config'/'secret.txt'
    secret.write_text('test-secret-only-012345678901234567890',encoding='utf-8')
    config.write_text('security:\n  web_password_hash: test\n'
        f"  session_secret_file: '{secret.as_posix()}'\n"
        'ai:\n  model: test\n  base_url: https://example.com\n  api_key_env: TEST_KEY\n',encoding='utf-8')
    archive=Archive.open(root/'archive'/'archive.sqlite')
    try:
        archive.upsert_groups([GroupConfig(group_id=1,name='备份群')])
        publish(archive,root/'reports',titles=('部署流水线故障','支付网关故障'),ids=['a','b'])
        s=service(archive);s.refresh();a,b=topics(s)
        target=s.merge(a['topic_id'],b['topic_id'],expected_revision=a['revision'],target_revision=b['revision'])
        renamed=s.update(target['topic_id'],title='备份保留人工名称',status='archived',expected_revision=target['revision'])
    finally:archive.close()
    backups=tmp_path/'backups';cache=tmp_path/'cache'
    saved=backup_data(config,backups,temp_root=cache)
    preview=inspect_backup(saved['path'],temp_root=cache)
    install=tmp_path/'install';install.mkdir()
    (install/'launcher.json').write_text(json.dumps({'config_path':str(config)}),encoding='utf-8')
    destination=tmp_path/'restored'
    restore_backup(saved['path'],destination,config,install,backup_root=backups,temp_root=cache,
        expected_sha256=preview['sha256'])
    restored=Archive.open(destination/'archive'/'archive.sqlite')
    try:
        s=service(restored);t=topics(s)[0]
        assert t['title']=='备份保留人工名称' and t['status']=='archived'
        assert t['revision']==renamed['revision'] and t['discussion_count']==2
        d=s.detail(t['topic_id'])['discussions'][0]
        assert s.source(d['discussion_id'],d['source_ids'][0])['anchor_id']==d['source_ids'][0]
        s.refresh();assert len(topics(s))==1
        assert restored.connection.execute('SELECT COUNT(*) FROM tracked_topic_aliases').fetchone()[0]==1
    finally:restored.close()


def test_detail_reads_each_current_report_file_once(ar,tmp_path,monkeypatch):
    from pathlib import Path
    _,path=publish(ar,tmp_path,titles=('部署流水线故障','支付网关故障'),ids=['a','b'],payload={
        'evidence_version':1,'main_topics':[{'topic':'部署流水线故障','summary':'进展', 'message_ids':['a']},
            {'topic':'支付网关故障','summary':'其他','message_ids':['b']}],
        'conclusions':[{'text':'结论','message_ids':['a']}],
        'open_questions':[{'text':'待处理','message_ids':['a']}]})
    s=service(ar);s.refresh()
    topic=next(t for t in topics(s) if t['title']=='部署流水线故障')
    read_bytes=Path.read_bytes;calls=[]
    def counted(p):
        calls.append(p)
        return read_bytes(p)
    monkeypatch.setattr(Path,'read_bytes',counted)
    s.detail(topic['topic_id'])
    assert calls==[path]


def test_corrupt_current_report_keeps_preexisting_historical_evidence(ar,tmp_path):
    publish(ar,tmp_path,ids=['old'])
    s=service(ar);s.refresh();old=s.detail(topics(s)[0]['topic_id'])['discussions'][0]
    _,path=publish(ar,tmp_path,titles=('全新故障对象',),ids=['new'])
    s.refresh()
    path.write_text('broken',encoding='utf-8');s.refresh()
    assert s.source(old['discussion_id'],'old')['anchor_id']=='old'


def test_repaired_report_restores_prior_episode_history_without_latest_stale_summary(ar,tmp_path):
    _,path=publish(ar,tmp_path,ids=['old'])
    s=service(ar);s.refresh();old=s.detail(topics(s)[0]['topic_id'])['discussions'][0]
    path.write_text('broken',encoding='utf-8');s.refresh()
    publish(ar,tmp_path,titles=('全新故障对象',),ids=['new']);s.refresh()
    assert s.source(old['discussion_id'],'old')['anchor_id']=='old'
    assert s.detail(old['topic_id'])['latest_progress']==''


@pytest.mark.parametrize('version,other', [('2.0','20'),('2.0-rc1','2.0rc1'),('2/0','20')])
def test_meaningful_version_separators_do_not_collapse(ar,tmp_path,version,other):
    publish(ar,tmp_path,titles=(f'SDK {version} 部署故障',),ids=['first'])
    publish(ar,tmp_path,group=2,titles=(f'SDK {other} 部署故障',),ids=['second'])
    s=service(ar);s.refresh()
    assert len(topics(s))==2
    assert all(t['discussion_count']==1 for t in topics(s))


def duplicate_payload(summaries):
    return {'evidence_version':1,'main_topics':[{'topic':'部署流水线故障',
        'summary':summary,'message_ids':['shared']} for summary in summaries]}


def test_duplicate_shared_sources_keep_manual_body_through_reorder_and_becoming_single(ar,tmp_path):
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 排查进展','权限排查进展']))
    s=service(ar);s.refresh();t=topics(s)[0]
    dns=next(d for d in s.detail(t['topic_id'])['discussions'] if d['summary']=='DNS 排查进展')
    moved=s.move(dns['discussion_id'],new_title='DNS 人工持续跟踪',expected_revision=dns['revision'])
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['权限排查进展','DNS 排查进展']))
    s.refresh()
    manual=s.detail(moved['topic_id'])
    assert manual['total']==1
    assert manual['discussions'][0]['summary']=='DNS 排查进展'
    assert manual['discussions'][0]['discussion_id']==dns['discussion_id']
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 排查进展']))
    s.refresh();assert s.refresh()['discussions']==2
    reopened=Archive.open(tmp_path/'topics.sqlite')
    try:
        restarted=service(reopened);restarted.refresh()
        current=restarted.detail(moved['topic_id'])['discussions'][0]
        assert current['discussion_id']==dns['discussion_id'] and current['is_current']
        assert current['summary']=='DNS 排查进展' and current['link_mode']=='manual'
    finally:reopened.close()


def test_changed_duplicate_body_keeps_manual_old_episode_historical(ar,tmp_path):
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 排查进展','权限排查进展']))
    s=service(ar);s.refresh();t=topics(s)[0]
    dns=next(d for d in s.detail(t['topic_id'])['discussions'] if d['summary']=='DNS 排查进展')
    moved=s.move(dns['discussion_id'],new_title='DNS 人工持续跟踪',expected_revision=dns['revision'])
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['权限排查进展','完全不同的故障进展']))
    s.refresh()
    old=s.detail(moved['topic_id'])
    assert old['total']==1 and old['discussions'][0]['summary']=='DNS 排查进展'
    assert not old['discussions'][0]['is_current'] and old['latest_progress']==''
    assert s.refresh()['discussions']==3
    assert s.source(dns['discussion_id'],'shared')['anchor_id']=='shared'


def test_unique_wording_change_retains_manual_association(ar,tmp_path):
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 初步排查']))
    s=service(ar);s.refresh();t=topics(s)[0];dns=s.detail(t['topic_id'])['discussions'][0]
    moved=s.move(dns['discussion_id'],new_title='DNS 人工持续跟踪',expected_revision=dns['revision'])
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 排查补充：已经检查配置']))
    s.refresh();current=s.detail(moved['topic_id'])['discussions'][0]
    assert current['discussion_id']==dns['discussion_id'] and current['is_current']
    assert current['summary']=='DNS 排查补充：已经检查配置'
    assert s.refresh()['discussions']==1


def test_single_to_duplicate_preserves_exact_manual_body(ar,tmp_path):
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 排查进展']))
    s=service(ar);s.refresh();dns=s.detail(topics(s)[0]['topic_id'])['discussions'][0]
    moved=s.move(dns['discussion_id'],new_title='DNS 人工持续跟踪',expected_revision=dns['revision'])
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['权限排查进展','DNS 排查进展']))
    s.refresh();manual=s.detail(moved['topic_id'])
    assert manual['total']==1 and manual['discussions'][0]['is_current']
    assert manual['discussions'][0]['discussion_id']==dns['discussion_id']
    assert manual['discussions'][0]['summary']=='DNS 排查进展'


def test_existing_ordinal_index_adopts_matching_body_without_moving_manual_opinion(ar,tmp_path):
    import hashlib
    from qq_digest.topic_tracking import normalized_title
    rid,_=publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['DNS 排查进展','权限排查进展']))
    s=service(ar);s.refresh();t=topics(s)[0]
    records=sorted(s.detail(t['topic_id'])['discussions'],key=lambda d:d['discussion_id'])
    base=json.dumps(['daily',rid,normalized_title('部署流水线故障'),['shared']],ensure_ascii=False)
    with ar.transaction():
        for occurrence,d in enumerate(records):
            legacy=hashlib.sha256(f'{base}:{occurrence}'.encode('utf-8')).hexdigest()
            ar.connection.execute('UPDATE topic_discussions SET identity=? WHERE discussion_id=?',
                                  (legacy,d['discussion_id']))
    dns=next(d for d in records if d['summary']=='DNS 排查进展')
    moved=s.move(dns['discussion_id'],new_title='DNS 人工持续跟踪',expected_revision=dns['revision'])
    publish(ar,tmp_path,ids=['shared'],payload=duplicate_payload(['权限排查进展','DNS 排查进展']))
    s.refresh()
    current=s.detail(moved['topic_id'])['discussions'][0]
    assert current['discussion_id']==dns['discussion_id'] and current['summary']=='DNS 排查进展'
    assert current['is_current'] and s.refresh()['discussions']==2


@pytest.mark.parametrize('section',['conclusions','open_questions'])
def test_associated_statement_secondary_sources_can_open_context_and_are_revalidated(ar,tmp_path,section):
    payload={'evidence_version':1,'main_topics':[{'topic':'部署流水线故障','summary':'进展',
        'message_ids':['topic-source']}],section:[{'text':'有归属的结论或问题',
        'message_ids':['topic-source','second-source','outside']}],
        'overview':'无关总览','overview_message_ids':['unrelated']}
    publish(ar,tmp_path,date='2026-09-01',ids=['outside'])
    _,path=publish(ar,tmp_path,ids=['topic-source','second-source','unrelated'],payload=payload)
    s=service(ar);s.refresh();t=topics(s)[0];d=s.detail(t['topic_id'])['discussions'][0]
    assert d['source_ids']==['topic-source']
    assert d[section][0]['source_ids']==['topic-source','second-source']
    assert s.source(d['discussion_id'],'second-source')['anchor_id']=='second-source'
    for mid in ('outside','unrelated'):
        with pytest.raises(LookupError):s.source(d['discussion_id'],mid)
    ar.connection.execute("UPDATE messages SET timestamp='2026-09-01T04:00:00+00:00' WHERE msg_id='second-source'")
    ar.connection.commit()
    with pytest.raises(LookupError):s.source(d['discussion_id'],'second-source')
    path.write_text('broken',encoding='utf-8')
    with pytest.raises(LookupError):s.source(d['discussion_id'],'topic-source')
