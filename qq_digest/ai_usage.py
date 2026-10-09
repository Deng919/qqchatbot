"""Persistent API attempt accounting, independent of business transactions."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


class UsageLimitError(RuntimeError):
    pass


class UsageConflict(ValueError):
    pass


LIMIT_MESSAGE = '本月 AI 调用已达上限，可调整上限或下月继续'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS ai_usage_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL DEFAULT 0,
    monthly_limit INTEGER NOT NULL DEFAULT 0, timezone TEXT NOT NULL, rates TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS ai_usage_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, started_at TEXT NOT NULL,
    deadline TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','success','failed','unknown')),
    usage TEXT, rates TEXT
);
CREATE INDEX IF NOT EXISTS ai_usage_month ON ai_usage_attempts(month,attempt_id);
'''
TOKEN_FIELDS = ('prompt_tokens','completion_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')


def provider_key(base_url):
    """Origin only: never persist URL credentials, paths or query strings."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('https','http') or not parsed.hostname:
        raise ValueError('接口来源必须是 HTTP 或 HTTPS 地址')
    host = parsed.hostname.lower()
    if ':' in host:host = '['+host+']'
    port = parsed.port
    return parsed.scheme+'://'+host+(f':{port}' if port and port != (443 if parsed.scheme=='https' else 80) else '')


def normalize_rates(rates):
    if not isinstance(rates,list) or len(rates)>100:raise ValueError('最多设置 100 个模型单价')
    result=[];seen=set()
    for rate in rates:
        if not isinstance(rate,dict) or set(rate) != {'provider','model','input_hit','input_miss','output'}:
            raise ValueError('模型单价格式无效')
        if not isinstance(rate['provider'],str) or len(rate['provider'])>300:raise ValueError('接口来源无效')
        source=provider_key(rate['provider'])
        if source != rate['provider']:raise ValueError('接口来源只填写协议与主机')
        model=rate['model']
        if not isinstance(model,str) or not model.strip() or model!=model.strip() or len(model)>100:
            raise ValueError('模型名称无效')
        if (source,model) in seen:raise ValueError('模型单价重复')
        seen.add((source,model));entry={'provider':source,'model':model}
        for key in ('input_hit','input_miss','output'):
            value=rate[key]
            if value is None:entry[key]=None;continue
            if not isinstance(value,str) or not re.fullmatch(r'\d{1,7}(?:\.\d{1,6})?',value) or Decimal(value)>1000000:
                raise ValueError('单价需为非负金额，最多六位小数')
            entry[key]=format(Decimal(value),'f')
        result.append(entry)
    return result


def normalize_usage(usage):
    if not isinstance(usage,dict):return None
    clean={key:usage[key] for key in TOKEN_FIELDS if type(usage.get(key)) is int and 0<=usage[key]<=10**12}
    return clean or None


def estimate_cost(usage,rates):
    if not usage or not rates or not all(key in usage for key in ('prompt_tokens','completion_tokens')):return None
    prompt=usage['prompt_tokens'];hit=usage.get('prompt_cache_hit_tokens');miss=usage.get('prompt_cache_miss_tokens')
    if hit is not None and miss is None:miss=prompt-hit
    if miss is not None and hit is None:hit=prompt-miss
    hit_price=rates.get('input_hit');miss_price=rates.get('input_miss');output_price=rates.get('output')
    if hit is None or miss is None:
        if prompt and (hit_price is None or miss_price is None or Decimal(hit_price)!=Decimal(miss_price)):return None
        hit=0;miss=prompt
    if min(hit,miss)<0 or hit+miss!=prompt:return None
    amounts=((hit,hit_price),(miss,miss_price),(usage['completion_tokens'],output_price))
    if any(count and price is None for count,price in amounts):return None
    return sum((Decimal(count)*Decimal(price or '0') for count,price in amounts),Decimal(0))/1000000


class UsageService:
    def __init__(self,path,timezone_name='Asia/Shanghai',now=None):
        self.path=Path(path)
        self.timezone_name=ZoneInfo(timezone_name).key
        self.now=now or (lambda:datetime.now(timezone.utc))

    @contextmanager
    def _db(self, *, read_only=False):
        if not read_only:self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro',uri=True) if read_only and self.path.is_file() else sqlite3.connect(':memory:' if read_only else self.path,timeout=30)
        db.row_factory=sqlite3.Row
        try:
            initialized=db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_usage_settings'").fetchone()
            if read_only and not initialized:
                db.close();db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
            if not read_only or not initialized:
                db.executescript(SCHEMA)
                db.execute('INSERT OR IGNORE INTO ai_usage_settings(singleton,timezone) VALUES(1,?)',(self.timezone_name,))
                db.commit()
            yield db
        finally:db.close()

    def _time(self):
        value=self.now()
        if value.tzinfo is None:raise ValueError('用量时钟必须包含时区')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _settings(db):
        row=dict(db.execute('SELECT * FROM ai_usage_settings WHERE singleton=1').fetchone())
        row.pop('singleton');row['rates']=json.loads(row['rates'])
        return row

    def settings(self):
        with self._db() as db:return self._settings(db)

    def update(self,monthly_limit,rates,expected_revision):
        if type(monthly_limit) is not int or not 0<=monthly_limit<=10**9:raise ValueError('月度上限需为非负整数')
        if type(expected_revision) is not int or expected_revision<0:raise ValueError('设置版本无效')
        rates=normalize_rates(rates)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute('UPDATE ai_usage_settings SET monthly_limit=?,rates=?,revision=revision+1 WHERE singleton=1 AND revision=?',
                (monthly_limit,json.dumps(rates),expected_revision))
            if changed.rowcount!=1:raise UsageConflict('用量设置已在其他窗口更新，请刷新后再保存')
            result=self._settings(db);db.commit();return result

    def reserve(self,provider,model,purpose,timeout_seconds):
        provider=provider_key(provider)
        if not isinstance(model,str) or len(model)>100 or not model.strip():raise ValueError('模型名称无效')
        if purpose not in ('summary','report_qa','archive_qa','vision','connection_test'):raise ValueError('调用用途无效')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');settings=self._settings(db)
            now=self._time()
            month=now.astimezone(ZoneInfo(settings['timezone'])).strftime('%Y-%m')
            calls=db.execute('SELECT count(*) FROM ai_usage_attempts WHERE month=?',(month,)).fetchone()[0]
            if settings['monthly_limit'] and calls>=settings['monthly_limit']:raise UsageLimitError(LIMIT_MESSAGE)
            rates=next((r for r in settings['rates'] if r['provider']==provider and r['model']==model),None)
            deadline=now+timedelta(seconds=max(60,float(timeout_seconds)*4+60))
            attempt=db.execute('INSERT INTO ai_usage_attempts(month,started_at,deadline,provider,model,purpose,status,rates) VALUES(?,?,?,?,?,?,?,?)',
                (month,now.isoformat(),deadline.isoformat(),provider,model,purpose,'pending',json.dumps(rates) if rates else None)).lastrowid
            db.commit();return attempt

    def finish(self,attempt_id,status,usage):
        if status not in ('success','failed','unknown'):raise ValueError('调用结果无效')
        clean=normalize_usage(usage)
        with self._db() as db:
            db.execute('UPDATE ai_usage_attempts SET status=?,usage=? WHERE attempt_id=? AND status IN (\'pending\',\'unknown\')',
                (status,json.dumps(clean) if clean else None,attempt_id))
            db.commit()

    def reject(self,attempt_id):
        """Business validation may reject a syntactically valid response later."""
        with self._db() as db:
            db.execute("UPDATE ai_usage_attempts SET status='failed' WHERE attempt_id=? AND status='success'",(attempt_id,))
            db.commit()

    def snapshot(self, *, read_only=False):
        with self._db(read_only=True) if read_only else self._db() as db:
            if not read_only:db.execute('BEGIN IMMEDIATE')
            now=self._time()
            if not read_only:db.execute("UPDATE ai_usage_attempts SET status='unknown' WHERE status='pending' AND deadline<?",(now.isoformat(),))
            settings=self._settings(db)
            month=now.astimezone(ZoneInfo(settings['timezone'])).strftime('%Y-%m')
            rows=db.execute("SELECT provider,model,CASE WHEN status='pending' AND deadline<? THEN 'unknown' ELSE status END AS status,usage,rates FROM ai_usage_attempts WHERE month=?",(now.isoformat(),month)).fetchall()
            if not read_only:db.commit()
        def empty():
            return dict(calls=0,successful=0,failed=0,unknown=0,pending=0,input_tokens=0,output_tokens=0,
                usage_unknown=0,cost_unknown=0,estimated_cost=None)
        total=empty();models={};known_cost=Decimal(0);priced=0
        for row in rows:
            item=models.setdefault((row['provider'],row['model']),dict(empty(),provider=row['provider'],model=row['model']))
            usage=json.loads(row['usage']) if row['usage'] else None
            cost=estimate_cost(usage,json.loads(row['rates']) if row['rates'] else None)
            for target in (total,item):
                target['calls']+=1;target[{'success':'successful'}.get(row['status'],row['status'])]+=1
                target['input_tokens']+=(usage or {}).get('prompt_tokens',0)
                target['output_tokens']+=(usage or {}).get('completion_tokens',0)
                if not usage or not all(key in usage for key in ('prompt_tokens','completion_tokens')):target['usage_unknown']+=1
                if cost is None:target['cost_unknown']+=1
            if cost is not None:
                known_cost+=cost;priced+=1
                item['estimated_cost']=format(Decimal(item['estimated_cost'] or '0')+cost,'f')
        if priced:total['estimated_cost']=format(known_cost,'f')
        return dict(total,settings=settings,month=month,models=list(models.values()))


def usage_for(config):
    return UsageService(config.archive_path,config.summary.timezone)
