{
 let offset=0,total=0,job=null,timer=null,queryId=0,lastRows=0,pollError=false;
 const names={pending:'待采集',stored:'已入库',failed:'失败待重试',no_content:'来源暂无全文',updating:'采集中'};
 const states={queued:'等待开始',running:'采集中',paused:'已暂停',completed:'已完成',partial:'已完成，有失败项',failed:'未能启动'};
 function renderJob(){
  const active=job&&['queued','running'].includes(job.status),c=job?.counts||{};
  $('#library-start').disabled=!!active;$('#library-pause').hidden=!active;$('#library-pause').disabled=!!job?.pause_requested;
  $('#library-resume').hidden=job?.status!=='paused';$('#library-retry').disabled=!!active;
  $('#library-job').textContent=job?`${states[job.status]} · ${job.processed}/${job.total} · 新入库 ${c.saved||0} · 复用 ${c.cached||0} · 无全文 ${c.no_content||0} · 失败 ${c.failed||0}${job.current?.length?' · '+job.current.map(s=>s.symbol).join('、'):''}${job.message?'｜'+job.message:''}`:'尚未启动全量采集';
  $('#library-errors').innerHTML=job?.errors?.length?`<summary>失败及来源无全文（前50只）</summary>`+table(['股票','名称','状态','原因'],job.errors.map(e=>[e.symbol,e.name,e.status==='failed'?'失败':'暂无全文',e.error||'—'])):'';
  $('#library-errors').hidden=!job?.errors?.length;
  clearTimeout(timer);
  if(active)timer=setTimeout(()=>watch().catch(retryWatch),3000);
 }
 function retryWatch(error){
  pollError=true;message(error.message);clearTimeout(timer);
  if(job&&['queued','running'].includes(job.status))timer=setTimeout(()=>watch().catch(retryWatch),5000);
 }
 async function watch(){
  job=await api('/stock-library/job');if(pollError){pollError=false;message('')}renderJob();
  if($('#library').classList.contains('active')&&Date.now()-lastRows>15000)await load(false);
 }
 async function load(auto=true){
  const token=++queryId,params=new URLSearchParams({q:$('#library-query').value.trim(),state:$('#library-state').value,industry:$('#library-industry').value,offset,limit:50});
  const data=await api('/stock-library?'+params);if(token!==queryId)return;
  total=data.total;job=data.job;lastRows=Date.now();
  if(offset>=total&&offset>0){offset=Math.max(0,Math.floor((total-1)/50)*50);return load(false)}
  const selected=$('#library-industry').value;
  $('#library-industry').innerHTML='<option value="">全部行业</option>'+data.industries.map(v=>`<option value="${esc(v)}" ${selected===v?'selected':''}>${esc(v)}</option>`).join('');
  const s=data.summary;
  $('#library-summary').textContent=`本地A股 ${s.total}只 · 经营评述已入库 ${s.stored}只 / ${s.reports||0}期 · 待采集或核对 ${s.due}只 · 失败 ${s.failed}只 · 来源无全文 ${s.no_content}只`;
  $('#library-results').innerHTML=table(['证券','名称','资料状态','经营评述','最新报告期','行业','最近采集','最近核对来源'],data.stocks.map(r=>[r.symbol,r.name,names[r.state],r.report_count+'期',r.report_date||'—',r.industry||'—',r.collected_at?new Date(r.collected_at).toLocaleString('zh-CN'):'—',r.checked_at?new Date(r.checked_at).toLocaleString('zh-CN'):'—']));
  $('#library-results').querySelectorAll('tbody tr').forEach((tr,index)=>{
   for(const n of [0,1]){
    const button=document.createElement('button'),cell=tr.cells[n],r=data.stocks[index];button.type='button';button.className='library-stock-link';button.textContent=cell.textContent;button.setAttribute('aria-label',`查看${r.name} ${r.symbol}行情与资料`);
    button.onclick=()=>document.dispatchEvent(new CustomEvent('quant:open-market',{detail:{symbol:r.symbol,source:'library'}}));cell.replaceChildren(button);
   }
  });
  $('#library-page').textContent=`${total?offset+1:0}—${Math.min(offset+50,total)} / ${total}`;$('#library-prev').disabled=offset===0;$('#library-next').disabled=offset+50>=total;
  renderJob();
  // Check new stocks and overdue documents when entering, at most once per day.
  if(auto&&s.due>0&&(!job||['completed','partial'].includes(job.status)&&job.finished_at&&Date.now()-new Date(job.finished_at)>86400000))await start('due');
 }
 async function start(mode){
  $('#library-start').disabled=true;
  try{await api('/stock-library/jobs',{mode});await load(false)}catch(e){message(e.message);$('#library-start').disabled=false}
 }
 $('#library-form').onsubmit=e=>{e.preventDefault();offset=0;load(false).catch(e=>message(e.message))};
 $('#library-prev').onclick=()=>{offset=Math.max(0,offset-50);load(false).catch(e=>message(e.message))};$('#library-next').onclick=()=>{offset+=50;load(false).catch(e=>message(e.message))};
 $('#library-start').onclick=()=>start('due');$('#library-retry').onclick=()=>start('retry');$('#library-refresh').onclick=()=>load(false).catch(e=>message(e.message));
 $('#library-pause').onclick=async()=>{try{await api('/stock-library/jobs/'+job.id+'/pause',{});await watch()}catch(e){message(e.message)}};
 $('#library-resume').onclick=async()=>{try{await api('/stock-library/jobs/'+job.id+'/resume',{});await load(false)}catch(e){message(e.message)}};
 document.querySelector('[data-view="library"]').addEventListener('click',()=>load().catch(e=>message(e.message)));
 if(new URLSearchParams(location.search).get('view')==='library')document.addEventListener('DOMContentLoaded',()=>document.querySelector('[data-view="library"]').click());
}
