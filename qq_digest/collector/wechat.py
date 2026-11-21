"""Read the existing local WeChat archive; no WeChat SDK, keys or process access."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .base import Collector
from ..models import NormalizedMessage


class WeChatArchiveCollector(Collector):
    def __init__(self, source, expected_binding=None):
        self.source=source
        self.expected_binding=expected_binding

    @contextmanager
    def _open(self):
        connection=None
        try:
            connection=sqlite3.connect(self.source.archive_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
            connection.row_factory=sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            row=connection.execute('SELECT sha256 FROM binding WHERE id=1').fetchone()
            if not row or not row[0] or (self.expected_binding is not None and row[0]!=self.expected_binding):
                raise ValueError('微信归档账号来源已改变')
            yield connection,row[0]
        except (sqlite3.Error,OSError):
            raise RuntimeError('微信本地归档暂不可读') from None
        finally:
            if connection is not None:connection.close()

    def binding(self):
        with self._open() as (_,binding):return binding

    def discover_groups(self):
        with self._open() as (db,_):
            cutoff=(datetime.now(timezone.utc)-timedelta(days=30)).timestamp()
            row=db.execute("SELECT SUM(create_time>=?),MAX(create_time) FROM archive_messages WHERE platform='wechat' AND group_id=?",
                           (cutoff,self.source.native_group_id)).fetchone()
            return [{'group_id':self.source.group_id,'name':self.source.name,'platform':'wechat',
                     'message_count_30d':row[0] or 0,
                     'latest_message_at':datetime.fromtimestamp(row[1],timezone.utc) if row[1] else None}]

    def collect(self,group_id,start,end):
        if group_id!=self.source.group_id:raise ValueError('微信来源群不匹配')
        if start.tzinfo is None or end.tzinfo is None:raise ValueError('采集时间需要时区')
        with self._open() as (db,binding):
            rows=db.execute("""SELECT * FROM archive_messages WHERE platform='wechat' AND group_id=?
                AND create_time>=? AND create_time<=? ORDER BY create_time,sort_seq,shard,local_id""",
                (self.source.native_group_id,start.timestamp(),end.timestamp()))
            for row in rows:
                identity=[binding,self.source.native_group_id,row['shard'],row['local_id']]
                digest=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                try:raw=json.loads(row['raw_json'])
                except (ValueError,TypeError):raw={}
                raw_type=raw.get('rawlocal_type',0) if isinstance(raw,dict) else 0
                kind,text=_content(row['content'],raw_type)
                provenance={'group_id':self.source.native_group_id,'sender_id':row['sender_id'],
                            'shard':row['shard'],'local_id':row['local_id'],'server_id':row['server_id']}
                yield NormalizedMessage(msg_id='wx-'+digest,group_id=group_id,sender_qq=None,
                    timestamp=datetime.fromtimestamp(row['create_time'],timezone.utc),message_type=kind,text=text,
                    content_json={'platform':'wechat','wechat':provenance,'sender_name':row['sender_id'] or '未知成员'},
                    raw_digest=digest,source_id='wechat:'+self.source.source_key+':'+binding,
                    device_id='local-wechat-archive',collected_at=datetime.now(timezone.utc))


def _content(content,raw_type):
    content=content if isinstance(content,str) else ''
    kind={1:'text',49:'unknown',10000:'system'}.get(raw_type & 0xffffffff if isinstance(raw_type,int) else 0,'unknown')
    if isinstance(raw_type,int) and raw_type & 0xffffffff==49 and len(content)<=2*1024*1024:
        if not re.search(r'<!\s*(DOCTYPE|ENTITY)',content,re.I):
            try:
                root=ET.fromstring(content[content.index('<'):])
                app=root if root.tag=='appmsg' else root.find('appmsg')
                if app is not None:
                    subtype=app.findtext('type')
                    fields=[app.findtext('title') or '',app.findtext('des') or '']
                    url=app.findtext('url') or ''
                    if urlsplit(url).scheme.lower() in ('http','https'):fields.append(url)
                    return {'5':'link','6':'file','57':'reply','19':'forward'}.get(subtype,'unknown'),'\n'.join(s for s in fields if s) or '[微信卡片]'
            except (ValueError,ET.ParseError):pass
        return 'unknown','[未解析微信卡片]'
    return kind,content


def bind_wechat_sources(archive, sources, *, strict=True):
    """Persist provenance; runtime callers isolate unavailable or changed sources."""
    errors = {}
    for source in sources:
        try:
            binding = WeChatArchiveCollector(source).binding()
            expected = ('wechat', source.source_key, source.native_group_id, binding)
            with archive.transaction():
                old = archive.connection.execute(
                    'SELECT platform,source_key,native_group_id,account_binding FROM group_sources WHERE group_id=?',
                    (source.group_id,)).fetchone()
                if old:
                    if tuple(old) != expected:
                        raise ValueError('微信来源绑定已改变，需使用新的内部群ID和来源键')
                    continue
                if archive.connection.execute('SELECT 1 FROM groups WHERE group_id=?', (source.group_id,)).fetchone():
                    raise ValueError('微信内部群ID已被占用')
                if archive.connection.execute('SELECT 1 FROM group_sources WHERE source_key=?',(source.source_key,)).fetchone():
                    raise ValueError('微信来源键已被绑定，请使用新的来源键和内部群ID')
                archive.connection.execute('INSERT INTO group_sources VALUES(?,?,?,?,?)', (source.group_id,*expected))
                archive.connection.execute('INSERT INTO groups(group_id,name) VALUES(?,?)', (source.group_id,source.name))
        except sqlite3.IntegrityError:
            if strict: raise ValueError('微信来源绑定冲突') from None
            errors[source.group_id] = '微信来源绑定冲突'
        except (ValueError, RuntimeError) as exc:
            if strict:
                raise
            errors[source.group_id] = str(exc)
    return errors


class CombinedCollector(Collector):
    def __init__(self,primary,sources,bindings,errors=None):
        self.primary=primary
        self.errors=errors or {}
        self.primary_error=None
        self.external={s.group_id:WeChatArchiveCollector(s,bindings.get(s.group_id)) for s in sources}

    def discover_groups(self):
        result=[]
        if self.primary is not None:
            try: result.extend(self.primary.discover_groups())
            except Exception: pass
        for gid,reader in self.external.items():
            if gid in self.errors: continue
            try: result.extend(reader.discover_groups())
            except (ValueError,RuntimeError): pass
        return result

    def collect(self,group_id,start,end):
        if group_id in self.errors:raise RuntimeError(self.errors[group_id])
        if group_id<0:
            if group_id not in self.external:raise ValueError('微信归档来源未配置')
            return self.external[group_id].collect(group_id,start,end)
        if self.primary_error:raise RuntimeError(self.primary_error)
        if self.primary is None:raise RuntimeError('QQ消息来源未启用')
        return self.primary.collect(group_id,start,end)
