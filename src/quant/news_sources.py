"""Public news feeds. No cookies, no authentication bypass, bounded pagination."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from hashlib import md5,sha1
import html,re,time
import httpx

NAMES={'ths':'同花顺快讯','cls':'财联社电报','sina':'新浪7×24'}
SH=ZoneInfo('Asia/Shanghai')

def text(value):
    return html.unescape(re.sub('<[^>]*>','',str(value or ''))).strip()

def parse_time(value):
    if isinstance(value,(int,float)) or str(value).isdigit():
        number=float(value)
        return datetime.fromtimestamp(number/(1000 if number>1e12 else 1),timezone.utc)
    return datetime.fromisoformat(str(value)).replace(tzinfo=SH).astimezone(timezone.utc)

def fetch(source,page=1,before=None):
    headers={'User-Agent':'Mozilla/5.0'}
    if source=='ths':
        url='https://news.10jqka.com.cn/tapp/news/push/stock';params={'page':page,'tag':'','track':'website'}
    elif source=='cls':
        url='https://www.cls.cn/v1/roll/get_roll_list'
        params={'app':'CailianpressWeb','category':'','last_time':int(before.timestamp()) if before else int(time.time()),'os':'web','refresh_type':'1','rn':'20','sv':'8.4.6'}
        params['sign']=md5(sha1(urlencode(params).encode()).hexdigest().encode()).hexdigest()
    elif source=='sina':
        url='https://zhibo.sina.com.cn/api/zhibo/feed'
        params={'page':page,'page_size':'20','zhibo_id':'152','tag_id':'0','dire':'f','dpc':'1','pagesize':'20','type':'1'}
    else:raise ValueError('未知新闻来源')
    with httpx.Client(timeout=httpx.Timeout(30,connect=10),follow_redirects=False,headers={**headers,'Referer':url}) as c:
        r=c.get(url,params=params)
    if r.status_code!=200:raise ValueError(f'{NAMES[source]} HTTP {r.status_code}')
    data=r.json()
    try:
        rows=data['data']['list'] if source=='ths' else data['data']['roll_data'] if source=='cls' else data['result']['data']['feed']['list']
        if not isinstance(rows,list) or not rows:raise ValueError('来源未返回可核验列表')
        result=[]
        for row in rows:
            content=text(row.get('digest') if source=='ths' else row.get('content') if source=='cls' else row.get('rich_text'))
            title=text(row.get('title')) or content[:100]
            published=parse_time(row['rtime'] if source=='ths' else row['ctime'] if source=='cls' else row['create_time'])
            source_id=str(row.get('id') or row.get('seq') or row.get('url') or '')
            if not source_id or not title or published>datetime.now(timezone.utc)+__import__('datetime').timedelta(minutes=10):raise ValueError('文章ID、正文或时间异常')
            source_url=str(row.get('url') or (f'https://www.cls.cn/detail/{source_id}' if source=='cls' else 'https://finance.sina.com.cn/7x24/'))
            if not source_url.startswith(('https://','http://')):source_url=url
            result.append(dict(source_id=source_id,title=title,content=content or title,published_at=published,source_url=source_url,raw=row))
        return result
    except (KeyError,TypeError,ValueError) as exc:raise ValueError(f'{NAMES[source]}：列表结构或时间异常') from exc
