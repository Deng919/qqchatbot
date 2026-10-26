const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true});try{
 const page=await browser.newPage(),fragment=fs.readFileSync('qq_digest/web/templates/knowledge_save.html','utf8');
 await page.setContent('<button id="opener" onclick="openKnowledgeSave(11,\'message\',this)">整理</button>'+fragment+'<script>window.api=()=>window.delay?new Promise(resolve=>window.resolveSource=resolve):Promise.resolve({text:"原消息标题",group_name:"测试群",date:"2026-10-26"});</script>');
 let accept=false,count=0;page.on('dialog',async dialog=>{count++;await(accept?dialog.accept():dialog.dismiss());});
 async function open(){await page.locator('#opener').click();await page.waitForFunction(()=>knowledgeSaveReady);}
 await open();await page.keyboard.press('Escape');assert.equal(await page.locator('#knowledge-save-dialog').isVisible(),false);assert.equal(count,0,'Untouched auto title is not a draft');
 await open();await page.locator('#knowledge-save-reason').fill('需要保留的说明');await page.keyboard.press('Escape');
 assert.equal(await page.locator('#knowledge-save-dialog').isVisible(),true,'Canceling close preserves changed fields');assert.equal(count,1);assert.equal(await page.locator('#knowledge-save-reason').inputValue(),'需要保留的说明');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),false);
 await page.locator('#knowledge-save-reason').fill('');count=0;await page.keyboard.press('Escape');assert.equal(count,0,'Reverting a draft removes the discard warning');
 await page.evaluate(()=>window.delay=true);await page.locator('#opener').click();await page.locator('#knowledge-save-title').fill('读取完成前的标题');await page.evaluate(()=>resolveSource({text:'源消息默认标题',group_name:'测试群',date:'2026-10-26'}));await page.waitForFunction(()=>knowledgeSaveReady);
 await page.keyboard.press('Escape');assert.equal(await page.locator('#knowledge-save-dialog').isVisible(),true,'Late auto-preview cannot mark typed input as saved');
 accept=true;count=0;await page.keyboard.press('Escape');assert.equal(await page.locator('#knowledge-save-dialog').isVisible(),false);assert.equal(count,1);
 console.log('Knowledge save: untouched/reverted close, cancel/accept draft discard, pending preview and navigation guard verified');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
