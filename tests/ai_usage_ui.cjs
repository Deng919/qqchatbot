const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const path='qq_digest/web/templates/ai_usage_script.html';
assert.ok(fs.existsSync(path),'AI usage settings script is not implemented');
class Node {constructor(){this.children=[];this.value='';this.disabled=false;this.hidden=false;this.dataset={};this._text='';}appendChild(n){this.children.push(n);return n;}set textContent(v){this._text=v;this.children=[];}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}addEventListener(){}}
const ids=['ai-usage-refresh','ai-usage-summary','ai-usage-form','ai-usage-options','ai-usage-limit','ai-usage-rates','ai-usage-save','ai-usage-result'];
const nodes=Object.fromEntries(ids.map(id=>[id,new Node()]));const calls=[];
const context={document:{getElementById:id=>nodes[id],createElement:()=>new Node()},console,Number,Map,Promise};
context.api=(method,url,body)=>new Promise((resolve,reject)=>calls.push({method,url,body,resolve,reject}));
vm.createContext(context);vm.runInContext(fs.readFileSync(path,'utf8').replace(/<\/?script>/g,''),context);
const data=(revision=0)=>({month:'2026-10',calls:1,successful:1,failed:0,unknown:0,pending:0,input_tokens:0,output_tokens:0,usage_unknown:1,cost_unknown:1,estimated_cost:null,models:[],current_model:{provider:'https://api.example.com',model:'model'},settings:{revision,monthly_limit:0,timezone:'Asia/Shanghai',rates:[]}});
const tick=()=>new Promise(setImmediate);
(async()=>{
  context.loadAIUsage();assert.equal(calls.length,2);calls[1].resolve(data(2));await tick();calls[0].resolve(data(0));await tick();
  assert.equal(context.usageRevision,2,'stale fetch must not replace current settings');
  assert.match(nodes['ai-usage-summary'].textContent,/费用未知/);assert.match(nodes['ai-usage-summary'].textContent,/1 次用量未知/);
  nodes['ai-usage-limit'].value='5';context.saveAIUsage({preventDefault(){}});context.saveAIUsage({preventDefault(){}});
  assert.equal(calls.length,3,'duplicate save must not issue a second request');assert.equal(calls[2].body.expected_revision,2);assert.equal(calls[2].body.monthly_limit,5);
  calls[2].reject(new Error('用量设置已在其他窗口更新，请刷新后再保存'));await tick();
  assert.equal(nodes['ai-usage-limit'].value,'5','failed save must preserve draft');assert.equal(context.usageRevision,2);
  assert.equal(nodes['ai-usage-save'].disabled,false);
  context.saveAIUsage({preventDefault(){}});const result=data(3);result.settings.monthly_limit=5;calls[3].resolve(result);await tick();
  assert.equal(context.usageRevision,3);assert.match(nodes['ai-usage-result'].textContent,/已保存/);
  context.renderAIUsage({...data(3),calls:2,successful:1,failed:1});
  assert.match(nodes['ai-usage-summary'].textContent,/失败 1（50.0%）/);
  console.log('Usage unknown display, stale response, duplicate save and conflict draft retention verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
