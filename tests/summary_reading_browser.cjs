const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
const read=name=>fs.readFileSync('qq_digest/web/templates/'+name,'utf8');
const reports=read('reports.html'),main=reports.match(/<script>([\s\S]*?)<\/script>/)[1];
const content=reports.split('{% block content %}')[1].split('<style>')[0].replace('{% include "summary_reading_controls.html" %}',read('summary_reading_controls.html')).replace(/{%[\s\S]*?%}/g,'');
const css=reports.match(/<style>[\s\S]*?<\/style>/)[0]+read('summary_reading_style.html')+read('inbox_workspace_style.html')+read('action_icons.html');
const item=i=>({key:'p'+i,text:'讨论 '+i+'：完整要点正文',group_id:11,group_name:'研发群',section:'讨论',report_id:5,report_kind:'daily',date_label:'2026-10-23',read:false,status:'legacy',source_ids:[]});
const group={group_id:11,group_name:'研发群',total:90,unread:90,report_count:1,preview:[]};
const otherGroup={group_id:22,group_name:'另一个群',total:1,unread:1,report_count:1,preview:[]};
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://reading.test/**',r=>r.fulfill({contentType:'text/html',body:'<body class="workspace-app workspace-reading"><style>*{box-sizing:border-box}body{margin:0}[hidden]{display:none!important}</style>'+css+'<main id="main-content"><header class="page-toolbar" style="height:80px"></header><div class="content">'+content+'</div></main></body>'}));
 await page.goto('http://reading.test/reports');
 await page.evaluate(()=>{window.requests=[];window.api=(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));window.appendBookmarkAction=()=>{};window.recentWeekDates=()=>({start:'2026-10-22',end:'2026-10-23'});window.refreshDateHints=()=>{};window.formatNumber=String;window.featureFlags={report_qa:false,report_revisions:false};});
 await page.evaluate(main);await page.evaluate(read('summary_reading_script.html').replace(/<\/?script>/g,'').replace('  summaryInit();',''));await page.evaluate(read('inbox_reading_script.html').replace(/<\/?script>/g,''));
 await page.evaluate(({group,otherGroup})=>{document.getElementById('report-from').value='2026-10-23';document.getElementById('report-to').value='2026-10-23';inboxRenderDashboard({total:2,point_total:91,unread:91,items:[group,otherGroup]});},{group,otherGroup});
 assert.equal(await page.locator('.summary-group-toggle').first().textContent(),'');
 assert.equal(await page.locator('.summary-group-toggle').first().getAttribute('aria-hidden'),'true');
 await page.locator('.summary-group-cover').first().hover();assert.equal(await page.getByRole('tooltip').count(),0);
 await page.locator('.summary-group-cover').first().click();
 await page.waitForFunction(()=>document.querySelector('.summary-group-cover').getAttribute('aria-expanded')==='true');
 assert.equal(await page.locator('.summary-group-toggle').first().textContent(),'');
 async function resolveRequest(value){await page.waitForFunction(()=>requests.length>0);return page.evaluate(value=>{const request=requests.shift();request.resolve(value);return request.url;},value);}
 async function pages(saved){for(let pageNumber=1;pageNumber<=3;pageNumber++){const start=(pageNumber-1)*30,items=Array.from({length:30},(_,i)=>({...item(start+i),read:(saved&&start+i===0)||start+i===1}));await resolveRequest({items,total:90,page:pageNumber,page_size:30});}}
 await pages(false);await page.waitForFunction(()=>document.querySelector('.summary-group-points').children.length===90);
 await page.locator('.summary-group-points').evaluate(n=>n.scrollTop=380);
 await page.locator('.inbox-list-column').evaluate(n=>n.scrollTop=180);assert.equal(await page.locator('.inbox-list-column').evaluate(n=>n.scrollTop),180);
 await page.evaluate(()=>{inboxSelectPoint('p0',true);document.getElementById('inbox-detail-actions').lastElementChild.click();inboxBackToList();inboxSelectPoint('p1',true);});
 await page.evaluate(()=>summarySave());assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem(summaryStorageKey)).scroll),180,'saving while a point is open retains the hidden outer list position');
 await resolveRequest({});const url=await resolveRequest({grouped:true,total:2,point_total:91,unread:90,page:1,page_size:30,items:[{...group,unread:89},otherGroup]});assert.match(url,/grouped=true/);await pages(true);
 await page.waitForFunction(()=>document.querySelector('.summary-group-points').children.length===90);assert.equal(await page.evaluate(()=>inboxSelectedKey),'p1');
 await page.locator('#inbox-back').click();
 assert.equal(await page.locator('.summary-group-points').evaluate(n=>n.scrollTop),380,'return after a hidden dashboard refresh restores the original inner reading position');
 assert.equal(await page.locator('.inbox-list-column').evaluate(n=>n.scrollTop),180,'return after hidden dashboard refresh restores the outer list position');
 assert.match(await page.locator('.summary-group-meta').first().textContent(),/89/);assert.deepEqual(errors,[]);
 await page.evaluate(()=>{inboxSelectPoint('p0',true);document.getElementById('inbox-detail-actions').lastElementChild.click();inboxBackToList();});
 await page.locator('.summary-group-points').evaluate(n=>n.scrollTop=760);
 await resolveRequest({});await page.locator('.inbox-list-column').evaluate(n=>n.scrollTop=300);await resolveRequest({grouped:true,total:2,point_total:91,unread:91,page:1,page_size:30,items:[group,otherGroup]});await pages(false);
 await page.waitForFunction(()=>document.querySelector('.summary-group-points').children.length===90);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(await page.locator('.summary-group-points').evaluate(n=>n.scrollTop),760,'a save completing after the user returns must retain their latest scrolling position');
 assert.equal(await page.locator('.inbox-list-column').evaluate(n=>n.scrollTop),300,'background refresh keeps scrolling performed while the response was pending');
 await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{window.scrollTo(0,300);inboxSelectPoint('p2',true);});await page.locator('#inbox-back').click();assert.equal(await page.evaluate(()=>window.scrollY),300,'mobile back retains document reading position');
 for(const width of [1440,1000,390])for(const count of [1,18]){
  await page.setViewportSize({width,height:900});
  await page.evaluate(count=>{
   window.requests=[];closeReport();inboxClearSelection();summaryView='groups';summarySelectGroups(['11']);
   document.getElementById('report-from').value='2026-10-23';document.getElementById('report-to').value='2026-10-23';
   summaryApplyView();summaryRenderGroups({total:count,page:1,page_size:30,reports:Array.from({length:count},(_,i)=>({report_id:i+1,report_kind:'daily',group_name:'研发群',window_start_date:'2026-10-23',window_end_date:'2026-10-23',created_at:'2026-10-23T12:00:00Z',preview:{available:true,overview:'概览正文。'.repeat(20),points:['讨论主题：要点正文。'.repeat(8)]}}))});
   if(count>1)summaryScrollTo(260);
  },count);
  const opener=page.locator('.summary-item-footer button').nth(count>1?2:0);
  await opener.click();const scroll=await page.evaluate(()=>summaryBrowseScroll);
  await resolveRequest({group_id:11,group_name:'研发群',current_version:1,window_start_date:'2026-10-23',window_end_date:'2026-10-23',markdown:'# 完整摘要\n\n正文内容。\n'.repeat(25)});
  await page.locator('#report-content h2').first().waitFor();
  assert.equal(await page.locator('.inbox-list-column').isVisible(),false,'Full report hides duplicate preview at '+width+'px with '+count+' reports');
  const sizes=await page.evaluate(()=>({workspace:document.getElementById('inbox-workspace').getBoundingClientRect().width,reading:document.querySelector('.inbox-reading-column').getBoundingClientRect().width}));
  assert.ok(Math.abs(sizes.workspace-sizes.reading)<2,'Full report uses the whole reading area');
  assert.equal(await page.locator('#report-group').inputValue(),'11');assert.equal(await page.locator('#report-from').inputValue(),'2026-10-23');
  if(process.env.QA_DIR)await page.screenshot({path:process.env.QA_DIR+'/full-report-'+width+'-'+count+'.png'});
  await page.locator('#report-detail .card-header button').click();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('.inbox-list-column').isVisible(),true,'Back restores the original report list');
  assert.equal(await page.evaluate(()=>summaryScrollPosition()),scroll,'Back restores the list position');
  assert.equal(await page.evaluate(()=>document.activeElement.textContent),'阅读完整摘要','Back restores keyboard focus');
  assert.equal(await page.locator('#report-group').inputValue(),'11');assert.equal(await page.locator('#report-to').inputValue(),'2026-10-23');
 }
 assert.deepEqual(errors,[]);
 console.log('Dashboard delayed saves and scroll; single/multiple full reports use all space at desktop/tablet/mobile widths, preserving return, focus and filters');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
