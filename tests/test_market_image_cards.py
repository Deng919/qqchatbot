import hashlib
import io

from PIL import Image
from tests.test_web import web_client
from tests.test_secondhand import message,offer,ingest
from qq_digest.secondhand import SecondhandService


def seed_market_photo(client,root,*,forward=False,count=1):
    stream=io.BytesIO();Image.new('RGB',(40,30),'blue').save(stream,format='PNG')
    data=stream.getvalue();path=root/'market-photo.png';path.write_bytes(data)
    refs=[{'key':'photo-'+str(i),'kind':'image','name':'商品配图'+str(i),'path':str(path),'md5':hashlib.md5(data).hexdigest()} for i in range(count)]
    metadata={'attachments':refs};kind='text'
    if forward:
        metadata={'forward_messages':[{'sender_qq':201,'sender_name':'合成发布者','timestamp':'2026-10-18T01:00:00+00:00','text':'出显示器，价格800元','attachments':refs},
                                     {'sender_qq':202,'sender_name':'另一发布者','timestamp':'2026-10-18T01:00:00+00:00','text':'出键盘，价格80元','attachments':[{**refs[0],'key':'other-photo','name':'另一个来源'}]}]};kind='forward'
    service=SecondhandService(client.app.state.archive);service.add_groups([123])
    units=ingest(service,[message(gid=123,mid='photo-post',text='出显示器，价格800元',kind=kind,metadata=metadata)])
    unit=next(u for u in units if u['text']=='出显示器，价格800元')
    service.accept_units([unit],{'items':[offer(unit,title='显示器')]})
    return service,service.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]['item_id'],data,path


def test_market_cards_and_detail_show_original_without_enabling_ai(web_client,monkeypatch,tmp_path):
    client,_,_=web_client;service,item_id,data,path=seed_market_photo(client,tmp_path)
    monkeypatch.setattr('qq_digest.attachments.AttachmentService._vision',lambda *args:(_ for _ in ()).throw(AssertionError('image browsing must not call AI')))
    client.post('/login',data={'password':'password123'})
    for grouped in ['', '&grouped=true']:
        result=client.get('/api/secondhand?date_from=2026-10-13&date_to=2026-10-19'+grouped).json()
        image=result['items'][0]['images'][0]
        assert image['name']=='商品配图0'
        assert str(path) not in str(result)
        assert client.get(image['url']).content==data
    detail=client.get('/api/secondhand/items/'+item_id).json()
    assert detail['item']['images'][0]['key']=='photo-0'
    assert client.app.state.archive.all_groups()[0].read_images is False
    image=detail['item']['images'][0]
    assert client.get('/api/secondhand/attachments?group_id=123&msg_id=photo-post').json()['items'][0]['url']
    path.write_bytes(b'changed');assert client.get(image['url']).status_code==404
    client.cookies.clear();assert client.get(image['url']).status_code==401


def test_market_photos_do_not_cross_forward_children_or_bypass_blocks(web_client,tmp_path):
    client,_,_=web_client;service,item_id,data,path=seed_market_photo(client,tmp_path,forward=True)
    client.post('/login',data={'password':'password123'})
    detail=client.get('/api/secondhand/items/'+item_id).json();images=detail['item']['images']
    assert len(images)==1 and images[0]['name']=='商品配图0'
    image=images[0];assert client.get(image['url']).content==data
    service.block(201);assert client.get(image['url']).status_code==404
    assert client.get('/api/secondhand/items/'+item_id).status_code==404


def test_card_image_preview_is_bounded_but_detail_keeps_source_gallery(web_client,tmp_path):
    client,_,_=web_client;service,item_id,_,_=seed_market_photo(client,tmp_path,count=9)
    client.post('/login',data={'password':'password123'})
    listing=client.get('/api/secondhand?date_from=2026-10-13&date_to=2026-10-19').json()
    assert len(listing['items'][0]['images'])==6
    assert len(client.get('/api/secondhand/items/'+item_id).json()['item']['images'])==9
    client.app.state.archive.connection.execute('UPDATE groups SET enabled=0 WHERE group_id=123');client.app.state.archive.connection.commit()
    assert client.get('/api/secondhand?date_from=2026-10-13&date_to=2026-10-19').json()['items']==[]


def test_explicit_market_preview_is_account_cache_only_and_original_stays_strict(web_client,tmp_path,monkeypatch):
    client,_,_=web_client;service,item_id,original,path=seed_market_photo(client,tmp_path)
    cfg=client.app.state.config;cfg.ntqq.qq_number=123456
    home=tmp_path/'profile';monkeypatch.setattr('qq_digest.attachments.Path.home',lambda:home)
    digest=hashlib.md5(original).hexdigest();cache=home/'Documents/Tencent Files/123456/nt_qq/nt_data/Pic/2026-10/Ori'/f'{digest}.jpg';cache.parent.mkdir(parents=True)
    exif=Image.Exif();exif[270]='synthetic private metadata'
    Image.new('RGB',(3000,1500),'red').save(cache,format='JPEG',exif=exif)
    client.post('/login',data={'password':'password123'})
    image=client.get('/api/secondhand/items/'+item_id).json()['item']['images'][0]
    assert image['preview_url']!=image['url']
    assert client.get(image['url']).content==original
    preview=client.get(image['preview_url']);assert preview.status_code==200
    assert preview.headers['content-type']=='image/jpeg' and preview.headers['cache-control']=='no-store'
    with Image.open(io.BytesIO(preview.content)) as decoded:
        assert max(decoded.size)<=1600 and not decoded.getexif() and 'icc_profile' not in decoded.info
        red,green,blue=decoded.convert('RGB').getpixel((0,0));assert red>blue
    path.write_bytes(b'changed');assert client.get(image['url']).status_code==404
    assert client.get(image['preview_url']).status_code==200
    context=client.get('/api/secondhand/source?group_id=123&msg_id=photo-post').json()
    assert next(message for message in context['messages'] if message['msg_id']=='photo-post')['images'][0]['preview_url']
    cache.write_bytes(b'not an image');assert client.get(image['preview_url']).status_code==404
    Image.new('RGB',(40,30),'red').save(cache,format='JPEG')
    assert client.get(image['preview_url']).status_code==200
    service.block(100);assert client.get(image['preview_url']).status_code==404


def test_oversized_cached_preview_is_an_unavailable_image_not_a_server_error(web_client,tmp_path,monkeypatch):
    client,_,_=web_client;_,item_id,original,_=seed_market_photo(client,tmp_path)
    client.app.state.config.ntqq.qq_number=123456
    home=tmp_path/'profile';monkeypatch.setattr('qq_digest.attachments.Path.home',lambda:home)
    cache=home/'Documents/Tencent Files/123456/nt_qq/nt_data/Pic/2026-10/Ori'/f'{hashlib.md5(original).hexdigest()}.jpg';cache.parent.mkdir(parents=True);cache.write_bytes(b'cached image')
    client.post('/login',data={'password':'password123'})
    image=client.get('/api/secondhand/items/'+item_id).json()['item']['images'][0]
    def bomb(*args,**kwargs):raise Image.DecompressionBombError('synthetic pixel limit')
    monkeypatch.setattr('qq_digest.attachments.Image.open',bomb)
    assert client.get(image['preview_url']).status_code==404
