const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('fs');
const root='qq_digest/web/templates/',read=name=>fs.existsSync(root+name)?fs.readFileSync(root+name,'utf8'):'';
const styles=read('base.html').match(/<style>([\s\S]*?)<\/style>/)[0]+read('inbox_workspace_style.html');
const stateStyles=['first_use.html','failures.html','collect.html','group_selector.html','ai_usage_settings.html'].map(name=>read(name).match(/<style>([\s\S]*?)<\/style>/)[0]).join('');
function contrast(a,b){function l(s){return s.match(/[\d.]+/g).slice(0,3).map(v=>{v=Number(v)/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);}const values=[l(a),l(b)].sort((x,y)=>x-y);return (values[1]+.05)/(values[0]+.05);}
const viewerStyles=read('base.html').match(/<style id="image-viewer-style">([\s\S]*?)<\/style>/)[0];
const accentMarkup='<a class="brand" href="/">群聊简报</a><a class="extension-manage" href="/features">功能开关</a><a class="inbox-group-button" aria-pressed="true">全部群聊</a><div class="summary-date-shortcuts"><button class="btn active">今天</button></div><div class="summary-view-tabs"><button class="btn active">摘要仪表盘</button></div><span class="summary-group-icon">群</span><span class="summary-group-toggle">⌄</span><button class="inbox-row is-unread" aria-pressed="true"><span class="inbox-row-meta">未读</span></button><div id="action-icon-tooltip" popover="manual">操作提示</div>';
const markup='<!doctype html><html><head><meta charset="utf-8">'+styles+read('appearance.html')+read('action_icons.html').match(/<style>([\s\S]*?)<\/style>/)[0]+stateStyles+viewerStyles+'<script>window.firstTheme=document.documentElement.dataset.theme;</script></head><body class="workspace-app"><header class="global-header"><a class="inbox-manage-groups" href="/groups">＋ 管理群聊</a><a class="inbox-sidebar-footer" href="/overview">运行记录 ↗</a></header><main id="main-content">'+read('appearance_control.html')+'<div class="card">正文<a href="/source">原消息</a><input class="input" value="输入"><button class="btn btn-primary" data-action-mode="label">保存</button></div><div class="setup-steps"><button class="btn" aria-current="step">步骤</button></div><div class="is-recovered"><span class="failure-badge">已恢复</span></div><div class="inspection-days"><a href="/day">补采日期</a></div><p class="inspection-error">异常</p><p class="group-selector-error">群聊加载失败</p><div class="usage-summary"><p class="usage-alert">已达上限</p></div><div class="status-line">状态</div><div class="msg-item">原消息</div><label class="toggle"><input type="checkbox"><span class="slider"></span></label><button class="inbox-row"><span class="inbox-row-meta">日期与来源</span></button>'+accentMarkup+'</main><dialog id="image-viewer"><div class="image-viewer-surface"><p role="status">图片暂时无法显示</p></div><button class="image-viewer-close" type="button">×</button></dialog></body></html>';
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const context=await browser.newContext({colorScheme:'light'});
  await context.route('http://theme.test/**',r=>r.fulfill({contentType:'text/html',body:markup}));
  const page=await context.newPage();
  await page.addInitScript(()=>{if(!sessionStorage.getItem('seeded')){localStorage.setItem('qq-digest-theme','dark');sessionStorage.setItem('seeded','1');}});
  await page.goto('http://theme.test/');
  assert.equal(await page.evaluate(()=>window.firstTheme),'dark','Saved dark preference applies before body paint');
  assert.equal(await page.locator('[data-theme-choice]:checked').inputValue(),'dark');
  assert.equal(await page.locator('.global-header [data-theme-choice]').count(),0);
  await page.locator('[data-theme-choice][value=dark]').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('[data-theme-choice]:checked').inputValue(),'system','native radio keyboard navigation applies the theme');await page.locator('[data-theme-choice][value=dark]').check();
  await page.waitForFunction(()=>{const rgb=getComputedStyle(document.querySelector('.summary-date-shortcuts .active')).color.match(/\d+/g).slice(0,3);return rgb.every(v=>v===rgb[0]);});
  const colors=await page.evaluate(()=>({bg:getComputedStyle(document.body).backgroundColor,text:getComputedStyle(document.body).color}));
  assert.notEqual(colors.bg,'rgb(255, 255, 255)');assert.notEqual(colors.bg,colors.text);
  const accentColors=await page.evaluate(()=>{
   const colors=[];
   for(const [selector,pseudo,properties] of [
    ['.brand','::before',['backgroundColor','boxShadow']],
    ['.extension-manage',null,['color']],['.inbox-group-button','::before',['color']],
    ['.summary-date-shortcuts .active',null,['color','backgroundColor','borderColor']],
    ['.summary-view-tabs .active',null,['color','backgroundColor','borderColor']],
    ['.summary-group-icon',null,['color','backgroundColor']],['.summary-group-toggle',null,['color']],
    ['.inbox-row[aria-pressed]',null,['boxShadow']],['.is-unread .inbox-row-meta','::before',['backgroundColor']],
    ['.btn-primary',null,['color','backgroundColor','borderColor']],
    ['#action-icon-tooltip',null,['color','backgroundColor','boxShadow']]
   ]) { const style=getComputedStyle(document.querySelector(selector),pseudo);for(const property of properties) colors.push({selector,pseudo,property,value:style[property]}); }
   return colors;
  });
  for(const sample of accentColors) for(const rgb of sample.value.matchAll(/rgba?\((\d+), (\d+), (\d+)/g))assert.ok(rgb[1]===rgb[2]&&rgb[2]===rgb[3],JSON.stringify(sample)+' must use neutral gray in night mode');
  await page.locator('.input').click();assert.equal(await page.locator('.input').evaluate(n=>getComputedStyle(n).outlineColor),'rgb(188, 188, 188)','Mouse-focused fields also use neutral outlines');
  assert.equal(await page.locator('button.btn-primary').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(60, 60, 60)','Labelled primary buttons retain a readable dark fill');
  assert.equal(await page.locator('.setup-steps button').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(60, 60, 60)');
  assert.equal(await page.locator('.is-recovered .failure-badge').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(42, 51, 45)');
  assert.equal(await page.locator('.inspection-days a').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(48, 48, 48)');
  assert.equal(await page.locator('.group-selector-error').evaluate(n=>getComputedStyle(n).color),'rgb(210, 155, 155)');
  assert.equal(await page.locator('.usage-alert').evaluate(n=>getComputedStyle(n).color),'rgb(213, 180, 127)');
  assert.equal(await page.locator('.status-line').evaluate(n=>getComputedStyle(n).borderColor),'rgb(59, 59, 59)');
  assert.equal(await page.locator('.msg-item').evaluate(n=>getComputedStyle(n).borderBottomColor),'rgb(59, 59, 59)');
  assert.equal(await page.locator('.toggle .slider').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(48, 48, 48)');
  await page.locator('.toggle input').focus();await page.keyboard.press('Space');assert.equal(await page.locator('.toggle .slider').evaluate(n=>getComputedStyle(n).outlineColor),'rgb(188, 188, 188)');
  await page.locator('button.btn-primary').hover();await page.waitForFunction(()=>getComputedStyle(document.querySelector('button.btn-primary')).backgroundColor==='rgb(71, 71, 71)');
  const primary=await page.locator('button.btn-primary').evaluate(n=>({fg:getComputedStyle(n).color,bg:getComputedStyle(n).backgroundColor}));assert.ok(contrast(primary.fg,primary.bg)>=4.5,'Primary hover label stays readable');
  await page.locator('.inbox-row').first().hover();const metadata=await page.locator('.inbox-row-meta').first().evaluate(n=>({fg:getComputedStyle(n).color,bg:getComputedStyle(n.parentElement).backgroundColor}));assert.ok(contrast(metadata.fg,metadata.bg)>=4.5,'Hovered row metadata stays readable');
  await page.evaluate(()=>document.getElementById('image-viewer').showModal());
  assert.equal(await page.locator('#image-viewer').evaluate(n=>getComputedStyle(n,'::backdrop').backgroundColor),'rgba(0, 0, 0, 0.86)');
  assert.equal(await page.locator('.image-viewer-close').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(39, 39, 39)');
  await page.locator('.image-viewer-close').focus();assert.equal(await page.locator('.image-viewer-close').evaluate(n=>getComputedStyle(n).outlineColor),'rgb(188, 188, 188)');
  await page.evaluate(()=>document.getElementById('image-viewer').close());
  for(const selector of ['.inbox-manage-groups','.inbox-sidebar-footer','main .card > a']){
   await page.locator(selector).hover();assert.equal(await page.locator(selector).evaluate(n=>getComputedStyle(n).textDecorationLine),'none');
  }
  await page.locator('[data-theme-choice][value=light]').check();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  await page.reload();assert.equal(await page.locator('[data-theme-choice]:checked').inputValue(),'light');
  await page.locator('[data-theme-choice][value=system]').check();
  await page.emulateMedia({colorScheme:'dark'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
  await page.locator('[data-theme-choice][value=light]').check();await page.emulateMedia({colorScheme:'light'});await page.emulateMedia({colorScheme:'dark'});
  assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  const other=await context.newPage();await other.goto('http://theme.test/other');await other.locator('[data-theme-choice][value=dark]').check();
  await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
  await page.locator('[data-theme-choice][value=light]').check();await other.evaluate(()=>localStorage.removeItem('qq-digest-theme'));await page.waitForFunction(()=>document.querySelector('[data-theme-choice]:checked').value==='dark');
  await page.evaluate(()=>localStorage.setItem('qq-digest-theme','invalid'));await page.reload();
  assert.equal(await page.locator('[data-theme-choice]:checked').inputValue(),'dark');
  const loginStyles=read('login.html').match(/<style>([\s\S]*?)<\/style>/)[0];
  await context.route('http://theme.test/login',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><html><head>'+loginStyles+read('appearance.html')+'</head><body class="login-shell"><header class="login-header">群聊简报</header><main class="login-main"><form class="login-panel"><input id="password"><button>登录</button></form></main></body></html>'}));
  await page.goto('http://theme.test/login');
  assert.equal(await page.locator('.login-header').evaluate(n=>getComputedStyle(n,'::before').backgroundColor),'rgb(188, 188, 188)');
  assert.equal(await page.locator('button').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(60, 60, 60)');
  await page.locator('button').hover();await page.waitForFunction(()=>getComputedStyle(document.querySelector('button')).backgroundColor==='rgb(71, 71, 71)');
  await page.locator('#password').click();assert.equal(await page.locator('#password').evaluate(n=>getComputedStyle(n).outlineColor),'rgb(188, 188, 188)');
  const fresh=await browser.newContext({colorScheme:'light'});await fresh.route('http://theme.test/**',r=>r.fulfill({contentType:'text/html',body:markup}));const freshPage=await fresh.newPage();await freshPage.goto('http://theme.test/');assert.equal(await freshPage.locator('[data-theme-choice]:checked').inputValue(),'dark');assert.equal(await freshPage.locator('html').getAttribute('data-theme'),'dark');await fresh.close();
  const blocked=await browser.newContext({colorScheme:'dark'});await blocked.route('http://theme.test/**',r=>r.fulfill({contentType:'text/html',body:markup}));
  const blockedPage=await blocked.newPage();await blockedPage.addInitScript(()=>Object.defineProperty(window,'localStorage',{get(){throw Error('blocked storage');}}));
  await blockedPage.goto('http://theme.test/');assert.equal(await blockedPage.locator('html').getAttribute('data-theme'),'dark');
  await blockedPage.locator('[data-theme-choice][value=light]').check();assert.equal(await blockedPage.locator('html').getAttribute('data-theme'),'light');
  await blocked.close();
  console.log('Theme first paint, persistence, system changes, cross-tab sync, storage failure and unlined links verified');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
