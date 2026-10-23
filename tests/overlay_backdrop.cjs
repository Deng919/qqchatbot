const {chromium}=require('playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
const base=fs.readFileSync('qq_digest/web/templates/base.html','utf8');
const start=base.indexOf('// Existing overlays share only accessibility behavior;');
const behavior=base.slice(start,base.indexOf('</script>',start));
const candidates=fs.readFileSync('qq_digest/web/templates/candidates.html','utf8');
const panelTag=candidates.match(/<aside id="candidate-detail"[^>]*>/)[0];
const backdropTag=candidates.match(/<div id="candidate-detail-backdrop"[^>]*>/)[0];
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:800});
  await page.setContent('<style>[hidden]{display:none!important}#candidate-detail-backdrop{position:fixed;inset:0;z-index:39;background:#0005}#candidate-detail{position:fixed;top:20px;right:16px;width:min(480px,calc(100% - 48px));height:300px;overflow:auto;background:white;z-index:40;padding:12px;box-sizing:border-box}</style><button id="opener">详情</button><main id="background"><button>后台操作</button></main>'+backdropTag+'</div>'+panelTag+'<button id="inner">弹窗内操作</button><div style="height:800px"></div><button id="last">末尾操作</button><dialog id="nested"><button>嵌套操作</button></dialog></aside>');
  await page.evaluate(()=>{window.closeCandidateDetail=()=>{document.getElementById('candidate-detail').hidden=true;document.getElementById('candidate-detail-backdrop').hidden=true;};document.getElementById('opener').onclick=()=>{document.getElementById('candidate-detail').hidden=false;document.getElementById('candidate-detail-backdrop').hidden=false;};});
  await page.addScriptTag({content:behavior});await page.locator('#opener').click();
  await page.waitForFunction(()=>document.getElementById('background').inert);
  assert.equal(await page.locator('#candidate-detail-backdrop').evaluate(n=>n.inert),false,'the active backdrop remains clickable');
  await page.locator('#inner').click();assert.equal(await page.locator('#candidate-detail').evaluate(n=>n.hidden),false,'inside interaction does not dismiss the panel');
  await page.locator('#nested').evaluate(n=>n.showModal());await page.waitForFunction(()=>document.getElementById('candidate-detail-backdrop').inert);await page.locator('#nested').evaluate(n=>n.close());await page.waitForFunction(()=>!document.getElementById('candidate-detail-backdrop').inert);
  await page.locator('#candidate-detail').evaluate(n=>n.scrollTop=n.scrollHeight);
  await page.mouse.click(10,350);await page.waitForFunction(()=>document.getElementById('candidate-detail').hidden);
  assert.equal(await page.locator('#background').evaluate(n=>n.inert),false);assert.equal(await page.evaluate(()=>document.activeElement.id),'opener','closing returns focus to the original detail button');
  await page.evaluate(()=>{window.nativeCancelCount=0;const dialog=document.createElement('dialog');dialog.id='native';dialog.innerHTML='<button>内部操作</button><input value="草稿">';dialog.style.padding='30px';dialog.addEventListener('cancel',()=>{window.nativeCancelCount++;});document.body.appendChild(dialog);dialog.showModal();});
  const native=page.locator('#native'),bounds=await native.boundingBox();
  await page.mouse.click(bounds.x+3,bounds.y+3);assert.equal(await native.evaluate(n=>n.open),true,'clicking dialog padding keeps it open');
  await page.mouse.move(bounds.x+40,bounds.y+40);await page.mouse.down();await page.mouse.move(5,5);await page.mouse.up();assert.equal(await native.evaluate(n=>n.open),true,'dragging out of a dialog does not dismiss it');
  await page.mouse.click(5,5);assert.equal(await native.evaluate(n=>n.open),false,'native dialog backdrop uses the existing cancel path');assert.equal(await page.evaluate(()=>window.nativeCancelCount),1);assert.equal(await native.locator('input').inputValue(),'草稿');
  await native.evaluate(n=>{n.addEventListener('cancel',event=>event.preventDefault(),{once:true});n.showModal();});await page.mouse.click(5,5);assert.equal(await native.evaluate(n=>n.open),true,'a page can still prevent cancellation while an operation is busy');await native.evaluate(n=>n.close());
 }
 assert.deepEqual(errors,[]);console.log('Active backdrop dismisses scrolled panels; inner actions, nested dialogs, background inertness and focus restore verified');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
