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
