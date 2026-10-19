"""Revalidate existing extracted listings against their original archived messages.

Used on a database snapshot before publishing repaired derived tables. No AI calls,
raw-message edits, or scan-receipt changes are needed for this repair.
"""
from __future__ import annotations

import json
import re

from .secondhand import SecondhandService
from .secondhand_evidence import source_units


def repair_existing(archive, timezone_name='Asia/Shanghai'):
    service=SecondhandService(archive,timezone_name)
    rows=[dict(row) for row in archive.connection.execute('SELECT * FROM market_posts')]
    grouped={}
    for row in rows:grouped.setdefault((row['group_id'],row['msg_id']),[]).append(row)
    result={'before_posts':len(rows),'after_posts':0,'repaired_sources':0,'failed_sources':0,'failures':[]}
    blocked=service.blocked_ids()
    for (group_id,msg_id),posts in grouped.items():
        message=archive.connection.execute('SELECT * FROM messages WHERE group_id=? AND msg_id=?',(group_id,msg_id)).fetchone()
        units={unit['key']:unit for unit in source_units([archive._row_to_message(message)],blocked)} if message else {}
        for source_key in dict.fromkeys(row['source_key'] for row in posts):
            old=[row for row in posts if row['source_key']==source_key]
            if any(row['seller_qq'] in blocked or row['forwarder_qq'] in blocked for row in old):continue
            unit=units.get(source_key)
            if unit is None or unit['state']!='ready':
                result['failed_sources']+=1;result['failures'].append('source unavailable');continue
            payload=[]
            for row in old:
                evidence=row['evidence'];price_text=''
                if row['price'] is not None:
                    for match in re.finditer(r'(?<![\d.])(?:￥|¥)?\d+(?:\.\d+)?\s*(?:rmb|元|块|r)?(?![\d.])',row['price_text'],re.I):
                        number=re.search(r'\d+(?:\.\d+)?',match[0])
                        if float(number[0])==row['price'] and match[0] in evidence:
                            price_text=match[0].strip();break
                payload.append({'source_key':source_key,'title':row['title'],'direction':row['direction'],
                                'price':row['price'] if price_text else None,'price_text':price_text,
                                'description':row['description'] if row['description'] in evidence else '',
                                'condition':row['condition_text'] if row['condition_text'] in evidence else '',
                                'status':row['status'],'evidence':evidence,
                                'bundle_mode':row.get('bundle_mode','single'),'bundle_label':row.get('bundle_label',''),
                                'components':json.loads(row.get('components_json') or '[]')})
            try:
                service.accept_units([unit],{'items':payload},validate_only=True)
                service.accept_units([unit],{'items':payload},record_scan=False)
                result['repaired_sources']+=1
            except ValueError as exc:
                result['failed_sources']+=1;result['failures'].append(str(exc))
    result['after_posts']=archive.connection.execute('SELECT count(*) FROM market_posts').fetchone()[0]
    result['failures']=sorted(set(result['failures']))
    return result
