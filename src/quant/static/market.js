// Full-history market view, separate from screening and overview interactions.
{
  let rows=[],page=0;
  const pageSize=50;
  function showPage(next){
    const pages=Math.max(1,Math.ceil(rows.length/pageSize));
    page=Math.max(0,Math.min(next,pages-1));
    const visible=rows.slice(page*pageSize,(page+1)*pageSize);
    $('#market-results').innerHTML=table(
      ['日期','开','高','低','收','涨跌幅 / %','成交量 / 万股','成交额 / 亿','换手率 / %'],
      visible.map(b=>[b.trade_date,num(b.open),num(b.high),num(b.low),num(b.close),num(b.pct_change),num(b.volume/1e4),num(b.amount/1e8),num(b.turnover_pct)]));
    $('#market-page-info').textContent=`第 ${page+1} / ${pages} 页 · 共 ${num(rows.length,0)} 条 · 每页 ${pageSize} 条（日期倒序）`;
    $('#market-newest').disabled=$('#market-prev').disabled=page===0;
    $('#market-oldest').disabled=$('#market-next').disabled=page===pages-1;
  }
  $('#market-newest').onclick=()=>showPage(0);
  $('#market-prev').onclick=()=>showPage(page-1);
  $('#market-next').onclick=()=>showPage(page+1);
  $('#market-oldest').onclick=()=>showPage(Math.ceil(rows.length/pageSize)-1);

  $('#market-form').onsubmit=async event=>{
    event.preventDefault();
    const button=event.submitter;
    button.disabled=true;
    try{
      const params=new URLSearchParams({limit:$('#market-range').value,adjustment:$('#market-adjust').value});
      for(const key of ['start','end'])if($('#market-'+key).value)params.set(key,$('#market-'+key).value);
      const result=await api(`/bars/${encodeURIComponent($('#symbol').value.trim().toUpperCase())}?${params}`);
      const available=result.available_start?`${result.available_start}—${result.available_end}，共 ${num(result.available_rows,0)} 条`:'无行情';
      const displayed=result.bars.length?`${result.bars[0].trade_date}—${result.bars.at(-1).trade_date}`:'无匹配日期';
      $('#market-info').textContent=`${result.name} ${result.symbol} · ${result.adjustment==='qfq'?'前复权':'不复权'} · 基准日 ${result.anchor||'—'}｜本地覆盖 ${available}｜本次显示 ${displayed}，${num(result.returned_rows,0)} 条`;
      rows=result.bars.slice().reverse();showPage(0);
      const values=result.bars.map(b=>Number(b.close));
      if(values.length){
        const min=Math.min(...values),max=Math.max(...values),range=max-min||1;
        const points=values.map((v,i)=>`${20+i*960/Math.max(1,values.length-1)},${210-(v-min)*180/range}`).join(' ');
        $('#price-chart').innerHTML=`<text x="20" y="20" fill="#718093" font-size="13">${esc(num(max))}</text><polyline points="${points}" fill="none" stroke="#09796e" stroke-width="2"/><text x="20" y="232" fill="#718093" font-size="13">${esc(result.bars[0].trade_date)}</text><text x="870" y="232" fill="#718093" font-size="13">${esc(result.bars.at(-1).trade_date)}</text>`;
      }else $('#price-chart').innerHTML='';
      const notices=[result.truncated?'所选范围只加载了最近一段记录，可切换“全部历史”查看。':'',result.turnover_note||''];
      message(notices.filter(Boolean).join(' '));
    }catch(error){message(error.message)}
    finally{button.disabled=false}
  };
}
