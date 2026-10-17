{
 let currentSymbol=null,detailGeneration=0,job=null,jobTimer=null,detailTimer=null;
 const labels={pending:'待解析',ready:'问答已入库',no_qa:'未提取到问答，可查看全文',needs_ocr:'扫描件，待OCR',failed:'解析失败，原文已保留'};
 const states={queued:'等待开始',running:'采集中',paused:'已暂停',completed:'已完成',partial:'已完成，有失败项',failed:'未能启动'};
 const dateLabel=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—';
 async function loadResearch(symbol,token=detailGeneration){
  const data=await api('/research/'+encodeURIComponent(symbol));if(symbol!==currentSymbol||token!==detailGeneration)return;
  const c=data.collection,docs=data.documents;
  $('#stock-research-info').textContent=`公告范围 ${data.start_date} 至 ${data.end_date} · ${docs.length}份`;
  $('#stock-research-records').innerHTML=docs.length?docs.map((d,index)=>{
   const ex=d.extraction||{},pairs=ex.qa_pairs||[];
   return `<details class="research-record" ${index===0?'open':''}><summary class="research-record-head"><strong>${esc(d.title)}</strong><span class="hint">${pairs.length}组问答 · ${esc(labels[d.status]||'待下载')}</span></summary>
    <p class="hint">公告 ${esc(dateLabel(d.published_at))}${d.parsed_at?' · 解析 '+esc(dateLabel(d.parsed_at)):''}</p>
    ${ex.topics?.length?'<div class="research-topics">'+ex.topics.map(t=>`<span>${esc(t)}</span>`).join('')+'</div>':''}
    ${ex.participants?.length?`<details><summary>文中机构 ${ex.participants.length}家</summary><p>${esc(ex.participants.join('、'))}</p></details>`:''}
    ${pairs.map((p,i)=>`<details class="research-qa"><summary><span class="research-q-number">${i+1}</span>${esc(p.question)}</summary><div class="document-body">${esc(p.answer)}</div><p class="hint">原文所在分段：第${esc((p.pages||[]).join('、'))}页 · ${esc(d.model||'')}</p></details>`).join('')}
    ${d.error?`<p class="hint">${esc(d.error)}</p>`:''}
    <div class="actions"><a href="${esc(d.source_url)}" target="_blank" rel="noopener noreferrer">公告来源</a>${d.revision_id?`<a href="/api/research-original/${d.revision_id}/pdf" target="_blank" rel="noopener noreferrer">原始PDF</a><button type="button" data-research-original="${d.revision_id}">查看全文</button>`:''}</div><div data-research-text="${d.revision_id}" hidden></div></details>`;
  }).join(''):c?.status==='no_records'?'<p class="hint">已核对，此范围内来源没有公开调研记录。</p>':c?.status==='failed'?`<p class="hint">采集失败：${esc(c.error)}。可在资料库重试。</p>`:c&&['pending','running'].includes(c.status)?'<p class="hint">正在采集，入库后自动显示。</p>':'<p class="hint">尚未采集。可在股票资料库启动调研采集。</p>';
  document.querySelectorAll('[data-research-original]').forEach(button=>button.onclick=async()=>{
   const target=$(`[data-research-text="${button.dataset.researchOriginal}"]`);
   if(!target.hidden){target.hidden=true;button.textContent='查看全文';return}
   try{const text=await api('/research-original/'+button.dataset.researchOriginal+'/text');if(token!==detailGeneration)return;
    target.innerHTML=text.pages.map(p=>`<h4>第${p.page}页</h4><div class="document-body">${esc(p.text||'此页无可提取文字')}</div>`).join('');target.hidden=false;button.textContent='收起全文';
   }catch(e){message(e.message)}
  });
  clearTimeout(detailTimer);
  if(c&&['pending','running'].includes(c.status))detailTimer=setTimeout(()=>loadResearch(symbol,token).catch(()=>{}),10000);
 }
 async function refreshJob(){
  const data=await api('/research-jobs');job=data.job;const s=data.summary,c=job?.counts||{},active=job&&['queued','running'].includes(job.status);
  $('#research-summary').textContent=`近一年 ${s.documents}份 · 覆盖 ${s.stocks}只股票 · 问答已入库 ${s.ready}份 · 无问答 ${s.no_qa}份 · 待OCR ${s.needs_ocr}份 · 解析失败 ${s.failed}份`;
  $('#research-job').textContent=job?`${states[job.status]} · 已核对 ${job.processed}/${job.total}只 · 有记录 ${c.saved||0}只 · 无记录 ${c.no_records||0}只 · 失败 ${c.failed||0}只${job.current?.length?' · '+job.current.join('、'):''}${job.message?'｜'+job.message:''}`:'尚未启动调研采集';
  $('#research-start').disabled=!!active;$('#research-pause').hidden=!active;$('#research-pause').disabled=!!job?.pause_requested;$('#research-resume').hidden=job?.status!=='paused';$('#research-retry').disabled=!!active;
  $('#research-errors').hidden=!job?.errors?.length;
  $('#research-errors').innerHTML=job?.errors?.length?'<summary>采集与解析失败</summary>'+table(['证券','名称','问题'],job.errors.map(e=>[e.symbol,e.name,e.error])):'';
  clearTimeout(jobTimer);if(active)jobTimer=setTimeout(()=>refreshJob().catch(()=>{jobTimer=setTimeout(()=>refreshJob().catch(e=>message(e.message)),10000)}),5000);
 }
 async function action(path,body={}){try{await api(path,body);await refreshJob()}catch(e){message(e.message)}}
 $('#research-start').onclick=()=>action('/research-jobs',{mode:'all'});
 $('#research-pause').onclick=()=>job&&action('/research-jobs/'+job.id+'/pause');
 $('#research-resume').onclick=()=>job&&action('/research-jobs/'+job.id+'/resume');
 $('#research-retry').onclick=()=>action('/research-jobs',{mode:'retry'});
 $('#research-refresh').onclick=()=>refreshJob().catch(e=>message(e.message));
 $('#stock-research-refresh').onclick=()=>currentSymbol&&loadResearch(currentSymbol).catch(e=>message(e.message));
 $('#stock-research-jump').onclick=()=>$('#stock-research').scrollIntoView({block:'start',behavior:'smooth'});
 document.addEventListener('quant:stock-detail',event=>{
  currentSymbol=event.detail.symbol;const token=++detailGeneration;clearTimeout(detailTimer);$('#stock-research-records').textContent='读取调研记录…';
  loadResearch(currentSymbol,token).catch(e=>{if(token===detailGeneration)$('#stock-research-records').textContent=e.message});
 });
 document.querySelector('[data-view="library"]').addEventListener('click',()=>refreshJob().catch(e=>message(e.message)));
}
