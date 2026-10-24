const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
const base=fs.readFileSync('qq_digest/web/templates/base.html','utf8');
const viewer=base.match(/<script id="image-viewer-behavior">([\s\S]*?)<\/script>/);
const style=base.match(/<style id="image-viewer-style">([\s\S]*?)<\/style>/);
assert.ok(viewer&&style,'a shared in-page image viewer must exist');
const start=base.indexOf('// Existing overlays share only accessibility behavior;'),overlays=base.slice(start,base.indexOf('</script>',start));
const original='/api/secondhand/image?group_id=11&msg_id=photo&key=good',preview=original+'&preview=true';
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],remote=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.context().route('**/*',route=>{
  const url=new URL(route.request().url());
  if(url.hostname!=='viewer.test'){remote.push(url.origin);return route.abort();}
  if(url.pathname.startsWith('/api/')){
   requests.push(url.search);const key=url.searchParams.get('key');
   if(key==='broken'||key==='fallback'&&!url.searchParams.has('preview'))return route.fulfill({status:404,body:'Unavailable'});
   return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1100"><rect width="1600" height="1100" fill="#52718f"/></svg>'});
  }
  return route.fulfill({contentType:'text/html',body:'<style>[hidden]{display:none!important}body{margin:24px}a img{width:80px;height:60px}#product{width:400px}#candidate-detail{position:fixed;inset:40px;background:white;z-index:10}</style><button id="before">开始</button><a id="photo" class="market-image-link" target="_blank" href="'+preview+'"><img alt="商品配图" src="'+preview+'"></a><dialog id="product"><button>关闭商品</button><a id="nested-photo" class="market-image-link" href="'+preview+'"><img src="'+preview+'"></a></dialog><section id="candidate-detail" hidden><a id="context-photo" class="message-image-link" href="/api/groups/11/messages/photo/images/original?key=good&preview=true"><img src="'+preview+'"></a></section><div style="height:1600px"></div>'});
 });
 await page.goto('http://viewer.test/');await page.addStyleTag({content:style[1]});await page.addScriptTag({content:overlays});await page.addScriptTag({content:viewer[1]});
 await page.locator('#photo').click();const dialog=page.locator('#image-viewer'),image=dialog.locator('img');
 await dialog.waitFor({state:'visible'});await image.waitFor({state:'visible'});await page.waitForFunction(()=>{const img=document.querySelector('#image-viewer img');return img.complete&&img.naturalWidth>0;});
 assert.equal(await image.getAttribute('src'),original,'enlargement uses original bytes, not the thumbnail preview');
 assert.equal(page.context().pages().length,1,'a single click never opens an external tab');
 assert.ok((await image.boundingBox()).width>240,'the picture is actually larger than the card thumbnail');
 await image.click();assert.equal(await dialog.evaluate(n=>n.open),true,'clicking the image keeps the preview open');
 await page.getByRole('button',{name:'关闭图片预览',exact:true}).click();await dialog.waitFor({state:'hidden'});
 await page.waitForFunction(()=>document.activeElement.id==='photo');assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
 assert.equal(await image.getAttribute('src'),null,'closing releases the image source');
 await page.locator('#product').evaluate(n=>n.showModal());await page.locator('#nested-photo').click();await dialog.waitFor({state:'visible'});
 await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'关闭图片预览');
 await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});assert.equal(await page.locator('#product').evaluate(n=>n.open),true,'Escape closes only the image');
 await page.waitForFunction(()=>document.activeElement.id==='nested-photo');await page.locator('#product').evaluate(n=>n.close());
 await page.evaluate(()=>{document.getElementById('candidate-detail').hidden=false;document.addEventListener('keydown',e=>{if(e.key==='Escape')document.getElementById('candidate-detail').hidden=true;});});
 await page.locator('#context-photo').click();await dialog.waitFor({state:'visible'});await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
 assert.equal(await page.locator('#candidate-detail').evaluate(n=>n.hidden),false,'image Escape does not dismiss the source panel');await page.locator('#candidate-detail').evaluate(n=>n.hidden=true);
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:850});await page.locator('#photo').click();await dialog.waitFor({state:'visible'});
  await image.waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelector('#image-viewer img').naturalWidth>0);
  const rect=await image.boundingBox();assert.ok(rect.width<=width-30&&rect.height<=850-50,'large pictures fit the viewport');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(process.env.IMAGE_VIEWER_SCREENSHOTS){fs.mkdirSync(process.env.IMAGE_VIEWER_SCREENSHOTS,{recursive:true});await page.screenshot({path:process.env.IMAGE_VIEWER_SCREENSHOTS+'/viewer-'+width+'.png'});}
  await page.mouse.click(5,5);await dialog.waitFor({state:'hidden'});
 }
 await page.evaluate(()=>document.getElementById('photo').href='/api/secondhand/image?key=fallback&preview=true');await page.locator('#photo').click();await dialog.waitFor({state:'visible'});
 await page.waitForFunction(()=>{const img=document.querySelector('#image-viewer img');return !img.hidden&&img.complete&&img.naturalWidth>0&&img.getAttribute('src').includes('preview=true');});
 assert.equal(requests.filter(search=>search==='?key=fallback').length,1,'an unavailable strict original falls back once');await page.keyboard.press('Escape');
 await page.evaluate(()=>document.getElementById('photo').href='/api/secondhand/image?key=broken&preview=true');await page.locator('#photo').click();
 await dialog.getByRole('status').filter({hasText:'图片暂时无法显示'}).waitFor();await page.keyboard.press('Escape');
 await page.evaluate(()=>document.getElementById('photo').href='https://external.invalid/private.jpg');await page.locator('#photo').click();assert.equal(await dialog.evaluate(n=>n.open),false);assert.deepEqual(remote,[]);
 assert.equal(page.context().pages().length,1);assert.deepEqual(errors,[]);
 console.log('Single-click original enlargement, nested overlays, Escape/backdrop/close, focus restore, fallback, safe URLs and responsive fit verified');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
