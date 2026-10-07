"""Read cached QQ identities locally; never infer a person from message text."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3


def _name(value):
    if not isinstance(value,str):return ''
    return ''.join(ch for ch in value if ch.isprintable()).strip()[:160]


def _qq(value):
    if isinstance(value,bool):return None
    if not isinstance(value,(str,int)):return None
    try:value=int(value)
    except (ValueError,TypeError,OverflowError):return None
    return value if 0<value<2**63 else None


def _metadata(value):
    if isinstance(value,dict):return dict(value)
    try:value=json.loads(value or '{}')
    except (ValueError,TypeError):return {}
    return value if isinstance(value,dict) else {}


def sender_fields(row):
    metadata=_metadata(row['content_json'])
    name=_name(metadata.get('sender_name'))
    if not name:return {}
    qq=_qq(row['sender_qq'])
    return {'sender_name':name,'sender_label':name+(f' · {qq}' if qq else '')}


def refresh_message_sender(connection,message):
    incoming=message.content_json
    if not _name(incoming.get('sender_name')) and not (incoming.get('sender_uid') and _qq(message.sender_qq)):return
    row=connection.execute('SELECT sender_qq,content_json FROM messages WHERE group_id=? AND msg_id=?',
        (message.group_id,message.msg_id)).fetchone()
    if row is None:return
    metadata=_metadata(row['content_json']);old_uid=metadata.get('sender_uid');uid=incoming.get('sender_uid')
    if old_uid and old_uid!=uid:return
    old_qq=_qq(row['sender_qq']);incoming_qq=_qq(message.sender_qq)
    if old_qq and incoming_qq and old_qq!=incoming_qq:return
    qq=incoming_qq or old_qq
    if _name(incoming.get('sender_name')):metadata['sender_name']=_name(incoming['sender_name'])
    if uid:metadata['sender_uid']=uid
    if metadata==_metadata(row['content_json']) and qq==row['sender_qq']:return
    connection.execute('UPDATE messages SET sender_qq=?,content_json=? WHERE group_id=? AND msg_id=?',
        (qq,json.dumps(metadata,ensure_ascii=False),message.group_id,message.msg_id))


class SenderIdentityResolver:
    def __init__(self,db_dir):
        self.db_dir=Path(db_dir)
        self.people={};self.members={};self.qq_uids={}
        self._load()

    def _rows(self,file,table,columns):
        path=self.db_dir/file
        if not path.is_file():return []
        connection=None
        try:
            connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
            available={row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not set(columns)<=available:return []
            return connection.execute('SELECT '+','.join('"'+col+'"' for col in columns)+f' FROM "{table}"').fetchall()
        except (sqlite3.Error,OSError):return []
        finally:
            if connection:connection.close()

    def _person(self,uid,qq,nickname=''):
        if not isinstance(uid,str) or not uid:return
        person=self.people.setdefault(uid,{'qq':set(),'nickname':set()})
        number=_qq(qq)
        if number:
            person['qq'].add(number);self.qq_uids.setdefault(number,set()).add(uid)
        name=_name(nickname)
        if name:person['nickname'].add(name)

    def _load(self):
        for uid,qq,name in self._rows('profile_info.db','profile_info_v6',['1000','1002','20002']):
            self._person(uid,qq,name)
        for uid,qq in self._rows('nt_msg.db','nt_uid_mapping_table',['48902','1002']):
            self._person(uid,qq)
        for gid,uid,qq,card,name in self._rows('group_info.db','group_member3',['60001','1000','1002','64003','20002']):
            if not isinstance(uid,str) or not uid:continue
            self._person(uid,qq)
            self.members.setdefault((gid,uid),set()).add((_qq(qq),_name(card),_name(name)))

    def resolve(self,group_id,uid='',qq=None):
        qq=_qq(qq);fallback={'sender_qq':qq} if qq else {}
        if not isinstance(uid,str):return fallback
        if not uid and qq:
            uids=self.qq_uids.get(qq,set())
            if len(uids)==1:uid=next(iter(uids))
        person=self.people.get(uid)
        if person is None or len(person['qq'])>1:return fallback
        mapped=next(iter(person['qq']),None)
        if qq and mapped and qq!=mapped:return fallback
        qq=qq or mapped
        result={'sender_qq':qq} if qq else {}
        members=self.members.get((group_id,uid),set())
        cards={row[1] for row in members if row[1]}
        names={row[2] for row in members if row[2]}
        name=next(iter(cards)) if len(cards)==1 else next(iter(names)) if len(names)==1 else ''
        if not name and len(person['nickname'])==1:name=next(iter(person['nickname']))
        if name:result['sender_name']=name
        return result

    def enrich(self,message):
        metadata=dict(message.content_json)
        result=self.resolve(message.group_id,metadata.get('sender_uid',''),message.sender_qq)
        if result.get('sender_name'):metadata['sender_name']=result['sender_name']
        return message.model_copy(update={'sender_qq':result.get('sender_qq',message.sender_qq),'content_json':metadata})


def enrich_archive_senders(archive,resolver):
    changed=0
    with archive.transaction():
        cursor=archive.connection.execute('SELECT group_id,msg_id,sender_qq,content_json FROM messages')
        while rows:=cursor.fetchmany(500):
            for row in rows:
                metadata=_metadata(row['content_json'])
                result=resolver.resolve(row['group_id'],metadata.get('sender_uid',''),row['sender_qq'])
                if not result:continue
                if result.get('sender_name'):metadata['sender_name']=result['sender_name']
                raw=json.dumps(metadata,ensure_ascii=False)
                qq=result.get('sender_qq',row['sender_qq'])
                if qq==row['sender_qq'] and metadata==_metadata(row['content_json']):continue
                archive.connection.execute('UPDATE messages SET sender_qq=?,content_json=? WHERE group_id=? AND msg_id=?',
                    (qq,raw,row['group_id'],row['msg_id']));changed+=1
    return changed
