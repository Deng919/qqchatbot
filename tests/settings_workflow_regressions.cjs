const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {chromium} = require('C:/Users/xun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const python = 'D:/CodexTools/python/Scripts/python.exe';
function render(template, path) {
  return execFileSync(python, ['-c', `import sys\nfrom starlette.requests import Request\nfrom qq_digest.web.app import templates\nr=Request({'type':'http','scheme':'http','server':('settings.test',80),'path':sys.argv[2],'query_string':b'','headers':[]})\nprint(templates.env.get_template(sys.argv[1]).render(request=r,features={}))`, template, path], {encoding:'utf8', env:{...process.env, PYTHONIOENCODING:'utf-8'}});
}
const html = {setup:render('first_use.html','/setup'), ai:render('settings.html','/settings'), updates:render('desktop_updates.html','/desktop-updates')};
function setupState() {return {available:true,active:true,completed:false,step:2,revision:'a'.repeat(64),selected_groups:[100],group_names:{100:'Group A'},imports:{},start_date:'2026-10-05',end_date:'2026-10-05',ai_ready:false,ai:{key_configured:false,bridge_available:false,provider:'compatible',model:'sample',base_url:'https://example.com'},ntqq:{db_dir:'D:/fixture/source',qq_number:123456},schedule:{interval_minutes:20,hour:22,minute:0,auto_collection:false,auto_daily:false},features_revision:0};}
async function pageFor(browser,kind,handler) {
  const page=await browser.newPage();
  await page.route('http://settings.test/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(request.isNavigationRequest())return route.fulfill({contentType:'text/html',body:path==='/reports'?'<h1>Reports</h1>':html[kind]});
    const reply=await handler(path,request,route);
    if(reply===false)return;
    await route.fulfill({contentType:'application/json',body:JSON.stringify(reply||{})});
  });
  await page.goto('http://settings.test/'+(kind==='setup'?'setup':kind==='ai'?'settings':'desktop-updates'));
  return page;
}
const groups=[{group_id:100,name:'Group A'},{group_id:200,name:'Group B'}];
async function sourceSelection(browser) {
  let state=setupState();
  const page=await pageFor(browser,'setup',async(path,request)=>{
    if(path==='/api/setup')return state;
    if(path==='/api/setup/groups')return {groups};
    if(path==='/api/setup/source'){state={...state,selected_groups:[],revision:'b'.repeat(64)};return state;}
  });
  await page.locator('.group-selector input[value="200"]').check();
  await page.locator('[data-setup-step="1"]').click();
  await page.locator('[data-setup-step="2"]').click();
  assert.equal(await page.locator('.group-selector input:checked').count(),2,'step navigation must preserve selection drafts');
  await page.locator('#setup-scan').click();
  assert.equal(await page.locator('.group-selector input:checked').count(),2,'rescanning must preserve selection drafts');
  await page.locator('[data-setup-step="1"]').click();
  await page.locator('#setup-save-source').click();
  await page.locator('[data-setup-panel="2"]').waitFor({state:'visible'});
  assert.equal(await page.locator('.group-selector input:checked').count(),0,'saving a source must adopt its reset selection');
  state={...state,selected_groups:[200]};
  await page.locator('#setup-reload').evaluate(node=>node.hidden=false);
  await page.locator('#setup-reload').click();
  await page.waitForFunction(()=>document.querySelector('.group-selector input[value="200"]').checked);
  assert.equal(await page.locator('.group-selector input:checked').count(),1,'explicit reload must adopt the saved selection');
  await page.close();
}
async function exitSetup(browser) {
  const state=setupState();let exitRequest,finishExit;
  const page=await pageFor(browser,'setup',async(path,request,route)=>{
    if(path==='/api/setup')return state;
    if(path==='/api/setup/groups')return {groups};
    if(path==='/api/setup/exit'){exitRequest=request.postDataJSON();await new Promise(resolve=>finishExit=resolve);await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'Another task is running'})});return false;}
  });
  await page.locator('#setup-fields:not([disabled])').waitFor();
  await page.getByRole('link',{name:'返回摘要',exact:true}).click();
  await page.waitForTimeout(100);
  assert.equal(new URL(page.url()).pathname,'/setup','return must wait until resuming succeeds');
  assert.deepEqual(exitRequest,{expected_revision:'a'.repeat(64)});
  finishExit();await page.locator('#setup-status').filter({hasText:'Another task is running'}).waitFor();
  assert.equal(new URL(page.url()).pathname,'/setup','resume failure must keep the user in setup');
  await page.unroute('http://settings.test/**');
  await page.route('http://settings.test/**',route=>route.fulfill({contentType:route.request().isNavigationRequest()?'text/html':'application/json',body:route.request().isNavigationRequest()?'<h1>Reports</h1>':JSON.stringify({...state,active:false})}));
  await page.getByRole('link',{name:'返回摘要',exact:true}).click();
  await page.waitForURL('http://settings.test/reports');
  await page.close();
}
async function aiDrafts(browser) {
  const connections=[{id:'a',name:'A',base_url:'https://example.com',model:'sample',supports_vision:false,json_mode:true},{id:'b',name:'B',base_url:'https://example.com',model:'sample',supports_vision:false,json_mode:true}];
  let revision='a'.repeat(64),prompts=0,accept=false;
  const data=()=>({available:true,revision,connections,purpose_connections:{}});
  const page=await pageFor(browser,'ai',async(path,request)=>{
    if(path==='/api/ai-settings/connections'){if(request.method()==='PUT'){revision='b'.repeat(64);}return data();}
    if(path==='/api/ai-settings/connections/a'&&request.method()==='DELETE'){connections.splice(0,1);revision='c'.repeat(64);return data();}
    if(path==='/api/features')return {catalog:[],values:{},revision:0};
    if(path==='/api/ai-usage')return {settings:{revision:0,monthly_limit:0,rates:[]},models:[],current_model:{}};
  });
  page.on('dialog',async dialog=>{prompts++;await (accept?dialog.accept():dialog.dismiss());});
  const editA=page.locator('.multi-ai-row').filter({hasText:'A · a'}).getByRole('button',{name:'编辑',exact:true});
  const editB=page.locator('.multi-ai-row').filter({hasText:'B · b'}).getByRole('button',{name:'编辑',exact:true});
  await editA.click();await editB.click();assert.equal(prompts,0,'selecting unchanged connections must not prompt');
  await page.locator('#multi-ai-name').fill('Draft B');await page.locator('#multi-ai-key').fill('synthetic-key');
  await editA.click();assert.equal(await page.locator('#multi-ai-key').inputValue(),'synthetic-key','rejecting edit discard must preserve the key');
  assert.equal(await page.locator('#multi-ai-name').inputValue(),'Draft B');
  await page.getByRole('button',{name:'新建连接',exact:true}).click();assert.equal(await page.locator('#multi-ai-name').inputValue(),'Draft B','rejecting new must preserve the draft');
  accept=true;await page.getByRole('button',{name:'新建连接',exact:true}).click();assert.equal(await page.locator('#multi-ai-key').inputValue(),'');
  await editA.click();await page.locator('#multi-ai-name').fill('Saved A');
  const before=prompts;await page.locator('#multi-ai-save').click();await page.waitForFunction(()=>document.getElementById('multi-ai-id').value==='');
  assert.equal(prompts,before,'successful save must clear the form without asking to discard its committed draft');
  await editA.click();await page.locator('#multi-ai-name').fill('Uncommitted A');
  await page.locator('.multi-ai-row').filter({hasText:'A · a'}).getByRole('button',{name:'删除',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.multi-ai-row').length===1);
  assert.equal(await page.locator('#multi-ai-id').inputValue(),'','deleting the edited connection must clear its stale form');
  await page.close();
}
async function updateLogin(browser) {
  let expired=false;
  const page=await pageFor(browser,'updates',async(path,request,route)=>{
    if(path==='/api/desktop-updates')return {current:'old',versions:[{version:'old',valid:true,compatibility:1,size_bytes:1},{version:'new',valid:true,compatibility:1,size_bytes:1,digest:'a'.repeat(64)}],transaction:{}};
    if(path==='/api/desktop-updates/switch'&&expired){await route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'未登录'})});return false;}
  });
  await page.getByRole('button',{name:'切换到此版本',exact:true}).click();expired=true;
  await page.locator('#version-confirm').click();
  await page.locator('#session-login-dialog').waitFor({state:'visible',timeout:1500});
  await page.getByRole('button',{name:'稍后登录',exact:true}).click();
  assert.equal(await page.locator('#version-preview').isVisible(),true,'re-login must preserve the selected action');
  assert.equal(await page.locator('#version-confirm').isEnabled(),true,'the operation can be retried after re-login');
  await page.close();
}
(async()=>{
  const browser=await chromium.launch({headless:true});let failures=0;
  try{for(const test of [sourceSelection,exitSetup,aiDrafts,updateLogin]){try{await test(browser);console.log('PASS '+test.name);}catch(error){failures++;console.error('FAIL '+test.name+': '+error.message);}}}finally{await browser.close();}
  process.exitCode=failures?1:0;
})().catch(error=>{console.error(error);process.exitCode=1;});
