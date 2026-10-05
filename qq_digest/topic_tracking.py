"""Conservative local topic links, persistent human corrections and cited history."""
from __future__ import annotations

import hashlib
import json
import threading
import unicodedata
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path

from .message_context import message_context
from .report_revisions import ReportRevisionService
from .report_sources import load_verified_report_sources


class TopicConflict(ValueError):
    pass


def normalized_title(title):
    text = unicodedata.normalize('NFKC', title).casefold()
    def identifier_separator(index, character):
        # Keep punctuation inside ASCII version/object identifiers (2.0, 2.0-rc1,
        # project-a). Sentence punctuation and surrounding spaces still normalize.
        if character not in '._-/:,' or not 0 < index < len(text)-1:
            return False
        return all(c.isascii() and c.isalnum() for c in (text[index-1],text[index+1]))
    return ''.join(c for i,c in enumerate(text) if not c.isspace() and
        (not unicodedata.category(c).startswith('P') or identifier_separator(i,c)))


def _body_text(summary):
    return ' '.join(unicodedata.normalize('NFKC', summary).split())


def _episode_base(kind, report_id, title, source_ids):
    return json.dumps([kind,report_id,normalized_title(title),sorted(source_ids)],ensure_ascii=False)


def _stored_base(row):
    evidence = json.loads(row['evidence'])
    source_ids = evidence['main_topics'][0].get('message_ids', [])
    return _episode_base(row['report_kind'],row['report_id'],row['title'],source_ids)


def specific_title(key):
    return len(key) >= 5 and key not in {
        '日常讨论', '日常交流', '闲聊交流', '其他讨论', '其他话题', '群内讨论', '技术讨论',
        '资源分享', '问题讨论', '今日话题', '日常话题', 'generaldiscussion', 'generalchat',
        'discussion', 'miscellaneous', '闲聊', '交流', '讨论', '分享', '选型',
    }


def _integer(value, label, minimum=1):
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError(f'{label}超出有效范围')
    return value


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError('话题名称须为 1 至 200 字')
    return value.strip()


def _date(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('日期格式须为 YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError('日期格式须为 YYYY-MM-DD') from None
    if parsed.isoformat() != value:
        raise ValueError('日期格式须为 YYYY-MM-DD')
    return value


def _pagination(page, page_size, total):
    _integer(page, '页码'); _integer(page_size, '每页数量')
    page_size = min(page_size, 100)
    return min(page, max(1, (total + page_size - 1)//page_size)), page_size


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            outermost = self._read_cache is None
            if outermost:
                self._read_cache = {}
            try:
                return method(self, *args, **kwargs)
            finally:
                if outermost:
                    self._read_cache = None
    return locked


class TopicTrackingService:
    def __init__(self, archive, timezone_name='Asia/Shanghai'):
        self.archive = archive
        self.db = archive.connection
        self.timezone_name = timezone_name
        # All instances over the same connection share a lock, including read routes.
        if not hasattr(archive, '_topic_tracking_lock'):
            archive._topic_tracking_lock = threading.RLock()
        self._lock = archive._topic_tracking_lock
        self._read_cache = None
        self.revisions = ReportRevisionService(archive)

    def _reports(self):
        return self.db.execute('''SELECT 'daily' AS report_kind,report_id,group_id,
            report_date AS start_date,report_date AS end_date,json_path FROM reports
            UNION ALL SELECT 'range',manual_report_id,group_id,start_date,end_date,json_path
            FROM manual_reports ORDER BY end_date,start_date,report_kind,report_id''').fetchall()

    def _verified(self, payload, group_id, start_date, end_date):
        return load_verified_report_sources(self.db, payload, group_id=group_id,
            start_date=start_date, end_date=end_date, timezone_name=self.timezone_name)

    def _episodes(self, report, payload, fingerprint, version):
        rows = payload.get('main_topics', [])
        if not isinstance(rows, list):
            raise ValueError('报告话题结构无效')
        episodes = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                title = _title(item.get('topic'))
            except ValueError:
                continue
            summary = item.get('summary', '')
            if not isinstance(summary, str):
                summary = ''
            # Save this episode's own evidence, never the entire report's references.
            evidence = {'evidence_version':payload.get('evidence_version'),
                'main_topics':[{'topic':title, 'summary':summary,
                                'message_ids':item.get('message_ids', [])}]}
            verified = self._verified(evidence, report['group_id'], report['start_date'], report['end_date'])
            source_ids = verified[0]['source_ids'] if verified else []
            evidence['main_topics'][0]['message_ids'] = source_ids
            for section, label in [('conclusions','重要结论'), ('open_questions','未解决问题或争议')]:
                candidates = payload.get(section, [])
                candidates = candidates if isinstance(candidates, list) else []
                claims = self._verified({'evidence_version':payload.get('evidence_version'),
                    section:candidates}, report['group_id'], report['start_date'], report['end_date'])
                evidence[section] = [{'text':c['text'], 'message_ids':c['source_ids']}
                    for c in (claims or []) if c['section']==label and set(c['source_ids']) & set(source_ids)]
            identity_base = _episode_base(report['report_kind'],report['report_id'],title,source_ids)
            episodes.append((identity_base,dict(group_id=report['group_id'], report_kind=report['report_kind'],
                report_id=report['report_id'], report_version=version, fingerprint=fingerprint,
                start_date=report['start_date'], end_date=report['end_date'], title=title,
                summary=summary, evidence=json.dumps(evidence,ensure_ascii=False))))
        counts = Counter(base for base,_ in episodes)
        stored_counts = Counter()
        body_bases = set()
        for stored in self.db.execute('SELECT * FROM topic_discussions WHERE report_kind=? AND report_id=?',
                (report['report_kind'],report['report_id'])):
            base = _stored_base(stored)
            stored_counts[base] += 1
            if stored['identity'].startswith('body:'):
                body_bases.add(base)
        body_bases.update(base for base,count in (counts + stored_counts).items()
                          if counts[base]>1 or stored_counts[base]>1)
        occurrences = Counter()
        for base,item in episodes:
            # Unique titles/references retain manual links across wording changes.
            # Once ambiguous duplicates exist, body identity stays in use even if
            # only one remains, so ordinal shifts cannot transfer human opinions.
            body_mode = base in body_bases
            content_key = json.dumps([base,_body_text(item['summary'])],ensure_ascii=False) if body_mode else base
            occurrence = occurrences[content_key]
            occurrences[content_key] += 1
            identity = hashlib.sha256(f'{content_key}:{occurrence}'.encode('utf-8')).hexdigest()
            yield {'identity':('body:' if body_mode else '') + identity,**item}

    def _adopt_legacy_episode(self, item):
        if not item['identity'].startswith('body:'):
            return None
        base = _stored_base(item)
        # Adopt an existing unambiguous body when promoting a prior unique/ordinal
        # index. Never carry a manual link onto a different duplicate's summary.
        for stored in self.db.execute("SELECT * FROM topic_discussions WHERE report_kind=? AND report_id=? "
                "AND identity NOT LIKE 'body:%' ORDER BY discussion_id",
                (item['report_kind'],item['report_id'])):
            if _stored_base(stored)==base and _body_text(stored['summary'])==_body_text(item['summary']):
                self.db.execute('UPDATE topic_discussions SET identity=? WHERE discussion_id=?',
                    (item['identity'],stored['discussion_id']))
                return stored
        return None

    def _new_topic(self, title):
        return self.db.execute('INSERT INTO tracked_topics(title,match_key) VALUES (?,?)',
            (title,normalized_title(title))).lastrowid

    def _automatic_topic(self, title):
        key = normalized_title(title)
        if specific_title(key):
            alias = self.db.execute('SELECT topic_id FROM tracked_topic_aliases WHERE match_key=?', (key,)).fetchone()
            if alias:
                return alias['topic_id']
            row = self.db.execute('''SELECT t.topic_id FROM tracked_topics t
                WHERE t.match_key=? AND EXISTS (SELECT 1 FROM topic_discussions d WHERE d.topic_id=t.topic_id)
                ORDER BY t.topic_id LIMIT 1''', (key,)).fetchone()
            if row:
                return row['topic_id']
        return self._new_topic(title)

    @synchronized
    def refresh(self):
        staged, skipped = [], []
        for report in self._reports():
            try:
                start, end = _date(report['start_date']), _date(report['end_date'])
                if not start or not end or start > end:
                    raise ValueError('报告日期范围无效')
                raw = Path(report['json_path']).read_bytes()
                payload = json.loads(raw.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise ValueError('报告内容无效')
                version = self.revisions.current_version(report['report_kind'],report['report_id'])
                fingerprint = hashlib.sha256(raw).hexdigest()
                staged.append((report,list(self._episodes(report,payload,fingerprint,version))))
            except (OSError, ValueError, TypeError, UnicodeError):
                skipped.append(report)
        with self.archive.transaction():
            # A single transaction replaces current flags and all staged reports.
            previous = {r['discussion_id']:dict(r) for r in self.db.execute('SELECT * FROM topic_discussions')}
            for report in skipped:
                self.db.execute('UPDATE topic_discussions SET available=0 WHERE report_kind=? AND report_id=? AND is_current=1',
                    (report['report_kind'],report['report_id']))
            self.db.execute('UPDATE topic_discussions SET is_current=0')
            for report, episodes in staged:
                self.db.execute('UPDATE topic_discussions SET available=1 WHERE report_kind=? AND report_id=?',
                    (report['report_kind'],report['report_id']))
                for item in episodes:
                    existing = self.db.execute('SELECT * FROM topic_discussions WHERE identity=?',
                        (item['identity'],)).fetchone()
                    if existing is None:
                        existing = self._adopt_legacy_episode(item)
                    if existing:
                        assignments = ','.join(f'{key}=?' for key in item if key!='identity')
                        self.db.execute(f'UPDATE topic_discussions SET {assignments},is_current=1,available=1 '
                            'WHERE discussion_id=?', (*[value for key,value in item.items() if key!='identity'],
                            existing['discussion_id']))
                    else:
                        topic_id = self._automatic_topic(item['title'])
                        columns = ','.join(item)
                        placeholders = ','.join('?' for _ in item)
                        self.db.execute(f'INSERT INTO topic_discussions(topic_id,{columns}) '
                            f'VALUES (?,{placeholders})', (topic_id,*item.values()))
            touched = set()
            for current in self.db.execute('SELECT * FROM topic_discussions').fetchall():
                old = previous.get(current['discussion_id'])
                if old is None or any(current[k]!=old[k] for k in ('fingerprint','report_version','evidence',
                        'title','summary','is_current','available')):
                    touched.add(current['topic_id'])
                    if old:
                        self.db.execute('UPDATE topic_discussions SET revision=revision+1 WHERE discussion_id=?',
                            (current['discussion_id'],))
            for topic_id in touched:
                self.db.execute('UPDATE tracked_topics SET revision=revision+1 WHERE topic_id=?', (topic_id,))
        return dict(reports_processed=len(staged), skipped_reports=len(skipped),
            discussions=self.db.execute('SELECT COUNT(*) FROM topic_discussions').fetchone()[0],
            topics=self.db.execute('SELECT COUNT(*) FROM tracked_topics t WHERE EXISTS '
                '(SELECT 1 FROM topic_discussions d WHERE d.topic_id=t.topic_id)').fetchone()[0])

    def _topic_row(self, topic_id):
        _integer(topic_id, '话题编号')
        row = self.db.execute('SELECT * FROM tracked_topics WHERE topic_id=?',(topic_id,)).fetchone()
        if row is None:
            raise LookupError('话题不存在')
        return row

    def _discussion_row(self, discussion_id):
        _integer(discussion_id, '讨论编号')
        row = self.db.execute('SELECT d.*,g.name AS group_name FROM topic_discussions d '
            'JOIN groups g ON g.group_id=d.group_id WHERE discussion_id=?',(discussion_id,)).fetchone()
        if row is None:
            raise LookupError('讨论不存在')
        return row

    def _current_available(self, row):
        if not row['available']:
            return False
        if not row['is_current']:
            return True
        key = ('report', row['report_kind'], row['report_id'], row['report_version'], row['fingerprint'])
        if key in self._read_cache:
            return self._read_cache[key]
        try:
            report = self.revisions.report(row['report_kind'],row['report_id'])
            available = (self.revisions.current_version(row['report_kind'],row['report_id']) == row['report_version']
                and hashlib.sha256(Path(report['json_path']).read_bytes()).hexdigest()==row['fingerprint'])
        except (OSError,LookupError):
            available = False
        self._read_cache[key] = available
        return available

    def _discussion(self, row):
        cache_key = ('discussion', row['discussion_id'])
        if cache_key in self._read_cache:
            return self._read_cache[cache_key]
        result = {key:row[key] for key in ('discussion_id','topic_id','revision','report_kind','report_id',
            'report_version','group_id','group_name','start_date','end_date','title','summary','link_mode')}
        result['date_label'] = row['start_date'] if row['start_date']==row['end_date'] else f"{row['start_date']} — {row['end_date']}"
        result['is_current'] = bool(row['is_current'])
        result.update(source_ids=[], conclusions=[], open_questions=[])
        self._read_cache[cache_key] = result
        if not self._current_available(row):
            result['evidence_status'] = 'unavailable'
            return result
        evidence = json.loads(row['evidence'])
        verified = self._verified(evidence,row['group_id'],row['start_date'],row['end_date'])
        if verified is None:
            result['evidence_status'] = 'legacy'
            return result
        source_ids = next((c['source_ids'] for c in verified if c['section']=='主要话题'), [])
        result['source_ids'] = source_ids
        result['evidence_status'] = 'cited' if source_ids else 'unverified'
        for section,label in [('conclusions','重要结论'), ('open_questions','未解决问题或争议')]:
            result[section] = [{'text':c['text'],'source_ids':c['source_ids']} for c in verified
                if c['section']==label and set(c['source_ids']) & set(source_ids)]
        return result

    def _topic(self, row):
        topic_id = row['topic_id']
        result = {key:row[key] for key in ('topic_id','title','status','revision')}
        stats = self.db.execute('SELECT COUNT(*) AS discussion_count, MIN(start_date) AS first_date, '
            'MAX(end_date) AS last_date FROM topic_discussions WHERE topic_id=?',(topic_id,)).fetchone()
        result.update(dict(stats))
        result['groups'] = [dict(g) for g in self.db.execute('SELECT DISTINCT g.group_id,g.name '
            'FROM groups g JOIN topic_discussions d ON d.group_id=g.group_id WHERE d.topic_id=? '
            'ORDER BY g.group_id',(topic_id,))]
        result.update(latest_progress='',latest_conclusions=[],open_questions=[])
        # Recent available episodes only; older versions remain on the timeline.
        for section,target in [(None,'latest_progress'),('conclusions','latest_conclusions'),
                               ('open_questions','open_questions')]:
            predicate = "d.summary!=''" if section is None else f"json_array_length(d.evidence,'$.{section}')>0"
            latest = self.db.execute('SELECT d.*,g.name AS group_name FROM topic_discussions d '
                'JOIN groups g ON g.group_id=d.group_id WHERE topic_id=? AND is_current=1 AND available=1 '
                f'AND {predicate} ORDER BY end_date DESC,discussion_id DESC',(topic_id,))
            for episode in latest:
                d = self._discussion(episode)
                if d['evidence_status']=='unavailable':
                    continue
                if section is None:
                    result[target] = d['summary']
                    break
                if d[section]:
                    scope = {key:d[key] for key in ('discussion_id','group_id','group_name','date_label')}
                    result[target] = [{**c,**scope} for c in d[section]]
                    break
        return result

    @synchronized
    def list_topics(self, q='', group_id=None, date_from=None, date_to=None,
                    status='all', page=1, page_size=20):
        if not isinstance(q,str) or len(q)>200:
            raise ValueError('搜索词不能超过 200 字')
        if status not in {'all','tracking','archived'}:
            raise ValueError('跟踪状态无效')
        start,end=_date(date_from),_date(date_to)
        if start and end and start>end:
            raise ValueError('开始日期不能晚于结束日期')
        clauses,params=['d.topic_id=t.topic_id'],[]
        if group_id is not None:
            _integer(group_id,'群号',minimum=-(2**63))
            clauses.append('d.group_id=?');params.append(group_id)
        if start:
            clauses.append('d.end_date>=?');params.append(start)
        if end:
            clauses.append('d.start_date<=?');params.append(end)
        where='EXISTS (SELECT 1 FROM topic_discussions d WHERE '+ ' AND '.join(clauses)+')'
        if q.strip():
            where+=' AND instr(lower(t.title),lower(?))>0';params.append(q.strip())
        if status!='all':
            where+=' AND t.status=?';params.append(status)
        total=self.db.execute(f'SELECT COUNT(*) FROM tracked_topics t WHERE {where}',params).fetchone()[0]
        page,page_size=_pagination(page,page_size,total)
        rows=self.db.execute(f'''SELECT t.* FROM tracked_topics t WHERE {where}
            ORDER BY (SELECT MAX(end_date) FROM topic_discussions d WHERE d.topic_id=t.topic_id) DESC,
            t.topic_id DESC LIMIT ? OFFSET ?''',(*params,page_size,(page-1)*page_size))
        return dict(topics=[self._topic(row) for row in rows],total=total,page=page,page_size=page_size)

    @synchronized
    def detail(self, topic_id, page=1, page_size=20):
        result=self._topic(self._topic_row(topic_id))
        total=result['discussion_count']
        page,page_size=_pagination(page,page_size,total)
        rows=self.db.execute('SELECT d.*,g.name AS group_name FROM topic_discussions d '
            'JOIN groups g ON g.group_id=d.group_id WHERE topic_id=? '
            'ORDER BY end_date DESC,discussion_id DESC LIMIT ? OFFSET ?',
            (topic_id,page_size,(page-1)*page_size))
        result.update(discussions=[self._discussion(row) for row in rows],total=total,page=page,page_size=page_size)
        return result

    def _check(self, row, revision):
        _integer(revision,'修订号')
        if row['revision']!=revision:
            raise TopicConflict('内容已在其他窗口更新，请刷新后重试')

    @synchronized
    def update(self, topic_id, *, title=None, status=None, expected_revision):
        if title is None and status is None:
            raise ValueError('请填写话题名称或跟踪状态')
        if title is not None:title=_title(title)
        if status is not None and status not in {'tracking','archived'}:
            raise ValueError('跟踪状态无效')
        with self.archive.transaction():
            row=self._topic_row(topic_id);self._check(row,expected_revision)
            # Keep the original matching key after a human rename.
            self.db.execute('UPDATE tracked_topics SET title=?,status=?,revision=revision+1 WHERE topic_id=?',
                (title if title is not None else row['title'],status or row['status'],topic_id))
        return self._topic(self._topic_row(topic_id))

    @synchronized
    def move(self, discussion_id, *, target_topic_id=None, new_title=None, expected_revision):
        if (target_topic_id is None)==(new_title is None):
            raise ValueError('请选择一个目标话题或填写新话题名称')
        if new_title is not None:new_title=_title(new_title)
        with self.archive.transaction():
            row=self._discussion_row(discussion_id);self._check(row,expected_revision)
            if target_topic_id is not None:self._topic_row(target_topic_id)
            else:target_topic_id=self._new_topic(new_title)
            self.db.execute("UPDATE topic_discussions SET topic_id=?,link_mode='manual',revision=revision+1 WHERE discussion_id=?",
                (target_topic_id,discussion_id))
            for tid in {row['topic_id'],target_topic_id}:
                self.db.execute('UPDATE tracked_topics SET revision=revision+1 WHERE topic_id=?',(tid,))
        return self._discussion(self._discussion_row(discussion_id))

    @synchronized
    def merge(self, topic_id, target_topic_id, *, expected_revision, target_revision):
        if topic_id==target_topic_id:
            raise ValueError('不能合并到同一话题')
        with self.archive.transaction():
            source = self._topic_row(topic_id)
            self._check(source,expected_revision)
            self._check(self._topic_row(target_topic_id),target_revision)
            self.db.execute("UPDATE topic_discussions SET topic_id=?,link_mode='manual',revision=revision+1 WHERE topic_id=?",
                (target_topic_id,topic_id))
            self.db.execute('UPDATE tracked_topic_aliases SET topic_id=? WHERE topic_id=?', (target_topic_id,topic_id))
            self.db.execute('INSERT INTO tracked_topic_aliases(match_key,topic_id) VALUES (?,?) '
                'ON CONFLICT(match_key) DO UPDATE SET topic_id=excluded.topic_id', (source['match_key'],target_topic_id))
            self.db.execute('UPDATE tracked_topics SET revision=revision+1 WHERE topic_id IN (?,?)',
                (topic_id,target_topic_id))
        return self._topic(self._topic_row(target_topic_id))

    @synchronized
    def suggestions(self, discussion_id):
        discussion=self._discussion_row(discussion_id)
        key=normalized_title(discussion['title'])
        matches=[]
        for row in self.db.execute('''SELECT t.* FROM tracked_topics t WHERE t.topic_id!=?
            AND EXISTS (SELECT 1 FROM topic_discussions d WHERE d.topic_id=t.topic_id)''',
            (discussion['topic_id'],)):
            score=SequenceMatcher(None,key,normalized_title(row['title'])).ratio()
            if score>=0.55:matches.append((score,row))
        matches.sort(key=lambda item:(-item[0],item[1]['topic_id']))
        return {'suggestions':[self._topic(row) for _,row in matches[:5]]}

    @synchronized
    def source(self, discussion_id, msg_id, direction='around', cursor=None):
        row=self._discussion_row(discussion_id)
        item=self._discussion(row)
        allowed = set(item['source_ids'])
        for section in ('conclusions', 'open_questions'):
            for claim in item[section]:
                allowed.update(claim['source_ids'])
        if not isinstance(msg_id,str) or msg_id not in allowed:
            raise LookupError('原消息未记录、已失效或不属于此讨论')
        return message_context(self.archive,row['group_id'],msg_id,direction=direction,cursor=cursor)
