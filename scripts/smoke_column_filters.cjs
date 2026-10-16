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
  const count=async expected=>{const n=await page.locator('#multi-results tbody tr').count();if(n!==expected)throw Error(`Expected ${expected} rows, got ${n}`)};
  const open=async key=>{await page.click(`[data-multi-filter="${key}"]`);await page.waitForSelector('#multi-filter-menu')};
  const apply=async()=>{await page.click('[data-filter-apply]');await page.waitForSelector('#multi-filter-menu',{state:'detached'})};
  const clearAll=async()=>{await page.click('#multi-clear-filters');await count(3)};
  if(await page.locator('[data-multi-filter]').count()!==15)throw Error('Each column needs a filter');
  await open('name');await page.fill('[data-filter-search]','招商');await apply();await count(1);
  if(!await page.locator('#multi-results tbody').innerText().then(t=>t.includes('招商银行')))throw Error('Search filter failed');
  await page.evaluate(id=>watchMulti(id),id);await count(1); // A same-task refresh retains filters.
  await open('name');await page.click('[data-filter-clear]');await count(3);
  await open('pe');await page.fill('[data-filter-min]','0');await page.fill('[data-filter-max]','0');await apply();await count(1);
  if(!await page.locator('#multi-results tbody').innerText().then(t=>t.includes('晨光生物')))throw Error('Zero should survive numeric filters');
  await open('pe');await page.fill('[data-filter-min]','10');await page.fill('[data-filter-max]','1');await page.click('[data-filter-apply]');
  if(!await page.locator('.multi-filter-error').innerText().then(t=>t.includes('最小值')))throw Error('Invalid range accepted');
  await page.keyboard.press('Escape');await count(1);await clearAll();
  await open('pe_lt_30');await page.click('[data-filter-none]');
  await page.locator('.multi-filter-values label').filter({hasText:'否'}).locator('input').check();await apply();await count(1);
  await open('pb');await page.fill('[data-filter-min]','2');await apply();await count(0);
  if(!await page.locator('#multi-results').innerText().then(t=>t.includes('没有匹配结果')))throw Error('Empty-result recovery missing');
  await open('pb');await page.click('[data-filter-clear]');await count(1);await clearAll();
  await open('pe');await page.fill('[data-filter-search]','采集日前');await apply();await count(1);
  if(!await page.locator('#multi-results tbody').innerText().then(t=>t.includes('中国平安')))throw Error('Missing-reason filter failed');
  await clearAll();
  await open('symbol');await page.click('[data-filter-none]');await apply();await count(0);await clearAll();
  await open('pe');await page.click('[data-filter-sort="desc"]');
  if(await page.locator('#multi-results tbody tr td:first-child').first().innerText()!=='600036.SH')throw Error('Menu sorting failed');
  await open('name');await page.fill('[data-filter-search]','晨光');await page.evaluate(()=>renderMultiSignals());
  if(await page.locator('[data-filter-search]').inputValue()!=='晨光')throw Error('Refresh lost filter draft');
  await apply();await count(1);
  const other='00000000-0000-0000-0000-000000000002';
  await page.route('**/api/multi-screens/'+other,route=>route.fulfill({json:{...result,id:other}}));
  await page.evaluate(id=>watchMulti(id),other);await count(3);
  await page.setViewportSize({width:1100,height:850});await open('peg_like_lt_1');
  const rect=await page.locator('#multi-filter-menu').boundingBox();
  if(rect.x<0||rect.x+rect.width>1100||rect.y<0||rect.y+rect.height>850)throw Error('Filter popup clipped by viewport');
  fs.mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/column-filters.png',fullPage:true});
  if(errors.length)throw Error(errors.join('; '));
  console.log('Verified: 15 column filters, search, checkbox selection, numeric bounds, multi-column AND, missing reasons, false/zero, empty recovery, clear/reset, sorting, refresh persistence, task isolation, popup placement.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
