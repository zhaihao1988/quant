{
  let offset=0,poll=null,scanning=false,last=null;
  const labels={aligned:'已对齐',new:'待补新数据',changed:'需校验内容',missing:'缺少源文件',busy:'正在写入 / 暂不可读',older:'本地较旧，已保护'};
  async function compare(){
    const params=new URLSearchParams({state:$('#source-state').value,q:$('#source-query').value,offset,limit:30});
    const data=await api('/sync/status?'+params);last=data;
    $('#source-summary').textContent=`数据库最新 ${data.dates.db_date||'—'} · 通达信最新 ${data.dates.local_date||'—'} · 待补 ${data.counts.new||0} · 待校验 ${data.counts.changed||0} · 已对齐 ${data.counts.aligned||0} · 缺文件 ${data.counts.missing||0}`;
    $('#source-note').textContent=data.scope+' '+data.auxiliary_sources.map(s=>`${s.name}：${s.updated_at||'缺失'}`).join('；');
    $('#source-results').innerHTML=table(['证券','名称','数据库日期','通达信日期','本地记录','状态','说明'],data.items.map(r=>[r.symbol,r.name,r.db_date||'—',r.local_date||'—',num(r.local_records,0),labels[r.state]||r.state,r.reason]));
    $('#source-page').textContent=`${data.total?offset+1:0}—${Math.min(offset+30,data.total)} / ${data.total}`;
    $('#source-prev').disabled=offset===0;$('#source-next').disabled=offset+30>=data.total;
    const running=data.scan?.status==='running';
    $('#scan-sources').disabled=running||scanning;$('#scan-sources').textContent=running?'正在对比…':'重新对比源文件';
    $('#import-button').disabled=Boolean(data.active);$('#import-button').textContent=data.active?'同步运行中…':'同步通达信数据 ↗';
    if(data.scan?.status==='failed')$('#source-note').textContent+=' 对比失败：'+data.scan.error;
    return running||Boolean(data.active);
  }
  async function loadStatus(){
    if(poll)clearTimeout(poll);
    try{
      const s=await api('/status');$('#connection').textContent='数据库已连接';
      $('#stats').innerHTML=[['本地证券',num(s.securities,0),'沪 · 深 · 北 A 股'],['日线记录',num(s.daily_records,0),'保留不复权原始行情'],['盘后选股基准日',s.latest_trade_date||'待导入',`文件最大日期 ${s.latest_file_date||'—'}`],['权息事件',num(s.capital_events,0),'分红 · 送转 · 股本变动']].map(([l,v,n])=>`<div class="stat"><label>${esc(l)}</label><strong>${esc(v)}</strong><small>${esc(n)}</small></div>`).join('');
      $('#imports').innerHTML=s.imports.length?`<table><thead><tr>${['开始时间','状态 / 模式','阶段','文件进度','新增修订 / 解析行','问题与重试'].map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${s.imports.map(r=>`<tr><td>${esc(new Date(r.started_at).toLocaleString())}</td><td>${esc(statusName[r.status]||r.status)}<br>${r.mode==='verify'?'完整文件校验':'增量同步'}</td><td>${esc(r.stage)}</td><td>${r.processed} / ${r.total}<br>跳过 ${r.skipped}</td><td>${num(r.changed_rows,0)} / ${num(r.parsed_rows,0)}</td><td>${r.errors.length?`<details><summary>${r.errors.length}项问题</summary><pre>${esc(r.errors.join('\n'))}</pre></details>`:'—'}${['partial','failed','interrupted'].includes(r.status)?`<button data-retry="${esc(r.id)}">重试失败项</button>`:''}</td></tr>`).join('')}</tbody></table>`:'<div class="empty">暂无同步任务</div>';
      const busy=await compare();if(busy)poll=setTimeout(loadStatus,2500);
    }catch(error){$('#connection').textContent='连接失败';message(error.message)}
  }
  window.loadStatus=loadStatus;$('#refresh').onclick=loadStatus;
  $('#source-state').onchange=()=>{offset=0;compare().catch(e=>message(e.message))};
  $('#source-search').onsubmit=e=>{e.preventDefault();offset=0;compare().catch(e=>message(e.message))};
  $('#source-prev').onclick=()=>{offset=Math.max(0,offset-30);compare().catch(e=>message(e.message))};
  $('#source-next').onclick=()=>{offset+=30;compare().catch(e=>message(e.message))};
  $('#scan-sources').onclick=async()=>{
    scanning=true;$('#scan-sources').disabled=true;
    try{await api('/sync/scan',{});poll=setTimeout(loadStatus,1000)}catch(e){message(e.message)}finally{scanning=false}
  };
  $('#import-button').onclick=async()=>{
    $('#import-button').disabled=true;
    try{const r=await api('/imports',{mode:$('#sync-mode').value});message(r.reused?'已有同步任务运行，已显示其进度。':'A股同步已提交。扩展行情本轮不处理。');await loadStatus()}
    catch(e){message(e.message);$('#import-button').disabled=false}
  };
  $('#imports').addEventListener('click',async event=>{
    const b=event.target.closest('[data-retry]');if(!b)return;b.disabled=true;
    try{await api('/imports/'+b.dataset.retry+'/retry',{});message('失败项重试已提交。');await loadStatus()}catch(e){message(e.message);b.disabled=false}
  });
  loadStatus().then(()=>{if(last?.stale&&last.scan?.status!=='running')$('#scan-sources').click()});
}
