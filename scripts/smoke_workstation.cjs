const {chromium}=require(process.env.QUANT_PLAYWRIGHT||'playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8765',{waitUntil:'networkidle'});
  await page.waitForFunction(()=>document.querySelector('#connection').textContent==='数据库已连接');
  await page.waitForFunction(()=>document.querySelector('#source-results tbody tr'),{timeout:120000});
  fs.mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/sync-comparison.png',fullPage:true});
  await page.click('[data-view="market"]');
  await page.waitForFunction(()=>window.echarts?.getInstanceByDom(document.getElementById('price-chart'))?.getOption().series?.some(s=>s.type==='candlestick'));
  for(const [timeframe,file] of [['daily','daily'],['weekly','weekly'],['monthly','monthly']]){
   if(timeframe!=='daily'){
    const response=page.waitForResponse(r=>r.url().includes('/api/market/')&&r.url().includes('timeframe='+timeframe));
    await page.selectOption('#market-timeframe',timeframe);await response;
    await page.waitForFunction(tf=>document.querySelector('#market-info').textContent.includes({weekly:'周线',monthly:'月线'}[tf]),timeframe);
   }
   const option=await page.evaluate(()=>{const o=echarts.getInstanceByDom(document.getElementById('price-chart')).getOption();return {grids:o.grid.length,axes:o.xAxis.length,names:o.series.map(s=>s.name),candles:o.series.find(s=>s.type==='candlestick').data,zoom:o.dataZoom[0].xAxisIndex}});
   if(option.grids!==3||option.axes!==3||!option.names.includes('DIF')||!option.names.includes('成交量(手)')||option.zoom.length!==3)throw Error('Missing linked panes or indicators');
   const response=await page.request.get('http://127.0.0.1:8765/api/market/300138.SZ?timeframe='+timeframe);const data=await response.json();const last=data.bars.at(-1);
   if(JSON.stringify(option.candles.at(-1))!==JSON.stringify([last.open,last.close,last.low,last.high]))throw Error('Candlestick OHLC ordering mismatch');
   await page.locator('#price-chart').screenshot({path:`artifacts/kline-${file}.png`});
  }
  const dailyResponse=page.waitForResponse(r=>r.url().includes('/api/market/')&&r.url().includes('timeframe=daily'));
  await page.selectOption('#market-timeframe','daily');await dailyResponse;
  await page.waitForFunction(()=>echarts.getInstanceByDom(document.getElementById('price-chart')).getOption().title[0].text.endsWith('日线'));
  await page.click('#chart-fit');
  if((await page.evaluate(()=>echarts.getInstanceByDom(document.getElementById('price-chart')).getOption().dataZoom[0].start))!==0)throw Error('Fit zoom failed');
  await page.click('#market-oldest');await page.waitForFunction(()=>document.querySelector('#market-results').textContent.includes('2010-11-05'));
  await page.fill('#market-ma','5,20,60,120');await page.click('#market-submit');
  await page.waitForFunction(()=>echarts.getInstanceByDom(document.getElementById('price-chart')).getOption().series.some(s=>s.name==='MA120'));
  if(errors.length)throw Error(errors.join('; '));
  console.log('Verified: source comparison, daily/weekly/monthly candlesticks, 3 linked panes, OHLC ordering, MACD, volume, MA parameters, zoom, historical table.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
