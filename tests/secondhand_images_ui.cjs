global.WorkspaceLayout={capture:()=>null,finish(){}};
const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const read=name=>fs.readFileSync('qq_digest/web/templates/'+name,'utf8');
const template=read('secondhand.html'),content=template.split('{% block content %}')[1].replace(/{%[\s\S]*?%}/g,'');
const script=read('secondhand_script.html').replace(/<\/?script>/g,'').replace('marketInit();','');
const imageUrl='/api/secondhand/image?group_id=11&msg_id=first&key=photo';
const item={item_id:'phone',title:'闲置手机',category:'digital',description:'配件齐全，支持当面检查。',price_text:'800元',group_count:1,seller_qq:123,status:'unknown',published_at:'2026-10-24',lane:'sale',images:[{key:'photo',name:'<img src=x onerror=alert(1)>.jpg',url:imageUrl}]};
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],remote=[],imageRequests=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.context().route('http://market.test/**',route=>{
  const url=new URL(route.request().url());if(url.pathname==='/api/secondhand/image'||url.pathname.endsWith('/images/original'))imageRequests.push(url.search);
  if(url.searchParams.get('key')==='preview-missing'&&url.searchParams.get('preview')==='true'||url.searchParams.get('key')==='both-missing')return route.fulfill({status:404,contentType:'text/plain',body:'Unavailable'});
  if(route.request().url().includes('key=missing'))return route.fulfill({status:404,contentType:'text/plain',body:'Unavailable'});
  if(url.pathname==='/api/secondhand/image'||url.pathname.endsWith('/images/original'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="160"><rect width="180" height="160" fill="#e1ede8"/><rect x="62" y="20" width="56" height="118" rx="8" fill="#385e51"/></svg>'});
  return route.fulfill({contentType:'text/html; charset=utf-8',body:'<body><style>*{box-sizing:border-box}body{font:14px/1.5 sans-serif;margin:24px;--text:#263b36;--text-soft:#61736d;--text-dim:#72847e;--border:#d8e1dd;--surface:#fff;--surface-subtle:#f4f7f5;--primary:#3e7864;--primary-dark:#2d5c4b;--border-strong:#b8c9c1}[hidden]{display:none!important}.btn{padding:7px 10px;border:1px solid var(--border);border-radius:5px;background:#fff}.input,select{padding:8px;border:1px solid var(--border)}.actions{display:flex;gap:8px}</style>'+content+'</body>'});
 });
 await page.context().route('https://external.invalid/**',route=>{remote.push(route.request().url());return route.abort();});
 await page.goto('http://market.test/secondhand');
 await page.evaluate(()=>{window.requests=[];window.api=(method,url,body)=>{requests.push({method,url,body});return Promise.resolve(window.detailData);};window.destroyMessageContext=()=>{};window.mountMessageContext=()=>{};});
 await page.evaluate(script);
 await page.evaluate(()=>marketLaneTabs({sale:5,want:0,promotion:0}));
 await page.evaluate(item=>document.getElementById('market-list').appendChild(marketCard(item)),item);
 assert.equal(await page.locator('.market-item img').count(),1,'cards show original source photos immediately');
 assert.equal(await page.locator('.market-item img').getAttribute('loading'),'lazy');
 assert.match(await page.locator('.market-item .market-images').textContent(),/原消息配图/);
 assert.match(await page.locator('.market-image-name').textContent(),/<img src=x onerror=alert\(1\)>\.jpg/,'caption is literal text');
 assert.equal(await page.locator('.market-image-name img').count(),0);
 const link=page.locator('.market-item .market-image-link');assert.equal(await link.getAttribute('href'),imageUrl);assert.equal(await link.getAttribute('target'),'_blank');assert.match(await link.getAttribute('rel'),/noopener/);
 const popupPromise=page.waitForEvent('popup');await link.click();const popup=await popupPromise;await popup.waitForLoadState();assert.equal(new URL(popup.url()).pathname,'/api/secondhand/image');await popup.close();
 assert.deepEqual(await page.evaluate(()=>requests),[],'viewing a photo makes no AI or mutation calls');
 await page.evaluate(item=>{window.detailData={item,sources:[],favorite:false};return marketOpen(item.item_id);},item);
 assert.equal(await page.locator('#market-dialog img').count(),1,'detail shows the same source photo beside its content');
 await page.evaluate(item=>{window.detailData={item:{...item,images:[]},sources:[],favorite:false};return marketOpen('no-photo');},item);
 assert.equal(await page.locator('#market-dialog img').count(),0,'opening another product clears the previous photos');
 await page.evaluate(()=>marketClose());
 await page.evaluate(item=>{const host=document.getElementById('market-list');host.textContent='';[undefined,[],{},[{url:'https://external.invalid/photo.jpg'}],[{url:'//external.invalid/photo.jpg'}],[{url:'/api/secondhand/image-other?x=1'}],[{url:'/api/secondhand/image?key=x\n'}]].forEach(images=>host.appendChild(marketCard({...item,images})));},item);
 assert.equal(await page.locator('.market-item img').count(),0,'missing images and arbitrary URLs leave no gallery');assert.equal(await page.locator('.market-item .market-images').count(),0);assert.deepEqual(remote,[]);
 await page.evaluate(item=>document.getElementById('market-list').appendChild(marketCard({...item,images:[{key:'missing',name:'missing.jpg',url:'/api/secondhand/image?key=missing'}]})),item);
 await page.locator('.market-item').last().scrollIntoViewIfNeeded();await page.waitForFunction(()=>document.querySelectorAll('.market-item img').length===0);
 assert.equal(await page.locator('.market-item .market-image-missing:visible').count(),1,'an unavailable referenced photo remains visibly marked as not downloaded');
 assert.match(await page.locator('.market-item .market-image-missing').textContent(),/图片未下载/);
 const related={...item,images:[{...item.images[0],related:true,source_msg_id:'adjacent-photo',source_timestamp:'2026-10-24T02:48:20+00:00',source_url:'/api/secondhand/source?group_id=11&msg_id=adjacent-photo'}]};
 await page.evaluate(item=>{const host=document.getElementById('market-list');host.textContent='';host.appendChild(marketCard(item));window.detailData={item,sources:[],favorite:false};window.mountMessageContext=(host,options)=>{host.textContent='原消息上下文：'+options.citedId;host.dataset.sourceUrl=options.url;};},related);
 assert.match(await page.locator('.market-images-label').textContent(),/连续消息配图/,'separately sent photos are distinguished from same-message photos');
 assert.match(await page.locator('.market-image-source').textContent(),/图片原消息/);
 assert.equal(await page.locator('.market-image-source').getAttribute('href'),'/api/secondhand/source?group_id=11&msg_id=adjacent-photo');
 assert.match(await page.locator('.market-image-source').getAttribute('title'),/2026/,'the photo source timestamp remains accessible');
 await page.locator('.market-image-source').click();
 await page.waitForFunction(()=>document.querySelector('#market-context').textContent.includes('adjacent-photo'));
 assert.equal(await page.locator('#market-dialog').evaluate(n=>n.open),true,'photo provenance opens the market context dialog');
 assert.equal(await page.locator('#market-context [data-source-url]').getAttribute('data-source-url'),'/api/secondhand/source?group_id=11&msg_id=adjacent-photo');
 assert.ok((await page.evaluate(()=>requests)).every(request=>request.method==='GET'),'photo provenance makes read-only requests');
 await page.evaluate(()=>{marketClose();marketN('market-detail-images').textContent='';});
 const missingRelated={...related,images:[{...related.images[0],url:'/api/secondhand/image?key=both-missing',preview_url:'/api/secondhand/image?key=both-missing&preview=true'}]};
 await page.evaluate(({item,related,missingRelated})=>{const host=document.getElementById('market-list');host.textContent='';[item,related,missingRelated].forEach(product=>host.appendChild(marketCard(product)));},{item,related,missingRelated});
 await page.waitForFunction(()=>document.querySelector('.market-image-missing'));
 assert.equal(await page.locator('.market-item').last().locator('.market-image-source').count(),1,'an undownloaded continuous photo retains its original-message link');
 for(const width of [1440,390]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'photo provenance stays within the page at '+width);if(process.env.SECONDHAND_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SECONDHAND_SCREENSHOT_DIR,'secondhand-related-missing-'+width+'.png'),fullPage:true});}
 await page.setViewportSize({width:1440,height:900});
 await page.evaluate(item=>{const host=document.getElementById('market-list');host.textContent='';['https://external.invalid/source','/api/secondhand/source?group_id=11&msg_id=other'].forEach(source_url=>host.appendChild(marketCard({...item,images:[{...item.images[0],related:true,source_msg_id:'adjacent-photo',source_url}]})));},item);
 assert.equal(await page.locator('.market-image-source').count(),0,'unsafe or mismatched photo provenance cannot become a source link');
 const cached={...item,images:[{...item.images[0],preview_url:imageUrl+'&preview=true'}]};
 await page.evaluate(item=>{const host=document.getElementById('market-list');host.textContent='';host.appendChild(marketCard(item));},cached);
 assert.equal(await page.locator('.market-item img').getAttribute('src'),imageUrl+'&preview=true','cached previews serve both thumbnail and click');
 assert.equal(await page.locator('.market-image-link').getAttribute('href'),imageUrl+'&preview=true');
 assert.equal(await page.locator('.market-images-label').textContent(),'原消息配图 · 本机预览');
 assert.match(await page.locator('.market-image-link').getAttribute('aria-label'),/^查看本机预览：<img src=x onerror=alert\(1\)>\.jpg$/);
 assert.match(await page.locator('.market-item img').getAttribute('alt'),/^本机预览/);assert.equal(await page.locator('.market-image-name img').count(),0);
 const cachedPopupPromise=page.waitForEvent('popup');await page.locator('.market-image-link').click();const cachedPopup=await cachedPopupPromise;await cachedPopup.waitForLoadState();assert.equal(new URL(cachedPopup.url()).searchParams.get('preview'),'true');await cachedPopup.close();
 await page.evaluate(item=>{window.detailData={item,sources:[],favorite:false};return marketOpen(item.item_id);},cached);
 assert.equal(await page.locator('#market-dialog img').getAttribute('src'),imageUrl+'&preview=true');assert.equal(await page.locator('#market-dialog .market-images-label').textContent(),'原消息配图 · 本机预览');
 if(process.env.SECONDHAND_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SECONDHAND_SCREENSHOT_DIR,'secondhand-cached-preview-1440.png')});
 await page.evaluate(()=>marketClose());
 for(const key of ['preview-missing','both-missing']){
  const original='/api/secondhand/image?key='+key,preview=original+'&preview=true';
  await page.evaluate(({item,original,preview})=>{const host=document.getElementById('market-list');host.textContent='';host.appendChild(marketCard({...item,images:[{name:'原消息照片',url:original,preview_url:preview}]}));},{item,original,preview});
  await page.locator('.market-item').scrollIntoViewIfNeeded();
  if(key==='preview-missing'){
   await page.waitForFunction(original=>{const img=document.querySelector('.market-item img');return img&&img.getAttribute('src')===original&&img.complete&&img.naturalWidth>0;},original);
   assert.equal(await page.locator('.market-item .market-image-link').getAttribute('href'),original);assert.match(await page.locator('.market-item .market-image-link').getAttribute('aria-label'),/^查看原图/);assert.equal(await page.locator('.market-item .market-images-label').textContent(),'原消息配图');
  }else{await page.waitForFunction(()=>!document.querySelector('.market-item img'));assert.match(await page.locator('.market-item .market-image-missing').textContent(),/图片未下载/);}
  assert.equal(imageRequests.filter(search=>search==='?key='+key+'&preview=true').length,key==='both-missing'?2:1,'each preview is tried once');assert.equal(imageRequests.filter(search=>search==='?key='+key).length,key==='both-missing'?2:1,'strict original is the only retry');
 }
 await page.evaluate(item=>{const host=document.getElementById('market-list');host.textContent='';host.appendChild(marketCard({...item,images:[{...item.images[0],preview_url:'https://external.invalid/unsafe-preview.jpg'}]}));},item);
 assert.equal(await page.locator('.market-item img').getAttribute('src'),imageUrl,'an unsafe preview falls back directly to the safe original');assert.deepEqual(remote,[]);
 await page.evaluate(item=>{const host=document.getElementById('market-list');host.textContent='';for(let i=0;i<5;i++)host.appendChild(marketCard({...item,item_id:'phone'+i,images:Array.from({length:6},(_,i)=>({...item.images[0],key:'photo'+i,url:item.images[0].url+i,name:'原图 '+(i+1)}))}));},item);
 for(const [width,columns] of [[1800,4],[1440,3],[390,1]]){
  await page.setViewportSize({width,height:900});
  assert.equal(await page.locator('#market-list').evaluate(n=>getComputedStyle(n).gridTemplateColumns.split(' ').length),columns,'responsive card grid at '+width);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'no page overflow at '+width);
  assert.ok(await page.locator('.market-item img').first().evaluate(n=>n.getBoundingClientRect().height)<=80,'card photos remain compact');
  assert.ok(await page.locator('.market-images-list').first().evaluate(n=>n.scrollWidth>n.clientWidth),'extra photos stay reachable in the compact strip');
  if(process.env.SECONDHAND_SCREENSHOT_DIR){fs.mkdirSync(process.env.SECONDHAND_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SECONDHAND_SCREENSHOT_DIR,'secondhand-images-'+width+'.png'),fullPage:true});}
 }
 await page.evaluate(item=>{window.detailData={item:{...item,images:Array.from({length:100},(_,i)=>({...item.images[0],url:item.images[0].url+i}))},sources:[],favorite:false};return marketOpen(item.item_id);},item);
 assert.equal(await page.locator('#market-dialog img').count(),100,'detail retains all bounded original photos');assert.ok(await page.locator('#market-dialog .market-images-list').evaluate(n=>n.clientHeight)<180,'large photo sets stay compact');
 if(process.env.SECONDHAND_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SECONDHAND_SCREENSHOT_DIR,'secondhand-images-detail-390.png')});
 const contextTemplate=read('message_context_script.html');await page.addStyleTag({content:contextTemplate.match(/<style>([\s\S]*?)<\/style>/)[1]});await page.addScriptTag({content:contextTemplate.replace(/<style>[\s\S]*?<\/style>/,'').replace(/<\/?script>/g,'')});
 await page.evaluate(()=>{marketClose();marketN('market-detail-images').textContent='';marketN('market-dialog').showModal();window.appendBookmarkAction=()=>{};});
 async function mountContext(image){await page.evaluate(image=>mountMessageContext(marketN('market-context'),{groupId:11,citedId:'preview',url:'/context',load:()=>Promise.resolve({messages:[{msg_id:'preview',timestamp:'2026-10-24',text:'原消息附件',images_enabled:true,read_images:false,images:[{key:'photo',name:'<img onerror=alert(1)> 商品图',status:'disabled',read_url:'/api/groups/11/messages/preview/images/photo/read',...image}]}],first_id:'preview',last_id:'preview'})}).ready,image);await page.locator('.message-image').scrollIntoViewIfNeeded();}
 const genericUrl='/api/groups/11/messages/preview/images/original?key=photo',callsBeforeContext=await page.evaluate(()=>requests.length);
 await mountContext({url:genericUrl,preview_url:genericUrl+'&preview=true'});
 assert.equal(await page.locator('.message-original-image').getAttribute('src'),genericUrl+'&preview=true');assert.equal(await page.locator('.message-image-link').getAttribute('href'),genericUrl+'&preview=true');assert.match(await page.locator('.message-image-link').getAttribute('aria-label'),/^查看本机预览/);assert.match(await page.locator('.message-image-caption').textContent(),/^本机预览/);assert.equal(await page.locator('.message-image-caption img').count(),0);
 if(process.env.SECONDHAND_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SECONDHAND_SCREENSHOT_DIR,'secondhand-context-cached-preview-390.png')});
 for(const key of ['preview-missing','both-missing']){
  const original='/api/groups/11/messages/preview/images/original?key='+key;await mountContext({url:original,preview_url:original+'&preview=true'});
  if(key==='preview-missing'){await page.waitForFunction(original=>{const image=document.querySelector('.message-original-image');return image&&image.getAttribute('src')===original&&image.complete&&image.naturalWidth>0;},original);assert.equal(await page.locator('.message-image-link').getAttribute('href'),original);assert.doesNotMatch(await page.locator('.message-image-caption').textContent(),/本机预览/);}else{await page.waitForFunction(()=>!document.querySelector('.message-original-image'));assert.match(await page.locator('.message-image-link').textContent(),/原图未下载或无法核实来源/);}
 }
 assert.equal(await page.evaluate(()=>requests.length),callsBeforeContext,'context preview and fallback never call AI or mutate group settings');
 assert.deepEqual(errors,[]);
 console.log('Secondhand and source-context photos: safe lazy originals/cached previews, literal captions, one original fallback, no AI, missing/error states and desktop/mobile layout verified');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
