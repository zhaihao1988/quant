const {chromium}=require(process.env.QUANT_PLAYWRIGHT||'playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  for(let attempt=0;attempt<30;attempt++){
   try{if((await page.request.get('http://127.0.0.1:8765/api/health')).ok())break}catch{}
   if(attempt===29)throw Error('Local web service did not become ready');
   await new Promise(resolve=>setTimeout(resolve,500));
  }
  await page.goto('http://127.0.0.1:8765',{waitUntil:'networkidle'});
  await page.click('[data-view="multi"]');
  await page.waitForFunction(()=>document.querySelectorAll('[name="multi-strategy"]').length===7);
  await page.click('#multi-settings > summary');
  await page.fill('#multi-symbols','300138 招商银行');
  const started=page.waitForResponse(r=>r.url().endsWith('/api/market-prepare')&&r.request().method()==='POST');
  await page.click('#multi-submit');
  const response=await started,preparation=await response.json();
  if(response.status()!==202)throw Error(JSON.stringify(preparation));
  let task=null;
  for(let attempt=0;attempt<300;attempt++){
   const prepared=await (await page.request.get('http://127.0.0.1:8765/api/market-prepare/'+preparation.id)).json();
   if(prepared.screen_id){task={id:prepared.screen_id};break}
   if(['failed','cancelled','interrupted'].includes(prepared.status))throw Error(JSON.stringify(prepared));
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
  if(!task)throw Error('Market preparation did not create a technical task');
  await page.waitForFunction(id=>{const s=document.querySelector('#multi-summary');return s.dataset.taskId===id&&['completed','partial','failed'].includes(s.dataset.status)},task.id,{timeout:300000});
  const data=await (await page.request.get('http://127.0.0.1:8765/api/multi-screens/'+task.id)).json();
  if(data.status!=='completed'||data.result.coverage.length!==2)throw Error(JSON.stringify(data.result.errors));
  if(!data.result.coverage.every(s=>s.daily>1000&&s.monthly>30&&s.evaluations.length===7))throw Error('Missing full history or strategy execution');
  fs.mkdirSync('artifacts',{recursive:true});
  await page.screenshot({path:'artifacts/multiscreener.png',fullPage:true});
  fs.writeFileSync('.runtime/multi-browser-result.json',JSON.stringify(data,null,2));
  await page.click('[data-view="screen"]');
  await page.waitForSelector('#screen-form');
  if(await page.locator('#screen-form [name="require_breakout"]').count()!==1)throw Error('Old screen missing');
  if(errors.length)throw Error(errors.join('; '));
  console.log(JSON.stringify({id:task.id,status:data.status,signals:data.result.signals.length,coverage:data.result.coverage.map(c=>({symbol:c.symbol,daily:c.daily,monthly:c.monthly})),old_screen:'present',browser_errors:errors}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
