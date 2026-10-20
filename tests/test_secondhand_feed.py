from tests.test_secondhand import market, message, offer, ingest


def feed(market, **kwargs):
    return market.list_items(date_from='2026-10-13',date_to='2026-10-19',grouped=True,**kwargs)


def test_related_publications_fold_before_pagination_and_keep_member_prices(market):
    units=ingest(market,[message(mid=str(n),text=f'出戴尔U2720显示器，第{n}次发布，价格{800-n}元') for n in range(1,32)])
    market.accept_units(units,{'items':[dict(offer(unit,str(800-n)),condition='') for n,unit in enumerate(units,1)]})
    result=feed(market,page_size=1)
    assert result['total']==1 and len(result['items'])==1
    item=result['items'][0]
    assert item['publication_count']==31 and item['member_count']==31
    assert len({p['item_id'] for p in item['publications']})==31
    assert {p['price'] for p in item['publications']}==set(range(769,800))
    assert all(market.detail(p['item_id'])['sources'] for p in item['publications'])


def test_promotion_is_separate_and_folded_by_seller(market):
    text='88VIP会员权益，支持单独购买\n网易云会员40元\n芒果年卡35元\n夸克会员10元'
    unit=ingest(market,[message(text=text)])[0]
    rows=[dict(offer(unit,str(price),title=title),condition='',description=part,evidence=part) for title,price,part in [('网易云会员',40,'网易云会员40元'),('芒果年卡',35,'芒果年卡35元'),('夸克会员',10,'夸克会员10元')]]
    market.accept_units([unit],{'items':rows})
    normal=feed(market)
    assert normal['total']==0 and normal['lane_counts']['promotion']==1
    promotion=feed(market,lane='promotion')
    assert promotion['total']==1 and promotion['items'][0]['member_count']==3
    assert promotion['items'][0]['publication_count']==1
    assert promotion['items'][0]['price_text']==''  # three separate prices are never presented as one total


def test_wanted_has_its_own_lane_and_one_repeat_card(market):
    units=ingest(market,[message(mid=str(day),day=day,text='求购戴尔U2720显示器，宿舍使用，预算800元') for day in [15,17,19]])
    market.accept_units(units,{'items':[dict(offer(unit),direction='want',condition='') for unit in units]})
    assert feed(market)['total']==0
    result=feed(market,lane='want')
    assert result['total']==1 and result['items'][0]['publication_count']==3
    assert result['items'][0]['active_days']==3


def test_unknown_original_author_is_not_grouped(market):
    units=ingest(market,[message(mid=str(n),kind='forward',metadata={'forward_messages':[{'text':'出戴尔U2720显示器，完好，800元','timestamp':'2026-10-18T10:00:00+08:00'}]}) for n in range(2)])
    market.accept_units(units,{'items':[offer(unit) for unit in units]})
    assert feed(market)['total']==2


def test_feed_history_and_counts_respect_date_and_group_filters(market):
    units=ingest(market,[message(mid='old',day=13),message(gid=22,mid='new',day=19)])
    market.accept_units(units,{'items':[offer(unit) for unit in units]})
    result=market.list_items(date_from='2026-10-19',date_to='2026-10-19',grouped=True,group_id=22)
    item=result['items'][0]
    assert item['publication_count']==1 and item['group_count']==1
    assert [p['msg_id'] for p in item['publications']]==['new']


def test_feed_filters_before_grouping_and_ungrouped_view_stays_available(market):
    units=ingest(market,[message(mid='a',text='出戴尔U2720显示器，完好底座，价格800元'),message(mid='b',text='出戴尔U2720显示器，完好有划痕，价格700元')])
    market.accept_units(units,{'items':[offer(units[0]),offer(units[1],'700')]})
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==2
    result=feed(market,max_price=750)
    assert result['total']==1 and len(result['items'][0]['publications'])==1
    assert result['items'][0]['publications'][0]['price']==700
