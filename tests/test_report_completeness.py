import json

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig


def coverage(source=20, included=20, truncated=False, **kwargs):
    from qq_digest.report_completeness import input_coverage
    return input_coverage({"source_messages": source, "included_messages": included,
                           "context_truncated": truncated}, **kwargs)


def test_normal_input_only_claims_local_input_scope():
    result = coverage()
    assert result["status"] == "normal"
    assert result["label"] == "输入已覆盖"
    assert result["notes"] == []


@pytest.mark.parametrize('source,included,truncated,mismatch,label', [
    (3,3,False,False,'消息较少'),
    (20,10,True,False,'部分消息未纳入'),
    (20,2,True,False,'部分消息未纳入'),
    (20,18,False,False,'已过滤无效消息'),
    (20,20,False,True,'采集范围异常'),
])
def test_input_labels_name_the_actual_reason(source,included,truncated,mismatch,label):
    assert coverage(source,included,truncated,archive_mismatch=mismatch)['label']==label


def test_low_sample_and_truncation_are_visible():
    result = coverage(20, 2, True)
    assert result["status"] == "limited"
    assert any("样本较少" in note for note in result["notes"])
    assert any("长度限制" in note for note in result["notes"])


def test_preprocessing_and_archive_mismatch_have_different_reasons():
    result = coverage(20, 18, archive_mismatch=True)
    assert any("预处理" in note for note in result["notes"])
    assert any("采集" in note and "归档" in note for note in result["notes"])


@pytest.mark.parametrize("diagnostics", [None, {}, {"source_messages": True, "included_messages": 1},
    {"source_messages": 2, "included_messages": 3}, {"source_messages": -1, "included_messages": 0},
    {"source_messages": 10, "included_messages": 10},
    {"source_messages": 10, "included_messages": 10, "context_truncated": "false"}])
def test_missing_or_invalid_diagnostics_never_claim_complete(diagnostics):
    from qq_digest.report_completeness import input_coverage
    assert input_coverage(diagnostics)["status"] == "unknown"


@pytest.fixture
def archived(tmp_path):
    archive = Archive.open(tmp_path / "archive.sqlite")
    archive.upsert_groups([GroupConfig(group_id=i, name=f"群{i}") for i in (1, 2, 3)])
    yield archive, tmp_path
    archive.close()


def write_report(archive, directory, group_id, payload=None):
    md, js = directory / f"{group_id}.md", directory / f"{group_id}.json"
    md.write_text("# 报告", encoding="utf-8")
    js.write_text(json.dumps(payload or {"diagnostics": {
        "source_messages": 20, "included_messages": 20, "context_truncated": False}}), encoding="utf-8")
    archive.record_report(group_id=group_id, report_date="2026-09-29", markdown_path=md,
                          json_path=js, candidate_ids=[])
    return js


def service(archive):
    from qq_digest.report_completeness import ReportCompletenessService
    return ReportCompletenessService(archive)


def test_partial_daily_run_stays_limited_even_with_old_report(archived):
    archive, directory = archived
    write_report(archive, directory, 1)
    write_report(archive, directory, 2)
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job, "partial_success", group_outcomes={1: "success", 2: "failed", 3: "skipped"})
    result = service(archive).daily("2026-09-29")
    assert result["status"] == "limited"
    assert result["failed_groups"] == 1
    assert result["no_message_groups"] == 1
    assert result["report_groups"] == 2
    assert result["participating_groups"] == 3
    assert any("失败" in note for note in result["notes"])


def test_normal_report_does_not_inherit_other_groups_limits(archived):
    archive,directory=archived
    normal=write_report(archive,directory,1)
    write_report(archive,directory,2,{'diagnostics':{'source_messages':3,'included_messages':3,'context_truncated':False}})
    job=archive.start_job('daily_digest',target_date='2026-09-29')
    archive.finish_job(job,'success',group_outcomes={1:'success',2:'success',3:'skipped'})
    report=service(archive).report(str(normal),'daily','2026-09-29')
    assert report['status']=='normal' and report['notes']==[]
    assert report['label']=='输入已覆盖'
    assert report['daily']['status']=='limited' and report['daily']['notes']


def test_unknown_daily_scope_does_not_replace_valid_report_input(archived):
    archive,directory=archived
    path=write_report(archive,directory,1)
    report=service(archive).report(str(path),'daily','2026-09-29')
    assert report['status']=='normal' and report['notes']==[]
    assert report['daily']['status']=='unknown'


def test_only_own_failed_attempt_marks_an_existing_report(archived):
    archive,directory=archived
    path=write_report(archive,directory,1)
    job=archive.start_job('daily_digest',target_date='2026-09-29')
    archive.finish_job(job,'partial_success',json.dumps([{'group_id':1,'stage':'summary'}]),group_outcomes={1:'failed',2:'success'})
    failed=service(archive).report(str(path),'daily','2026-09-29',group_id=1)
    assert failed['status']=='limited' and failed['label']=='本次生成失败'
    assert any('之前' in note for note in failed['notes'])
    other=service(archive).report(str(path),'daily','2026-09-29',group_id=2)
    assert other['status']=='normal' and other['notes']==[]


@pytest.mark.parametrize('scenario',['cached_notification','published_notification','regenerated'])
def test_report_is_not_marked_failed_after_notification_or_recovery(archived,scenario):
    archive,directory=archived
    path=write_report(archive,directory,1)
    job=archive.start_job('daily_digest',target_date='2026-09-29')
    if scenario=='published_notification':path=write_report(archive,directory,1)
    stage='notification' if scenario=='cached_notification' else 'summary'
    archive.finish_job(job,'partial_success',json.dumps([{'group_id':1,'stage':stage}]),group_outcomes={1:'failed'})
    if scenario=='regenerated':path=write_report(archive,directory,1)
    result=service(archive).report(str(path),'daily','2026-09-29',group_id=1)
    assert result['status']=='normal' and result['notes']==[]
    assert result['daily']['status']=='limited'


def test_successful_retry_clears_day_failure_but_not_input_warning(archived):
    archive, directory = archived
    write_report(archive, directory, 1)
    old = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(old, "failed", group_outcomes={1: "failed"})
    new = archive.start_job("daily_digest", target_date="2026-09-29", retry_of_job_id=old)
    archive.finish_job(new, "success", group_outcomes={1: "skipped"})
    result = service(archive).daily("2026-09-29")
    assert result["status"] == "normal"
    assert result["job_id"] == new
    assert result["label"] == "本次已覆盖"
    write_report(archive, directory, 1, {"diagnostics": {"source_messages": 2,
        "included_messages": 2, "context_truncated": False}})
    assert service(archive).daily("2026-09-29")["status"] == "limited"


def test_success_without_expected_report_is_limited(archived):
    archive, _ = archived
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job, "success", group_outcomes={1: "success"})
    assert service(archive).daily("2026-09-29")["missing_report_groups"] == 1
    assert service(archive).daily("2026-09-29")["status"] == "limited"


def test_quiet_group_is_explained_without_claiming_collection_failure(archived):
    archive, _ = archived
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job, "success", group_outcomes={1: "skipped"})
    result = service(archive).daily("2026-09-29")
    assert result["status"] == "normal"
    assert result["label"] == "生成成功，1 个群无消息跳过"
    assert result["failed_groups"] == 0
    assert any("无归档消息" in note for note in result["notes"])


def test_successful_reports_and_quiet_groups_are_normal(archived):
    archive, directory = archived
    write_report(archive, directory, 1)
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job, "success", group_outcomes={1: "success", 2: "skipped", 3: "skipped"})
    result = service(archive).daily("2026-09-29")
    assert result["status"] == "normal"
    assert result["label"] == "生成成功，2 个群无消息跳过"
    assert result["report_groups"] == 1 and result["no_message_groups"] == 2
    assert result["failed_groups"] == result["missing_report_groups"] == 0


@pytest.mark.parametrize("issue,expected", [("missing", "limited"), ("truncated", "limited"), ("unknown", "unknown")])
def test_quiet_groups_do_not_hide_real_report_problems(archived,issue,expected):
    archive, directory = archived
    if issue == "truncated":
        write_report(archive,directory,1,{"diagnostics":{"source_messages":20,"included_messages":10,"context_truncated":True}})
    elif issue == "unknown":
        write_report(archive,directory,1,{"overview":"旧报告"})
    job=archive.start_job("daily_digest",target_date="2026-09-29")
    archive.finish_job(job,"success",group_outcomes={1:"success",2:"skipped"})
    result=service(archive).daily("2026-09-29")
    assert result["status"]==expected
    assert "生成成功" not in result["label"]


def test_legacy_jobs_and_bad_report_json_are_unknown(archived):
    archive, directory = archived
    assert service(archive).daily("2026-09-29")["status"] == "unknown"
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job, "success")
    assert service(archive).daily("2026-09-29")["status"] == "unknown"
    js = write_report(archive, directory, 1)
    js.write_text("broken", encoding="utf-8")
    assert service(archive).report(str(js), "range", "2026-09-29")["status"] == "unknown"


def test_preflight_failure_and_running_job_cannot_claim_day_covered(archived):
    archive, _ = archived
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    assert service(archive).daily("2026-09-29")["status"] == "unknown"
    archive.finish_job(job, "failed", "AI unavailable")
    assert service(archive).daily("2026-09-29")["status"] == "limited"


def test_range_report_does_not_inherit_unrelated_daily_failure(archived):
    archive, directory = archived
    js = write_report(archive, directory, 1)
    job = archive.start_job("daily_digest", target_date="2026-09-29")
    archive.finish_job(job, "failed")
    result = service(archive).report(str(js), "range", "2026-09-29")
    assert result["status"] == "normal"
    assert result["daily"] is None
    assert "本地归档" in result["scope"]


def test_persisted_archive_mismatch_survives_report_read(archived):
    archive, directory = archived
    js = write_report(archive, directory, 1, {"diagnostics": {
        "source_messages": 20, "included_messages": 20, "context_truncated": False},
        "coverage": {"archive_mismatch": True}})
    result = service(archive).report(str(js), "range", "2026-09-29")
    assert result["status"] == "limited"
    assert any("采集" in note for note in result["notes"])

def reviewed_payload():
    return {
        'group_id':123,
        'window_start':'2026-10-06T00:00:00+08:00',
        'window_end':'2026-10-06T23:59:00+08:00',
        'window_end_inclusive':True,
        'diagnostics':{'source_messages':1000,'included_messages':900,'context_truncated':True},
        'source_review':{'version':1,'kind':'full_window','group_id':123,
            'window_start':'2026-10-06T00:00:00+08:00','window_end':'2026-10-06T23:59:00+08:00',
            'window_end_inclusive':True,'reviewed_at':'2026-10-29T04:00:00+00:00',
            'archive_message_count':1000,'claims_verified':True,'source_sha256':'a'*64},
    }


def test_full_source_review_explains_recovered_truncation_without_rewriting_old_diagnostics():
    from qq_digest.report_completeness import _read_input
    payload=reviewed_payload()
    result=_read_input(payload)
    assert result['status']=='normal'
    assert result['label']=='已核对完整归档'
    assert result['source_messages']==result['included_messages']==1000
    assert result['notes']==[]
    assert payload['diagnostics']['context_truncated'] is True
    assert payload['diagnostics']['included_messages']==900


@pytest.mark.parametrize('field,value',[
    ('version',True),('kind','citations'),('group_id',456),('archive_message_count',999),
    ('archive_message_count',True),('claims_verified',False),('source_sha256','not-a-hash'),
    ('reviewed_at','yesterday'),('reviewed_at','2026-10-29T12:00:00'),
    ('window_start','2026-10-05T00:00:00+08:00'),('window_end_inclusive',False),
])
def test_partial_or_mismatched_review_never_clears_truncation(field,value):
    from qq_digest.report_completeness import _read_input
    payload=reviewed_payload();payload['source_review'][field]=value
    assert _read_input(payload)['label']=='部分消息未纳入'


def test_full_source_review_does_not_hide_archive_mismatch_or_unknown_generation_stats():
    from qq_digest.report_completeness import _read_input
    payload=reviewed_payload();payload['coverage']={'archive_mismatch':True}
    assert _read_input(payload)['label']=='采集范围异常'
    payload=reviewed_payload();payload['diagnostics']={}
    assert _read_input(payload)['status']=='unknown'
