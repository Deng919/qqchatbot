import importlib.util
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def usage(tmp_path):
    if not importlib.util.find_spec('qq_digest.ai_usage'):return None
    from qq_digest.ai_usage import UsageService
    return UsageService(tmp_path/'archive.sqlite', now=lambda:datetime(2026,10,8,tzinfo=timezone.utc))


def rate(**patch):
    return dict(provider='https://api.example.com',model='model',input_hit='1',input_miss='2',output='3',**patch)


def reserve(usage):
    return usage.reserve('https://api.example.com', 'model', 'summary', 30)


def test_last_slot_is_atomic_and_counts_survive_restart(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    from qq_digest.ai_usage import UsageLimitError, UsageService
    usage.update(1,[],0)
    def claim(_):
        try:return reserve(usage)
        except UsageLimitError:return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(claim,range(8)))
    assert sum(value is not None for value in results)==1
    restarted=UsageService(usage.path,now=usage.now)
    with pytest.raises(UsageLimitError):reserve(restarted)
    assert restarted.snapshot()['calls']==1


def test_ledger_month_is_fixed_and_new_month_restores_limit(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    from qq_digest.ai_usage import UsageService, UsageLimitError
    usage.update(1,[],0);reserve(usage)
    shifted=UsageService(usage.path,timezone_name='America/Los_Angeles',now=usage.now)
    assert shifted.settings()['timezone']=='Asia/Shanghai'
    with pytest.raises(UsageLimitError):reserve(shifted)
    future=UsageService(usage.path,now=lambda:datetime(2026,10,31,16,tzinfo=timezone.utc))
    reserve(future)
    assert future.snapshot()['month']=='2026-11' and future.snapshot()['calls']==1


def test_reservation_uses_month_after_database_wait(usage,monkeypatch):
    from qq_digest.ai_usage import UsageLimitError
    clock=[datetime(2026,10,31,15,59,59,tzinfo=timezone.utc)]
    usage.now=lambda:clock[0]
    usage.update(1,[],0)
    original=usage._db
    @contextmanager
    def waited_db():
        clock[0]=datetime(2026,10,31,16,0,1,tzinfo=timezone.utc)
        with original() as db:yield db
    monkeypatch.setattr(usage,'_db',waited_db)
    reserve(usage)
    with pytest.raises(UsageLimitError):reserve(usage)
    assert usage.snapshot()['month']=='2026-11' and usage.snapshot()['calls']==1


def test_usage_and_cost_snapshot_are_preserved(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    usage.update(0,[rate()],0)
    attempt=reserve(usage)
    usage.finish(attempt,'success',{'prompt_tokens':100,'completion_tokens':10,'prompt_cache_hit_tokens':25,'prompt_cache_miss_tokens':75})
    first=usage.snapshot()
    assert first['successful']==1 and first['input_tokens']==100 and first['output_tokens']==10
    assert first['estimated_cost']=='0.000205'
    changed=rate();changed['output']='900'
    usage.update(0,[changed],1)
    assert usage.snapshot()['estimated_cost']==first['estimated_cost']


def test_unknown_is_not_zero_and_expired_reservations_are_unknown(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    attempt=reserve(usage);usage.finish(attempt,'failed',None)
    reserve(usage)
    snapshot=usage.snapshot()
    assert snapshot['calls']==2 and snapshot['failed']==1 and snapshot['pending']==1
    assert snapshot['usage_unknown']==2 and snapshot['estimated_cost'] is None
    usage.now=lambda:datetime(2026,10,8,1,tzinfo=timezone.utc)
    snapshot=usage.snapshot()
    assert snapshot['unknown']==1 and snapshot['pending']==0
    assert snapshot['calls']==2


@pytest.mark.parametrize('invalid',[True,-1,1.5,'2',2**63])
def test_settings_reject_non_integer_limits_without_overwriting(usage,invalid):
    assert usage is not None, 'AI usage ledger is not implemented'
    with pytest.raises(ValueError):usage.update(invalid,[],0)
    assert usage.settings()['revision']==0 and usage.settings()['monthly_limit']==0


def test_setting_conflict_and_invalid_rates_are_atomic(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    from qq_digest.ai_usage import UsageConflict
    usage.update(10,[rate()],0)
    with pytest.raises(UsageConflict):usage.update(20,[],0)
    for value in ('NaN','Infinity','-1','1e999'):
        invalid=rate();invalid['output']=value
        with pytest.raises(ValueError):usage.update(20,[invalid],1)
    assert usage.settings()['monthly_limit']==10 and usage.settings()['revision']==1


def test_missing_cache_split_does_not_invent_priced_input(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    usage.update(0,[rate()],0)
    usage.finish(reserve(usage),'success',{'prompt_tokens':100,'completion_tokens':10})
    result=usage.snapshot()
    assert result['input_tokens']==100 and result['usage_unknown']==0
    assert result['estimated_cost'] is None and result['cost_unknown']==1


def test_zero_input_needs_no_input_rate(usage):
    prices=rate();prices['input_hit']=prices['input_miss']=None
    usage.update(0,[prices],0)
    usage.finish(reserve(usage),'success',{'prompt_tokens':0,'completion_tokens':10})
    assert usage.snapshot()['estimated_cost']=='0.00003'


def test_malformed_usage_and_rates_do_not_leak_content(usage):
    assert usage is not None, 'AI usage ledger is not implemented'
    usage.finish(reserve(usage),'failed',{'prompt_tokens':True,'completion_tokens':-1,'secret':'private message'})
    result=usage.snapshot()
    assert result['usage_unknown']==1
    copied=usage.path.parent/'copy.sqlite'
    with sqlite3.connect(usage.path) as source, sqlite3.connect(copied) as target:source.backup(target)
    from qq_digest.ai_usage import UsageService
    assert UsageService(copied,now=usage.now).snapshot()['calls']==1
    assert b'private message' not in usage.path.read_bytes()
