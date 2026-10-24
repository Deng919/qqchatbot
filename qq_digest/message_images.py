"""Scoped image references and cached recognition; never expose local paths."""
from urllib.parse import quote, urlencode

from .attachments import VERSION, allowed, policy
from .secondhand_evidence import source_units


def image_source(archive, group_id, msg_id):
    row=archive.connection.execute('SELECT * FROM messages WHERE group_id=? AND msg_id=?',(group_id,msg_id)).fetchone()
    if row is None:raise LookupError('原消息不存在')
    blocked=set()
    if archive.connection.execute('SELECT 1 FROM market_groups WHERE group_id=?',(group_id,)).fetchone():
        blocked={r[0] for r in archive.connection.execute('SELECT qq FROM market_blocks')}
    if row['sender_qq'] in blocked:raise LookupError('此来源已屏蔽')
    message=archive._row_to_message(row)
    units=source_units([message],blocked)
    refs=[ref for unit in units if not unit.get('attachment_issue') for ref in unit['attachments']
          if isinstance(ref,dict) and ref.get('kind')=='image' and ref.get('key')][:100]
    return message,refs


def message_images(archive, group_id, msg_id):
    message,refs=image_source(archive,group_id,msg_id)
    group=policy(archive,group_id);enabled=bool(group and group['enabled'])
    readable=allowed(group,'image')
    base=f'/api/groups/{group_id}/messages/{quote(msg_id,safe="")}/images'
    market=bool(archive.connection.execute('SELECT 1 FROM market_groups WHERE group_id=?',(group_id,)).fetchone())
    cache={r['attachment_key']:r for r in archive.connection.execute(
        'SELECT * FROM message_attachments WHERE group_id=? AND msg_id=?',(group_id,msg_id))}
    items=[]
    for ref in refs:
        key=str(ref['key']);old=cache.get(key)
        current=readable and old and old['source_md5']==str(ref.get('md5','')).lower() and old['version']==VERSION
        items.append({'key':key,'name':str(ref.get('name') or '图片')[:200],
            'url':base+'/original?'+urlencode({'key':key}) if enabled else '',
            'preview_url':base+'/original?'+urlencode({'key':key,'preview':'true'}) if enabled and market else '',
            'read_url':base+'/'+quote(key,safe='')+'/read',
            'status':old['status'] if current else 'pending' if readable else 'disabled',
            'text':old['text'] if current else '', 'error':old['error'] if current else ''})
    return {'items':items,'enabled':enabled,'read_images':readable,
            'source_incomplete':bool(message.content_json.get('attachment_source_unavailable'))}
