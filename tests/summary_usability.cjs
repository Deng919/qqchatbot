const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
const read=name=>fs.readFileSync('qq_digest/web/templates/'+name,'utf8');
const reports=read('reports.html'),main=reports.match(/<script>([\s\S]*?)<\/script>/)[1];
const content=reports.split('{% block content %}')[1].split('<style>')[0].replace('{% include "summary_reading_controls.html" %}',read('summary_reading_controls.html')).replace(/{%[\s\S]*?%}/g,'');
const dashboard=read('dashboard.html'),coverage=dashboard.slice(dashboard.indexOf('<section id="daily-coverage"'),dashboard.indexOf('<div class="dashboard-grid"'));
const coverageScript=dashboard.slice(dashboard.indexOf('  function renderDailyCoverage('),dashboard.indexOf('  function loadStats('));
(async()=>{const browser=await chromium.launch({headless:true}),failures=[];try{
 const page=await browser.newPage({viewport:{width:390,height:844}});
 await page.route('http://usability.test/**',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><body><style>[hidden]{display:none!important}</style><button id="range-open-btn">生成摘要</button>'+coverage+content+read('group_selector.html')+'</body>'}));
 await page.goto('http://usability.test/reports');
 await page.evaluate(()=>{window.api=()=>new Promise(()=>{});window.refreshDateHints=()=>{};window.formatNumber=String;window.toast=()=>{};window.featureFlags={report_qa:false,report_revisions:false};});
 await page.evaluate(main);
 for(const file of ['summary_reading_script.html','summary_generation_script.html'])await page.evaluate(read(file).replace(/<\/?script>/g,'').replace('  summaryInit();',''));
 await page.addScriptTag({content:coverageScript});
 await page.evaluate(()=>{window.loadReports=()=>{};window.recentWeekDates=()=>({start:'2026-10-20',end:'2026-10-26'});document.getElementById('report-group').appendChild(Object.assign(document.createElement('option'),{value:'123',textContent:'测试群'}));});
 async function check(name,fn){try{await fn();console.log('PASS '+name);}catch(error){failures.push(name+': '+error.message);console.error('FAIL '+name+': '+error.message);}}
 await check('coverage link opens its report date despite prior reading filters',async()=>{
  await page.evaluate(()=>renderDailyCoverage({report_date:'2026-10-26',status:'normal',participating_groups:1,report_groups:1,failed_groups:0,no_message_groups:0}));
  const href=await page.locator('#daily-coverage a').getAttribute('href'),params=new URL(href,'http://usability.test').searchParams;
  assert.equal(params.get('date_from'),'2026-10-26');assert.equal(params.get('date_to'),'2026-10-26');assert.equal(params.get('view'),'groups');assert.equal(params.get('read'),'all');assert.equal(params.get('report_kind'),'all');assert.equal(params.get('group_id'),'');
  const opened=await page.evaluate(async href=>{sessionStorage.setItem(summaryStorageKey,JSON.stringify({view:'points',group:'123',from:'2026-10-22',to:'2026-10-24',read:'unread',kind:'daily'}));history.replaceState(null,'',href);window.api=async(method,url)=>url.startsWith('/api/groups')?{groups:[{group_id:123,name:'测试群'}]}:{previous_viewed_at:null};await summaryInit();const values=summaryValues();window.api=()=>new Promise(()=>{});return values;},href);
  assert.equal(opened.from,'2026-10-26');assert.equal(opened.to,'2026-10-26');assert.equal(opened.group,'');assert.equal(opened.view,'groups');assert.equal(opened.read,'all');assert.equal(opened.kind,'all');
 });
 await check('history changes dates only and clear filters resets all scope',async()=>{
  const state=await page.evaluate(()=>{summarySelectGroups(['123']);document.getElementById('report-from').value='2026-10-26';document.getElementById('report-to').value='2026-10-26';document.getElementById('summary-read-filter').value='unread';document.getElementById('report-kind').value='daily';summaryAllDates();const history=summaryValues();clearReportFilters();return {history,cleared:summaryValues()};});
  assert.equal(state.history.group,'123');assert.equal(state.history.read,'unread');assert.equal(state.history.kind,'daily');assert.equal(state.history.from,'');assert.equal(state.history.to,'');assert.equal(state.cleared.group,'');assert.equal(state.cleared.read,'all');assert.equal(state.cleared.kind,'all');
 });
 await check('pristine generation follows current scope; edited and running scopes stay',async()=>{
  const state=await page.evaluate(()=>{summarySelectGroups(['123']);document.getElementById('report-from').value='2026-10-26';document.getElementById('report-to').value='2026-10-26';rangeGroups=[{group_id:123,name:'测试群',enabled:true},{group_id:789,name:'其他群',enabled:true}];rangeGroupsLoaded=true;openRangeSummary();renderRangeGroups();closeRangeSummary();document.getElementById('report-from').value='2026-10-20';summarySelectGroups(['789']);openRangeSummary();const pristine=rangePayload();setRangeDates('today');closeRangeSummary();document.getElementById('report-from').value='2026-10-21';openRangeSummary();const edited=rangePayload();closeRangeSummary();rangeFormDirty=false;summaryGenerationBusy=true;document.getElementById('report-from').value='2026-10-22';summarySelectGroups(['123']);openRangeSummary();const running=rangePayload();closeRangeSummary();summaryGenerationBusy=false;clearTimeout(rangePreviewTimer);return {pristine,edited,running};});
  assert.deepEqual(state.pristine,{group_ids:[789],start_date:'2026-10-20',end_date:'2026-10-26'});assert.deepEqual(state.edited,{group_ids:[789],start_date:'2026-10-26',end_date:'2026-10-26'});assert.deepEqual(state.running,state.edited);
 });
 await check('collapsed filters show scope and existing archive does not imply setup missing',async()=>{
  await page.evaluate(()=>{summaryView='points';summarySelectGroups(['123']);document.getElementById('report-from').value='2026-10-22';document.getElementById('report-to').value='2026-10-24';summaryApplyView();const host=document.getElementById('summary-points');host.textContent='';summaryEmpty(host,false);});
  const label=await page.locator('.inbox-filter-details > summary').innerText();assert.match(label,/测试群/);assert.match(label,/2026-10-22/);assert.match(label,/2026-10-24/);assert.equal(await page.getByRole('heading',{name:'当前范围暂无摘要',exact:true}).count(),1);assert.equal(await page.locator('#summary-points').getByRole('link',{name:'首次设置'}).count(),0);
 });
 await check('editing either date keeps the range ordered before applying it',async()=>{
  for(const view of ['points','groups']){
   await page.evaluate(view=>{summaryView=view;document.getElementById('report-from').value='2026-10-22';document.getElementById('report-to').value='2026-10-24';document.querySelector('.inbox-filter-details').open=true;window.dateRequests=[];window.dateHintRefreshes=0;window.refreshDateHints=()=>dateHintRefreshes++;window.loadReports=()=>dateRequests.push({from:summaryValues().from,to:summaryValues().to});},view);
   await page.locator('#report-from').fill('2026-10-26');await page.locator('#report-from').blur();
   assert.deepEqual(await page.evaluate(()=>({requests:dateRequests,hints:dateHintRefreshes})),{requests:[{from:'2026-10-26',to:'2026-10-26'}],hints:1},view+' applies only the aligned start range');
   await page.evaluate(()=>{dateRequests=[];dateHintRefreshes=0;});
   await page.locator('#report-to').fill('2026-10-20');await page.locator('#report-to').blur();
   assert.deepEqual(await page.evaluate(()=>({requests:dateRequests,hints:dateHintRefreshes})),{requests:[{from:'2026-10-20',to:'2026-10-20'}],hints:1},view+' applies only the aligned end range');
  }
 });
 if(failures.length)throw new Error(failures.join('\n'));
 console.log('Summary contextual navigation, history, generation scope and collapsed reading conditions verified');
}finally{await browser.close();}})().catch(error=>{console.error(error.message);process.exitCode=1;});
