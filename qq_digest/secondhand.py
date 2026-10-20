"""Conservative cross-group listings with source snapshots, blocks and favorites."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .secondhand_evidence import digest, qq_number
from .secondhand_categories import CATEGORY_LABELS, item_category


def recent_window(timezone_name='Asia/Shanghai',now=None):
    day=(now or datetime.now(timezone.utc)).astimezone(ZoneInfo(timezone_name)).date()
    return (day-timedelta(days=6)).isoformat(),day.isoformat()


def date_window(start,end,timezone_name='Asia/Shanghai'):
    fallback=recent_window(timezone_name)
    start=start or fallback[0];end=end or fallback[1]
    a=date.fromisoformat(start);b=date.fromisoformat(end)
    if a.isoformat()!=start or b.isoformat()!=end or a>b or (b-a).days>365:raise ValueError('请选择有效日期范围，最多366天')
    tz=ZoneInfo(timezone_name)
    return datetime.combine(a,datetime.min.time(),tz).astimezone(timezone.utc).isoformat(),datetime.combine(b+timedelta(days=1),datetime.min.time(),tz).astimezone(timezone.utc).isoformat()


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())


def identity_body(value):
    value=re.sub(r'(?:价格|降价|现价|售价|到手价|现|降至|降到|￥|¥)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:元|块|rmb|r)?','',value,flags=re.I)
    value=re.sub(r'\d+(?:\.\d+)?\s*(?:元|块|rmb)','',value,flags=re.I)
    return normalized(value)


_PRICE_TEXT_RE = re.compile(r'^\s*(?:￥|¥)?\d+(?:\.\d+)?\s*(?:元|块|rmb|r)?\s*$', re.I)
_PROMOTION_RE = re.compile(r'会员|权益|年卡|课程|资料|代开|代购|优惠|快递', re.I)
_BUNDLE_RE = re.compile(r'打包|全套|整套|整包|合集|整箱|一起(?:出|卖|售)|全部(?:出|卖|售)')


def _item_identity(unit,direction,title,evidence,mode='single',components=()):
    body=identity_body(evidence)
    name=normalized(title) if mode=='single' else [mode,sorted(normalized(c['title']) for c in components)]
    identity=[unit['seller_qq'],direction,name,body]
    if unit['seller_qq'] is None or unit.get('original_time_unknown') or len(body)<8:identity.append(unit['key'])
    return digest(identity)


def _names(title,components_json):
    components=json.loads(components_json or '[]')
    return {normalized(title)}|{normalized(c['title']) for c in components}


def _join_split_bundles(posts, sources):
    grouped={}
    for post in posts:grouped.setdefault((post[4],post[10]),[]).append(post)
    result=[]
    for (source_key,direction),rows in grouped.items():
        unit=sources[source_key];text=unit['text']
        shared=len({row[16] for row in rows})==1
        prices={row[13] for row in rows if row[13] is not None}
        evidence=text if _BUNDLE_RE.search(text) else rows[0][16]
        separately_priced=len(re.findall(r'\d+(?:\.\d+)?\s*(?:元|块|rmb|r)(?![a-z])',evidence,re.I))>1
        mode='bundle' if _BUNDLE_RE.search(evidence) else 'collection'
        if (len(rows)<2 or (not shared and mode!='bundle') or (mode=='collection' and prices) or len(prices)>1
                or separately_priced or re.search(r'单独|单出|可拆|分别',text) or any(row[23]!='single' for row in rows)):
            result.extend(rows);continue
        parent=list(rows[0]);label=evidence[:80].strip()
        components=[{'title':row[9],'evidence':row[16],'price_text':''} for row in rows]
        parent[0]=digest([source_key,normalized(label),direction,mode]);parent[9]=label
        parent[1]=_item_identity(unit,direction,label,evidence,mode,components)
        parent[11]=evidence;parent[12]='';parent[15]='unknown';parent[16]=evidence
        parent[21]=digest([source_key,mode,label]);parent[22]=label;parent[23]=mode
        parent[24]=json.dumps(components,ensure_ascii=False,separators=(',',':'))
        result.append(tuple(parent))
    return result


def _components(item, *, text, evidence, title):
    mode=item.get('bundle_mode','single')
    if mode not in {'single','bundle','collection'}:
        raise ValueError('打包类型无效')
    label=item.get('bundle_label','')
    if not isinstance(label,str) or len(label)>160 or (label and label not in evidence):
        raise ValueError('打包名称无法核对原文')
    raw=item.get('components',[])
    if mode=='single':
        if raw not in (None,[]): raise ValueError('单件商品不能带组件')
        return mode,'',[],''
    if not label: label=title if title in evidence else evidence[:80]
    if not isinstance(raw,list) or not 2<=len(raw)<=100:
        raise ValueError('打包组件格式无效')
    if mode=='bundle' and (not _BUNDLE_RE.search(evidence) or re.search(r'单独|单出|可拆|分别',evidence)):
        raise ValueError('打包出售缺少原文依据')
    if len(re.findall(r'\d+(?:\.\d+)?\s*(?:元|块|rmb|r)(?![a-z])',evidence,re.I))>1:
        raise ValueError('独立价格不能作为整体打包价格')
    result=[]
    for component in raw:
        if not isinstance(component,dict): raise ValueError('打包组件格式无效')
        name=component.get('title');part=component.get('evidence')
        if not isinstance(name,str) or not 2<=len(name.strip())<=160 or not isinstance(part,str) or not part.strip() or part not in evidence:
            raise ValueError('打包组件缺少原文证据')
        terms=re.findall(r'[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]{2,}',name)
        if not any(term.casefold() in part.casefold() for term in terms):
            raise ValueError('打包组件名称无法核对原文')
        component_price=component.get('price_text','')
        if not isinstance(component_price,str) or len(component_price)>24 or (component_price and (component_price not in part or not _PRICE_TEXT_RE.fullmatch(component_price))):
            raise ValueError('打包组件价格无法核对原文')
        result.append({'title':name.strip(),'evidence':part,'price_text':component_price})
    return mode,label,result,digest([text,'bundle',label])


class SecondhandService:
    def __init__(self,archive,timezone_name='Asia/Shanghai'):
        self.archive=archive;self.db=archive.connection;self.timezone_name=timezone_name

    def add_groups(self,ids):
        with self.archive.transaction():
            for gid in ids:
                if not self.db.execute('SELECT 1 FROM groups WHERE group_id=?',(gid,)).fetchone():raise LookupError('群聊不存在')
                self.db.execute('INSERT OR IGNORE INTO market_groups VALUES(?)',(gid,))

    def groups(self):
        return [dict(row) for row in self.db.execute('SELECT g.group_id,g.name,g.enabled,g.read_images FROM groups g JOIN market_groups m USING(group_id) ORDER BY g.name')]

    def blocked_ids(self):return {row[0] for row in self.db.execute('SELECT qq FROM market_blocks')}

    def blocked(self):return [dict(row) for row in self.db.execute('SELECT * FROM market_blocks ORDER BY created_at DESC')]

    def block(self,qq):
        qq=qq_number(qq)
        if qq is None:raise ValueError('请输入有效 QQ 号')
        with self.archive.transaction():self.db.execute('INSERT OR IGNORE INTO market_blocks VALUES(?,?)',(qq,datetime.now(timezone.utc).isoformat()))

    def unblock(self,qq):
        if qq_number(qq) is None:raise ValueError('请输入有效 QQ 号')
        with self.archive.transaction():self.db.execute('DELETE FROM market_blocks WHERE qq=?',(qq,))

    def set_favorite(self,item_id,value):
        self.detail(item_id)
        if type(value) is not bool:raise ValueError('收藏状态无效')
        with self.archive.transaction():
            if value:self.db.execute('INSERT OR IGNORE INTO market_favorites VALUES(?)',(item_id,))
            else:self.db.execute('DELETE FROM market_favorites WHERE item_key=?',(item_id,))

    def pending_units(self,units):
        return [unit for unit in units if not self.db.execute('SELECT 1 FROM market_scans WHERE source_key=? AND fingerprint=? AND state IN (\'done\',\'forward_unavailable\',\'image_unavailable\')',(unit['key'],unit['fingerprint'])).fetchone()]

    def record_scans(self,units,state='done',note=''):
        now=datetime.now(timezone.utc).isoformat()
        for unit in units:
            if state=='done' and not unit.get('images_unavailable'):
                self.db.execute("DELETE FROM market_scans WHERE source_key IN (?,?) AND state='image_unavailable'",
                    (digest([unit['key'],'image']),digest([unit['group_id'],unit['msg_id'],unit['path']+'.image'])))
            self.db.execute('INSERT INTO market_scans VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET fingerprint=excluded.fingerprint,state=excluded.state,note=excluded.note,updated_at=excluded.updated_at',
                (unit['key'],unit['group_id'],unit['msg_id'],unit['fingerprint'],state,note[:500],now))

    def accept_units(self,units,payload,*,validate_only=False,record_scan=True):
        if not isinstance(payload,dict) or not isinstance(payload.get('items'),list) or len(payload['items'])>500:raise ValueError('交易识别格式无效')
        sources={unit['key']:unit for unit in units};prepared=[];blocked=self.blocked_ids()
        for item in payload['items']:
            if not isinstance(item,dict):raise ValueError('商品格式无效')
            unit=sources.get(item.get('source_key'))
            if unit is None or unit['state']!='ready':raise ValueError('商品引用了无效来源')
            if unit['seller_qq'] in blocked or unit['forwarder_qq'] in blocked:continue
            if not self.db.execute('SELECT 1 FROM market_groups WHERE group_id=?',(unit['group_id'],)).fetchone():raise ValueError('商品来源不在二手群范围')
            text=unit['text'];title=item.get('title');evidence=item.get('evidence')
            if not isinstance(title,str) or not 2<=len(title.strip())<=160 or title.strip() in {'商品','物品','二手','已出','出了','求购'}:raise ValueError('缺少明确物品名称')
            if not isinstance(evidence,str) or not evidence.strip() or evidence not in text:raise ValueError('商品缺少原文证据')
            terms=re.findall(r'[a-zA-Z0-9]{3,}|[\u4e00-\u9fff]{2,}',title)
            if not any(term.casefold() in evidence.casefold() for term in terms):raise ValueError('物品名称无法核对原文')
            direction=item.get('direction');status=item.get('status','unknown')
            if direction not in {'sale','want'} or status not in {'unknown','sold','fulfilled'}:raise ValueError('交易类型或状态无效')
            markers=r'已出|已售|售出|已卖|已收|已购|收到'
            if status!='unknown' and (not re.search(markers,evidence) or re.search(r'(?:未|没|不).{0,2}(?:'+markers+r')',evidence)):raise ValueError('成交状态缺少证据')
            if status=='sold' and direction!='sale' or status=='fulfilled' and direction!='want':raise ValueError('交易状态不匹配')
            price=item.get('price');price_text=item.get('price_text','')
            if not isinstance(price_text,str) or len(price_text)>24 or (price_text and (price_text not in evidence or not _PRICE_TEXT_RE.fullmatch(price_text))):raise ValueError('价格原文无法核对')
            if price is not None:
                if type(price) not in (int,float) or not math.isfinite(price) or not 0<=price<=1e9:raise ValueError('价格无效')
                quoted=re.findall(r'(?<!\d)\d+(?:\.\d+)?(?!\d)',price_text)
                if not any(float(value)==float(price) for value in quoted):raise ValueError('价格缺少原文证据')
            description=item.get('description','');condition=item.get('condition','')
            if (not isinstance(description,str) or len(description)>12000 or not isinstance(condition,str) or len(condition)>100
                    or (description and description not in evidence) or (condition and condition not in evidence)):
                raise ValueError('商品说明或成色无法核对原文')
            bundle_mode,bundle_label,components,bundle_key=_components(item,text=text,evidence=evidence,title=title.strip())
            if bundle_mode!='single':bundle_key=digest([unit['key'],bundle_mode,bundle_label])
            # Full source text is retained; extraction cannot supply the identity or source dates.
            identity_title=bundle_label or title
            item_key=_item_identity(unit,direction,identity_title,evidence,bundle_mode,components)
            post_key=digest([unit['key'],normalized(identity_title),direction,bundle_mode])
            if status!='unknown' and unit['seller_qq'] and not unit.get('original_time_unknown'):
                candidates={row['item_key'] for row in self.db.execute('SELECT item_key,title FROM market_posts WHERE seller_qq=? AND direction=? AND original_time_unknown=0',(unit['seller_qq'],direction)) if normalized(row['title'])==normalized(title)}
                candidates.update(row[1] for row in prepared if row[6]==unit['seller_qq'] and row[10]==direction and not row[20] and normalized(row[9])==normalized(title))
                if len(candidates)==1:item_key=next(iter(candidates))
                elif candidates:status='unknown'
            prepared.append((post_key,item_key,unit['group_id'],unit['msg_id'],unit['key'],unit['path'],unit['seller_qq'],unit['seller_name'],unit['forwarder_qq'],title.strip(),direction,
                description,condition,price,price_text,status,evidence,unit['published_at'],unit['forwarded_at'],int(unit['incomplete']),int(bool(unit.get('original_time_unknown'))),bundle_key,bundle_label,bundle_mode,json.dumps(components,ensure_ascii=False,separators=(',',':'))))
        prepared=_join_split_bundles(prepared,sources)
        if validate_only:return len(prepared)
        with self.archive.transaction():
            inherited=[]
            for unit in units:
                inherited.extend(dict(row) for row in self.db.execute('SELECT p.* FROM market_posts p JOIN market_favorites f USING(item_key) WHERE p.source_key=?',(unit['key'],)))
            for unit in units:self.db.execute('DELETE FROM market_posts WHERE source_key=?',(unit['key'],))
            self.db.executemany('INSERT INTO market_posts VALUES('+','.join('?' for _ in range(25))+')',prepared)
            for row in prepared:
                if any(old['source_key']==row[4] and old['direction']==row[10] and _names(old['title'],old.get('components_json','[]'))&_names(row[9],row[24]) for old in inherited):
                    self.db.execute('INSERT OR IGNORE INTO market_favorites VALUES(?)',(row[1],))
            if record_scan:self.record_scans(units)
        return len(prepared)

    def _rows(self):
        rows=[dict(row) for row in self.db.execute('''SELECT p.*,g.name AS group_name,msg.text AS source_text FROM market_posts p
            JOIN groups g ON g.group_id=p.group_id JOIN market_groups m ON m.group_id=p.group_id
            JOIN messages msg ON msg.group_id=p.group_id AND msg.msg_id=p.msg_id
            WHERE g.enabled=1 AND NOT EXISTS(SELECT 1 FROM market_blocks b WHERE b.qq=p.seller_qq OR b.qq=p.forwarder_qq)
            ORDER BY p.published_at DESC,p.msg_id DESC,p.post_key DESC''')]
        for row in rows:
            try:row['components']=json.loads(row.get('components_json') or '[]')
            except (TypeError,ValueError):row['components']=[]
        return rows

    def _decorate(self,key,latest,rows,favorites):
        valid=[row for row in rows if row.get('seller_qq') is not None and not row.get('original_time_unknown')]
        local_dates=[]
        for row in valid:
            try:local_dates.append(datetime.fromisoformat(row['published_at']).astimezone(ZoneInfo(self.timezone_name)).date())
            except (TypeError,ValueError):continue
        group_count=len({row['group_id'] for row in rows})
        active_days=len(set(local_dates));span_days=(max(local_dates)-min(local_dates)).days+1 if local_dates else 0
        bundle_count=len(latest.get('components') or []) if latest.get('bundle_mode') in {'bundle','collection'} else 0
        text=' '.join(dict.fromkeys(row.get('evidence','')+' '+(row.get('source_text','') if row['path']=='0' else '') for row in rows))
        flags=[]
        if latest['direction']=='sale' and len(rows)>=3 and (active_days>=2 or group_count>=2):flags.append('重复发布')
        if latest['direction']=='want' and span_days>=3:flags.append('长期求购')
        promotion_terms=set(_PROMOTION_RE.findall(text))
        list_evidence=bool(re.search(r'权益|\bvip\b',text,re.I) or len(re.findall(r'\d+[.、]',text))>=2)
        if (len(promotion_terms)>=2 and list_evidence) or (promotion_terms and re.search(r'代开|代购|长期(?:接|出|收)|优惠|欢迎咨询|每日|天天',text)):
            flags.append('疑似推广')
        item={**latest,'item_id':key,'title':latest.get('bundle_label') or latest['title'],'source_count':len(rows),
              'group_count':group_count,'repeat_count':len(rows),'active_days':active_days,'span_days':span_days,
              'behavior_flags':flags,'bundle_count':bundle_count,'components':latest.get('components') or [],
              'favorite':key in favorites,'possible_duplicate':False}
        item['category']=item_category(item)
        return item

    def _feed_groups(self,items,all_rows,start,end,group_id):
        """Presentation groups only: every publication retains its original item ID."""
        buckets={}
        for item in items:
            lane='promotion' if '疑似推广' in item['behavior_flags'] else item['direction']
            identity=[item['seller_qq'],lane,item['category']]
            if lane!='promotion':identity.extend([normalized(item['title']),item['bundle_mode']])
            if item['seller_qq'] is None or item['original_time_unknown']:identity.append(item['item_id'])
            buckets.setdefault(digest(identity),[]).append((lane,item))
        rows_by_item={}
        for row in all_rows:
            if start<=row['published_at']<end and (group_id is None or row['group_id']==group_id):
                rows_by_item.setdefault(row['item_key'],[]).append(row)
        result=[];counts={'sale':0,'want':0,'promotion':0}
        fields=('group_id','msg_id','group_name','published_at','price','price_text','title','evidence','status','seller_qq','original_time_unknown')
        for key,entries in buckets.items():
            lane,latest=entries[0];members=[entry[1] for entry in entries]
            sources=[row for member in members for row in rows_by_item.get(member['item_id'],[])]
            sources.sort(key=lambda row:(row['published_at'],row['msg_id'],row['post_key']),reverse=True)
            publications=[{**{field:row[field] for field in fields},'item_id':row['item_key']} for row in sources]
            days={datetime.fromisoformat(row['published_at']).astimezone(ZoneInfo(self.timezone_name)).date() for row in sources if not row['original_time_unknown']}
            source_count=len({row['source_key'] for row in sources})
            group={**latest,'feed_id':key,'lane':lane,'member_count':len(members),'publication_count':source_count,
                   'publications':publications,'group_count':len({row['group_id'] for row in sources}),
                   'active_days':len(days),'span_days':(max(days)-min(days)).days+1 if days else 0}
            if lane=='promotion':group.update(price=None,price_text='')
            counts[lane]+=1;result.append(group)
        return result,counts

    def list_items(self,*,date_from=None,date_to=None,q='',group_id=None,direction='all',status='all',min_price=None,max_price=None,favorite=False,page=1,page_size=30,grouped=False,lane='sale',category='all'):
        start,end=date_window(date_from,date_to,self.timezone_name)
        if type(page) is not int or page<1 or type(page_size) is not int or not 1<=page_size<=100:raise ValueError('分页无效')
        if direction not in {'all','sale','want'} or status not in {'all','unknown','sold','fulfilled'}:raise ValueError('筛选无效')
        if grouped and lane not in {'sale','want','promotion'}:raise ValueError('交易分区无效')
        if category!='all' and category not in CATEGORY_LABELS:raise ValueError('商品分类无效')
        for price in (min_price,max_price):
            if price is not None and (not math.isfinite(price) or price<0):raise ValueError('价格范围无效')
        if min_price is not None and max_price is not None and min_price>max_price:raise ValueError('价格范围无效')
        favorites={row[0] for row in self.db.execute('SELECT item_key FROM market_favorites')};clusters={}
        all_rows=self._rows()
        for row in all_rows:clusters.setdefault(row['item_key'],[]).append(row)
        items=[]
        for key,rows in clusters.items():
            selected=[row for row in rows if start<=row['published_at']<end and (group_id is None or row['group_id']==group_id)]
            if not selected:continue
            latest=selected[0]
            if latest['price'] is None:
                previous=next((row for row in rows if row['price'] is not None and row['published_at']<=latest['published_at']),None)
                if previous:latest={**latest,'price':previous['price'],'price_text':previous['price_text']+'（最近已知价格）'}
            if direction!='all' and latest['direction']!=direction or status!='all' and latest['status']!=status:continue
            if q and q.casefold() not in (latest['title']+' '+latest['description']+' '+latest['evidence']).casefold():continue
            if favorite and key not in favorites:continue
            if min_price is not None and (latest['price'] is None or latest['price']<min_price) or max_price is not None and (latest['price'] is None or latest['price']>max_price):continue
            history=[row for row in rows if row['published_at']<end]
            activity=[row for row in all_rows if latest['seller_qq'] is not None and not latest['original_time_unknown']
                      and row['seller_qq']==latest['seller_qq'] and row['direction']==latest['direction']
                      and normalized(row['title'])==normalized(latest['title']) and not row['original_time_unknown'] and row['published_at']<end]
            item=self._decorate(key,latest,activity or history,favorites)
            item['source_count']=len(history);item['group_count']=len({row['group_id'] for row in history})
            items.append(item)
        for item in items:
            item['possible_duplicate']=any(other['item_key']!=item['item_key'] and item['seller_qq'] and item['seller_qq']==other['seller_qq'] and normalized(item['title'])==normalized(other['title']) for other in items)
        items.sort(key=lambda row:(row['published_at'],row['item_key']),reverse=True)
        lane_counts={};ungrouped_total=len(items)
        if grouped:
            items,lane_counts=self._feed_groups(items,all_rows,start,end,group_id)
            lane_counts={key:sum(item['lane']==key and (category=='all' or item['category']==category) for item in items) for key in lane_counts}
            items=[item for item in items if item['lane']==lane]
        categories=[{'id':key,'label':label,'count':sum(item['category']==key for item in items)} for key,label in CATEGORY_LABELS.items()]
        if category!='all':items=[item for item in items if item['category']==category]
        total=len(items);page=min(page,max(1,(total+page_size-1)//page_size))
        coverage=[dict(row) for row in self.db.execute('SELECT state,count(*) AS count FROM market_scans WHERE group_id IN (SELECT group_id FROM market_groups) GROUP BY state')]
        return {'items':items[(page-1)*page_size:page*page_size],'total':total,'page':page,'page_size':page_size,'date_from':date_from or recent_window(self.timezone_name)[0],'date_to':date_to or recent_window(self.timezone_name)[1],'coverage':coverage,'grouped':grouped,'lane':lane,'lane_counts':lane_counts,'ungrouped_total':ungrouped_total,'category':category,'categories':categories}

    def detail(self,item_id):
        all_rows=self._rows();rows=[row for row in all_rows if row['item_key']==item_id]
        if not rows:raise LookupError('商品不存在或已屏蔽')
        latest=rows[0];favorite=bool(self.db.execute('SELECT 1 FROM market_favorites WHERE item_key=?',(item_id,)).fetchone())
        activity=[row for row in all_rows if latest['seller_qq'] is not None and not latest['original_time_unknown']
                  and row['seller_qq']==latest['seller_qq'] and row['direction']==latest['direction']
                  and normalized(row['title'])==normalized(latest['title']) and not row['original_time_unknown']]
        item=self._decorate(item_id,latest,activity or rows,{item_id} if favorite else set())
        item['source_count']=len(rows);item['group_count']=len({row['group_id'] for row in rows})
        return {'item_id':item_id,'item':item,'sources':rows,'favorite':favorite}
