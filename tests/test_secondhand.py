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
            'condition':'完好' if '完好' in unit['text'] else '', 'description':unit['text'],'status':status,'evidence':unit['text']}


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


def test_bundle_stays_one_listing_and_exposes_components(market):
    text = '桌游全套打包出售：狼人杀、三国杀、UNO，整套80元'
    unit = ingest(market, [message(text=text)])[0]
    row = {'source_key': unit['key'], 'title': '桌游全套', 'direction': 'sale', 'price': 80,
           'price_text': '80元', 'condition': '', 'description': '桌游全套打包出售：狼人杀、三国杀、UNO，整套80元',
           'status': 'unknown', 'evidence': text, 'bundle_mode': 'bundle', 'bundle_label': '桌游全套',
           'components': [{'title': '狼人杀', 'evidence': '狼人杀'}, {'title': '三国杀', 'evidence': '三国杀'}, {'title': 'UNO', 'evidence': 'UNO'}]}
    market.accept_units([unit], {'items': [row]})
    listed = market.list_items(date_from='2026-10-13', date_to='2026-10-19')
    assert listed['total'] == 1
    assert listed['items'][0]['bundle_mode'] == 'bundle'
    assert listed['items'][0]['bundle_count'] == 3
    detail = market.detail(listed['items'][0]['item_id'])
    assert [component['title'] for component in detail['item']['components']] == ['狼人杀', '三国杀', 'UNO']


def test_separately_priced_items_from_one_source_stay_separate(market):
    text = '出 ItemA 10元，出 ItemB 20元'
    unit = ingest(market, [message(text=text)])[0]
    rows = [
        {'source_key': unit['key'], 'title': 'ItemA', 'direction': 'sale', 'price': 10, 'price_text': '10元',
         'condition': '', 'description': '', 'status': 'unknown', 'evidence': '出 ItemA 10元'},
        {'source_key': unit['key'], 'title': 'ItemB', 'direction': 'sale', 'price': 20, 'price_text': '20元',
         'condition': '', 'description': '', 'status': 'unknown', 'evidence': '出 ItemB 20元'},
    ]
    market.accept_units([unit], {'items': rows})
    assert market.list_items(date_from='2026-10-13', date_to='2026-10-19')['total'] == 2


def test_optional_fields_and_price_text_must_be_source_verifiable(market):
    unit = ingest(market, [message()])[0]
    bad = offer(unit)
    bad['description'] = 'AI补写的说明'
    with pytest.raises(ValueError, match='说明或成色'):
        market.accept_units([unit], {'items': [bad]})
    bad = offer(unit)
    bad['condition'] = '九成新'
    with pytest.raises(ValueError, match='说明或成色'):
        market.accept_units([unit], {'items': [bad]})
    bad = offer(unit)
    bad['price_text'] = '戴尔U2720显示器800元'
    with pytest.raises(ValueError, match='价格原文'):
        market.accept_units([unit], {'items': [bad]})


@pytest.mark.parametrize('price_text', ['价格800元', '价格：800元', '价格 800元'])
def test_literal_price_label_is_accepted_without_rewriting_source(market, price_text):
    unit = ingest(market, [message(text='出戴尔U2720显示器，完好带底座，' + price_text)])[0]
    row = dict(offer(unit), price_text=price_text)

    assert market.accept_units([unit], {'items': [row]}) == 1
    stored = market.db.execute('SELECT price,price_text,evidence FROM market_posts').fetchone()
    assert stored['price'] == 800 and stored['price_text'] == price_text
    assert stored['evidence'] == unit['text']


@pytest.mark.parametrize('price_text', [
    '价格800-900元', '价格800至900元', '价格不是800元', '不低于800元',
    '戴尔U2720显示器800元', '价格800元不含配件',
])
def test_price_label_does_not_admit_ranges_negations_or_product_text(market, price_text):
    unit = ingest(market, [message(text='出戴尔U2720显示器，完好带底座，' + price_text)])[0]
    with pytest.raises(ValueError, match='价格原文'):
        market.accept_units([unit], {'items': [dict(offer(unit), price_text=price_text)]})
    assert market.db.execute('SELECT count(*) FROM market_posts').fetchone()[0] == 0


def test_labeled_price_still_requires_exact_source_and_matching_amount(market):
    unit = ingest(market, [message()])[0]
    for values in ({'price_text': '价格900元'}, {'price_text': '价格800元', 'price': 900}):
        with pytest.raises(ValueError, match='价格'):
            market.accept_units([unit], {'items': [dict(offer(unit), **values)]})
    assert market.db.execute('SELECT count(*) FROM market_posts').fetchone()[0] == 0


def test_activity_metrics_label_repeated_sales_and_long_wanted(market):
    sale_messages = [
        message(gid=11, mid='sale-1', day=17),
        message(gid=22, mid='sale-2', day=18),
        message(gid=11, mid='sale-3', day=19),
    ]
    sale_units = ingest(market, sale_messages)
    market.accept_units(sale_units, {'items': [offer(unit) for unit in sale_units]})
    sale = market.list_items(date_from='2026-10-13', date_to='2026-10-19')['items'][0]
    assert sale['repeat_count'] == 3 and sale['active_days'] == 3 and sale['span_days'] == 3
    assert '重复发布' in sale['behavior_flags']

    want_messages = [
        message(gid=11, mid='want-1', day=15, text='求购戴尔U2720显示器，预算800元'),
        message(gid=11, mid='want-2', day=16, text='求购戴尔U2720显示器，预算800元'),
        message(gid=22, mid='want-3', day=17, text='求购戴尔U2720显示器，预算800元'),
    ]
    want_units = ingest(market, want_messages)
    market.accept_units(want_units, {'items': [dict(offer(unit), direction='want', title='戴尔U2720显示器', price_text='800', price=800, condition='', description='') for unit in want_units]})
    wanted = [item for item in market.list_items(date_from='2026-10-13', date_to='2026-10-19')['items'] if item['direction'] == 'want'][0]
    assert wanted['span_days'] == 3 and '长期求购' in wanted['behavior_flags']


def test_multi_component_service_bundle_is_marked_suspected_promotion(market):
    text = '88会员权益打包：芒果TV年卡、优酷年卡、夸克会员，35元'
    unit = ingest(market, [message(text=text)])[0]
    row = {'source_key': unit['key'], 'title': '88会员权益打包', 'direction': 'sale', 'price': 35,
           'price_text': '35元', 'condition': '', 'description': text, 'status': 'unknown', 'evidence': text,
           'bundle_mode': 'bundle', 'bundle_label': '88会员权益打包',
           'components': [{'title': '芒果TV年卡', 'evidence': '芒果TV年卡'}, {'title': '优酷年卡', 'evidence': '优酷年卡'}, {'title': '夸克会员', 'evidence': '夸克会员'}]}
    market.accept_units([unit], {'items': [row]})
    item = market.list_items(date_from='2026-10-13', date_to='2026-10-19')['items'][0]
    assert '疑似推广' in item['behavior_flags']


def test_split_bundle_payload_is_reunited_but_independent_prices_are_kept(market):
    unit = ingest(market, [message(text='搬家桌游打包出售：狼人杀、三国杀、UNO，整套80元')])[0]
    rows = [dict(offer(unit, '80', title=name), condition='') for name in ['狼人杀', '三国杀', 'UNO']]
    market.accept_units([unit], {'items': rows})
    items = market.list_items(date_from='2026-10-13', date_to='2026-10-19')['items']
    assert len(items) == 1 and items[0]['bundle_count'] == 3
    assert '疑似推广' not in items[0]['behavior_flags']
    assert len(market.detail(items[0]['item_id'])['sources']) == 1


def test_activity_matches_seller_title_without_merging_different_goods(market):
    units = ingest(market, [message(mid=str(day), day=day, text=f'出戴尔U2720显示器，第{day}天再发，完好带底座，价格800元') for day in [17, 18, 19]])
    market.accept_units(units, {'items': [offer(unit) for unit in units]})
    items = market.list_items(date_from='2026-10-13', date_to='2026-10-19')['items']
    assert len(items) == 3  # behavior matching must not assert these are the same physical item
    assert all(item['repeat_count'] == 3 and '重复发布' in item['behavior_flags'] for item in items)
    assert all(market.detail(item['item_id'])['item']['repeat_count'] == 3 for item in items)


def test_historical_results_do_not_leak_future_activity(market):
    units = ingest(market, [message(mid=str(day), day=day) for day in [17, 18, 19]])
    market.accept_units(units, {'items': [offer(unit) for unit in units]})
    item = market.list_items(date_from='2026-10-17', date_to='2026-10-17')['items'][0]
    assert item['repeat_count'] == 1 and '重复发布' not in item['behavior_flags']


def test_single_used_course_is_not_automatically_an_advert(market):
    unit = ingest(market, [message(text='转让健身私教课程，因毕业用不上了，价格800元')])[0]
    market.accept_units([unit], {'items': [dict(offer(unit, title='私教课程'), condition='')]})
    assert '疑似推广' not in market.list_items(date_from='2026-10-13', date_to='2026-10-19')['items'][0]['behavior_flags']


def test_invalid_component_keeps_previous_listing(market):
    unit = ingest(market, [message()])[0]
    market.accept_units([unit], {'items': [offer(unit)]})
    row = dict(offer(unit), bundle_mode='bundle', bundle_label='显示器', components=[{'title': '键盘', 'evidence': '键盘'}, {'title': '鼠标', 'evidence': '鼠标'}])
    with pytest.raises(ValueError):market.accept_units([unit], {'items': [row]})
    assert market.db.execute('SELECT count(*) FROM market_posts').fetchone()[0] == 1


def test_repair_legacy_rows_preserves_raw_messages_and_favorite(market):
    from qq_digest.secondhand_repair import repair_existing
    unit=ingest(market,[message()])[0]
    market.accept_units([unit],{'items':[offer(unit)]})
    key=market.db.execute('SELECT item_key FROM market_posts').fetchone()[0]
    market.set_favorite(key,True)
    market.db.execute("UPDATE market_posts SET description='invented',condition_text='invented',price_text='戴尔U2720显示器，完好带底座，价格800元'")
    market.db.commit()
    before=[tuple(row) for row in market.db.execute('SELECT * FROM messages')]
    result=repair_existing(market.archive)
    assert result['failed_sources']==0
    row=market.db.execute('SELECT * FROM market_posts').fetchone()
    assert row['price_text']=='800元' and row['description']=='' and row['condition_text']==''
    assert market.detail(key)['favorite']
    assert before==[tuple(row) for row in market.db.execute('SELECT * FROM messages')]
    again=repair_existing(market.archive)
    assert again['after_posts']==result['after_posts']==1


def test_shared_unpriced_post_is_one_collection_without_claiming_bundle_sale(market):
    unit=ingest(market,[message(text='出：数学建模习题、数学竞赛习题、工图画图工具')])[0]
    market.accept_units([unit],{'items':[dict(offer(unit,'',title=title),condition='') for title in ['数学建模习题','数学竞赛习题','工图画图工具']]})
    item=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]
    assert item['bundle_mode']=='collection' and item['bundle_count']==3
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==1
    from qq_digest.secondhand_repair import repair_existing
    assert repair_existing(market.archive)['failed_sources']==0


def test_promotion_uses_source_header_for_each_separately_priced_service(market):
    text='出88VIP会员权益，支持单独购买\n网易云40元\n芒果年卡35元\n夸克10元'
    unit=ingest(market,[message(text=text)])[0]
    rows=[dict(offer(unit,str(price),title=title),condition='',description=part,evidence=part) for title,price,part in [('网易云',40,'网易云40元'),('芒果年卡',35,'芒果年卡35元'),('夸克',10,'夸克10元')]]
    market.accept_units([unit],{'items':rows})
    items=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items']
    assert len(items)==3 and all('疑似推广' in item['behavior_flags'] for item in items)


def test_old_market_schema_upgrades_without_losing_rows(market):
    from qq_digest.secondhand_schema import initialize
    unit=ingest(market,[message()])[0];market.accept_units([unit],{'items':[offer(unit)]})
    for column in ['bundle_key','bundle_label','bundle_mode','components_json']:
        market.db.execute('ALTER TABLE market_posts DROP COLUMN '+column)
    initialize(market.db);initialize(market.db)
    row=market.db.execute('SELECT * FROM market_posts').fetchone()
    assert row['bundle_mode']=='single' and row['components_json']=='[]' and row['evidence']==unit['text']


def test_repair_keeps_favorite_specific_to_independently_priced_item(market):
    from qq_digest.secondhand_repair import repair_existing
    unit=ingest(market,[message(text='键盘10元，鼠标20元')])[0]
    rows=[dict(offer(unit,str(price),title=title),condition='',description=part,evidence=part) for title,price,part in [('键盘',10,'键盘10元'),('鼠标',20,'鼠标20元')]]
    market.accept_units([unit],{'items':rows})
    key=market.db.execute("SELECT item_key FROM market_posts WHERE title='键盘'").fetchone()[0]
    market.set_favorite(key,True);repair_existing(market.archive)
    assert [i['title'] for i in market.list_items(date_from='2026-10-13',date_to='2026-10-19',favorite=True)['items']]==['键盘']


def test_automatic_bundle_identity_survives_repair_and_explicit_reextraction(market):
    from qq_digest.secondhand_repair import repair_existing
    text='桌游全套打包出售：狼人杀、三国杀、UNO，整套80元'
    unit=ingest(market,[message(text=text)])[0]
    market.accept_units([unit],{'items':[dict(offer(unit,'80',title=t),condition='') for t in ['狼人杀','三国杀','UNO']]})
    key=market.db.execute('SELECT item_key FROM market_posts').fetchone()[0];market.set_favorite(key,True)
    before=tuple(market.db.execute('SELECT * FROM market_posts').fetchone())
    assert repair_existing(market.archive)['failed_sources']==0
    assert market.db.execute('SELECT item_key FROM market_posts').fetchone()[0]==key
    assert tuple(market.db.execute('SELECT * FROM market_posts').fetchone())==before
    row=dict(offer(unit,'80',title='桌游全套'),condition='',bundle_mode='bundle',bundle_label='桌游全套',components=[{'title':t,'evidence':t} for t in ['狼人杀','三国杀','UNO']])
    market.accept_units([unit],{'items':[row]})
    assert market.detail(key)['favorite']


def test_course_with_notes_still_needs_promotion_evidence(market):
    unit=ingest(market,[message(text='转让健身私教课程，附带课程资料，因毕业用不上了，价格800元')])[0]
    market.accept_units([unit],{'items':[dict(offer(unit,title='私教课程'),condition='')]})
    assert '疑似推广' not in market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]['behavior_flags']


def test_explicit_ai_bundle_cannot_override_separate_prices(market):
    unit=ingest(market,[message(text='键盘鼠标分别出售，键盘10元，鼠标20元')])[0]
    row=dict(offer(unit,'10',title='键盘鼠标'),condition='',bundle_mode='bundle',bundle_label='键盘鼠标',components=[{'title':'键盘','evidence':'键盘10元','price_text':'10元'},{'title':'鼠标','evidence':'鼠标20元','price_text':'20元'}])
    with pytest.raises(ValueError):market.accept_units([unit],{'items':[row]})


def test_bundle_recovers_local_component_quotations(market):
    unit=ingest(market,[message(text='桌游打包出售：狼人杀、三国杀，80元')])[0]
    market.accept_units([unit],{'items':[dict(offer(unit,'',title=title),condition='',description=title,evidence=title) for title in ['狼人杀','三国杀']]})
    items=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items']
    assert len(items)==1 and items[0]['bundle_mode']=='bundle'
