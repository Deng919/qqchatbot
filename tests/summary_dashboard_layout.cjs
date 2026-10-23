const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const p=await browser.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 const styles=fs.readFileSync('qq_digest/web/templates/inbox_workspace_style.html','utf8');
 const icons=fs.readFileSync('qq_digest/web/templates/action_icons.html','utf8');
 const points=Array.from({length:18},(_,i)=>'<button class="inbox-row"><span class="inbox-row-meta">2026-10-23 · 讨论 · 未读</span><strong class="inbox-row-title">'+i+' 标题与完整内容入口</strong><span class="inbox-row-preview">正文摘要'+'，用于检查多行长文字不会撑高卡片。</span></button>').join('');
 await p.setContent('<style>*{box-sizing:border-box}body{font:14px/1.5 sans-serif;margin:16px}button{font:inherit}</style>'+styles+'<div class="summary-dashboard-grid"><details class="summary-group-stack"><summary class="summary-group-cover"><div class="summary-group-meta"><span class="summary-group-icon">群</span><span id="unread">有 53 条未读</span></div><h2 class="summary-group-title">讨论群</h2><p class="summary-group-preview">群摘要</p><div class="summary-group-footer"><span>10 份摘要 · 53 条要点</span><span class="summary-group-toggle" aria-hidden="true"></span></div></summary><div class="summary-group-content"><div class="summary-group-points">'+points+'</div></div></details></div>'+icons);
 const cover=p.locator('summary'),toggle=p.locator('.summary-group-toggle'),grid=p.locator('.summary-group-points');
 await cover.waitFor();assert.notEqual(await toggle.evaluate(n=>getComputedStyle(n).display),'none','the dashboard disclosure icon must remain visible');
 const iconBox=await toggle.boundingBox(),unreadBox=await p.locator('#unread').boundingBox();assert.ok(iconBox.y>=unreadBox.y+unreadBox.height,'arrow belongs in the footer, away from unread metadata');
 const angle=()=>toggle.evaluate(n=>{const m=new DOMMatrix(getComputedStyle(n,'::before').transform);return Math.round(Math.atan2(m.b,m.a)*180/Math.PI);});
 const collapsed=await toggle.evaluate(n=>getComputedStyle(n,'::before').transform);assert.equal(await angle(),135,'closed card has a downward expansion arrow');await cover.click();await p.waitForFunction(()=>document.querySelector('summary').getAttribute('aria-expanded')==='true');
 assert.equal(await angle(),-45,'open card has an upward collapse arrow');
 await p.mouse.move(0,0);await toggle.hover();assert.equal(await p.getByRole('tooltip').count(),0,'the inline disclosure needs no floating tooltip');
 await cover.focus();assert.equal(await p.getByRole('tooltip').count(),0,'keyboard focus keeps the preview unobstructed');
 assert.equal(await toggle.textContent(),'','the disclosure only shows an arrow');
 for(const width of [1440,1024,768,390,320]){
  await p.setViewportSize({width,height:900});
  const geometry=await grid.evaluate(n=>{const r=n.getBoundingClientRect();return {height:n.clientHeight,scroll:n.scrollHeight,tops:[...new Set([...n.children].filter(c=>{const b=c.getBoundingClientRect();return b.top>=r.top&&b.top<r.bottom;}).map(c=>Math.round(c.getBoundingClientRect().top)))],overflow:getComputedStyle(n).overflowY};});
  assert.equal(geometry.tops.length,2,'only two rows visible at '+width);assert.ok(geometry.scroll>geometry.height,'remaining points stay available by scrolling');assert.equal(geometry.overflow,'auto');
  await grid.locator('button').first().focus();await grid.locator('button').last().focus();assert.ok(await grid.evaluate(n=>n.scrollTop)>0,'keyboard focus reaches remaining points at '+width);await grid.evaluate(n=>n.scrollTop=0);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
 await cover.focus();await p.keyboard.press('Enter');await p.waitForFunction(()=>document.querySelector('summary').getAttribute('aria-expanded')==='false');assert.equal(await toggle.evaluate(n=>getComputedStyle(n,'::before').transform),collapsed);assert.deepEqual(errors,[]);
 console.log('Dashboard two-row viewport, scroll/keyboard access, arrow-only disclosure and no covering tooltip verified');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
