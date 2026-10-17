import json,runpy
from pathlib import Path
from uuid import uuid4
from test_integration import isolated_db
from test_company_documents import operate
from quant.db import connect


def test_prefetch_only_hits_and_resumes_from_stored_reports(isolated_db,tmp_path,monkeypatch):
    from quant import company_documents,config
    monkeypatch.setattr(config,'ROOT',tmp_path)
    calls=[]
    def fetch(symbol,page):
        calls.append((symbol,page))
        return 'https://basic.10jqka.com.cn/300138/operate.html',operate()
    monkeypatch.setattr(company_documents,'fetch_page',fetch)
    run=uuid4()
    with connect() as c:
        c.execute("INSERT INTO security(symbol,code,market,name) VALUES ('600036.SH','600036','SH','测试非命中')")
        c.execute("INSERT INTO multi_screen_run(id,status,effective_date,parameters,total,processed) VALUES (%s,'completed','2026-10-16','{}',2,2)",(run,))
        c.execute("INSERT INTO multi_screen_item(run_id,ordinal,symbol,result) VALUES (%s,1,'300138.SZ','{\"signals\":[{}]}'),(%s,2,'600036.SH','{\"signals\":[]}')",(run,run))
    script=Path(__file__).resolve().parents[1]/'scripts'/'prefetch_discussions.py'
    runpy.run_path(str(script),run_name='__main__')
    path=tmp_path/'.runtime'/f'discussion-prefetch-{run}.json'
    first=json.loads(path.read_text(encoding='utf8'))
    assert first['total']==1 and first['saved']==1 and first['failed']==0
    runpy.run_path(str(script),run_name='__main__')
    second=json.loads(path.read_text(encoding='utf8'))
    assert second['cached']==1 and second['saved']==0
    assert calls==[('300138.SZ','operate')]
