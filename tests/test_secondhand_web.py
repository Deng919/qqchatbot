from tests.test_web import web_client


def test_market_auth_default_week_and_page(web_client):
    client,_,_=web_client
    assert client.get('/api/secondhand').status_code==401
    client.post('/login',data={'password':'password123'})
    data=client.get('/api/secondhand').json()
    from datetime import date
    assert (date.fromisoformat(data['date_to'])-date.fromisoformat(data['date_from'])).days==6
    page=client.get('/secondhand')
    assert page.status_code==200 and '二手信息' in page.text and 'market-list' in page.text


def test_market_block_validation_and_switch(web_client):
    client,_,_=web_client;client.post('/login',data={'password':'password123'})
    assert client.post('/api/secondhand/blocks',json={'qq':True}).status_code==422
    assert client.post('/api/secondhand/blocks',json={'qq':123456}).status_code==200
    assert client.get('/api/secondhand/blocks').json()['items'][0]['qq']==123456
    assert client.delete('/api/secondhand/blocks/123456').status_code==200
    assert client.get('/api/secondhand?min_price=200&max_price=100').status_code==422
    state=client.get('/api/features').json()
    assert client.put('/api/features',json={'values':{'secondhand':False},'expected_revision':state['revision']}).status_code==200
    assert client.get('/api/secondhand').status_code==403


def test_market_cannot_mutate_or_read_unknown_item(web_client):
    client,_,_=web_client;client.post('/login',data={'password':'password123'})
    key='a'*64
    assert client.get('/api/secondhand/items/'+key).status_code==404
    assert client.patch('/api/secondhand/items/'+key,json={'favorite':True}).status_code==404
    assert client.post('/api/secondhand/refresh',json={'date_from':'2026-10-19','date_to':'2026-10-13'}).status_code==422


def test_market_context_hides_blocked_neighbors(web_client):
    client,_,_=web_client;archive=client.app.state.archive;client.post('/login',data={'password':'password123'})
    from qq_digest.secondhand import SecondhandService
    from tests.test_secondhand import message
    service=SecondhandService(archive);service.add_groups([123]);service.block(200)
    archive.ingest([message(gid=123,mid='allowed'),message(gid=123,mid='blocked',sender=200)])
    response=client.get('/api/secondhand/source?group_id=123&msg_id=allowed')
    assert response.status_code==200
    assert all(row['sender_qq']!=200 for row in response.json()['messages'])


def test_market_grouped_feed_is_opt_in_and_validates_lane(web_client):
    from qq_digest.secondhand import SecondhandService
    from tests.test_secondhand import message,offer,ingest
    client,_,_=web_client;client.post('/login',data={'password':'password123'})
    service=SecondhandService(client.app.state.archive);service.add_groups([123])
    units=ingest(service,[message(gid=123,mid=str(n),text=f'出戴尔U2720显示器，第{n}次发布，价格800元') for n in range(3)])
    service.accept_units(units,{'items':[dict(offer(unit),condition='') for unit in units]})
    params='date_from=2026-10-13&date_to=2026-10-19'
    assert client.get('/api/secondhand?'+params).json()['total']==3
    data=client.get('/api/secondhand?grouped=true&'+params).json()
    assert data['total']==1 and data['items'][0]['publication_count']==3
    assert data['lane_counts']=={'sale':1,'want':0,'promotion':0}
    assert client.get('/api/secondhand?grouped=true&lane=invalid').status_code==422


def test_market_category_filter_and_facets_are_served(web_client):
    from qq_digest.secondhand import SecondhandService
    from tests.test_secondhand import message,offer,ingest
    client,_,_=web_client;client.post('/login',data={'password':'password123'})
    service=SecondhandService(client.app.state.archive);service.add_groups([123])
    units=ingest(service,[message(gid=123,mid='a',text='全麦面包'),message(gid=123,mid='b',text='显示器')])
    service.accept_units(units,{'items':[offer(u,'',u['text']) for u in units]})
    params='date_from=2026-10-13&date_to=2026-10-19&category=food'
    for grouped in ['', '&grouped=true']:
        data=client.get('/api/secondhand?'+params+grouped).json()
        assert data['total']==1 and data['items'][0]['category']=='food'
        assert sum(c['count'] for c in data['categories'])==2
    assert client.get('/api/secondhand?category=invalid').status_code==422
