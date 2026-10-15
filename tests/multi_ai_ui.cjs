const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const file='qq_digest/web/templates/ai_connections_script.html';
assert.ok(fs.existsSync(file),'missing dedicated multi AI script');
class Node{constructor(){this.children=[];this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.style={};this.listeners={};this._text='';}appendChild(n){this.children.push(n);return n;}set textContent(v){this._text=v;this.children=[];}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}addEventListener(k,f){this.listeners[k]=f;}focus(){}querySelectorAll(){return Object.values(nodes);}}
const nodes={};function get(id){return nodes[id]||(nodes[id]=new Node());}
const calls=[],ctx={document:{getElementById:get,createElement:()=>new Node()},confirm:()=>true,console};
ctx.api=(method,url,body)=>new Promise((resolve,reject)=>calls.push({method,url,body,resolve,reject}));
vm.createContext(ctx);vm.runInContext(fs.readFileSync(file,'utf8').replace(/<\/?script>/g,''),ctx);
const tick=()=>new Promise(setImmediate), event={preventDefault(){}};
const result=(rev='a')=>({available:true,revision:rev.repeat(64),connections:[{id:'other',name:'Other',base_url:'https://other.example/v1',model:'chat',supports_vision:true,json_mode:true,key_configured:true}],purpose_connections:{}});
(async()=>{
 calls[0].resolve(result());await tick();
 assert.match(get('multi-ai-list').textContent,/测试连接/);
 ctx.editAIConnection(ctx.aiConnections[0]);get('multi-ai-name').value='draft';
 ctx.saveAIConnection(event);ctx.saveAIConnection(event);
 assert.equal(calls.length,2);assert.equal(calls[1].body.expected_revision,'a'.repeat(64));assert.equal(get('multi-ai-name').disabled,true);
 calls[1].reject(Object.assign(new Error('conflict'),{status:409}));await tick();
 assert.equal(get('multi-ai-name').value,'draft');assert.equal(ctx.aiConnectionRevision,'a'.repeat(64));
 get('ai-purpose-summary').value='other';ctx.aiPurposeDirty=true;
 ctx.saveAIConnection(event);calls[2].resolve(result('b'));await tick();
 assert.equal(get('ai-purpose-summary').value,'other','connection save must keep unsaved purpose selection');assert.equal(get('multi-ai-key').value,'');
 ctx.testAIConnection('other');ctx.testAIConnection('other');
 assert.equal(calls.length,4);assert.equal(calls[3].url,'/api/ai-settings/connections/test');assert.equal(calls[3].body.expected_revision,'b'.repeat(64));
 calls[3].resolve({ok:true});await tick();assert.match(get('multi-ai-result').textContent,/测试成功/);
 ctx.editAIConnection(ctx.aiConnections[0]);get('multi-ai-name').value='keep';ctx.aiConnectionDirty=true;
 ctx.saveAIPurposes(event);calls[4].resolve(result('c'));await tick();assert.equal(get('multi-ai-name').value,'keep');
 console.log('Multi AI revisions, duplicate guards, conflict drafts, purpose drafts and test button passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
