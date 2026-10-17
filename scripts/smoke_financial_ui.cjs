const {chromium}=require(process.env.QUANT_PLAYWRIGHT||'playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1700,height:1100}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.QUANT_BASE_URL||'http://127.0.0.1:8765';
  const id='00000000-0000-0000-0000-000000000001';let submitted=null;
  const common={signal_date:'2026-10-14',strategy_label:'日线突破',signal_level:'Daily',details:{hidden_marker:'DO_NOT_SHOW_TECH_DETAILS'},fundamental_status:'not_connected'};
  const result={id,status:'completed',effective_date:'2026-10-14',processed:3,total:3,result:{signals:[
   {...common,symbol:'300138.SZ',name:'晨光生物',pe:0,pb:1,revenue_growth_yoy:.1,pe_lt_30:false},
   {...common,symbol:'600036.SH',name:'招商银行',pe:10},
   {...common,symbol:'601318.SH',name:'中国平安',fundamental_labels:{pe:'采集日前不可用',pb:'冲突待确认'}}],errors:[],coverage:[],execution:{mode:'processes',workers:4,resumed_after:0}}};
  // All mutations intercepted; safe even while a real market-wide scan is running.
  await page.route('**/api/multi-screens',route=>{
   if(route.request().method()==='POST'){submitted=route.request().postDataJSON();return route.fulfill({json:{id,reused:false},status:202})}
   return route.fulfill({json:[]});
  });
  await page.route('**/api/multi-screens/'+id,route=>route.fulfill({json:result}));
  // The web entry now prepares/syncs before creating the technical task.
  await page.route('**/api/multi-screens/latest',route=>route.fulfill({json:{effective_date:'2026-10-14'}}));
  await page.route('**/api/market-prepare',route=>{
   if(route.request().method()==='POST'){submitted=route.request().postDataJSON().screen;return route.fulfill({json:{id,reused:false},status:202})}
   return route.fulfill({json:null});
  });
  await page.route('**/api/market-prepare/'+id,route=>route.fulfill({json:{id,status:'completed',stage:'mock aligned',screen_id:id,result:{effective_date:'2026-10-14'}}}));
  await page.goto(base,{waitUntil:'networkidle'});await page.click('[data-view="multi"]');
  await page.waitForFunction(()=>document.querySelectorAll('[name="multi-strategy"]').length===7);
  await page.fill('#multi-symbols','300138.SZ');await page.click('#multi-submit');
  await page.waitForSelector('#multi-results tbody tr');
  if(submitted?.workers!==4)throw Error('Default worker count missing');
  const text=await page.locator('#multi-results').innerText();
  for(const field of ['市值','PE','PB','营收同比','净利同比','PEG','未接入','否','采集日前不可用','冲突待确认'])if(!text.includes(field))throw Error('Missing column/value: '+field);
  if(text.includes('技术详情')||text.includes('DO_NOT_SHOW_TECH_DETAILS'))throw Error('Technical details still shown');
  await page.click('[data-multi-sort="pe"]');await page.click('[data-multi-sort="pe"]');
  const order=await page.locator('#multi-results tbody tr td:first-child').allTextContents();
  if(JSON.stringify(order)!==JSON.stringify(['600036.SH','300138.SZ','601318.SH']))throw Error('Sort or missing-value placement wrong');
  fs.mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/financial-columns.png',fullPage:true});
  if(errors.length)throw Error(errors.join('; '));
  console.log('Verified: default 4 workers, legacy fundamental columns, missing markers, false/zero values, numeric sort, technical details hidden.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
