// Missing decoration, stale accessible labels or clipped tooltips must fail this test.
const { chromium } = require('playwright');
const fs = require('node:fs'), assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1100,height:700}}), errors=[];
    page.on('pageerror', error => errors.push(error.message));
    const fragment='qq_digest/web/templates/action_icons.html';
    await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>.btn{padding:8px;font-size:14px} #clip{overflow:hidden;width:180px;height:90px;margin:0} button{cursor:pointer}</style><div id="clip"><button id="save" type="button" class="btn">收藏</button><button id="read" type="button" class="btn">标为已读</button></div><button class="btn" id="main">生成摘要</button><button class="btn" id="submit" type="submit">删除</button><button class="btn" id="page">下一页</button><dialog><button class="btn" type="button" id="block">屏蔽此人</button></dialog>'+(fs.existsSync(fragment)?fs.readFileSync(fragment,'utf8'):''));
    const save=page.locator('#save');
    await page.waitForTimeout(50);
    assert.equal(await save.getAttribute('aria-label'),'收藏','icon actions need an accessible operation name');
    assert.equal(await save.evaluate(n=>getComputedStyle(n).fontSize),'0px');
    assert.equal(await page.locator('#submit').getAttribute('data-action-icon'),null,'destructive submit retains its wording');
    assert.equal(await page.locator('#main').getAttribute('data-action-mode'),'label','primary action adds an icon while keeping text');
    assert.notEqual(await page.locator('#main').evaluate(n=>getComputedStyle(n).fontSize),'0px');
    for(const label of ['查看详情','查看原消息','查看原消息 1','完整摘要','整理成资料','标为已读','不感兴趣','屏蔽此人','屏蔽原发布人 12345','清除筛选','重试','返回阅读']){
      const button=await page.evaluateHandle(label=>{const b=document.createElement('button');b.type='button';b.className='btn';b.textContent=label;document.body.appendChild(b);return b;},label);
      await page.waitForTimeout(20);assert.equal(await button.evaluate(n=>n.dataset.actionMode),'label',label+' should be understandable without hovering');
      assert.notEqual(await button.evaluate(n=>getComputedStyle(n).fontSize),'0px');await button.dispose();
    }
    await page.evaluate(()=>{const actions=document.createElement('div');actions.id='card-actions';actions.style.cssText='display:flex;flex-wrap:wrap;gap:8px;width:225px';for(const label of ['查看详情','不感兴趣','屏蔽此人']){const b=document.createElement('button');b.className='btn';b.type='button';b.textContent=label;actions.appendChild(b);}document.body.appendChild(actions);});await page.waitForTimeout(30);
    const rows=await page.locator('#card-actions button').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().y));assert.equal(rows[0],rows[1],'Readable card actions should fit two per row without needless vertical bulk');
    await page.evaluate(()=>{const nav=document.createElement('nav');nav.className='global-nav';nav.innerHTML='<a id="nav-bookmarks" href="#bookmarks">收藏</a>';document.body.appendChild(nav);});
    await page.waitForFunction(()=>document.querySelector('#nav-bookmarks').dataset.actionMode==='label');
    await page.locator('#nav-bookmarks').hover();assert.equal(await page.getByRole('tooltip').isVisible(),false,'visible navigation labels need no duplicate tooltip');
    await page.locator('#nav-bookmarks').focus();assert.equal(await page.getByRole('tooltip').isVisible(),false,'keyboard focus does not repeat visible navigation text');
    await page.locator('#main').hover();assert.equal(await page.getByRole('tooltip').isVisible(),false,'labelled action buttons need no duplicate tooltip');
    assert.equal(await page.locator('#page').getAttribute('aria-label'),'下一页');
    assert.equal(await page.locator('#page').evaluate(n=>getComputedStyle(n).fontSize),'0px','pagination is compact');
    await page.evaluate(()=>{const d=document.createElement('details');d.id='disclosure';d.innerHTML='<summary>日期与筛选</summary><p>条件</p>';document.body.appendChild(d);});
    await page.waitForFunction(()=>document.querySelector('#disclosure summary').classList.contains('action-disclosure'));
    const summary=page.locator('#disclosure summary');assert.equal(await summary.getAttribute('aria-expanded'),'false');await summary.click();
    await page.waitForFunction(()=>document.querySelector('#disclosure summary').getAttribute('aria-expanded')==='true');
    await summary.focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>!document.querySelector('#disclosure').open);
    assert.equal(await summary.textContent(),'日期与筛选','disclosure title remains available');
    await save.hover();const tip=page.getByRole('tooltip');await tip.waitFor({state:'visible'});assert.equal(await tip.textContent(),'收藏');
    assert.ok((await tip.boundingBox()).y>=0,'tooltip stays inside the viewport even at the top edge');
    await page.keyboard.press('Escape');await tip.waitFor({state:'hidden'});
    await page.mouse.move(800,600);await page.locator('#read').focus();assert.equal(await tip.isVisible(),false,'read-state action already has visible wording');
    await page.evaluate(()=>{document.querySelector('#save').onclick=function(){this.textContent='取消收藏';};});await save.click();
    await page.waitForFunction(()=>document.querySelector('#save').getAttribute('aria-label')==='取消收藏');
    assert.equal(await save.textContent(),'取消收藏','decoration does not replace the business content or click handler');
    await page.evaluate(()=>{const b=document.createElement('button');b.type='button';b.className='btn btn-sm';b.textContent='编辑';b.id='dynamic';document.body.appendChild(b);});
    await page.waitForFunction(()=>document.querySelector('#dynamic').getAttribute('aria-label')==='编辑');
    await page.evaluate(()=>{const recent=document.createElement('div');recent.id='find-recent';['删除','收藏','constructor','toString'].forEach(label=>{const b=document.createElement('button');b.type='button';b.className='btn';b.textContent=label;recent.appendChild(b);});document.body.appendChild(recent);});
    await page.waitForTimeout(50);
    assert.equal(await page.locator('#find-recent [data-action-icon]').count(),0,'recent search queries stay readable even when they match action names');
    await page.evaluate(()=>{const b=document.createElement('button');b.type='button';b.className='btn';b.id='unknown';b.textContent='constructor';document.body.appendChild(b);});
    await page.waitForTimeout(50);assert.equal(await page.locator('#unknown').getAttribute('data-action-icon'),null,'inherited object keys are not actions');
    await page.evaluate(()=>{const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='收藏';button.id='modal-save';document.querySelector('dialog').appendChild(button);document.querySelector('dialog').showModal();});await page.locator('#modal-save').hover();await tip.waitFor({state:'visible'});assert.equal(await tip.textContent(),'收藏');
    assert.equal(await tip.evaluate(n=>n.matches(':popover-open')),true,'tooltips must escape modal and card clipping');
    await page.locator('#modal-save').evaluate(n=>n.disabled=true);await tip.waitFor({state:'hidden'});
    await page.evaluate(()=>document.querySelector('dialog').close());
    const touch=await browser.newContext({viewport:{width:320,height:650},isMobile:true,hasTouch:true});const phone=await touch.newPage();
    await phone.setContent(await page.content());
    const mobile=phone.locator('#save');await mobile.waitFor();
    assert.equal(await mobile.evaluate(n=>getComputedStyle(n,'::after').content),'"取消收藏"','touch devices have visible action captions');
    const bounds=await mobile.boundingBox();assert.ok(bounds.width>=44&&bounds.height>=44,'touch target remains large enough');
    assert.ok(await phone.getByRole('button',{name:'查看详情',exact:true}).first().evaluate(n=>n.getBoundingClientRect().height>=44),'meaningful labelled actions retain touch targets');
    assert.equal(await phone.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await phone.addStyleTag({content:'*{box-sizing:border-box}'+fs.readFileSync('qq_digest/web/templates/bookmarks.html','utf8').match(/<style>([\s\S]*?)<\/style>/)[1]});
    await phone.evaluate(()=>{const modal=document.createElement('dialog');modal.id='long-bookmark';modal.className='bookmark-dialog';modal.innerHTML='<div class="bookmark-heading"><h2>可以长期复用的排查步骤与完整原消息标题，不应该把关闭按钮挤到下一行</h2><button type="button" class="btn">关闭</button></div>';document.body.appendChild(modal);modal.showModal();});
    await phone.waitForTimeout(40);const closePosition=await phone.evaluate(()=>({heading:document.querySelector('#long-bookmark .bookmark-heading').getBoundingClientRect().top,close:document.querySelector('#long-bookmark button').getBoundingClientRect().top}));
    assert.ok(Math.abs(closePosition.heading-closePosition.close)<2,'Long bookmark titles keep close in the expected top-right position');
    assert.deepEqual(errors,[]);console.log('Icon action hover, focus, labels, dynamic updates, modal clipping and touch targets verified');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
