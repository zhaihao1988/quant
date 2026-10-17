{
  let rows=[],page=0,chart=null,result=null,initial=false,requestId=0,openingFromScreen=false,screenScroll=0;
  const pageSize=50,up='#ef5350',down='#26a69a';
  const colors=['#e5e7eb','#f4d35e','#c084fc','#4ade80','#60a5fa','#f472b6','#fb923c','#a3e635'];
  const periodNames={daily:'日线',weekly:'周线',monthly:'月线'};
  function showPage(next){
    const pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.max(0,Math.min(next,pages-1));
    $('#market-results').innerHTML=table(['日期','周期','开','高','低','收','涨跌幅 / %','成交量 / 万股','成交额 / 亿','换手率 / %'],rows.slice(page*pageSize,(page+1)*pageSize).map(b=>[b.trade_date,b.is_partial?'周期未结束':'—',num(b.open),num(b.high),num(b.low),num(b.close),num(b.pct_change),num(b.volume/1e4),num(b.amount/1e8),num(b.turnover_pct)]));
    $('#market-page-info').textContent=`第 ${page+1} / ${pages} 页 · 共 ${num(rows.length,0)} 条 · 每页 ${pageSize} 条（日期倒序）`;
    $('#market-newest').disabled=$('#market-prev').disabled=page===0;$('#market-oldest').disabled=$('#market-next').disabled=page===pages-1;
  }
  $('#market-newest').onclick=()=>showPage(0);$('#market-prev').onclick=()=>showPage(page-1);$('#market-next').onclick=()=>showPage(page+1);$('#market-oldest').onclick=()=>showPage(Math.ceil(rows.length/pageSize)-1);
  function hover(index){
    const b=result?.bars[index];if(!b)return;
    $('#chart-readout').textContent=`${b.trade_date}  ${b.is_partial?'[周期未结束] ':''}开 ${num(b.open)}  高 ${num(b.high)}  低 ${num(b.low)}  收 ${num(b.close)}  涨跌 ${num(b.pct_change)}%  成交 ${num(b.volume/100,0)}手  换手 ${num(b.turnover_pct)}%`;
    $('#chart-indicators').innerHTML=Object.entries(b.ma).map(([p,v],i)=>`<span style="color:${colors[i%colors.length]}">MA${esc(p)} ${esc(num(v))}</span>`).join('')+`<span>DIF ${esc(num(b.dif,4))}　DEA ${esc(num(b.dea,4))}　MACD ${esc(num(b.macd,4))}</span>`;
  }
  function draw(){
    if(!chart){chart=echarts.init($('#price-chart'),null,{renderer:'canvas'});new ResizeObserver(()=>chart.resize()).observe($('#price-chart'));
      chart.on('updateAxisPointer',e=>{const a=e.axesInfo?.find(x=>x.axisIndex===0)||e.axesInfo?.[0];if(a)hover(typeof a.value==='number'?a.value:result.bars.findIndex(b=>b.trade_date===a.value))});
    }
    const bars=result.bars,dates=bars.map(b=>b.trade_date),periods=result.indicator_parameters.ma;
    const line=(name,data,grid,color)=>({name,type:'line',xAxisIndex:grid,yAxisIndex:grid,data,showSymbol:false,lineStyle:{width:1.2,color},itemStyle:{color},connectNulls:false});
    const axes=[0,1,2].map(i=>({type:'category',gridIndex:i,data:dates,boundaryGap:true,axisLine:{lineStyle:{color:'#3a4655'}},axisTick:{show:false},axisLabel:{show:i===2,color:'#92a0b4',fontSize:10},axisPointer:{show:true,label:{show:i===2}}}));
    const yAxes=[0,1,2].map(i=>({scale:true,gridIndex:i,position:'right',splitNumber:i===0?5:2,axisLabel:{color:'#92a0b4',fontSize:10,formatter:v=>i===1?(Math.abs(v)>=10000?(v/10000).toFixed(1)+'万':num(v,0)):num(v,i===0?2:3)},splitLine:{lineStyle:{color:'#223042',type:'dashed'}},axisPointer:{label:{show:true}}}));
    const series=[{name:'K线',type:'candlestick',data:bars.map(b=>[b.open,b.close,b.low,b.high]),itemStyle:{color:'transparent',color0:down,borderColor:up,borderColor0:down},barMaxWidth:16},
      ...periods.map((p,i)=>line('MA'+p,bars.map(b=>b.ma[String(p)]),0,colors[i%colors.length])),
      {name:'成交量(手)',type:'bar',xAxisIndex:1,yAxisIndex:1,data:bars.map(b=>({value:b.volume/100,itemStyle:{color:b.close>=b.open?up:down}}))},
      line('量MA5',bars.map(b=>b.volume_ma5===null?null:b.volume_ma5/100),1,'#e5e7eb'),line('量MA10',bars.map(b=>b.volume_ma10===null?null:b.volume_ma10/100),1,'#f4d35e'),
      {name:'MACD',type:'bar',xAxisIndex:2,yAxisIndex:2,data:bars.map(b=>({value:b.macd,itemStyle:{color:b.macd>=0?up:down}}))},line('DIF',bars.map(b=>b.dif),2,'#f4d35e'),line('DEA',bars.map(b=>b.dea),2,'#e5e7eb')];
    chart.setOption({animation:false,backgroundColor:'#111923',textStyle:{color:'#cbd5e1'},
      title:[{text:`${result.name} ${result.symbol} · ${periodNames[result.timeframe]}`,left:16,top:10,textStyle:{fontSize:13,color:'#dbe4ee'}},{text:'成交量（手） MA5 / MA10',left:16,top:'54%',textStyle:{fontSize:11,color:'#92a0b4'}},{text:`MACD (${result.indicator_parameters.macd.join(',')})`,left:16,top:'74%',textStyle:{fontSize:11,color:'#92a0b4'}}],
      grid:[{left:30,right:76,top:40,height:'44%'},{left:30,right:76,top:'58%',height:'12%'},{left:30,right:76,top:'78%',height:'14%'}],
      axisPointer:{link:[{xAxisIndex:'all'}],lineStyle:{color:'#a1acba',type:'dashed'}},
      tooltip:{trigger:'axis',axisPointer:{type:'cross'},backgroundColor:'#162332',borderColor:'#42546b',textStyle:{color:'#e5e7eb'},confine:true,formatter:params=>{const b=bars[params[0]?.dataIndex];return b?`${esc(b.trade_date)}${b.is_partial?' · 周期未结束':''}<br>开 ${num(b.open)}　收 ${num(b.close)}<br>低 ${num(b.low)}　高 ${num(b.high)}<br>成交量 ${num(b.volume/100,0)} 手<br>DIF ${num(b.dif,4)}　DEA ${num(b.dea,4)}<br>MACD ${num(b.macd,4)}`:''}},
      xAxis:axes,yAxis:yAxes,series,
      dataZoom:[{type:'inside',xAxisIndex:[0,1,2],startValue:Math.max(0,bars.length-120),endValue:Math.max(0,bars.length-1)},{type:'slider',xAxisIndex:[0,1,2],bottom:5,height:22,borderColor:'#314052',textStyle:{color:'#92a0b4'},dataBackground:{lineStyle:{color:'#60758d'},areaStyle:{color:'#354b61'}}}]},true);
    hover(bars.length-1);$('#chart-window-note').textContent=`已加载 ${bars.length} 根${periodNames[result.timeframe]}；初始视窗最近120根，可滚轮缩放、拖动底部滑块或点击“显示全部”。`;
  }
  $('#chart-fit').onclick=()=>chart?.dispatchAction({type:'dataZoom',start:0,end:100});
  $('#chart-recent').onclick=()=>result&&chart?.dispatchAction({type:'dataZoom',startValue:Math.max(0,result.bars.length-120),endValue:result.bars.length-1});
  async function symbolValue(){
    const value=$('#symbol').value.trim().toUpperCase();
    if(/^\d{6}\.(SH|SZ|BJ)$/.test(value))return value;
    if(/^\d{6}$/.test(value))return value+'.'+(value.startsWith('6')?'SH':/^[48]|^92/.test(value)?'BJ':'SZ');
    const matches=await api('/securities?q='+encodeURIComponent(value));const exact=matches.find(m=>m.name===value)||matches.find(m=>m.symbol===value);
    if(exact||matches.length===1)return (exact||matches[0]).symbol;
    throw Error('未唯一匹配证券，请输入完整代码，如300138.SZ');
  }
  $('#market-form').onsubmit=async event=>{
    event.preventDefault();const current=++requestId,button=$('#market-submit');
    result=null;rows=[];chart?.clear();$('#chart-readout').textContent='正在加载行情…';$('#chart-indicators').textContent='';$('#market-info').textContent='';$('#chart-window-note').textContent='';$('#market-note').textContent='';showPage(0);button.disabled=true;button.textContent='查询中…';
    for(const id of ['market-newest','market-prev','market-next','market-oldest'])$('#'+id).disabled=true;
    try{
      const symbol=await symbolValue();if(current!==requestId)return;$('#symbol').value=symbol;
      document.dispatchEvent(new CustomEvent('quant:stock-detail',{detail:{symbol}}));
      const params=new URLSearchParams({limit:$('#market-range').value,adjustment:$('#market-adjust').value,timeframe:$('#market-timeframe').value,ma:$('#market-ma').value,fast:$('#macd-fast').value,slow:$('#macd-slow').value,signal:$('#macd-signal').value});
      for(const key of ['start','end'])if($('#market-'+key).value)params.set(key,$('#market-'+key).value);
      const incoming=await api(`/market/${encodeURIComponent(symbol)}?${params}`);
      if(current!==requestId)return;result=incoming;
      $('#market-info').textContent=`${result.name} ${result.symbol} · ${periodNames[result.timeframe]} · ${result.adjustment==='qfq'?'前复权':'不复权'} · 基准日 ${result.anchor||'—'}｜本地日线 ${result.available_start||'—'}—${result.available_end||'—'}，共 ${num(result.available_rows,0)} 条｜本次 ${num(result.returned_rows,0)} 根K线`;
      rows=result.bars.slice().reverse();showPage(0);
      if(rows.length)draw();else{chart?.clear();$('#chart-readout').textContent='所选区间无行情';$('#chart-indicators').textContent=''}
      $('#market-note').textContent=result.timeframe==='daily'?'成交量图以手展示，表格以万股展示；换手率按可用历史股本估算。':'周/月换手率按每日估算值累加；未结束周期以截止日期计算，后续仍可能变化。';message(result.truncated?'所选范围只加载了一段记录，可切换全部历史查看。':'');initial=true;
    }catch(error){if(current===requestId){message(error.message);$('#chart-readout').textContent='行情加载失败';showPage(page)}}finally{if(current===requestId){button.disabled=false;button.textContent='查询行情'}}
  };
  document.addEventListener('quant:open-market',event=>{
    const symbol=event.detail?.symbol;if(!/^\d{6}\.(SH|SZ|BJ)$/.test(symbol||''))return;
    screenScroll=window.scrollY;openingFromScreen=true;
    $('#symbol').value=symbol;$('#market-timeframe').value='daily';$('#market-adjust').value='qfq';
    $('#market-range').value='20000';$('#market-start').value='';$('#market-end').value='';
    $('#market-back').hidden=false;
    document.querySelector('[data-view="market"]').click();openingFromScreen=false;
    $('#market-form').requestSubmit();$('#market').scrollIntoView({block:'start'});
  });
  $('#market-back').onclick=()=>{
    document.querySelector('[data-view="multi"]').click();window.scrollTo(0,screenScroll);
  };
  $('#market-timeframe').onchange=()=>$('#market-form').requestSubmit();
  document.querySelector('[data-view="market"]').addEventListener('click',()=>{if(!openingFromScreen&&!initial&&!$('#market-submit').disabled)$('#market-form').requestSubmit();else chart?.resize()});
}
