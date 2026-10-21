import pytest
from tests.test_secondhand import market,message,offer,ingest
from tests.test_web import web_client


def test_dismiss_tracks_product_across_groups_and_price_changes_but_not_seller(market):
    units=ingest(market,[message(),message(22,'b',text='出戴尔U2720显示器，价格700元'),message(mid='c',text='出苹果手机，价格800元')])
    market.accept_units(units,{'items':[offer(units[0]),dict(offer(units[1],'700'),condition=''),offer(units[2],title='苹果手机')]})
    params=dict(date_from='2026-10-13',date_to='2026-10-19')
    before=market.list_items(**params)
    selected=next(i for i in before['items'] if i['title']=='戴尔U2720显示器')
    keys=market.dismiss_items([selected['item_id']])
    assert len(keys)==1 and market.blocked_ids()==set()
    after=market.list_items(**params,grouped=True)
    assert after['total']==1 and after['items'][0]['title']=='苹果手机'
    assert sum(c['count'] for c in after['categories'])==1
    assert market.list_items(**params)['total']==1
    unit=ingest(market,[message(mid='new',text='出戴尔U2720显示器，降至600元')])[0]
    market.accept_units([unit],{'items':[dict(offer(unit,'600'),condition='')]})
    assert market.list_items(**params)['total']==1
    hidden=market.dismissed()
    assert len(hidden)==1 and hidden[0]['title']=='戴尔U2720显示器'
    market.restore_items(keys)
    assert market.list_items(**params,grouped=True)['total']==2


def test_dismiss_is_atomic_and_keeps_another_seller_visible(market):
    units=ingest(market,[message(),message(22,'b',sender=200)])
    market.accept_units(units,{'items':[offer(u) for u in units]})
    items=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items']
    with pytest.raises(LookupError):market.dismiss_items([items[0]['item_id'],'missing'])
    assert market.dismissed()==[]
    market.dismiss_items([items[0]['item_id']])
    assert market.list_items(date_from='2026-10-13',date_to='2026-10-19')['total']==1


def test_dismissed_bundle_stays_hidden_after_price_and_component_order_change(market):
    def add(mid,price,reverse=False):
        unit=ingest(market,[message(mid=mid,text=f'显示器键盘打包，价格{price}元')])[0]
        titles=['显示器','键盘']
        if reverse:titles.reverse()
        market.accept_units([unit],{'items':[dict(offer(unit,str(price),title=title),condition='') for title in titles]})
    params=dict(date_from='2026-10-13',date_to='2026-10-19')
    add('first',1000)
    first=market.list_items(**params)['items'][0]
    assert first['bundle_mode']=='bundle'
    market.dismiss_items([first['item_id']])
    add('lower-price',800,True)
    assert market.list_items(**params)['total']==0


def test_interest_routes_auth_validation_and_operation_lock(web_client):
    client,_,_=web_client
    assert client.post('/api/secondhand/dismiss',json={'item_ids':['a']}).status_code==401
    client.post('/login',data={'password':'password123'})
    assert client.post('/api/secondhand/dismiss',json={'item_ids':[]}).status_code==422
    assert client.post('/api/secondhand/dismiss',json={'item_ids':['missing']}).status_code==404
    assert client.get('/api/secondhand/dismissed').json()=={'items':[]}
    with client.app.state.operations.claim('backup'):
        assert client.post('/api/secondhand/dismissed/restore',json={'keys':['a'*64]}).status_code==409
