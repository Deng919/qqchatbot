"""Read-time categories, inferred conservatively from each item's own evidence."""
from __future__ import annotations

import re


CATEGORY_LABELS = {
    'electronics': '数码电器',
    'books': '书籍文具',
    'home': '家居日用',
    'fashion': '服饰美妆',
    'sports': '运动出行',
    'food': '食品饮品',
    'digital': '线上资源',
    'services': '票券服务',
    'other': '其他与综合',
}

_RULES = {key: re.compile(pattern, re.I) for key, pattern in {
    'electronics': r'手机|电脑(?!包)|笔记本(?!子)|显示器|耳机|键盘|鼠标|充电|无线充|扩展坞|网线|相机|云台|手环|台灯|电锅|暖风机|吸尘器|U\s*盘|硬盘|单片机套件|苹果\s*\d|iPhone|iPad|MacBook|Nintendo|Switch\s*\d|kindle|漫步者|酷态科',
    'books': r'教材|教科书|纸质书|练习|习题|真题|单词书|文具|计算器|画图工具|课程笔记|课的笔记|教程|实践册|习概|马原|微积分|线性代数|有机化学|特种加工|西方经济学|工程制图|机械制图|画法几何|电路理论|复变函数|语言学概论|英语视听说|批判性思维|数学竞赛|数学建模|计算机[三四34]{1,2}级',
    'home': r'置物架|晾衣架|椅子|桌子|床垫|枕头|靠枕|镜子|化妆镜|挂钩|雨伞|排插|插线板|电脑包|背包|浴帽|罩袋|洗衣液|织物喷雾|打包盒|热熔胶|vhb胶|水杯架',
    'fashion': r'粉饼|保湿霜|唇膏|口红|护肤|化妆品|水手服|胸挡|女巫帽|连衣裙|外套|短袖|羽绒服|牛仔裤|运动鞋',
    'sports': r'球拍|羽毛球|网球|弹力带|跆拳道服|道鞋|头盔|电动车|电驴|单车|自行车|吉他(?!课)|狼人杀|三国杀|大富翁|\buno\b',
    'food': r'零食|面包|牛奶|奶茶|月饼|豆浆|魔芋面|浓汤|养生茶|啤酒|饼干|巧克力|饮料|咖啡(?!机)',
    'digital': r'(?:网易(?:云)?|优酷|爱奇艺|腾讯视频|QQ音乐|夸克|芒果(?:\s*TV)?).{0,10}(?:会员|年卡|月卡|VIP|账号|权益)|(?:网盘|云盘)(?:年卡|会员|账号|资源|文件|链接|发货)|激活码|软件授权|网课|线上课|电子(?:版|书|资料|笔记)|\bPDF\b|订阅',
    'services': r'门票|歌手.*(?:票|座)|歌会.*票|演唱会.*票|买一送一|优惠券|兑换券|奶茶券|储值卡|健身卡|游泳.*(?:卡|课)|私教课|琴行.*课|洗鞋|洗衣(?!液|粉|凝珠)|寄件|快递(?!打包盒|盒)|高德打车|飞猪|万豪|希尔顿|淘票票|饿了么|淘宝闪购',
}.items()}

# Only delivery/entitlement qualifiers immediately attached to this name may
# supplement it. An entire advertising paragraph is not an item's category.
_MODIFIER = r'(?:(?:PDF|电子版|电子书|电子资料|电子笔记|数字版|网盘电子版|网盘发货|网盘链接|云盘发货|会员|年卡|月卡|账号|优惠券|兑换券|储值卡|门票)[\s/+、与和]*)+'
_MODIFIER_PREFIX = re.compile(r'^\s*(?:是|为|仅|提供|支持)?\s*('+_MODIFIER+r')',re.I)
_MODIFIER_ONLY = re.compile(_MODIFIER,re.I)


def _category_text(title,evidence):
    parts=[part.strip() for part in re.split(r'[\n\r,，;；。]',evidence)]
    modifiers=[]
    for index,part in enumerate(parts):
        position=part.casefold().find(title.casefold())
        if position<0:continue
        suffix=part[position+len(title):]
        attached=_MODIFIER_PREFIX.match(suffix)
        if attached:modifiers.append(attached[1])
        for following in parts[index+1:]:
            if not _MODIFIER_ONLY.fullmatch(following):break
            modifiers.append(following)
    return ' '.join([title,*modifiers])


def _matches(text):
    found={key for key,pattern in _RULES.items() if pattern.search(text)}
    # A food voucher / gym pass is an offline entitlement, not physical stock.
    if 'services' in found:
        found.difference_update({'food','sports'})
    # Explicit electronic delivery distinguishes a digital book from a paper book.
    if 'digital' in found:
        found.discard('books')
    return found


def item_category(item):
    components=item.get('components') or []
    if components:
        # A component's evidence may quote the entire package. Do not let that
        # quote classify another unknown component, and never split the package.
        categories=[_matches(part['title']) for part in components]
        if any(len(found)!=1 for found in categories):return 'other'
        combined=set().union(*categories)
        return next(iter(combined)) if len(combined)==1 else 'other'
    found=_matches(_category_text(item['title'],item.get('evidence','')))
    return next(iter(found)) if len(found)==1 else 'other'
