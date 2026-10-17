let multiTask=null,multiTimer=null,multiCatalog=null,multiStarting=false,multiLatestDate=null;
let multiPreparing=false,marketPrepareTimer=null,marketPrepareTask=null,marketPrepareWasActive=false;
let multiSignals=[],multiSort={key:null,ascending:true};
let multiFilters={},multiFilterOpen=null,multiTableObserver=null;
const multiColumns=[['symbol','证券'],['name','名称'],['signal_date','信号日期'],['strategy_label','策略'],['signal_level','级别'],['price_date','价格日期'],['financial_report_date','财报期'],['revenue_total','营业总收入'],['net_profit_parent','归母净利润'],['equity_parent','归母净资产'],['pe','PE'],['pb','PB'],['revenue_growth_yoy','营收同比（比率）'],['profit_growth_yoy','净利同比（比率）'],['peg_like_ratio','PEG类指标'],['net_profit_positive_3y_latest','连续3年及最新净利为正'],['growth_positive','增长为正'],['pe_lt_30','PE<30'],['peg_like_lt_1','PEG<1'],['total_shares','总股本（股）'],['float_shares','流通A股（股）'],['market_cap_formatted','总市值'],['float_market_cap','流通A股市值'],['turnover_pct','参考换手率（%）']];
const multiBoolean=new Set(['net_profit_positive_3y_latest','growth_positive','pe_lt_30','peg_like_lt_1']);
const multiMissing=v=>v===null||v===undefined||v===''||(typeof v==='number'&&Number.isNaN(v));
const multiNumeric=new Set(['market_cap_formatted','pe','pb','revenue_growth_yoy','profit_growth_yoy','peg_like_ratio','total_shares','float_shares','float_market_cap','turnover_pct','revenue_total','net_profit_parent','equity_parent']);
const multiMoney=new Set(['market_cap_formatted','float_market_cap','revenue_total','net_profit_parent','equity_parent']);
function multiToken(row,key){return multiMissing(row[key])?'missing:'+multiCell(row,key):JSON.stringify([typeof row[key],row[key]])}
function multiMatches(row,key,filter){
 if(filter.values&&!filter.values.has(multiToken(row,key)))return false;
 if(filter.min!==null||filter.max!==null){
  const value=row[key];
  if(typeof value!=='number'||!Number.isFinite(value))return false;
  if(filter.min!==null&&value<filter.min||filter.max!==null&&value>filter.max)return false;
 }
 return true;
}
function closeMultiFilter(focus=false){
 const key=multiFilterOpen?.key;
 document.querySelector('#multi-filter-menu')?.remove();multiFilterOpen=null;
 const button=key&&document.querySelector(`[data-multi-filter="${key}"]`);
 if(button){button.setAttribute('aria-expanded','false');if(focus)button.focus()}
}
function positionMultiFilter(){
 const menu=$('#multi-filter-menu'),button=multiFilterOpen&&document.querySelector(`[data-multi-filter="${multiFilterOpen.key}"]`);
 if(!menu||!button)return;
 const rect=button.getBoundingClientRect();
 menu.style.left=Math.max(8,Math.min(rect.right-menu.offsetWidth,window.innerWidth-menu.offsetWidth-8))+'px';
 const below=window.innerHeight-rect.bottom-8;
 menu.style.top=Math.max(8,below>=menu.offsetHeight?rect.bottom+4:rect.top-menu.offsetHeight-4)+'px';
}
function openMultiFilter(key){
 if(multiFilterOpen?.key===key){closeMultiFilter(true);return}
 closeMultiFilter();
 const label=multiColumns.find(c=>c[0]===key)[1],filter=multiFilters[key];
 // Other columns narrow the candidate list; retain this column's selected values so they can be cleared.
 const options=new Map();
 for(const row of multiSignals){
  const token=multiToken(row,key);
  if(Object.entries(multiFilters).every(([other,f])=>other===key||multiMatches(row,other,f))||filter?.values?.has(token)){
   options.set(token,{token,label:multiCell(row,key),value:row[key]});
  }
 }
 const choices=[...options.values()].sort((a,b)=>typeof a.value==='number'&&typeof b.value==='number'?a.value-b.value:a.label.localeCompare(b.label,'zh-CN',{numeric:true}));
 const selected=new Set(filter?.values||choices.map(c=>c.token));
 multiFilterOpen={key};
 const menu=document.createElement('div');menu.id='multi-filter-menu';menu.className='multi-filter-menu';menu.setAttribute('role','dialog');menu.setAttribute('aria-label',label+'筛选');
 menu.innerHTML=`<strong>${esc(label)}筛选</strong><div class="multi-filter-sort"><button type="button" data-filter-sort="asc">升序 ↑</button><button type="button" data-filter-sort="desc">降序 ↓</button></div><input type="search" data-filter-search aria-label="搜索筛选值" placeholder="搜索值…">`+
  (multiNumeric.has(key)?`<div class="multi-filter-range"><label>最小值<input type="number" step="any" data-filter-min value="${esc(filter?.min??'')}"></label><label>最大值<input type="number" step="any" data-filter-max value="${esc(filter?.max??'')}"></label></div><small>${multiMoney.has(key)?'金额单位：元':key.includes('growth')?'同比为比率，0.10代表10%':key==='turnover_pct'?'百分数，1代表1%':key.endsWith('shares')?'股本单位：股':'区间包含边界值'}</small>`:'')+
  '<div class="multi-filter-select"><button type="button" data-filter-all>全选搜索结果</button><button type="button" data-filter-none>取消搜索结果</button></div><div class="multi-filter-values"></div><p class="multi-filter-error" role="alert"></p><div class="multi-filter-actions"><button type="button" data-filter-clear>清除此列</button><button type="button" data-filter-cancel>取消</button><button type="button" class="primary" data-filter-apply>确定</button></div>';
 document.body.append(menu);
 const visibleChoices=()=>{const term=menu.querySelector('[data-filter-search]').value.trim().toLocaleLowerCase();return choices.filter(c=>c.label.toLocaleLowerCase().includes(term))};
 function renderChoices(){
  const visible=visibleChoices();
  menu.querySelector('.multi-filter-values').innerHTML=visible.length?visible.map(c=>`<label class="check"><input type="checkbox" value="${esc(c.token)}" ${selected.has(c.token)?'checked':''}><span>${esc(c.label)}</span></label>`).join(''):'<p class="hint">无匹配值</p>';
  menu.querySelectorAll('input[type=checkbox]').forEach(input=>input.onchange=()=>{if(input.checked)selected.add(input.value);else selected.delete(input.value)});
 }
 menu.querySelector('[data-filter-search]').oninput=renderChoices;
 menu.querySelector('[data-filter-all]').onclick=()=>{visibleChoices().forEach(c=>selected.add(c.token));renderChoices()};
 menu.querySelector('[data-filter-none]').onclick=()=>{visibleChoices().forEach(c=>selected.delete(c.token));renderChoices()};
 menu.querySelector('[data-filter-cancel]').onclick=()=>closeMultiFilter(true);
 menu.querySelector('[data-filter-clear]').onclick=()=>{delete multiFilters[key];closeMultiFilter();renderMultiSignals()};
 menu.querySelectorAll('[data-filter-sort]').forEach(button=>button.onclick=()=>{multiSort={key,ascending:button.dataset.filterSort==='asc'};closeMultiFilter();renderMultiSignals()});
 menu.querySelector('[data-filter-apply]').onclick=()=>{
  const low=menu.querySelector('[data-filter-min]'),high=menu.querySelector('[data-filter-max]');
  if(low&&!low.validity.valid||high&&!high.validity.valid){menu.querySelector('.multi-filter-error').textContent='请输入有效数值';return}
  const min=low?.value?Number(low.value):null,max=high?.value?Number(high.value):null;
  if(min!==null&&max!==null&&min>max){menu.querySelector('.multi-filter-error').textContent='最小值不能大于最大值';return}
  const applied=menu.querySelector('[data-filter-search]').value.trim()?new Set(visibleChoices().filter(c=>selected.has(c.token)).map(c=>c.token)):new Set(selected);
  const values=choices.every(c=>applied.has(c.token))&&applied.size===choices.length?null:applied;
  if(values===null&&min===null&&max===null)delete multiFilters[key];else multiFilters[key]={values,min,max};
  closeMultiFilter();renderMultiSignals();
 };
 renderChoices();positionMultiFilter();
 document.querySelector(`[data-multi-filter="${key}"]`).setAttribute('aria-expanded','true');menu.querySelector('[data-filter-search]').focus();
}
document.addEventListener('pointerdown',event=>{if(multiFilterOpen&&!event.target.closest('#multi-filter-menu,[data-multi-filter]'))closeMultiFilter()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&multiFilterOpen){event.preventDefault();closeMultiFilter(true)}});
window.addEventListener('resize',positionMultiFilter);
document.addEventListener('scroll',event=>{if(multiFilterOpen&&!$('#multi-filter-menu')?.contains(event.target))positionMultiFilter()},true);
function multiCell(row,key){
 const value=row[key];
 if(multiMissing(value))return ['symbol','name','signal_date','strategy_label','signal_level','price_date','financial_report_date'].includes(key)?'—':(row.fundamental_labels?.[key]||'未接入');
 if(multiBoolean.has(key))return value===true?'是':value===false?'否':String(value);
 if(multiMoney.has(key)&&typeof value==='number')return Math.abs(value)>=1e8?num(value/1e8)+'亿':Math.abs(value)>=1e4?num(value/1e4)+'万':num(value);
 return typeof value==='number'?num(value):String(value);
}
function renderMultiSignals(){
 const previousScroll=$('#multi-results .table-wrap')?.scrollLeft||0;
 multiTableObserver?.disconnect();
 const rows=multiSignals.filter(row=>Object.entries(multiFilters).every(([key,f])=>multiMatches(row,key,f)));
 if(multiSort.key)rows.sort((a,b)=>{
  const x=a[multiSort.key],y=b[multiSort.key];
  if(multiMissing(x)||multiMissing(y))return multiMissing(x)?(multiMissing(y)?0:1):-1;
  const comparison=typeof x==='number'&&typeof y==='number'?x-y:typeof x==='boolean'&&typeof y==='boolean'?Number(x)-Number(y):String(x).localeCompare(String(y),'zh-CN',{numeric:true});
  return multiSort.ascending?comparison:-comparison;
 });
 multiDisplayedRows=rows;
 $('#multi-results').innerHTML=`<div class="multi-filter-summary"><span>显示 ${rows.length} / ${multiSignals.length} 条信号 · 财务参考日 ${esc(multiSignals[0]?.financial_reference_date||'—')} · ${multiView==='archive'?'当次归档':'最新财务'}</span>${Object.keys(multiFilters).length?'<button type="button" id="multi-clear-filters">清除全部筛选</button>':''}</div><div class="table-wrap">`+table(multiColumns.map(c=>c[1]),rows.map(r=>multiColumns.map(c=>multiCell(r,c[0]))))+'</div>'+(rows.length?'':'<p class="hint">没有匹配结果，可清除筛选。</p>');
 $('#multi-results').classList.remove('empty');
 const wrap=$('#multi-results .table-wrap');
 wrap.querySelectorAll('tbody tr').forEach((tr,index)=>{
  for(const column of [0,1]){
   const cell=tr.cells[column],button=document.createElement('button');
   button.type='button';button.className='stock-chart-link';button.textContent=cell.textContent;
   button.setAttribute('aria-label',`查看${rows[index].name} ${rows[index].symbol}行情`);
   button.onclick=()=>{closeMultiFilter();document.dispatchEvent(new CustomEvent('quant:open-market',{detail:{symbol:rows[index].symbol}}))};
   cell.replaceChildren(button);
  }
 });
 const navigation=document.createElement('div');navigation.className='actions';navigation.setAttribute('aria-label','结果表列导航');
 navigation.innerHTML='<span class="hint">左右查看全部指标：</span><button type="button" data-multi-jump="symbol">证券与信号</button><button type="button" data-multi-jump="total_shares">股本与市值</button><button type="button" data-multi-jump="pe">PE / PB / 增长指标 →</button>';
 const scroll=document.createElement('div');scroll.style.cssText='overflow-x:auto;height:20px;margin-bottom:8px;';scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','结果表横向滚动条');
 const track=document.createElement('div');track.style.height='1px';scroll.append(track);
 wrap.before(navigation,scroll);
 const syncSize=()=>{track.style.width=wrap.scrollWidth+'px';scroll.scrollLeft=wrap.scrollLeft};
 scroll.onscroll=()=>{if(wrap.scrollLeft!==scroll.scrollLeft)wrap.scrollLeft=scroll.scrollLeft};
 wrap.addEventListener('scroll',()=>{if(scroll.scrollLeft!==wrap.scrollLeft)scroll.scrollLeft=wrap.scrollLeft},{passive:true});
 navigation.querySelectorAll('[data-multi-jump]').forEach(button=>button.onclick=()=>{
  closeMultiFilter();
  const th=wrap.querySelector(`[data-multi-sort="${button.dataset.multiJump}"]`).closest('th');
  const pinned=[...wrap.querySelectorAll('thead th')].slice(0,2).reduce((n,e)=>n+e.getBoundingClientRect().width,0);
  wrap.scrollLeft=button.dataset.multiJump==='symbol'?0:Math.max(0,wrap.scrollLeft+th.getBoundingClientRect().left-wrap.getBoundingClientRect().left-pinned);
  scroll.scrollLeft=wrap.scrollLeft;
 });
 document.querySelectorAll('#multi-results th').forEach((th,i)=>{
  const [key,label]=multiColumns[i];th.innerHTML=`<div class="multi-column-header"><button type="button" data-multi-sort="${esc(key)}">${esc(label)}${multiSort.key===key?(multiSort.ascending?' ↑':' ↓'):''}</button><button type="button" class="multi-column-filter ${multiFilters[key]?'active':''}" data-multi-filter="${esc(key)}" title="筛选${esc(label)}" aria-label="筛选${esc(label)}" aria-haspopup="dialog" aria-expanded="${multiFilterOpen?.key===key}"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path fill="currentColor" d="${multiFilters[key]?'M1 2h14L10 8v5l-4 2V8z':'M2 5h12L8 12z'}"/></svg></button></div>`;
  th.querySelector('[data-multi-sort]').onclick=()=>{closeMultiFilter();multiSort={key,ascending:multiSort.key===key?!multiSort.ascending:true};renderMultiSignals()};
  th.querySelector('[data-multi-filter]').onclick=()=>openMultiFilter(key);
 });
 $('#multi-clear-filters')?.addEventListener('click',()=>{closeMultiFilter();multiFilters={};renderMultiSignals()});
 wrap.scrollLeft=previousScroll;syncSize();
 multiTableObserver=new ResizeObserver(syncSize);multiTableObserver.observe(wrap);
 positionMultiFilter();
}
const multiState={queued:'等待开始',running:'选股中',completed:'已完成',partial:'已完成，有失败项',cancelled:'已停止',interrupted:'已中断',failed:'失败'};
let multiSnapshot=null,multiActive=false,multiHistoryOffset=0,multiWatchGeneration=0,multiLoadTimer=null;
let multiDisplayedRows=[],multiView='latest',multiPoolNames={};
let multiRunningId=null,multiProgressTimer=null,marketAttachedScreen=null;
let multiExpectedDate=null,multiCalendarNote=null;
const multiPrefsKey='quant.multi.settings.v1';
function applyMultiButtons(){
 const busy=multiActive||multiPreparing||multiStarting||!!multiRunningId;
 $('#multi-submit').disabled=busy;$('#multi-market-update').disabled=busy;$('#multi-force').disabled=busy;
 $('#multi-stop').hidden=!busy;$('#multi-stop').disabled=false;
 const r=multiSnapshot,outdated=r&&((multiLatestDate&&r.effective_date!==multiLatestDate)||(multiExpectedDate&&r.effective_date!==multiExpectedDate));
 $('#multi-resume').hidden=busy||!r||!r.input_signature||!['cancelled','interrupted','failed'].includes(r.status);
 $('#multi-resume').disabled=!!outdated;$('#multi-resume').title=outdated?'数据日期已更新，请开始新的选股':'';
 $('#multi-retry').hidden=busy||!r||!r.input_signature||!['partial','failed'].includes(r.status);
 $('#multi-retry').disabled=!!outdated;
 $('#multi-result-view').disabled=multiActive;
}
function rememberMultiSettings(){
 const data={symbols:$('#multi-symbols').value,pool:$('#multi-pool').value,workers:$('#multi-workers').value,
  strategies:[...document.querySelectorAll('[name="multi-strategy"]:checked')].map(x=>x.value)};
 try{localStorage.setItem(multiPrefsKey,JSON.stringify(data))}catch{}
 const scope=data.symbols.trim()?'指定股票：'+data.symbols.trim():multiPoolNames[data.pool]||'全部A股';
 $('#multi-settings-summary').textContent=`选股设置 · ${scope} · ${data.strategies.length}个策略`;
}
function restoreMultiSettings(){
 try{
  const p=JSON.parse(localStorage.getItem(multiPrefsKey)||'null');if(!p)return;
  $('#multi-symbols').value=typeof p.symbols==='string'?p.symbols:'';
  if([...$('#multi-pool').options].some(o=>o.value===p.pool))$('#multi-pool').value=p.pool;
  if(['1','2','4'].includes(p.workers))$('#multi-workers').value=p.workers;
  if(Array.isArray(p.strategies))document.querySelectorAll('[name="multi-strategy"]').forEach(x=>x.checked=p.strategies.includes(x.value));
 }catch{}
}
async function loadMultiLatestDate(){
 const r=await api('/multi-screens/latest');multiLatestDate=r.effective_date;
 multiExpectedDate=r.expected_date;multiCalendarNote=r.calendar_note;
 $('#multi-date').value=multiLatestDate||'暂无行情';applyMultiButtons();
}
async function loadMultiHistory(){
 const params=new URLSearchParams({limit:20,offset:multiHistoryOffset,day:$('#multi-history-date').value,state:$('#multi-history-state').value,scope:$('#multi-history-scope').value});
 if(!params.get('day'))params.delete('day');
 const data=await api('/multi-screens/history?'+params);
 $('#multi-history').innerHTML=data.items.length?'<table><thead><tr><th>交易日</th><th>版本</th><th>状态</th><th>进度</th><th>信号</th><th>完成时间</th><th></th></tr></thead><tbody>'+data.items.map(r=>`<tr><td>${esc(r.effective_date)}</td><td>V${r.version}</td><td>${esc(multiState[r.status]||r.status)}${r.archive_state==='saved'?' · 已归档':r.archive_state==='legacy'?' · 旧记录':''}</td><td>${r.processed}/${r.total}</td><td>${r.signals}</td><td>${esc(r.finished_at?new Date(r.finished_at).toLocaleString():'—')}</td><td><button type="button" data-multi-id="${r.id}">查看</button></td></tr>`).join('')+'</tbody></table>':'暂无记录';
 document.querySelectorAll('[data-multi-id]').forEach(b=>b.onclick=()=>{multiStarting=false;multiView='latest';$('#multi-result-view').value='latest';watchMulti(b.dataset.multiId)});
 $('#multi-history-page').textContent=`${data.total?multiHistoryOffset+1:0}—${Math.min(multiHistoryOffset+20,data.total)} / ${data.total}`;
 $('#multi-history-prev').disabled=multiHistoryOffset===0;$('#multi-history-next').disabled=multiHistoryOffset+20>=data.total;
 return data.items;
}
async function loadMultiPlans(){
 const previous=$('#multi-history-scope').value,plans=await api('/multi-screens/plans');
 $('#multi-history-scope').innerHTML='<option value="">全部方案</option>'+plans.map(p=>{
  const s=p.parameters,label=(s.symbols.length?'指定'+s.symbols.length+'只':multiPoolNames[s.taxonomy_id]||'全部A股')+' · '+s.strategies.length+'个策略';
  return `<option value="${p.scope_signature}">${esc(label)}</option>`;
 }).join('');
 if(plans.some(p=>p.scope_signature===previous))$('#multi-history-scope').value=previous;
}
async function loadMulti(){
 clearTimeout(multiLoadTimer);
 try{
  if(!multiCatalog){
   const [catalog,pools]=await Promise.all([api('/multi-screens/strategies'),api('/taxonomies')]);multiCatalog=catalog;
   $('#multi-strategies').innerHTML=catalog.strategies.map(s=>`<label class="check"><input type="checkbox" name="multi-strategy" value="${esc(s.id)}" checked> ${esc(s.name)}</label>`).join('');
   $('#multi-parameters').textContent=JSON.stringify(catalog.strategies.map(s=>({name:s.name,parameters:s.parameters})),null,2);
   multiPoolNames=Object.fromEntries(pools.map(p=>[p.id,p.name]));
   $('#multi-pool').innerHTML='<option value="">全部本地 A 股</option>'+pools.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${p.members}只</option>`).join('');
   restoreMultiSettings();rememberMultiSettings();$('#multi-form').addEventListener('change',rememberMultiSettings);
  }
  await loadMultiLatestDate();await loadMultiPlans();const runs=await loadMultiHistory();
  if(!multiTask&&!multiStarting&&runs.length){
   const selected=[...document.querySelectorAll('[name="multi-strategy"]:checked')].map(x=>x.value).sort();
   const input=$('#multi-symbols').value.trim().split(/[,，;；\s]+/).filter(Boolean).map(x=>x.toUpperCase());
   const matching=r=>{
    const p=r.parameters;if(!p)return false;
    const symbols=(p.symbols||[]).map(x=>x.toUpperCase());
    const sameSymbols=input.length===symbols.length&&input.every(x=>symbols.some(s=>s===x||s.split('.')[0]===x));
    return sameSymbols&&(p.taxonomy_id||'')===$('#multi-pool').value&&JSON.stringify([...(p.strategies||[])].sort())===JSON.stringify(selected);
   };
   const candidates=runs.filter(matching);
   const r=runs.find(x=>['queued','running'].includes(x.status))||candidates.find(x=>['completed','partial'].includes(x.status))||candidates[0]||runs.find(x=>['completed','partial'].includes(x.status)&&x.archive_state==='saved')||runs[0];
   await watchMulti(r.id);
  }
  await watchMarketPrepare();
 }catch(e){message('连接中断，正在重连。'+e.message);multiLoadTimer=setTimeout(loadMulti,5000)}
}
async function watchMulti(id,retry=0){
 if(multiTask!==id){closeMultiFilter();multiFilters={};$('#multi-archive-version').value=''}
 multiTask=id;clearTimeout(multiTimer);const generation=++multiWatchGeneration;
 try{
  const revision=multiView==='archive'?$('#multi-archive-version').value:'';
  const r=await api('/multi-screens/'+id+'?view='+multiView+(revision?'&revision='+revision:''));
  if(generation!==multiWatchGeneration||multiTask!==id||multiStarting)return;
  multiSnapshot=r;multiActive=['queued','running'].includes(r.status)||r.archive_state==='pending';const out=r.result||{},signals=out.signals||[],errors=out.errors||[];
  if(multiActive&&multiRunningId!==id){multiRunningId=id;watchRunProgress(id)}
  const saved=!!r.archive_info;$('#multi-result-view option[value="archive"]').disabled=!saved;
  $('#multi-archive-version-label').hidden=multiView!=='archive';
  $('#multi-summary').textContent=`${r.effective_date} · V${r.version||1} · ${multiState[r.status]||r.status} · ${new Set(signals.map(s=>s.symbol)).size}只股票 / ${signals.length}条信号`;
  $('#multi-summary').dataset.taskId=id;$('#multi-summary').dataset.status=r.status;
  const archiveNote=r.archive_state==='pending'?'正在归档':r.archive_state==='saved'?'已归档':r.archive_state==='failed'?'归档失败，可补存':'旧记录未保存当次财务';
  const scope=r.parameters?.symbols?.length?'指定'+r.parameters.symbols.length+'只股票':multiPoolNames[r.parameters?.taxonomy_id]||'全部A股';
  $('#multi-execution').textContent=`${scope} · 已扫描 ${r.processed}/${r.total} · ${archiveNote}`+
   (r.archive_info?.export_error?' · '+r.archive_info.export_error:'')+
   (r.archive_error?' · '+r.archive_error:'')+
   (multiLatestDate&&r.effective_date!==multiLatestDate?` · 这是历史结果，当前数据为${multiLatestDate}`:'');
  const comparison=r.comparison||r.archive_info?.comparison;
  $('#multi-comparison').textContent=comparison?.available?`与${comparison.previous_date}同方案结果相比：新增${comparison.added.length}只，持续${comparison.retained.length}只，消失${comparison.removed.length}只` : '';
  multiSignals=signals;
  if(signals.length)renderMultiSignals();else{$('#multi-results').innerHTML=`<div class="empty">${multiActive?'选股中，结果会陆续显示。':errors.length?'暂无信号，请查看未计算与错误明细。':'当日没有触发信号，结果照常保存。'}</div>`;multiDisplayedRows=[]}
  const diag=r.diagnostics,issueRows=diag?.rows||errors.map(e=>({...e,label:'未分类',action:'查看原始问题'}));
  $('#multi-errors').innerHTML=(diag?`<p class="hint">整股未计算 ${diag.whole_stock_uncomputed}只 · 部分策略异常 ${diag.partial_strategy_error_stocks}只 · 历史不足 ${diag.history_short_stocks}只（正常门槛，不计为程序错误）</p><p class="hint">问题来自所选批次，历史记录保留原始信息。代码修复后，重新开始选股将生成新的结果版本。</p>`:'')+
   (issueRows.length?'<label>问题分类<select id="multi-issue-category"><option value="">全部</option>'+[...new Set(issueRows.map(e=>e.label))].map(label=>`<option>${esc(label)}</option>`).join('')+'</select></label><div id="multi-issue-table"></div>':'无错误');
  const issueTable=()=>{const selected=$('#multi-issue-category')?.value||'';if($('#multi-issue-table'))$('#multi-issue-table').innerHTML=table(['证券','分类','策略','问题','处理方式'],issueRows.filter(e=>!selected||e.label===selected).map(e=>[e.symbol||'任务',e.label,e.strategy_id||'整股数据',e.error,e.action]))};
  $('#multi-issue-category')?.addEventListener('change',issueTable);issueTable();
  $('#multi-coverage').innerHTML=out.coverage?.length?table(['证券','日K','周K','月K','起始日','最新日'],out.coverage.map(c=>[c.symbol,c.daily,c.weekly,c.monthly,c.first_date,c.last_date])):'暂无记录';
  $('#multi-export-all').disabled=multiActive;$('#multi-export-filtered').disabled=multiActive;$('#multi-export-json').disabled=!saved;
  $('#multi-archive-retry').hidden=!(r.archive_state==='failed'||r.archive_info?.export_error);
  applyMultiButtons();
  if(out.fatal_error)message(out.fatal_error);
  if(multiActive)multiTimer=setTimeout(()=>watchMulti(id),5000);
 }catch(e){
  if(generation!==multiWatchGeneration)return;
  if(multiView==='archive'&&e.message.includes('没有当次归档')){multiView='latest';$('#multi-result-view').value='latest';message(e.message);return watchMulti(id)}
  message('连接中断，正在重连；后台任务不受网页断线影响。');multiTimer=setTimeout(()=>watchMulti(id,retry+1),Math.min(10000,2000*(retry+1)));
 }
}
async function submitMulti(force=false){
 multiStarting=true;marketAttachedScreen=null;applyMultiButtons();message('');rememberMultiSettings();clearTimeout(multiTimer);
 try{
  const symbols=[];
  for(const input of $('#multi-symbols').value.split(/[,，;；\s]+/).filter(Boolean)){
   if(/^\d{6}\.(SH|SZ|BJ)$/i.test(input)){symbols.push(input.toUpperCase());continue}
   const found=await api('/securities?q='+encodeURIComponent(input));const exact=found.filter(s=>s.code===input||s.name===input||s.symbol.toUpperCase()===input.toUpperCase());
   if(exact.length!==1)throw Error(`无法确定股票：${input}，请填写代码或完整名称`);symbols.push(exact[0].symbol);
  }
  const strategies=[...document.querySelectorAll('[name="multi-strategy"]:checked')].map(x=>x.value);if(!strategies.length)throw Error('请至少选择一个策略');
  const screen={strategies,symbols:[...new Set(symbols)],workers:Number($('#multi-workers').value)};
  if($('#multi-pool').value)screen.taxonomy_id=$('#multi-pool').value;
  multiView='latest';$('#multi-result-view').value='latest';
  const r=await api('/market-prepare',{screen,force});marketPrepareTask=r.id;marketPrepareWasActive=true;await watchMarketPrepare(r.id);
 }catch(e){multiStarting=false;applyMultiButtons();message(e.message)}
}
async function watchMarketPrepare(id=null){
 clearTimeout(marketPrepareTimer);
 try{
  const job=await api('/market-prepare'+(id?'/'+id:''));
  const freshness=multiExpectedDate&&multiLatestDate!==multiExpectedDate?` · 待更新至${multiExpectedDate}`:multiCalendarNote?' · '+multiCalendarNote:'';
  if(!job){$('#multi-market-status').textContent='开始选股时自动检查并更新行情。'+freshness;return}
  const active=['queued','running'].includes(job.status),p=job.screen_progress||job.import_progress;
  multiPreparing=active;marketPrepareTask=job.id;
  const snapshot=job.result?.after||job.result?.before;
  const idleStage=['completed','partial'].includes(job.status)?(multiExpectedDate&&multiLatestDate!==multiExpectedDate?'行情待更新':'行情已同步'):job.stage;
  $('#multi-market-status').textContent=(active?job.stage:idleStage)+(active&&p?` · ${p.processed}/${p.total}`:'')+
   (job.result?.effective_date?` · 交易日${job.result.effective_date}`:'')+
   (snapshot?.missing_files?` · ${snapshot.missing_files}只缺文件`:'')+
   (snapshot?.not_updated_files?` · ${snapshot.not_updated_files}份未到目标日期`:'')+
   (job.error?' · '+job.error:'')+freshness;
  if(job.screen_id&&(marketPrepareWasActive||active)&&marketAttachedScreen!==job.screen_id){marketAttachedScreen=job.screen_id;multiStarting=false;await loadMultiLatestDate();await watchMulti(job.screen_id)}
  applyMultiButtons();
  if(active){marketPrepareWasActive=true;marketPrepareTimer=setTimeout(()=>watchMarketPrepare(job.id),2500);return}
  if(marketPrepareWasActive){
   if(job.result?.reused_result)message('相同数据与设置已有结果，已直接打开。');
   marketPrepareWasActive=false;multiStarting=false;await loadMultiLatestDate();
   if(job.screen_id&&multiTask===job.screen_id)await watchMulti(job.screen_id);else if(!job.screen_id&&multiTask)await watchMulti(multiTask);
   await loadMultiPlans();await loadMultiHistory();applyMultiButtons();
  }
  if(active&&!job.screen_id&&multiSnapshot)$('#multi-execution').textContent='正在准备新任务，以下为上次查看的结果。';
 }catch(e){message('连接中断，正在重连。');marketPrepareTimer=setTimeout(()=>watchMarketPrepare(id),4000)}
}
$('#multi-form').onsubmit=e=>{e.preventDefault();submitMulti()};
$('#multi-force').onclick=()=>submitMulti(true);
$('#multi-market-update').onclick=async()=>{
 try{multiPreparing=true;applyMultiButtons();const r=await api('/market-prepare',{});marketPrepareTask=r.id;marketPrepareWasActive=true;await watchMarketPrepare(r.id)}
 catch(e){multiPreparing=false;applyMultiButtons();message(e.message)}
};
$('#multi-stop').onclick=async()=>{
 try{if(multiPreparing&&marketPrepareTask)await api('/market-prepare/'+marketPrepareTask+'/cancel',{});else if(multiRunningId||multiTask)await api('/multi-screens/'+(multiRunningId||multiTask)+'/cancel',{});message('正在停止，已完成内容会保留。')}
 catch(e){message(e.message)}
};
async function continueMulti(retry){
 if(!multiTask)return;multiStarting=true;applyMultiButtons();
 try{const r=await api('/multi-screens/'+multiTask+(retry?'/retry?workers='+Number($('#multi-workers').value):'/resume'),{workers:Number($('#multi-workers').value)});multiStarting=false;multiPreparing=false;multiView='latest';$('#multi-result-view').value='latest';await watchMulti(r.id)}
 catch(e){multiStarting=false;applyMultiButtons();message(e.message)}
}
$('#multi-resume').onclick=()=>continueMulti(false);$('#multi-retry').onclick=()=>continueMulti(true);
async function watchRunProgress(id){
 clearTimeout(multiProgressTimer);
 try{
  const p=await api('/multi-screens/'+id+'/progress');
  if(['queued','running'].includes(p.status)||p.archive_state==='pending'){
   multiRunningId=id;applyMultiButtons();multiProgressTimer=setTimeout(()=>watchRunProgress(id),3000);
  }else{
   if(multiRunningId===id)multiRunningId=null;applyMultiButtons();
   if(multiTask===id)await watchMulti(id);
   await loadMultiHistory();
  }
 }catch{multiProgressTimer=setTimeout(()=>watchRunProgress(id),5000)}
}
$('#multi-result-view').onchange=async()=>{
 multiView=$('#multi-result-view').value;
 if(multiView==='archive'&&multiTask){
  try{const rows=await api('/multi-screens/'+multiTask+'/archives');$('#multi-archive-version').innerHTML='<option value="">最近归档</option>'+rows.map(r=>`<option value="${r.revision}">R${r.revision} · ${esc(multiState[r.status]||r.status)} · ${esc(new Date(r.created_at).toLocaleString())}</option>`).join('')}
  catch(e){message(e.message)}
 }
 if(multiTask)watchMulti(multiTask);
};
$('#multi-archive-version').onchange=()=>{if(multiTask)watchMulti(multiTask)};
const archiveRevisionQuery=()=>multiView==='archive'&&$('#multi-archive-version').value?'&revision='+$('#multi-archive-version').value:'';
$('#multi-export-all').onclick=()=>{if(multiTask)window.location.href='/api/multi-screens/'+multiTask+'/export?format=csv&view='+multiView+archiveRevisionQuery()};
$('#multi-export-json').onclick=()=>{if(multiTask)window.location.href='/api/multi-screens/'+multiTask+'/export?format=json&view=archive'+archiveRevisionQuery()};
$('#multi-export-filtered').onclick=()=>{
 if(!multiSnapshot)return;
 const safe=v=>'"'+String(v??'').replace(/^[=+@\t\r-]/,m=>"'"+m).replaceAll('"','""')+'"';
 const rows=[multiColumns.map(c=>c[1]),...multiDisplayedRows.map(r=>multiColumns.map(c=>multiCell(r,c[0])))];
 const blob=new Blob(['\ufeff'+rows.map(row=>row.map(safe).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`quant-${multiSnapshot.effective_date}-V${multiSnapshot.version}-筛选结果.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('#multi-archive-retry').onclick=async()=>{try{await api('/multi-screens/'+multiTask+'/archive',{});await watchMulti(multiTask)}catch(e){message(e.message)}};
$('#multi-refresh').onclick=async()=>{try{await loadMultiLatestDate();await loadMultiHistory();if(multiTask)await watchMulti(multiTask)}catch(e){message(e.message)}};
$('#multi-history-search').onsubmit=e=>{e.preventDefault();multiHistoryOffset=0;loadMultiHistory().catch(e=>message(e.message))};
$('#multi-history-prev').onclick=()=>{multiHistoryOffset=Math.max(0,multiHistoryOffset-20);loadMultiHistory().catch(e=>message(e.message))};
$('#multi-history-next').onclick=()=>{multiHistoryOffset+=20;loadMultiHistory().catch(e=>message(e.message))};
$('#multi-open-history').onclick=()=>$('#multi-history-panel').scrollIntoView({behavior:'smooth'});
document.querySelector('[data-view="multi"]').addEventListener('click',loadMulti);
if(new URLSearchParams(window.location.search).get('view')==='multi')document.addEventListener('DOMContentLoaded',()=>document.querySelector('[data-view="multi"]').click());
let fundamentalTimer=null;
async function loadFundamentalStatus(){
 clearTimeout(fundamentalTimer);const job=await api('/fundamental-refresh');const active=job&&['queued','running'].includes(job.status);
 $('#multi-fundamental-update').disabled=!!active;
 $('#multi-fundamental-status').textContent=job?(active?`资料更新中 · ${job.processed}/${job.total}`:job.status==='completed'?`资料已更新 · ${job.result?.reference_date||''}`:'资料更新未完成，请查看数据总览'):'';
 if(active)fundamentalTimer=setTimeout(()=>loadFundamentalStatus().catch(e=>message(e.message)),2500);
}
$('#multi-fundamental-update').onclick=async()=>{try{await api('/fundamental-refresh',{});await loadFundamentalStatus()}catch(e){message(e.message)}};
$('#multi-settings').addEventListener('toggle',()=>{if($('#multi-settings').open)loadFundamentalStatus().catch(e=>message(e.message))});
