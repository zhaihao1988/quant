// Development-only browser test; set QUANT_PLAYWRIGHT if Playwright is installed outside this project.
const {chromium}=require(process.env.QUANT_PLAYWRIGHT||'playwright');
const fs=require('node:fs');
(async()=>{
  fs.mkdirSync('artifacts',{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8765',{waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.querySelector('#connection').textContent==='数据库已连接');
    await page.screenshot({path:'artifacts/overview.png',fullPage:true});
    await page.click('[data-view="market"]');
    await page.click('#market-form button');
    await page.waitForSelector('#market-results tbody tr');
    if(!await page.locator('#market-info').innerText().then(t=>t.includes('300138.SZ')))throw Error('Missing market identity');
    await page.screenshot({path:'artifacts/market.png',fullPage:true});
    await page.click('[data-view="screen"]');
    await page.waitForFunction(()=>document.querySelector('[name=taxonomy_id]').options.length>1);
    await page.fill('[name=min_amount]','1');
    await page.click('#screen-form button');
    await page.waitForSelector('#screen-results tbody tr',{timeout:120000});
    await page.screenshot({path:'artifacts/screen.png',fullPage:true});
    await page.click('[data-view="graph"]');await page.click('#graph-form button');
    await page.waitForSelector('#graph-results tbody tr');
    await page.screenshot({path:'artifacts/graph.png',fullPage:true});
    await page.click('[data-view="news"]');await page.fill('#news-title','晨光生物发布公司动态');
    await page.click('#news-form button');
    await page.waitForSelector('#news-results tbody tr',{timeout:120000});
    await page.screenshot({path:'artifacts/news.png',fullPage:true});
    if(errors.length)throw Error(errors.join('; '));
    console.log('Browser verified: overview, market, screen, graph, news; no page errors.');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
