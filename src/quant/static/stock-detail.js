{
 let symbol=null,generation=0,documents=[],textKind='discussion',period='',poll=null,refreshState=null;
 const metricColumns=[['market_cap','总市值','amount'],['pe','PE（年报）'],['pb','PB'],['peg_like_ratio','PEG类指标'],
  ['revenue_total','营业总收入','amount'],['net_profit_parent','归母净利润','amount'],['equity_parent','归母净资产','amount'],
  ['revenue_growth_yoy','营收同比','ratio'],['profit_growth_yoy','净利同比','ratio'],['turnover_pct','参考换手率','pct'],
  ['total_shares','总股本','shares'],['float_shares','流通A股','shares']];
 function amount(value){return Math.abs(value)>=1e8?num(value/1e8)+'亿':Math.abs(value)>=1e4?num(value/1e4)+'万':num(value)}
 function format(value,kind){return kind==='amount'?amount(value):kind==='shares'?amount(value)+'股':kind==='ratio'?num(value*100)+'%':kind==='pct'?num(value)+'%':num(value)}
 function when(value){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'}
 function renderFinance(data){
  const f=data.fundamentals;
  $('#stock-finance-info').textContent=`${data.name} ${data.symbol} · 财报 ${f.financial_report_date||'—'} · 价格 ${f.price_date||'—'}`;
  $('#stock-finance-metrics').innerHTML='<dl class="stock-metrics">'+metricColumns.map(([key,label,kind])=>{
   const infinite=f[key]==='inf'||f[key]==='-inf';
   const available=f[key]!==null&&f[key]!==undefined&&Number.isFinite(Number(f[key]));
   return `<div><dt>${esc(label)}</dt><dd class="${available||infinite?'':'missing'}">${esc(infinite?(f[key]==='inf'?'∞':'−∞'):available?format(Number(f[key]),kind):f.fundamental_labels?.[key]||'未接入')}</dd></div>`;
  }).join('')+`</dl><details class="stock-finance-basis"><summary>指标口径</summary><p class="hint">财务参考日 ${esc(f.financial_reference_date)} · 最新已采集财务<br>PE按最近年报计算；市值使用不复权收盘价。<br>营收、利润为年初累计；同比对比上年同期。<br>归母净资产取最新财报，总股本取最新已采集股本。</p></details>`;
  const rows=data.financials.reports,seen=new Set(),unique=rows.filter(r=>!seen.has(r.report_date)&&seen.add(r.report_date));
  const value=(r,key)=>r.review_state==='verified'?r.standard[key]?.value:null;
  $('#stock-financial-history').innerHTML=table(['报告期','营业总收入','归母净利润','归母净资产','经营现金流','状态'],unique.map(r=>[
   r.report_date,...['revenue_total','net_profit_parent','equity_parent','cashflow_operating'].map(k=>value(r,k)==null?'—':amount(value(r,k))),r.review_state==='verified'?'已核验':'待核验，不用于指标']));
 }
 function renderText(){
  document.querySelectorAll('[data-stock-text]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.stockText===textKind)));
  const candidates=documents.filter(d=>d.kind===textKind).sort((a,b)=>(b.report_date||'').localeCompare(a.report_date||''));
  if(textKind==='discussion'){
   if(!candidates.some(d=>d.report_date===period))period=candidates[0]?.report_date||'';
   $('#stock-text-period').innerHTML=candidates.map(d=>`<option value="${esc(d.report_date)}" ${d.report_date===period?'selected':''}>${esc(d.report_date)}</option>`).join('');
  }
  $('#stock-text-period-label').hidden=textKind!=='discussion'||!candidates.length;
  const doc=textKind==='discussion'?candidates.find(d=>d.report_date===period):candidates[0];
  $('#stock-text-source').innerHTML=doc?`来源：<a href="${esc(doc.source_url)}" target="_blank" rel="noopener noreferrer">同花顺F10</a> · 采集 ${esc(when(doc.collected_at))}${doc.is_excerpt?' · 网页仅提供摘要':''}`:'';
  if(!doc){$('#stock-text-content').textContent=refreshState&&['queued','running'].includes(refreshState.status)?'正在获取资料，行情与财务可继续查看。':'本地暂无此项资料，可点击“更新文字资料”。';return}
  if(textKind==='executives'){
   $('#stock-text-content').innerHTML=(doc.details.people||[]).map(p=>`<details><summary>${esc(p.name)} · ${esc(p.role)}</summary><p class="hint">${esc(p.dates.join(' · '))}</p><p>${esc(p.biography)}</p></details>`).join('');
  }else if(textKind==='business'||textKind==='company'){
   const order=['公司名称','所属地域','所属申万行业','主营业务','产品类型','产品名称','经营范围','董事长','总裁','控股股东','实际控制人','最终控制人','公司简介'];
   const rank=k=>order.includes(k)?order.indexOf(k):order.length;
   $('#stock-text-content').innerHTML='<dl class="company-facts">'+Object.entries(doc.details).sort(([a],[b])=>rank(a)-rank(b)).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')+'</dl>';
  }else $('#stock-text-content').innerHTML=`<div class="document-body" tabindex="0" aria-label="经营评述正文">${esc(doc.content)}</div>`;
 }
 async function loadText(current,auto=true,token=generation){
  const incoming=await api('/company-documents/'+encodeURIComponent(current));if(current!==symbol||token!==generation)return;
  documents=incoming.documents;refreshState=incoming.refresh;
  const active=refreshState&&['queued','running'].includes(refreshState.status);
  $('#stock-text-refresh').disabled=!!active;
  $('#stock-text-status').textContent=active?(refreshState.status==='queued'?'资料等待更新…':'正在更新同花顺资料…'):refreshState?.error?`${refreshState.error}；已有资料保留。`:refreshState?.finished_at?`最近核对 ${when(refreshState.finished_at)}`:'首次查看时自动获取并保存。';
  renderText();clearTimeout(poll);
  if(active)poll=setTimeout(()=>loadText(current,false,token).catch(e=>textError(current,e,token)),1500);
  else if(auto&&incoming.auto_refresh_due)await refreshText(current,false,token);
 }
 function textError(current,error,token=generation){if(current===symbol&&token===generation){$('#stock-text-status').textContent=error.message;$('#stock-text-refresh').disabled=false}}
 async function refreshText(current,force,token=generation){
  $('#stock-text-refresh').disabled=true;
  try{await api('/company-documents/'+encodeURIComponent(current)+'/refresh',{force});if(current===symbol&&token===generation)await loadText(current,false,token)}
  catch(e){textError(current,e,token)}
 }
 document.addEventListener('quant:stock-detail',async event=>{
  const current=event.detail.symbol;if(current===symbol)return;symbol=current;const token=++generation;clearTimeout(poll);
  documents=[];refreshState=null;period='';textKind='discussion';
  $('#stock-finance-info').textContent=current;$('#stock-finance-metrics').textContent='财务加载中…';$('#stock-financial-history').textContent='财务加载中…';
  $('#stock-text-status').textContent='读取本地资料…';$('#stock-text-refresh').disabled=true;renderText();
  api('/stock-detail/'+encodeURIComponent(current)).then(data=>{if(token===generation)renderFinance(data)}).catch(error=>{if(token===generation){$('#stock-finance-metrics').textContent=error.message;$('#stock-financial-history').textContent='财务暂不可用'}});
  loadText(current,true,token).catch(e=>textError(current,e,token));
 });
 $('#stock-text-refresh').onclick=()=>symbol&&refreshText(symbol,true);
 document.querySelectorAll('[data-stock-text]').forEach(b=>b.onclick=()=>{textKind=b.dataset.stockText;renderText()});
 $('#stock-text-period').onchange=()=>{period=$('#stock-text-period').value;renderText()};
 $('#stock-text-jump').onclick=()=>$('.stock-text-section').scrollIntoView({block:'start',behavior:'smooth'});
}
