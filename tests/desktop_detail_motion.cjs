const {chromium}=require('playwright'),assert=require('node:assert/strict');
const url=process.argv[2],only=process.argv[3];
const capture=async(page,name)=>{if(process.env.MOTION_QA_DIR)await page.screenshot({path:require('node:path').join(process.env.MOTION_QA_DIR,name),fullPage:false});};
if(!url)throw new Error('Usage: node tests/desktop_detail_motion.cjs <isolated-test-url> [sidebar|deck|category]');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const context=await browser.newContext({viewport:{width:1440,height:950}}),errors=[];
 await context.request.post(url+'/login',{form:{password:'navigation-test-password'}});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(6000);
 await page.goto(url+'/desktop-workspace');await page.locator('iframe[aria-hidden=false]').waitFor();
 const active=async()=>await (await page.locator('iframe[aria-hidden=false]').elementHandle()).contentFrame();
 const reports=await active();await reports.waitForFunction(()=>typeof inboxRenderDashboard==='function');await page.waitForTimeout(500);
 if(!only||only==='sidebar'){
  const originalGroupScroll=await reports.evaluate(()=>{inboxRenderGroups(Array.from({length:24},(_,i)=>({group_id:800+i,name:'关注测试群 '+i})));const list=document.getElementById('inbox-group-list');list.scrollTop=90;return list.scrollTop;});
  await page.evaluate(()=>QQDesktopNavigation.navigate('/search',{reuse:true}));
  const search=await active(),outgoing=search.locator('.inbox-sidebar-groups');
  assert.equal(await outgoing.count(),1,'the outgoing followed-groups section remains visible for its exit animation');
  const geometry=await outgoing.evaluate(n=>{const list=n.querySelector('[data-desktop-group-list]');return {listHeight:list.clientHeight,sectionHeight:n.clientHeight,scroll:list.scrollTop};});
  assert.ok(geometry.listHeight<=geometry.sectionHeight&&Math.abs(geometry.scroll-originalGroupScroll)<1,'exit retains the bounded group list and its scroll position: '+JSON.stringify(geometry));
  await page.waitForTimeout(65);const opacity=await outgoing.evaluate(n=>Number(getComputedStyle(n).opacity));
  assert.ok(opacity>0&&opacity<1,'outgoing groups fade progressively');
  await capture(page,'sidebar-exit.png');
  await page.waitForTimeout(350);assert.equal(await outgoing.count(),0,'exit copy is removed');
  await page.evaluate(()=>QQDesktopNavigation.navigate('/reports',{reuse:true}));await page.waitForTimeout(50);
  const incoming=await reports.locator('.inbox-sidebar-groups').evaluate(n=>Number(getComputedStyle(n).opacity));
  assert.ok(incoming>0&&incoming<1,'returning groups enter progressively');
  const reversal=await reports.evaluate(async()=>{const before=document.querySelector('.inbox-sidebar-groups').getBoundingClientRect();await parent.QQDesktopNavigation.navigate('/search',{reuse:true});const ghost=parent.document.querySelector('iframe[aria-hidden=false]').contentDocument.querySelector('[data-desktop-ghost]').getBoundingClientRect();return {before:before.top,after:ghost.top};});
  assert.ok(Math.abs(reversal.before-reversal.after)<.5,'interrupting the group entrance preserves its displayed position');
  // Interrupt both directions; no stale exit copy or final opacity remains.
  await page.evaluate(async()=>{await QQDesktopNavigation.navigate('/search',{reuse:true});await QQDesktopNavigation.navigate('/reports',{reuse:true});});
  await page.waitForTimeout(500);assert.equal(await reports.locator('.inbox-sidebar-groups').count(),1);
  assert.equal(await reports.locator('.inbox-sidebar-groups').evaluate(n=>getComputedStyle(n).opacity),'1');
 }
 if(!only||only==='deck'){
  await reports.evaluate(()=>{
   summaryCancelDetail();summaryView='points';summaryApplyView();summaryExpanded={};const original=api;
   api=function(method,url,payload){if(method==='GET'&&url.startsWith('/api/summary-reading?')){const id=new URLSearchParams(url.split('?')[1]).get('group_id');return Promise.resolve({page:1,total:6,page_size:30,items:Array.from({length:6},(_,i)=>({key:'motion-'+id+'-'+i,group_id:Number(id),group_name:'动画测试群',text:'讨论标题：用于检验卡片向下展开的正文。',section:'讨论',report_kind:'daily',report_id:1,report_date:'2026-10-30',date_label:'2026-10-30',read:true,status:'legacy',source_ids:[]}))});}return original(method,url,payload);};
   window.motionDashboardData={total:6,point_total:36,unread:0,items:Array.from({length:6},(_,i)=>({group_id:700+i,group_name:'动画测试群 '+(i+1),total:6,unread:0,report_count:1,preview:['卡片应保持原来的列和顶部位置。']}))};inboxRenderDashboard(motionDashboardData);
  });
  // Measure cover anchoring after the independent result/card entrance transitions settles.
  await reports.evaluate(async()=>{await new Promise(requestAnimationFrame);await Promise.all(document.getElementById('summary-points').getAnimations({subtree:true}).map(animation=>animation.finished.catch(()=>{})));});
  for(const index of [1,2]){
   const card=reports.locator('.summary-group-stack').nth(index),cover=card.locator('summary'),before=await cover.boundingBox();
   await cover.click();await page.waitForTimeout(35);const after=await cover.boundingBox();
   assert.ok(Math.abs(after.x-before.x)<2&&Math.abs(after.y-before.y)<2,'card '+(index+1)+' expands below its original cover without moving to another row: '+JSON.stringify({before,after}));
   const drawer=reports.locator('.summary-deck-drawer');await drawer.waitFor();
   assert.ok(await reports.locator('.summary-deck-flight').count()>=3,'notes visibly pull out of the selected stack');
   await page.waitForTimeout(780);const positions=await drawer.locator('.inbox-row').evaluateAll(nodes=>nodes.slice(0,3).map(node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width};}));
   assert.equal(positions.length,3);assert.ok(positions[0].x<positions[1].x&&positions[1].x<positions[2].x,'notes spread across the full next row');
   assert.ok(positions.every(box=>box.y>=before.y+before.height),'notes land below the cover row');assert.ok(Math.max(...positions.map(box=>box.y))-Math.min(...positions.map(box=>box.y))<2,'notes share the same full row');
   if(index===1)await capture(page,'desktop-expanded-card.png');
   await cover.click();await page.waitForTimeout(480);assert.equal(await drawer.count(),0,'collapse removes the full-width row');assert.equal(await reports.locator('.summary-deck-flight').count(),0,'returning note copies are cleaned up');
  }
  await reports.evaluate(()=>{const covers=document.querySelectorAll('.summary-group-cover');covers[1].click();covers[2].click();covers[1].click();covers[2].click();});await page.waitForTimeout(100);
  assert.equal(await reports.locator('.summary-group-stack[open]').count(),1,'rapid switching keeps one active stack');assert.equal(await reports.locator('.summary-group-stack[open]').getAttribute('data-group-id'),'702');assert.equal(await reports.locator('.summary-deck-drawer').count(),1);
  assert.equal(await reports.locator('.summary-deck-drawer .inbox-row').first().evaluate(n=>getComputedStyle(n).opacity),'0','canceled earlier copies do not reveal the new moving note too soon');
  await page.waitForTimeout(800);await reports.locator('.summary-dashboard-overview').click();await page.waitForTimeout(480);assert.equal(await reports.locator('.summary-deck-drawer').count(),0,'blank dashboard area returns the notes');
  await reports.locator('.summary-group-cover').nth(2).click();await page.waitForTimeout(100);await page.keyboard.press('Escape');await page.waitForTimeout(480);assert.equal(await reports.locator('.summary-deck-drawer').count(),0,'Escape returns the notes');assert.equal(await reports.locator('.summary-group-cover').nth(2).evaluate(n=>n===document.activeElement),true);
  await reports.locator('.summary-group-cover').nth(2).click();await reports.evaluate(()=>inboxFilterPage('动画测试群 1'));assert.equal(await reports.locator('.summary-deck-drawer').count(),0,'filtering away the source cannot leave an orphan drawer');await reports.evaluate(()=>inboxFilterPage(''));
  await reports.evaluate(()=>{summaryExpanded={'group:700':true,'group:701':true};inboxRenderDashboard(motionDashboardData);});
  assert.equal(await reports.locator('.summary-group-stack[open]').getAttribute('data-group-id'),'701');
  await reports.locator('.summary-group-cover').nth(1).click();await reports.evaluate(()=>inboxRenderDashboard(motionDashboardData));
  assert.equal(await reports.locator('.summary-deck-drawer').count(),0,'closing a restored group clears legacy expansion flags');
  await reports.locator('.summary-group-cover').nth(2).click();await page.waitForTimeout(850);await page.setViewportSize({width:1100,height:950});await page.waitForTimeout(150);
  const row=await reports.evaluate(()=>{const cards=document.querySelectorAll('.summary-group-stack'),drawer=document.querySelector('.summary-deck-drawer');return {third:cards[2].offsetTop,fourth:cards[3].offsetTop,after:drawer.previousElementSibling.dataset.groupId};});
  assert.equal(row.third,row.fourth,'resize preserves the natural source cover row');assert.equal(row.after,'703','drawer follows the complete two-column source row');
  await page.setViewportSize({width:1440,height:950});await reports.locator('.summary-group-cover').nth(2).click();
  await page.emulateMedia({reducedMotion:'reduce'});const cover=reports.locator('.summary-group-cover').nth(2);await cover.focus();await page.keyboard.press('Enter');await page.waitForTimeout(80);
  assert.equal(await reports.locator('.summary-group-stack').nth(2).evaluate(n=>n.open),true,'keyboard disclosure still works');
  const height=await reports.locator('.summary-group-stack').nth(2).evaluate(n=>n.getBoundingClientRect().height);await page.waitForTimeout(80);assert.equal(await reports.locator('.summary-group-stack').nth(2).evaluate(n=>n.getBoundingClientRect().height),height,'reduced motion settles immediately');
  await page.setViewportSize({width:390,height:844});assert.ok(await reports.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'narrow viewport has no horizontal overflow');await reports.locator('.summary-group-cover').nth(2).scrollIntoViewIfNeeded();await capture(page,'mobile-expanded-card.png');await page.setViewportSize({width:1440,height:950});
 }
 if(!only||only==='category'){
  await page.evaluate(()=>QQDesktopNavigation.navigate('/secondhand',{reuse:true}));const market=await active();await market.locator('.market-category.is-active').waitFor();
  assert.equal(await market.locator('.market-category.is-active').evaluate(n=>getComputedStyle(n).boxShadow),'none','selected category has no inset white edge');
  await capture(page,'market-category.png');
 }
 assert.deepEqual(errors,[]);console.log('Sidebar enter/exit, full-row deck spread, interrupt/close/filter, keyboard and category selection verified.');
}finally{await browser.close();}})().catch(error=>{console.error(error.stack);process.exit(1);});
