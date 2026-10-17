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
 $('#multi-results').innerHTML='<p class="hint">统一使用最新财务与股本参考，信号日期保持原样。PE为最近年报口径；价格日期单列显示。参考换手率按最新股本计算，非历史当时口径。同比0.10代表10%。列名排序，三角按钮筛选。</p>'+`<div class="multi-filter-summary"><span>显示 ${rows.length} / ${multiSignals.length} 条信号 · ${new Set(rows.map(r=>r.symbol)).size} 只股票 · 财务参考日 ${esc(multiSignals[0]?.financial_reference_date||'未更新')}</span>${Object.keys(multiFilters).length?'<button type="button" id="multi-clear-filters">清除全部筛选</button>':''}</div><div class="table-wrap">`+table(multiColumns.map(c=>c[1]),rows.map(r=>multiColumns.map(c=>multiCell(r,c[0]))))+'</div>'+(rows.length?'':'<p class="hint">没有匹配结果，可清除筛选或放宽条件。</p>');
 $('#multi-results').classList.remove('empty');
 const wrap=$('#multi-results .table-wrap');
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
  wrap.scrollLeft+=th.getBoundingClientRect().left-wrap.getBoundingClientRect().left;
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
const multiState={queued:'排队中',running:'运行中',completed:'已完成',partial:'完成，存在数据或执行问题',cancelled:'已停止',interrupted:'服务中断，请重新运行',failed:'失败'};
async function loadMulti(){
 loadFundamentalStatus().catch(e=>message(e.message));
 try{
  await loadMultiLatestDate();
  if(!multiCatalog){
   multiCatalog=await api('/multi-screens/strategies');
   $('#multi-strategies').innerHTML=multiCatalog.strategies.map(s=>`<label class="check"><input type="checkbox" name="multi-strategy" value="${esc(s.id)}" checked> ${esc(s.name)}</label>`).join('');
   $('#multi-parameters').textContent=JSON.stringify(multiCatalog.strategies,null,2);
   const pools=await api('/taxonomies');$('#multi-pool').innerHTML='<option value="">全部本地 A 股</option>'+pools.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${p.members}只</option>`).join('');
  }
  const runs=await api('/multi-screens');
  $('#multi-history').innerHTML=runs.map(r=>`<button type="button" data-multi-id="${esc(r.id)}">${esc(r.effective_date)} · ${esc(multiState[r.status]||r.status)} · ${r.processed}/${r.total} · ${r.signals}条信号</button>`).join(' ')||'尚无任务';
  document.querySelectorAll('[data-multi-id]').forEach(b=>b.onclick=()=>watchMulti(b.dataset.multiId));
  if(!multiTask&&!multiStarting&&runs.length)await watchMulti(runs[0].id);
  await watchMarketPrepare();
 }catch(e){message(e.message)}
}
async function watchMulti(id){
 if(multiTask!==id){closeMultiFilter();multiFilters={}}
 multiTask=id;clearTimeout(multiTimer);
 try{
  const r=await api('/multi-screens/'+id);if(multiTask!==id||multiStarting)return;
  const active=['queued','running'].includes(r.status),out=r.result||{},signals=out.signals||[],errors=out.errors||[];
  $('#multi-submit').disabled=active||multiPreparing;$('#multi-stop').disabled=!active;
  $('#multi-market-update').disabled=active||multiPreparing;
  const outdated=!!multiLatestDate&&r.effective_date!==multiLatestDate;
  $('#multi-resume').disabled=multiPreparing||outdated||!['cancelled','interrupted','failed'].includes(r.status);
  $('#multi-resume').title=outdated?'旧任务日期早于最新数据，请运行最新日期的新任务':'';
  $('#multi-summary').textContent=`${r.effective_date} · ${multiState[r.status]||r.status} · 已扫描 ${r.processed}/${r.total} · ${new Set(signals.map(s=>s.symbol)).size}只股票 / ${signals.length}条信号 ${r.current_symbol?'· '+r.current_symbol:''}`;
  $('#multi-summary').dataset.taskId=id;$('#multi-summary').dataset.status=r.status;
  const execution=out.execution;$('#multi-execution').textContent=execution?`${execution.workers>1?execution.workers+'个进程并行':'串行'} · 已保留此前 ${execution.resumed_after||0} 只股票结果`:'原串行任务';
  if(outdated)$('#multi-execution').textContent+=` · 当前展示旧日期任务；新选股自动使用 ${multiLatestDate}`;
  multiSignals=signals;
  if(signals.length)renderMultiSignals();else $('#multi-results').innerHTML=`<div class="empty">${active?'正在扫描，信号会陆续展示。':'当前任务没有当日技术信号，请同时核对数据覆盖与错误。'}</div>`;
  $('#multi-errors').innerHTML=errors.length?table(['证券','策略','问题'],errors.map(e=>[e.symbol||'任务',e.strategy_id||'数据读取',e.error])):'暂无数据或执行错误';
  $('#multi-coverage').innerHTML=out.coverage?.length?table(['证券','日K','周K','月K','起始日','最新日','历史检查提示'],out.coverage.map(c=>[c.symbol,c.daily,c.weekly,c.monthly,c.first_date,c.last_date,(c.evaluations||[]).flatMap(e=>e.history_warnings||[]).join('；')||'—'])):'尚无覆盖记录';
  if(out.fatal_error)message(out.fatal_error);
  if(active)multiTimer=setTimeout(()=>watchMulti(id),2000);
 }catch(e){message(e.message);$('#multi-submit').disabled=false}
}
document.querySelector('[data-view="multi"]').addEventListener('click',loadMulti);
async function loadMultiLatestDate(){
 const latest=await api('/multi-screens/latest');multiLatestDate=latest.effective_date;
 $('#multi-date').value=multiLatestDate||'暂无可用盘后数据';
}
$('#multi-form').onsubmit=async e=>{
 e.preventDefault();multiStarting=true;clearTimeout(multiTimer);$('#multi-submit').disabled=true;message('');
 try{
  const symbols=[];
  for(const input of $('#multi-symbols').value.split(/[,，;；\s]+/).filter(Boolean)){
   if(/^\d{6}\.(SH|SZ|BJ)$/i.test(input)){symbols.push(input.toUpperCase());continue}
   const found=await api('/securities?q='+encodeURIComponent(input));
   const exact=found.filter(s=>s.code===input||s.name===input||s.symbol.toUpperCase()===input.toUpperCase());
   if(exact.length!==1)throw Error(`无法唯一确认证券：${input}，请填写代码或完整名称`);
   symbols.push(exact[0].symbol);
  }
  const body={strategies:[...document.querySelectorAll('[name="multi-strategy"]:checked')].map(i=>i.value),symbols:[...new Set(symbols)],workers:Number($('#multi-workers').value)};
  // Always let the server resolve the latest session at submission time.
  if($('#multi-pool').value)body.taxonomy_id=$('#multi-pool').value;
  const r=await api('/market-prepare',{screen:body});marketPrepareTask=r.id;marketPrepareWasActive=true;await watchMarketPrepare(r.id);
 }catch(e){multiStarting=false;message(e.message);$('#multi-submit').disabled=false}
};
async function watchMarketPrepare(id=null){
 clearTimeout(marketPrepareTimer);
 const job=await api('/market-prepare'+(id?'/'+id:''));
 if(!job){$('#multi-market-status').textContent='选股前自动核对通达信日期、修改时间和数据库；有变化先同步，再选股。';return}
 const active=['queued','running'].includes(job.status),p=job.import_progress;
 multiPreparing=active;if(active)marketPrepareTask=job.id;
 $('#multi-market-update').disabled=active||$('#multi-stop').disabled===false;
 if(active){$('#multi-submit').disabled=true;$('#multi-resume').disabled=true}
 const snapshot=job.result?.after||job.result?.before;
 $('#multi-market-status').textContent='行情核对：'+job.stage+
  (p?` · ${p.processed}/${p.total}份 · 新增/修订${p.changed_rows}行`:'')+
  (snapshot?` · 通达信${snapshot.local_date||'—'} / 数据库${snapshot.db_date||'—'}`:'')+
  (job.result?.effective_date?` · 最新选股日${job.result.effective_date}`:'')+
  (snapshot?.missing_files?` · ${snapshot.missing_files}只缺源文件，保留缺失提示`:'')+
  (job.error?' · '+job.error:'');
 if(active){marketPrepareWasActive=true;marketPrepareTimer=setTimeout(()=>watchMarketPrepare(job.id).catch(e=>message(e.message)),2000);return}
 if(marketPrepareWasActive||marketPrepareTask===job.id){
  marketPrepareWasActive=false;marketPrepareTask=null;multiStarting=false;
  await loadMultiLatestDate();
  if(job.screen_id)await watchMulti(job.screen_id);
  else if(multiTask)await watchMulti(multiTask);
  else $('#multi-submit').disabled=false;
 }
}
$('#multi-market-update').onclick=async()=>{
 try{
  $('#multi-market-update').disabled=true;
  const r=await api('/market-prepare',{});marketPrepareTask=r.id;marketPrepareWasActive=true;
  await watchMarketPrepare(r.id);
 }catch(e){$('#multi-market-update').disabled=false;message(e.message)}
};
$('#multi-stop').onclick=async()=>{if(multiTask){try{await api('/multi-screens/'+multiTask+'/cancel',{});message('已请求停止，将在当前股票处理结束后停止。')}catch(e){message(e.message)}}};
$('#multi-refresh').onclick=loadMulti;
$('#multi-resume').onclick=async()=>{
 if(!multiTask)return;
 multiStarting=true;clearTimeout(multiTimer);$('#multi-resume').disabled=true;
 try{const r=await api('/multi-screens/'+multiTask+'/resume',{workers:Number($('#multi-workers').value)});multiStarting=false;await watchMulti(r.id)}
 catch(e){multiStarting=false;message(e.message);$('#multi-resume').disabled=false}
};

// A direct link can open the result table without starting another scan.
if(new URLSearchParams(window.location.search).get('view')==='multi'){
 document.addEventListener('DOMContentLoaded',()=>document.querySelector('[data-view="multi"]').click());
}

let fundamentalTimer=null,fundamentalWasActive=false;
async function loadFundamentalStatus(){
 clearTimeout(fundamentalTimer);
 const job=await api('/fundamental-refresh'),active=job&&['queued','running'].includes(job.status);
 $('#multi-fundamental-update').disabled=!!active;
 if(!job){$('#multi-fundamental-status').textContent='尚未更新股本及财务，可读取通达信本地资料并计算全市场指标。';return}
 const coverage=job.result?.coverage,labels={queued:'排队中',running:'更新中',completed:'已完成',partial:'完成，部分源文件有问题',failed:'失败',interrupted:'已中断，可重新更新'};
 $('#multi-fundamental-status').textContent=`股本及财务：${labels[job.status]||job.status} · ${job.stage} ${job.total?'· '+job.processed+'/'+job.total:''}`+
  (coverage?` · 股本${coverage.total_shares}只 / PE${coverage.pe}只 / PB${coverage.pb}只 · 参考日${job.result.reference_date}`:'')+
  (job.errors?.length?' · '+job.errors.length+'项问题：'+(job.errors[0].error||'请查看导入记录'):'');
 if(active)fundamentalTimer=setTimeout(()=>loadFundamentalStatus().catch(e=>message(e.message)),2000);
 else if(fundamentalWasActive&&multiTask)await watchMulti(multiTask);
 fundamentalWasActive=!!active;
}
$('#multi-fundamental-update').onclick=async()=>{
 try{$('#multi-fundamental-update').disabled=true;await api('/fundamental-refresh',{});await loadFundamentalStatus()}
 catch(e){$('#multi-fundamental-update').disabled=false;message(e.message)}
};
