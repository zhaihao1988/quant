let multiTask=null,multiTimer=null,multiCatalog=null,multiStarting=false;
let multiSignals=[],multiSort={key:null,ascending:true};
const multiColumns=[['symbol','证券'],['name','名称'],['signal_date','信号日期'],['strategy_label','策略'],['signal_level','级别'],['market_cap_formatted','市值'],['pe','PE'],['pb','PB'],['revenue_growth_yoy','营收同比（比率）'],['profit_growth_yoy','净利同比（比率）'],['peg_like_ratio','PEG类指标'],['net_profit_positive_3y_latest','连续3年及最新净利为正'],['growth_positive','增长为正'],['pe_lt_30','PE<30'],['peg_like_lt_1','PEG<1']];
const multiBoolean=new Set(['net_profit_positive_3y_latest','growth_positive','pe_lt_30','peg_like_lt_1']);
const multiMissing=v=>v===null||v===undefined||v===''||(typeof v==='number'&&Number.isNaN(v));
function multiCell(row,key){
 const value=row[key];
 if(multiMissing(value))return ['symbol','name','signal_date','strategy_label','signal_level'].includes(key)?'—':'未接入';
 if(multiBoolean.has(key))return value===true?'是':value===false?'否':String(value);
 if(key==='market_cap_formatted'&&typeof value==='number')return Math.abs(value)>=1e8?num(value/1e8)+'亿':Math.abs(value)>=1e4?num(value/1e4)+'万':num(value);
 return typeof value==='number'?num(value):String(value);
}
function renderMultiSignals(){
 const rows=[...multiSignals];
 if(multiSort.key)rows.sort((a,b)=>{
  const x=a[multiSort.key],y=b[multiSort.key];
  if(multiMissing(x)||multiMissing(y))return multiMissing(x)?(multiMissing(y)?0:1):-1;
  const comparison=typeof x==='number'&&typeof y==='number'?x-y:typeof x==='boolean'&&typeof y==='boolean'?Number(x)-Number(y):String(x).localeCompare(String(y),'zh-CN',{numeric:true});
  return multiSort.ascending?comparison:-comparison;
 });
 $('#multi-results').innerHTML='<p class="hint">沿用旧项目基本面列；当前未接入财务数据，缺失项不填零。同比保持原代码比率值（0.10代表10%）。点击列名排序。</p><div class="table-wrap">'+table(multiColumns.map(c=>c[1]),rows.map(r=>multiColumns.map(c=>multiCell(r,c[0]))))+'</div>';
 $('#multi-results').classList.remove('empty');
 document.querySelectorAll('#multi-results th').forEach((th,i)=>{
  const [key,label]=multiColumns[i];th.innerHTML=`<button type="button" data-multi-sort="${esc(key)}">${esc(label)}${multiSort.key===key?(multiSort.ascending?' ↑':' ↓'):''}</button>`;
  th.querySelector('button').onclick=()=>{multiSort={key,ascending:multiSort.key===key?!multiSort.ascending:true};renderMultiSignals()};
 });
}
const multiState={queued:'排队中',running:'运行中',completed:'已完成',partial:'完成，存在数据或执行问题',cancelled:'已停止',interrupted:'服务中断，请重新运行',failed:'失败'};
async function loadMulti(){
 try{
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
 }catch(e){message(e.message)}
}
async function watchMulti(id){
 multiTask=id;clearTimeout(multiTimer);
 try{
  const r=await api('/multi-screens/'+id);if(multiTask!==id||multiStarting)return;
  const active=['queued','running'].includes(r.status),out=r.result||{},signals=out.signals||[],errors=out.errors||[];
  $('#multi-submit').disabled=active;$('#multi-stop').disabled=!active;
  $('#multi-resume').disabled=!['cancelled','interrupted','failed'].includes(r.status);
  $('#multi-summary').textContent=`${r.effective_date} · ${multiState[r.status]||r.status} · 已扫描 ${r.processed}/${r.total} · ${new Set(signals.map(s=>s.symbol)).size}只股票 / ${signals.length}条信号 ${r.current_symbol?'· '+r.current_symbol:''}`;
  $('#multi-summary').dataset.taskId=id;$('#multi-summary').dataset.status=r.status;
  const execution=out.execution;$('#multi-execution').textContent=execution?`${execution.workers>1?execution.workers+'个进程并行':'串行'} · 已保留此前 ${execution.resumed_after||0} 只股票结果`:'原串行任务';
  multiSignals=signals;
  if(signals.length)renderMultiSignals();else $('#multi-results').innerHTML=`<div class="empty">${active?'正在扫描，信号会陆续展示。':'当前任务没有当日技术信号，请同时核对数据覆盖与错误。'}</div>`;
  $('#multi-errors').innerHTML=errors.length?table(['证券','策略','问题'],errors.map(e=>[e.symbol||'任务',e.strategy_id||'数据读取',e.error])):'暂无数据或执行错误';
  $('#multi-coverage').innerHTML=out.coverage?.length?table(['证券','日K','周K','月K','起始日','最新日','历史检查提示'],out.coverage.map(c=>[c.symbol,c.daily,c.weekly,c.monthly,c.first_date,c.last_date,(c.evaluations||[]).flatMap(e=>e.history_warnings||[]).join('；')||'—'])):'尚无覆盖记录';
  if(out.fatal_error)message(out.fatal_error);
  if(active)multiTimer=setTimeout(()=>watchMulti(id),2000);
 }catch(e){message(e.message);$('#multi-submit').disabled=false}
}
document.querySelector('[data-view="multi"]').addEventListener('click',loadMulti);
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
  if($('#multi-date').value)body.as_of=$('#multi-date').value;
  if($('#multi-pool').value)body.taxonomy_id=$('#multi-pool').value;
  const r=await api('/multi-screens',body);multiStarting=false;await watchMulti(r.id);
 }catch(e){multiStarting=false;message(e.message);$('#multi-submit').disabled=false}
};
$('#multi-stop').onclick=async()=>{if(multiTask){try{await api('/multi-screens/'+multiTask+'/cancel',{});message('已请求停止，将在当前股票处理结束后停止。')}catch(e){message(e.message)}}};
$('#multi-refresh').onclick=loadMulti;
$('#multi-resume').onclick=async()=>{
 if(!multiTask)return;
 multiStarting=true;clearTimeout(multiTimer);$('#multi-resume').disabled=true;
 try{const r=await api('/multi-screens/'+multiTask+'/resume',{workers:Number($('#multi-workers').value)});multiStarting=false;await watchMulti(r.id)}
 catch(e){multiStarting=false;message(e.message);$('#multi-resume').disabled=false}
};
