from datetime import datetime, timezone
from pathlib import Path

import pytest

from qq_digest.archive import Archive
from qq_digest.models import GroupConfig, NormalizedMessage
from qq_digest.secondhand import SecondhandService, recent_window
from qq_digest.secondhand_evidence import source_units


@pytest.fixture
def market(tmp_path):
    archive = Archive.open(tmp_path/'market.sqlite')
    archive.upsert_groups([GroupConfig(group_id=11,name='二手1'),GroupConfig(group_id=22,name='二手2')])
    service = SecondhandService(archive)
    service.add_groups([11,22])
    yield service
    archive.close()


def message(gid=11,mid='a',sender=100,text='出戴尔U2720显示器，完好带底座，价格800元',day=19,metadata=None,kind='text'):
    at=datetime(2026,10,day,2,tzinfo=timezone.utc)
    return NormalizedMessage(msg_id=mid,group_id=gid,sender_qq=sender,text=text,timestamp=at,collected_at=at,content_json=metadata or {},message_type=kind)


def offer(unit,price='800',title='戴尔U2720显示器',status='unknown'):
    return {'source_key':unit['key'],'title':title,'direction':'sale','price_text':price,'price':float(price) if price else None,
            'condition':'完好','description':unit['text'],'status':status,'evidence':unit['text']}


def ingest(service,messages):
    service.archive.ingest(messages)
    return source_units(messages,blocked=service.blocked_ids())


def test_recent_week_uses_local_date():
    assert recent_window('Asia/Shanghai',datetime(2026,10,18,17,tzinfo=timezone.utc))==('2026-10-13','2026-10-19')


def test_cross_group_dedup_keeps_sources_and_idempotence(market):
    units=ingest(market,[message(),message(22,'b')])
    market.accept_units(units,{'items':[offer(unit) for unit in units]})
    items=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items']
    assert len(items)==1 and items[0]['source_count']==2
    market.accept_units(units,{'items':[offer(unit) for unit in units]})
    assert len(market.detail(items[0]['item_id'])['sources'])==2


def test_different_sellers_and_two_different_goods_stay_separate(market):
    units=ingest(market,[message(),message(22,'b',sender=200),message(mid='c',text='出戴尔U2720显示器，屏幕有划痕缺底座，价格800元')])
    market.accept_units(units,{'items':[offer(unit) for unit in units]})
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==3


def test_price_updates_order_by_original_time(market):
    units=ingest(market,[message(day=18),message(22,'b',text='出戴尔U2720显示器，完好带底座，价格700元',day=19)])
    market.accept_units(units,{'items':[offer(units[1],'700'),offer(units[0])]})
    item=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]
    assert item['price']==700 and item['source_count']==2
    assert {source['price'] for source in market.detail(item['item_id'])['sources']}=={700,800}


def test_unquoted_price_and_sold_status_are_rejected(market):
    unit=ingest(market,[message(text='出戴尔U2720显示器，完好带底座，价格私聊')])[0]
    with pytest.raises(ValueError):market.accept_units([unit],{'items':[offer(unit)]})
    row=offer(unit,'',status='sold')
    with pytest.raises(ValueError):market.accept_units([unit],{'items':[row]})
    row['status']='unknown';market.accept_units([unit],{'items':[row]})
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]['price'] is None


@pytest.mark.parametrize('text',['戴尔U2720显示器不出了','戴尔U2720显示器尚未售出','戴尔U2720显示器撤回'])
def test_withdrawn_or_negated_sale_is_not_sold(market,text):
    unit=ingest(market,[message(text=text)])[0]
    with pytest.raises(ValueError):market.accept_units([unit],{'items':[offer(unit,'',status='sold')]})


def test_blocks_hide_existing_and_filter_before_processing(market):
    units=ingest(market,[message()]);market.accept_units(units,{'items':[offer(units[0])]})
    market.block(100)
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==0
    assert source_units([message()],blocked=market.blocked_ids())==[]
    market.unblock(100)
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==1


def test_forward_preserves_original_identity_time_and_does_not_trust_nickname():
    m=message(kind='forward',metadata={'forward_messages':[{'sender_name':'卖家甲','timestamp':'2026-10-10T10:00:00+08:00','text':'出U2720显示器800元'}]})
    units=source_units([m]);assert units[0]['seller_qq'] is None and units[0]['forwarder_qq']==100
    assert units[0]['published_at'].startswith('2026-10-10')
    assert source_units([message(kind='forward',text='[转发]')])[0]['state']=='forward_unavailable'


def test_nested_forward_expands_multiple_items_and_blocked_original():
    m=message(kind='forward',metadata={'forward_messages':[{'sender_qq':200,'text':'出售电脑500元'}, {'forward_messages':[{'sender_qq':300,'text':'求购手机100元'}]}]})
    units=source_units([m],blocked={200});assert len(units)==1 and units[0]['seller_qq']==300
    assert units[0]['path']=='0.1.0'


def test_favorites_filters_and_group_delete(market):
    unit=ingest(market,[message()])[0];market.accept_units([unit],{'items':[offer(unit)]})
    row=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]
    market.set_favorite(row['item_id'],True)
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19',favorite=True,q='U2720',max_price=900)['total']==1
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19',group_id=22)['total']==0
    market.archive.delete_group(11)
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==0


def test_payload_unknown_source_and_no_evidence_do_not_publish(market):
    unit=ingest(market,[message()])[0];row=offer(unit);row['source_key']='invented'
    with pytest.raises(ValueError):market.accept_units([unit],{'items':[row]})
    row=offer(unit);row['evidence']='invented'
    with pytest.raises(ValueError):market.accept_units([unit],{'items':[row]})
    assert market.archive.connection.execute('SELECT count(*) FROM market_posts').fetchone()[0]==0


def test_verified_original_image_does_not_call_vision(tmp_path):
    import hashlib,io
    from PIL import Image
    from qq_digest.attachments import AttachmentService
    image=io.BytesIO();Image.new('RGB',(8,8),'white').save(image,format='PNG');data=image.getvalue()
    path=tmp_path/'goods.png';path.write_bytes(data)
    service=AttachmentService(None,vision=lambda _:pytest.fail('viewing original must not invoke AI'))
    body,mime=service.original_image({'kind':'image','name':'goods.png','path':str(path),'md5':hashlib.md5(data).hexdigest()})
    assert body==data and mime=='image/png'
    with pytest.raises(ValueError):service.original_image({'kind':'image','path':str(path),'md5':'0'*32})


def test_explicit_sale_update_only_matches_unique_seller_item(market):
    unit=ingest(market,[message()])[0];market.accept_units([unit],{'items':[offer(unit)]})
    sold=ingest(market,[message(mid='sold',text='戴尔U2720显示器已出',day=19)])[0]
    market.accept_units([sold],{'items':[offer(sold,'',status='sold')]})
    items=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items']
    assert len(items)==1 and items[0]['status']=='sold'


def test_collector_recovers_structured_forward_not_card_preview():
    import json
    from qq_digest.collector.protobuf import make_bytes_field, make_varint_field
    from qq_digest.forward_sources import forward_records
    payload={'messages':[{'sender_qq':200,'sender_name':'卖家','timestamp':'2026-10-10T10:00:00+08:00','text':'出售显示器800元'}]}
    inner=make_varint_field(45002,6)+make_bytes_field(48271,json.dumps(payload).encode())
    records=forward_records(make_bytes_field(40800,inner))
    assert records[0]['sender_qq']==200 and records[0]['text']=='出售显示器800元'
    inner=make_varint_field(45002,6)+make_bytes_field(48271,json.dumps({'meta':{'detail':{'news':[{'text':'张三:出售显示器800元'}]}}}).encode())
    assert forward_records(make_bytes_field(40800,inner))==[]


def test_undated_forward_cannot_overwrite_dated_price(market):
    direct=message(text='出戴尔U2720显示器，完好带底座，价格700元',day=18)
    forward=message(mid='forward',kind='forward',metadata={'forward_messages':[{'sender_qq':100,'text':'出戴尔U2720显示器，完好带底座，价格800元'}]})
    units=ingest(market,[direct,forward])
    market.accept_units(units,{'items':[offer(units[0],'700'),offer(units[1])]})
    rows=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items']
    assert len(rows)==2
    assert next(row for row in rows if not row['forwarder_qq'])['price']==700


def test_failed_image_marker_is_not_trade_evidence():
    unit=source_units([message(kind='file',text='[图片]\n[附件 missing.png：请先在 QQ 下载附件，再重读]',metadata={'_attachment_original_text':'[图片]'})])[0]
    assert unit['state']=='image_unavailable'
    units=source_units([message(text='出售显示器800元\n[附件 missing.png：请先下载]',metadata={'_attachment_original_text':'出售显示器800元'})])
    assert [unit['state'] for unit in units]==['ready','image_unavailable']
    assert units[0]['incomplete'] and '请先下载' not in units[0]['text']


def test_historical_filter_does_not_borrow_future_price(market):
    units=ingest(market,[message(day=18,text='出戴尔U2720显示器，完好带底座'),message(day=19,mid='new')])
    market.accept_units(units,{'items':[offer(units[0],''),offer(units[1])]})
    assert market.list_items(date_from='2026-10-18',date_to='2026-10-18')['items'][0]['price'] is None
