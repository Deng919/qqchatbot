import pytest

from tests.test_secondhand import market, message, offer, ingest
from tests.test_secondhand_feed import feed


@pytest.mark.parametrize('title,evidence,category',[
    ('全麦面包','全麦面包还没拆封','food'),
    ('芋泥月饼','芋泥月饼出一盒','food'),
    ('纯牛奶','纯牛奶六瓶','food'),
    ('奶茶','奶茶刚点错了','food'),
    ('戴尔U2720显示器','戴尔U2720显示器完好','electronics'),
    ('苹果7p','苹果7p手机','electronics'),
    ('Nintendo Switch 2','Nintendo Switch 2闲置','electronics'),
    ('32G U盘','32G U盘','electronics'),
    ('高等数学练习册','高等数学练习册无笔记','books'),
    ('习概马原','习概马原纸质教材','books'),
    ('计算器fx999','计算器fx999考试用','books'),
    ('分层置物架','分层置物架宿舍用','home'),
    ('rog电脑包','rog电脑包','home'),
    ('紫色洗衣液','紫色洗衣液','home'),
    ('快递打包盒','快递打包盒','home'),
    ('画法几何','画法几何','books'),
    ('十佳歌手vip座','十佳歌手vip座','services'),
    ('一次性浴帽or食品罩袋','一次性浴帽or食品罩袋','home'),
    ('方里粉饼','方里粉饼全新','fashion'),
    ('水手服替换胸挡','水手服替换胸挡','fashion'),
    ('电动车头盔','电动车头盔九成新','sports'),
    ('二手网球拍','二手网球拍带拍套','sports'),
    ('网易云黑胶 VIP 年卡','网易云黑胶 VIP 年卡','digital'),
    ('芒果/优酷年卡','芒果/优酷年卡','digital'),
    ('激活码','激活码软件授权','digital'),
    ('课程资料','课程资料是网盘电子版','digital'),
    ('stm32教程','stm32教程纸质书','books'),
    ('高等数学教程','高等数学教程，PDF电子版，网盘发货','digital'),
    ('奶茶','奶茶优惠券，一张5元','services'),
    ('保温壶','88VIP长期优惠，网易云40元，保温壶20元','other'),
    ('网易云音乐抱枕','网易云音乐抱枕，实物全新20元','other'),
    ('网易云','网易云40元','other'),
    ('未知物件','未知物件，另外出售网易云会员40元','other'),
    ('计算机三四级','计算机三四级','books'),
    ('茉莉奶白买一送一券','茉莉奶白买一送一券','services'),
    ('十佳歌手VIP票','十佳歌手VIP票','services'),
    ('东迈健身卡','东迈健身卡','services'),
    ('菜鸟寄件','菜鸟寄件优惠','services'),
    ('万豪尊贵金卡','万豪尊贵金卡','services'),
    ('未知型号','未知型号，有一个','other'),
    ('芒果','芒果，有需要私聊','other'),
    ('苹果','苹果，有需要私聊','other'),
])
def test_categories_follow_the_item_evidence(market,title,evidence,category):
    unit=ingest(market,[message(text=evidence)])[0]
    market.accept_units([unit],{'items':[dict(offer(unit,'',title),condition='')]})
    item=market.list_items(date_from='2026-10-13',date_to='2026-10-19')['items'][0]
    assert item['category']==category
    assert market.detail(item['item_id'])['item']['category']==category


def test_component_categories_do_not_split_mixed_packages(market):
    for n,titles in enumerate([['充电宝','显示器'],['充电宝','全麦面包'],['充电宝','未知型号']]):
        text='打包出售'+ '和'.join(titles)
        unit=ingest(market,[message(mid=str(n),text=text)])[0]
        row=dict(offer(unit,'',text),bundle_mode='bundle',bundle_label=text,
                 components=[{'title':title,'evidence':text,'price_text':''} for title in titles])
        market.accept_units([unit],{'items':[row]})
    rows={item['title']:item for item in feed(market)['items']}
    assert len(rows)==3
    assert rows['打包出售充电宝和显示器']['category']=='electronics'
    assert rows['打包出售充电宝和全麦面包']['category']=='other'
    assert rows['打包出售充电宝和未知型号']['category']=='other'
    assert all(len(row['components'])==2 for row in rows.values())


def test_promotion_categories_use_own_evidence_and_keep_prices(market):
    text='88VIP会员权益，长期优惠\n网易云会员40元\n菜鸟寄件3元\n全麦面包5元'
    unit=ingest(market,[message(text=text)])[0]
    rows=[dict(offer(unit,str(price),title),condition='',description=part,evidence=part)
          for title,price,part in [('网易云会员',40,'网易云会员40元'),('菜鸟寄件',3,'菜鸟寄件3元'),('全麦面包',5,'全麦面包5元')]]
    market.accept_units([unit],{'items':rows})
    result=feed(market,lane='promotion',category='services')
    assert result['total']==1 and result['items'][0]['member_count']==1
    assert result['items'][0]['publications'][0]['price']==3
    assert {c['id']:c['count'] for c in result['categories'] if c['count']}=={'digital':1,'services':1,'food':1}
    assert feed(market,lane='promotion',category='digital')['items'][0]['publications'][0]['price']==40


def test_category_facets_count_all_pages_and_lane_facets_respect_category(market):
    units=ingest(market,[message(mid='a',text='充电宝'),message(mid='b',text='全麦面包'),
                         message(mid='c',text='全麦面包',sender=200),message(mid='d',text='网球拍')])
    market.accept_units(units,{'items':[dict(offer(u,'',u['text']),direction='want' if n==2 else 'sale') for n,u in enumerate(units)]})
    result=feed(market,page_size=1,category='food')
    assert result['total']==1 and result['lane_counts']=={'sale':1,'want':1,'promotion':0}
    assert sum(c['count'] for c in result['categories'])==3
    assert result['items'][0]['category']=='food'
    assert feed(market,q='全麦',category='electronics')['total']==0
    assert sum(c['count'] for c in feed(market,q='全麦')['categories'])==1
    assert feed(market,category='food',lane='want')['total']==1
    with pytest.raises(ValueError,match='分类'):feed(market,category='made-up')


def test_category_counts_respect_date_group_price_favorite_and_blocks(market):
    units=ingest(market,[message(mid='a',text='全麦面包10元',day=13),message(gid=22,mid='b',text='全麦面包20元',sender=200),message(mid='c',text='充电宝30元')])
    market.accept_units(units,{'items':[offer(u,str(p),title) for u,p,title in zip(units,[10,20,30],['全麦面包','全麦面包','充电宝'])]})
    a=feed(market,group_id=22)['items'][0]
    market.set_favorite(a['item_id'],True)
    for kwargs in [{'group_id':22},{'min_price':15,'max_price':25},{'favorite':True}]:
        result=feed(market,**kwargs)
        assert {c['id']:c['count'] for c in result['categories'] if c['count']}=={'food':1}
    result=market.list_items(date_from='2026-10-19',date_to='2026-10-19',grouped=True,category='food')
    assert result['total']==1
    market.block(200)
    assert feed(market,favorite=True)['total']==0
    assert sum(c['count'] for c in feed(market,favorite=True)['categories'])==0
