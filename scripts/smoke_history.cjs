const {chromium}=require(process.env.QUANT_PLAYWRIGHT||'playwright');
const fs=require('node:fs');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:1050}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8765',{waitUntil:'networkidle'});
    await page.click('[data-view="market"]');
    await page.click('#market-form button');
    await page.waitForFunction(()=>document.querySelector('#market-info').textContent.includes('2010-11-05'));
    const info=await page.locator('#market-info').innerText();
    if(!info.includes('3,839'))throw Error('Full history was not loaded: '+info);
    await page.click('#market-oldest');
    await page.waitForFunction(()=>document.querySelector('#market-results').textContent.includes('2010-11-05'));
    fs.mkdirSync('artifacts',{recursive:true});
    await page.screenshot({path:'artifacts/market-full-history.png',fullPage:true});
    await page.selectOption('#market-range','120');await page.click('#market-form button');
    await page.waitForFunction(()=>document.querySelector('#market-info').textContent.includes('120 条'));
    await page.selectOption('#market-range','20000');
    await page.fill('#market-start','2020-01-01');await page.fill('#market-end','2020-12-31');
    await page.click('#market-form button');
    await page.waitForFunction(()=>document.querySelector('#market-info').textContent.includes('基准日 2020-12-31'));
    await page.click('#market-oldest');
    const dates=await page.locator('#market-results tbody tr td:first-child').allTextContents();
    if(!dates.length||dates.some(d=>!d.startsWith('2020-')))throw Error('Date range failed');
    if(errors.length)throw Error(errors.join('; '));
    console.log('Verified: 3839 bars since 2010-11-05; oldest-page navigation; 120-bar preset; 2020 custom range.');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
