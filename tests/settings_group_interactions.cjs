const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {chromium}=require('C:/Users/xun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const python='D:/CodexTools/python/Scripts/python.exe';
function render(template,path){return execFileSync(python,['-X','utf8','-c',`import sys
from starlette.requests import Request
from qq_digest.web.app import templates
r=Request({'type':'http','scheme':'http','server':('interaction.test',80),'path':sys.argv[2],'query_string':b'','headers':[]})
print(templates.env.get_template(sys.argv[1]).render(request=r,features={}))`,template,path],{encoding:'utf8'});}
const html={search:render('search.html','/search'),settings:render('settings.html','/settings'),groups:render('groups.html','/groups'),collect:render('collect.html','/collect')};
const groups=[123,456].map((id,index)=>({group_id:id,name:'Group '+index,revision:0,enabled:true,daily_summary:false,read_images:false,read_files:false,category:'general',keywords:[],collection_window_days:30,important_candidates:false,message_count:0,sync_status:'never_synced'}));
const features={revision:0,values:{tasks:false,auto_daily:false},catalog:[{key:'tasks',group:'扩展功能',title:'Tasks',description:''},{key:'auto_daily',group:'自动任务',title:'Daily',description:''}]};
function usage(revision=0,output=3){return {month:'2026-10',calls:1,successful:1,failed:0,unknown:0,pending:0,input_tokens:1,output_tokens:1,estimated_cost:null,models:[],current_model:{provider:'test',model:'test'},settings:{revision,monthly_limit:10,timezone:'Asia/Shanghai',rates:[{provider:'test',model:'test',input_hit:1,input_miss:2,output}]}};}
async function pageFor(browser,kind,handler=()=>undefined){
 const page=await browser.newPage();page.setDefaultTimeout(2500);
 await page.route('http://interaction.test/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  if(request.isNavigationRequest())return route.fulfill({contentType:'text/html',body:html[kind]});
  if(await handler(path,request,route)===true)return;
  const data=path==='/api/groups'?{groups}:path==='/api/features'?features:path==='/api/ai-usage'?usage():path==='/api/ai-settings/connections'?{available:true,revision:'a'.repeat(64),connections:[],purpose_connections:{}}:path==='/api/desktop-settings'?{storage_path:'D:/fixture/data',export_path:'D:/fixture/export',backup_path:'D:/fixture/backup',packaged:false}:path==='/api/storage'?{inventory:{total_bytes:0,categories:[],paths:{}},policy:{revision:0,enabled:true,backup_days:30,backup_keep:3,cache_days:7}}:path==='/api/history-inspection'?{page:1,total:0,page_size:20,source_available:true,running:false,settings:{enabled:false,lookback_days:7,interval_hours:24},groups:[]}:{};
  await route.fulfill({json:data});
 });
 await page.goto('http://interaction.test/'+kind);return page;
}
async function dirtyAIRegistration(browser){
 const page=await pageFor(browser,'settings');
 await page.locator('#multi-ai-name').fill('draft');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),false,'AI connection draft must participate in shared leave protection');
 await page.close();
}
async function revertedConnectionDraft(browser){
 const page=await pageFor(browser,'settings');
 await page.locator('#multi-ai-name').fill('draft');await page.locator('#multi-ai-name').fill('');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverted new connection must be clean');
 await page.locator('#multi-ai-key').fill('synthetic-key');await page.locator('#multi-ai-key').fill('');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'cleared secret must match its empty baseline');await page.close();
}
async function revertedPurposeDraft(browser){
 const page=await pageFor(browser,'settings',async(path,request,route)=>{if(path==='/api/ai-settings/connections'){await route.fulfill({json:{available:true,revision:'a'.repeat(64),connections:[{id:'other',name:'Other',base_url:'https://example.com',model:'test',supports_vision:true}],purpose_connections:{}}});return true;}});
 await page.locator('#ai-purpose-summary').selectOption('other');await page.locator('#ai-purpose-summary').selectOption('');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverted purpose selection must be clean');
 await page.locator('.multi-ai-row').getByRole('button',{name:'编辑',exact:true}).click();await page.locator('#multi-ai-name').fill('Edited');await page.locator('#multi-ai-name').fill('Other');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverted existing connection must match its rendered baseline');await page.close();
}
async function revertedUsageLimit(browser){
 const page=await pageFor(browser,'settings');await page.locator('#ai-usage-limit').fill('20');await page.locator('#ai-usage-limit').fill('10');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverted usage limit must be clean');await page.close();
}
async function revertedUsageRate(browser){
 const page=await pageFor(browser,'settings');await page.locator('#ai-usage-rates').locator('..').evaluate(x=>x.open=true);await page.locator('#usage-price-0-output').fill('8');await page.locator('#usage-price-0-output').fill('3');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverted usage rate must be clean');await page.close();
}
async function revertedInspectionDraft(browser){
 const page=await pageFor(browser,'collect');await page.locator('#history-inspection').locator('..').evaluate(x=>x.open=true);await page.locator('#inspection-settings').locator('..').evaluate(x=>x.open=true);await page.locator('#inspection-days').fill('8');await page.locator('#inspection-days').fill('7');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverted inspection settings must be clean');await page.close();
}
async function inspectionSaveKeepsNewerDraft(browser){
 let finish,saved={enabled:false,lookback_days:7,interval_hours:24};
 const page=await pageFor(browser,'collect',async(path,request,route)=>{
  if(path==='/api/history-inspection/settings'){const submitted=request.postDataJSON();await new Promise(resolve=>finish=resolve);saved=submitted;await route.fulfill({json:saved});return true;}
  if(path==='/api/history-inspection'){await route.fulfill({json:{page:1,total:0,page_size:20,source_available:true,running:false,settings:saved,groups:[]}});return true;}
 });await page.locator('#history-inspection').locator('..').evaluate(x=>x.open=true);await page.locator('#inspection-settings').locator('..').evaluate(x=>x.open=true);await page.locator('#inspection-days').fill('8');await page.locator('#inspection-save').click();await page.locator('#inspection-days').fill('9');finish();await page.waitForFunction(()=>!document.getElementById('inspection-save').disabled);await page.waitForFunction(()=>inspectionSavedSettings.lookback_days===8);
 assert.equal(await page.locator('#inspection-days').inputValue(),'9','late saved settings must preserve newer input');await page.locator('#inspection-days').fill('8');assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true,'reverting to the newly saved settings must be clean');await page.close();
}
async function settingsPolicyLeaveProtection(browser){
 const page=await pageFor(browser,'settings');
 const clean=()=>page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true})));
 assert.equal(await clean(),true,'untouched settings must not block leaving');
 await page.locator('[data-settings-section=features]').click();await page.locator('#feature-tasks').check();assert.equal(await clean(),false,'feature draft must block leaving');
 await page.locator('#feature-tasks').uncheck();assert.equal(await clean(),true,'reverted feature must be clean');
 await page.locator('[data-settings-section=automatic]').click();await page.locator('#feature-auto_daily').check();assert.equal(await clean(),false,'automatic draft must block leaving');
 await page.close();
}
async function storagePolicyLeaveProtection(browser){
 const page=await pageFor(browser,'settings');
 const clean=()=>page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true})));
 await page.locator('[data-settings-section=storage]').click();await page.locator('#storage-backup-days').fill('60');assert.equal(await clean(),false,'storage policy draft must block leaving');
 await page.locator('#storage-backup-days').fill('30');assert.equal(await clean(),true,'reverted storage policy must be clean');await page.close();
}
async function inspectionLeaveProtection(browser){
 const page=await pageFor(browser,'collect');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),true);
 await page.locator('#history-inspection').locator('..').evaluate(x=>x.open=true);await page.locator('#inspection-settings').locator('..').evaluate(x=>x.open=true);await page.locator('#inspection-days').fill('8');
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),false,'inspection setting drafts must block leaving');await page.close();
}
async function groupDiscard(browser){
 let accept=false,prompts=0,finish;
 const page=await pageFor(browser,'groups',async(path,request,route)=>{if(request.method()==='PATCH'){await new Promise(resolve=>finish=resolve);await route.fulfill({json:{revision:1}});return true;}});
 page.on('dialog',async d=>{prompts++;await (accept?d.accept():d.dismiss());});
 await page.locator('.group-name').first().click();await page.locator('.group-advanced').evaluate(x=>x.open=true);await page.locator('#group-keywords').fill('draft');
 await page.locator('.group-name').nth(1).click();
 assert.equal(await page.locator('#group-detail-title').innerText(),'Group 0 · 123','declining discard must keep original group');
 assert.equal(await page.locator('#group-keywords').inputValue(),'draft');
 await page.getByRole('button',{name:'关闭详情',exact:true}).click();assert.equal(await page.locator('#group-detail').isVisible(),true);
 assert.equal(await page.evaluate(()=>document.dispatchEvent(new Event('qq-digest-check-unsaved',{cancelable:true}))),false);
 assert.equal(prompts,2);accept=true;
 await page.locator('.group-name').nth(1).click();assert.equal(await page.locator('#group-keywords').inputValue(),'');
 await page.close();
}
async function groupLateSave(browser){
 let finish;
 const page=await pageFor(browser,'groups',async(path,request,route)=>{if(request.method()==='PATCH'){await new Promise(resolve=>finish=resolve);await route.fulfill({json:{revision:1}});return true;}});
 await page.locator('.group-name').first().click();await page.locator('.group-advanced').evaluate(x=>x.open=true);await page.locator('#group-keywords').fill('submitted');await page.locator('#group-detail-save').click();
 await page.locator('#group-keywords').fill('newer draft');finish();await page.waitForFunction(()=>!groupSaveBusy);
 assert.match(await page.locator('#group-detail-result').innerText(),/尚未保存/,'late success must not label newer input as saved');
 await page.close();
}
async function featureSaveFeedbackTracksDraft(browser){
 let values={...features.values},fail=false;
 const page=await pageFor(browser,'settings',async(path,request,route)=>{if(path==='/api/features'&&request.method()==='PUT'){if(fail)await route.fulfill({status:503,json:{detail:'Synthetic save failure'}});else{values={...values,...request.postDataJSON().values};await route.fulfill({json:{...features,revision:1,values}});}return true;}});
 for(const [section,key,result] of [['features','tasks','features-result'],['automatic','auto_daily','automatic-result']]){
  await page.locator('[data-settings-section='+section+']').click();await page.locator('#feature-'+key).check();
  assert.match(await page.locator('#'+result).innerText(),/尚未保存/,'a changed setting must say it is not saved');
  await page.locator('#'+section+'-save').click();await page.waitForFunction(id=>!document.getElementById(id).disabled,section+'-save');
  assert.match(await page.locator('#'+result).innerText(),/已保存/);
  await page.locator('#feature-'+key).uncheck();assert.match(await page.locator('#'+result).innerText(),/尚未保存/,'a previous success cannot label a newer draft as saved');
  await page.locator('#feature-'+key).check();assert.equal(await page.locator('#'+result).isHidden(),true,'reverting to saved values must remove dirty feedback');
 }
 await page.locator('[data-settings-section=features]').click();fail=true;await page.locator('#feature-tasks').uncheck();await page.locator('#features-save').click();await page.waitForFunction(()=>!document.getElementById('features-save').disabled);
 await page.locator('#feature-tasks').check();assert.match(await page.locator('#features-result').innerText(),/Synthetic save failure/,'editing or reverting must not erase save failure details');await page.close();
}
async function deepSeekTestRequiresSavedKey(browser){
 const requests=[];let finish;
 const page=await pageFor(browser,'settings',async(path,request,route)=>{if(path==='/api/ai-settings/test'){requests.push(request.postDataJSON());await new Promise(resolve=>finish=resolve);await route.fulfill({json:{ok:true}});return true;}});
 await page.locator('#deepseek-api-key').fill('synthetic-draft-key');assert.equal(await page.locator('#deepseek-test-btn').isDisabled(),true,'an unsaved key cannot be tested as though it were saved');
 assert.match(await page.locator('#deepseek-test-hint').innerText(),/先.*保存/);
 await page.evaluate(()=>testDeepSeekConnection());assert.equal(requests.length,0,'direct invocation cannot bypass the save-first guard');
 await page.locator('#deepseek-api-key').fill('');assert.equal(await page.locator('#deepseek-test-btn').isEnabled(),true);
 await page.locator('#deepseek-test-btn').click();await page.waitForFunction(()=>document.getElementById('deepseek-test-btn').disabled);assert.deepEqual(requests,[{}],'test only reads saved settings and never submits a draft secret');
 await page.locator('#deepseek-api-key').fill('newer-draft');finish();await page.waitForFunction(()=>!deepSeekTesting);assert.equal(await page.locator('#deepseek-test-btn').isDisabled(),true,'completion must keep testing disabled while a newer draft exists');await page.close();
}
async function emptyKeywordGroupBrowsing(browser){
 const reads=[];
 const page=await pageFor(browser,'search',async(path,request,route)=>{if(path==='/api/messages'){reads.push(new URL(request.url()));await route.fulfill({json:{results:[],total:0,page:1,page_size:20}});return true;}});
 await page.locator('#find-filters > summary').click();await page.locator('#search-group').evaluate(select=>GroupSelector.setSelected(select,[123,456]));
 await page.locator('#search-from').fill('2026-10-01');await page.locator('#search-to').fill('2026-10-05');await page.locator('#search-sender').fill('1001');await page.locator('#search-message-type').selectOption('file');
 await page.locator('#search-form button[type=submit]').click();await page.waitForFunction(()=>document.getElementById('search-count').textContent==='共 0 条归档消息');
 assert.equal(reads.length,1);assert.deepEqual(reads[0].searchParams.getAll('group_ids'),['123','456']);assert.equal(reads[0].searchParams.get('sender_qq'),'1001');assert.equal(reads[0].searchParams.get('message_type'),'file');assert.equal(reads[0].searchParams.get('date_from'),'2026-10-01');assert.equal(reads[0].searchParams.get('date_to'),'2026-10-05');
 await page.reload();await page.waitForFunction(()=>document.getElementById('search-count').textContent==='共 0 条归档消息');assert.equal(reads.length,2,'group-only browsing is restored after reload');
 await page.locator('#search-group').evaluate(select=>GroupSelector.setSelected(select,null));await page.locator('#search-form button[type=submit]').click();assert.equal(reads.length,2,'clearing explicit groups cannot silently widen a saved group browse');assert.equal(await page.locator('#find-validation').isVisible(),true);await page.close();
}
async function deepSeekSaveFreezesKey(browser){
 let finish;
 const page=await pageFor(browser,'settings',async(path,request,route)=>{if(path==='/api/ai-settings/key'){await new Promise(resolve=>finish=resolve);await route.fulfill({status:503,json:{detail:'Synthetic failure'}});return true;}});
 await page.locator('#deepseek-api-key').fill('synthetic-key');await page.locator('#deepseek-save-btn').click();
 assert.equal(await page.locator('#deepseek-api-key').isDisabled(),true,'pending save must freeze the submitted key');
 finish();await page.waitForFunction(()=>!document.getElementById('deepseek-save-btn').disabled);
 assert.equal(await page.locator('#deepseek-api-key').isEnabled(),true);assert.equal(await page.locator('#deepseek-api-key').inputValue(),'synthetic-key');await page.close();
}
async function usageRefreshConflict(browser){
 let reads=0,payload,accept=false;
 const page=await pageFor(browser,'settings',async(path,request,route)=>{
  if(path==='/api/ai-usage'){await route.fulfill({json:++reads===1?usage(0,3):usage(1,8)});return true;}
  if(path==='/api/ai-usage/settings'){payload=request.postDataJSON();await route.fulfill({status:409,json:{detail:'Synthetic revision conflict'}});return true;}
 });page.on('dialog',d=>accept?d.accept():d.dismiss());
 await page.locator('#ai-usage-limit').fill('20');await page.locator('#ai-usage-refresh').click();await page.waitForFunction(()=>!document.getElementById('ai-usage-save').disabled);
 assert.equal(await page.evaluate(()=>usageRevision),0,'declining external changes must keep the draft base revision');
 assert.equal(await page.locator('#ai-usage-limit').inputValue(),'20');await page.locator('#ai-usage-save').click();await page.waitForFunction(()=>!usageBusy);
 assert.equal(payload.expected_revision,0);assert.equal(payload.rates[0].output,'3');
 accept=true;await page.locator('#ai-usage-refresh').click();await page.waitForFunction(()=>usageRevision===1);
 assert.equal(await page.locator('#ai-usage-limit').inputValue(),'10');assert.equal(await page.locator('#usage-price-0-output').inputValue(),'8');await page.close();
}
async function batchUncheckedValidation(browser){
 let payload;
 const page=await pageFor(browser,'groups',async(path,request,route)=>{if(path==='/api/groups/batch/settings'){payload=request.postDataJSON();await route.fulfill({status:409,json:{detail:'Synthetic conflict'}});return true;}});
 await page.locator('#group-batch-select').evaluate(x=>x.groupSelector.setSelected([123]));await page.getByRole('button',{name:'批量设置',exact:true}).click();
 await page.locator('[data-batch-apply=collection_window_days]').check();await page.locator('#batch-value-collection_window_days').fill('0');await page.locator('[data-batch-apply=collection_window_days]').uncheck();await page.locator('[data-batch-apply=enabled]').check();
 await page.getByRole('button',{name:'应用所选设置',exact:true}).click();await page.waitForFunction(()=>document.getElementById('group-batch-settings-error').textContent);
 assert.deepEqual(payload,{group_ids:[123],patch:{enabled:true}});assert.equal(await page.getByRole('button',{name:'应用所选设置',exact:true}).isEnabled(),true);await page.keyboard.press('Escape');assert.equal(await page.locator('#group-batch-settings-dialog').evaluate(n=>n.open),false,'Escape changes modal state immediately');await page.locator('#group-batch-settings-dialog').waitFor({state:'hidden'});await page.close();
}
async function lateCategorySaveKeepsCurrentRoute(browser){
 let finish;
 const page=await pageFor(browser,'settings',async(path,request,route)=>{if(path==='/api/features'&&request.method()==='PUT'){await new Promise(resolve=>finish=resolve);await route.fulfill({json:{...features,revision:1,values:{tasks:true,auto_daily:false}}});return true;}});
 await page.locator('[data-settings-section=features]').click();await page.locator('#feature-tasks').check();await page.locator('#features-save').click();await page.locator('[data-settings-section=ai]').click();finish();await page.waitForFunction(()=>!document.getElementById('features-save').disabled);
 assert.equal(new URL(page.url()).search,'?section=ai','a background save must not rewrite the selected category URL');await page.close();
}
(async()=>{const browser=await chromium.launch({headless:true});let failures=0;try{for(const test of [dirtyAIRegistration,revertedConnectionDraft,revertedPurposeDraft,revertedUsageLimit,revertedUsageRate,revertedInspectionDraft,inspectionSaveKeepsNewerDraft,settingsPolicyLeaveProtection,storagePolicyLeaveProtection,inspectionLeaveProtection,groupDiscard,groupLateSave,featureSaveFeedbackTracksDraft,deepSeekTestRequiresSavedKey,emptyKeywordGroupBrowsing,deepSeekSaveFreezesKey,usageRefreshConflict,batchUncheckedValidation,lateCategorySaveKeepsCurrentRoute]){try{await test(browser);console.log('PASS '+test.name);}catch(e){failures++;console.error('FAIL '+test.name+': '+e.message);}}}finally{await browser.close();}process.exitCode=failures?1:0;})().catch(e=>{console.error(e);process.exitCode=1;});
