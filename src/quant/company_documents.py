"""Versioned public F10 narratives; independent of technical screens and valuations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
import hashlib,re,time
from uuid import uuid4
import httpx
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb
from .db import connect
from .services import clean

PARSER_VERSION='ths-f10-v1'
BASE='https://basic.10jqka.com.cn'
_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='company-documents')
_last_request=0.0


def validate_symbol(symbol):
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',symbol):raise ValueError('请输入本地A股完整代码')
    with connect() as conn:
        row=conn.execute('SELECT symbol,name FROM security WHERE symbol=%s',(symbol,)).fetchone()
    if not row:raise ValueError('证券不在本地数据库中')
    return row


def plain(node):
    if node is None:return ''
    for item in node.select('script,style,.more,.close'):item.decompose()
    text=node.get_text('\n',strip=True).replace('\r','')
    return '\n'.join(re.sub(r'[\t \u3000]+',' ',line).strip() for line in text.splitlines() if line.strip())


def decode_page(body):
    meta=re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)',body[:4096])
    enc=meta.group(1).decode('ascii').lower() if meta else 'utf-8'
    if enc in ('gbk','gb2312'):enc='gb18030'
    if enc not in ('utf-8','utf8','gb18030'):raise ValueError('网页编码发生变化')
    return body.decode(enc,errors='strict'),enc


def parse_page(body,code,page):
    text,encoding=decode_page(body)
    soup=BeautifulSoup(text,'html.parser')
    if not soup.title or not re.search(r'[（(]'+re.escape(code)+r'[）)]',soup.title.get_text()):
        raise ValueError('网页证券代码不匹配，可能是验证页或跳转页')
    documents=[]
    def add(kind,title,content,report_key='',details=None,is_excerpt=False):
        if len(content)<10:return
        documents.append(dict(kind=kind,title=title,content=content,report_key=report_key,
          report_date=report_key or None,details=details or {},is_excerpt=is_excerpt))
    if page=='operate':
        fields={}
        for li in soup.select('#intro .main_intro_list > li'):
            label=li.find('span');value=li.find('p')
            if label and value:fields[label.get_text(strip=True).rstrip('：:')]=plain(value)
        add('business','主营业务与产品','\n\n'.join(k+'：'+v for k,v in fields.items()),details=fields)
        observe=soup.select_one('#observe')
        if observe:
            tabs=observe.select('.m_tab a.operateTab')
            blocks=observe.select('.m_tab_content.m_tab_content2')
            if len(tabs)!=len(blocks):raise ValueError('经营评述日期与正文数量不一致')
            for tab,block in zip(tabs,blocks):
                report=date.fromisoformat(tab.get_text(strip=True)).isoformat()
                full=block.select_one('p.clearfix')
                paragraph=full if full is not None else block.select_one('p.f14.pr')
                content=plain(paragraph)
                content=re.sub(r'(收起▲|查看全部▼)\s*$','',content).strip()
                add('discussion','董事会经营评述',content,report,is_excerpt=full is None)
    elif page=='company':
        section=soup.select_one('#detail');fields={}
        if section:
            for td in section.select('td'):
                label=td.find('strong',recursive=False)
                if not label:continue
                name=re.sub(r'\s+','',label.get_text()).rstrip('：:')
                # Remove embedded hover biographies from company fact cells.
                for popup in td.select('.person_table,script,style'):popup.decompose()
                label.decompose();value=plain(td)
                if name and value:fields[name]=value
        add('company','公司资料','\n\n'.join(k+'：'+v for k,v in fields.items()),details=fields)
        people=[];seen=set()
        for table in soup.select('#manager table.ggintro'):
            name=plain(table.select_one('h3'));role=plain(table.select_one('.jobs'))
            bio=plain(table.select_one('.mainintro'))
            dates=[plain(n) for n in table.select('thead .date')]
            if name and bio and (name,role,bio) not in seen:
                seen.add((name,role,bio));people.append({'name':name,'role':role,'dates':dates,'biography':bio})
        add('executives','高管介绍','\n\n'.join(p['name']+' · '+p['role']+'\n'+p['biography'] for p in people),details={'people':people})
    else:raise ValueError('不支持的资料页面')
    if not documents:raise ValueError('未识别到有效正文，原有资料保留')
    return documents,encoding


def save_page(symbol,page,url,body):
    documents,encoding=parse_page(body,symbol[:6],page)
    digest=hashlib.sha256(body).hexdigest()
    with connect() as conn:
        row=conn.execute('''INSERT INTO company_page_revision(symbol,page,source_url,sha256,body,encoding,parser_version)
          VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(symbol,page,sha256) DO NOTHING RETURNING id''',
          (symbol,page,url,digest,body,encoding,PARSER_VERSION)).fetchone()
        if not row:return
        for doc in documents:
            conn.execute('''INSERT INTO company_document(page_id,symbol,kind,report_key,report_date,title,content,details,is_excerpt)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
              (row['id'],symbol,doc['kind'],doc['report_key'],doc['report_date'],doc['title'],doc['content'],Jsonb(doc['details']),doc['is_excerpt']))


def fetch_page(symbol,page):
    global _last_request
    delay=1-(time.monotonic()-_last_request)
    if delay>0:time.sleep(delay)
    _last_request=time.monotonic()
    url=f'{BASE}/{symbol[:6]}/{page}.html'
    headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.5735.90 Safari/537.36',
             'Referer':f'https://stockpage.10jqka.com.cn/{symbol[:6]}/'}
    with httpx.Client(timeout=20,follow_redirects=False,headers=headers) as client:
        deadline=time.monotonic()+25
        with client.stream('GET',url) as response:
            if response.status_code!=200:raise ValueError(f'同花顺返回HTTP {response.status_code}，请稍后重试')
            chunks=[];size=0
            for chunk in response.iter_bytes():
                if time.monotonic()>deadline:raise ValueError('网页响应过慢，请稍后重试')
                size+=len(chunk)
                if size>4_000_000:raise ValueError('网页大小异常，未入库')
                chunks.append(chunk)
    return url,b''.join(chunks)


def run_refresh(symbol,job_id):
    with connect() as conn:
        claimed=conn.execute("UPDATE company_refresh SET status='running',started_at=now() WHERE symbol=%s AND job_id=%s AND status='queued' RETURNING symbol",(symbol,job_id)).fetchone()
    if not claimed:return
    saved=0;errors=[]
    for page in ('operate','company'):
        try:
            url,body=fetch_page(symbol,page);save_page(symbol,page,url,body);saved+=1
        except (httpx.HTTPError,UnicodeError,ValueError) as exc:
            errors.append(page+'：'+(str(exc) if isinstance(exc,ValueError) else '网络或编码异常，请稍后重试'))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Company page refresh failed: %s %s',symbol,page)
            errors.append(page+'：资料保存失败，请查看本地日志')
    with connect() as conn:
        conn.execute('''UPDATE company_refresh SET status=%s,finished_at=now(),pages_saved=%s,error=%s,
          last_success_at=CASE WHEN %s>0 THEN now() ELSE last_success_at END WHERE symbol=%s AND job_id=%s''',
          ('completed' if saved==2 else 'partial' if saved else 'failed',saved,'；'.join(errors) or None,saved,symbol,job_id))


def expired(state):
    if not state:return False
    age=datetime.now(timezone.utc)-(state.get('started_at') or state['requested_at'])
    return state['status'] in ('queued','running') and age>timedelta(minutes=15 if state['status']=='queued' else 3)


def reserve_refresh(symbol,force=False):
    validate_symbol(symbol)
    with connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(779949)')
        row=conn.execute('SELECT * FROM company_refresh WHERE symbol=%s',(symbol,)).fetchone()
        now=datetime.now(timezone.utc)
        if row and not expired(row):
            if row['status'] in ('queued','running'):return row['job_id'],True
            # A failed request is throttled too; do not loop when the site is unavailable.
            cooldown=timedelta(seconds=30) if force else timedelta(hours=1) if row['status']!='completed' else timedelta(days=7)
            if now-(row['finished_at'] or row['requested_at'])<cooldown:return row['job_id'],True
        pending=conn.execute("SELECT count(*) AS n FROM company_refresh WHERE status IN ('queued','running') AND requested_at>now()-interval '15 minutes'").fetchone()['n']
        if pending>=20:raise ValueError('资料更新队列较忙，请稍后重试')
        job=uuid4()
        conn.execute('''INSERT INTO company_refresh(symbol,job_id,status) VALUES (%s,%s,'queued')
          ON CONFLICT(symbol) DO UPDATE SET job_id=EXCLUDED.job_id,status='queued',requested_at=now(),
          started_at=NULL,finished_at=NULL,error=NULL,pages_saved=0''',(symbol,job))
    _executor.submit(run_refresh,symbol,job)
    return job,False


def documents(symbol):
    security=validate_symbol(symbol)
    with connect() as conn:
        state=conn.execute('SELECT * FROM company_refresh WHERE symbol=%s',(symbol,)).fetchone()
        docs=conn.execute('''SELECT DISTINCT ON(d.kind,d.report_key) d.id,d.kind,d.report_date,d.title,d.content,d.details,d.is_excerpt,
          p.collected_at,p.source_url,p.sha256,p.parser_version FROM company_document d
          JOIN company_page_revision p ON p.id=d.page_id WHERE d.symbol=%s
          ORDER BY d.kind,d.report_key,p.collected_at DESC,p.id DESC''',(symbol,)).fetchall()
    if expired(state):state={**state,'status':'interrupted','error':'上次资料更新已中断，可重新更新'}
    latest=max((d['collected_at'] for d in docs),default=None)
    now=datetime.now(timezone.utc)
    checked=max((stamp for stamp in (latest,state.get('last_success_at') if state else None) if stamp),default=None)
    stale=checked is None or now-checked>timedelta(days=7)
    retry=state and state['status'] in ('partial','failed','interrupted')
    due=(stale or bool(retry)) and (not state or state['status'] not in ('queued','running') and now-(state['finished_at'] or state['requested_at'])>timedelta(hours=1))
    return clean({**security,'documents':docs,'refresh':state,'stale':stale,'auto_refresh_due':due,
                  'source':'同花顺公开F10','source_url':f'{BASE}/{symbol[:6]}/operate.html'})
