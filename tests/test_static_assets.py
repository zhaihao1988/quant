from hashlib import sha256
from fastapi.testclient import TestClient


def test_home_revalidates_html_and_versions_changed_assets(tmp_path,monkeypatch):
    from quant import api
    monkeypatch.setattr(api,'static',tmp_path)
    names=('style.css','app.js','sync.js','market.js','stock-detail.js','multiscreener.js')
    (tmp_path/'index.html').write_text('<link href="/static/style.css">'+''.join(f'<script src="/static/{n}"></script>' for n in names[1:]),encoding='utf8')
    for name in names:(tmp_path/name).write_bytes(b'initial')
    client=TestClient(api.app)
    first=client.get('/')
    version=sha256(b'initial').hexdigest()[:12]
    assert first.headers['cache-control']=='no-cache'
    assert first.headers['content-type'].startswith('text/html')
    assert f'/static/multiscreener.js?v={version}' in first.text
    assert f'/static/stock-detail.js?v={version}' in first.text
    (tmp_path/'multiscreener.js').write_bytes(b'updated filter buttons')
    second=client.get('/')
    updated=sha256(b'updated filter buttons').hexdigest()[:12]
    assert f'/static/multiscreener.js?v={updated}' in second.text
    assert f'/static/style.css?v={version}' in second.text
    assert f'/static/multiscreener.js?v={version}' not in second.text
    (tmp_path/'stock-detail.js').write_bytes(b'updated company detail')
    third=client.get('/')
    assert f'/static/stock-detail.js?v={sha256(b"updated company detail").hexdigest()[:12]}' in third.text
