const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
class Node {
  constructor(){this.childNodes=[];this.textContent='';this.value='active';this.disabled=false;this.listeners={};}
  appendChild(n){this.childNodes.push(n);return n;}
  set textContent(value){this._text=value;this.childNodes=[];}
  get textContent(){return this._text;}
  addEventListener(k,f){this.listeners[k]=f;}
  querySelectorAll(){return this.childNodes.flatMap(n=>[n,...n.querySelectorAll()]).filter(n=>n.tag==='button');}
}
const nodes={},requests=[];
const doc={getElementById(id){return nodes[id]||=(new Node());},createElement(tag){const n=new Node();n.tag=tag;return n;}};
const ctx=vm.createContext({document:doc,console,Date,Promise,formatNumber:n=>String(n),toast(){},confirm:()=>true,
 api(method,url,body){return new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));}});
const src=fs.readFileSync('qq_digest/web/templates/failures.html','utf8');
vm.runInContext(src.match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
const tick=()=>new Promise(setImmediate);
const item=(id,status)=>({id,fingerprint:id.repeat(64).slice(0,64),status,kind:'message_sync',group_name:'甲',at:'',error:'失败'});
const data=items=>({items,counts:{active:items.filter(i=>i.status==='active').length,pending:1,recovered:1,ignored:1}});
(async()=>{
  requests.shift().resolve(data([item('a','active'),item('b','pending'),item('c','recovered'),item('d','ignored')]));await tick();
  assert.equal(typeof ctx.fSetIgnored,'function','ignore behavior missing');
  assert.equal(ctx.fVisible().length,2,'active filter hides ignored and recovered');
  ctx.fIgnoreAll();const first=requests.shift();
  assert.equal(first.url,'/api/failures/ignore');
  assert.deepEqual(JSON.parse(JSON.stringify(first.body)),{items:[{id:'a',fingerprint:'a'.repeat(64)}]});
  assert.equal(nodes['failure-ignore-all'].disabled,true);
  ctx.fIgnoreAll();ctx.fSetIgnored([item('a','active')],false);assert.equal(requests.length,0,'duplicate mutation guarded');
  first.resolve({changed:1,skipped:0});await tick();requests.shift().resolve(data([item('a','ignored'),item('z','active')]));await tick();
  assert.equal(ctx.fVisible()[0].id,'z','new arrivals remain active after bulk snapshot');
  nodes['failure-filter'].value='ignored';ctx.fRender();assert.equal(nodes['failure-ignore-all'].disabled,true);
  ctx.fSetIgnored([ctx.fVisible()[0]],true);const restore=requests.shift();assert.equal(restore.url,'/api/failures/restore');
  restore.resolve({changed:1,skipped:0});await tick();requests.shift().resolve(data([item('a','active'),item('z','active')]));await tick();
  assert.equal(nodes['failure-filter'].value,'ignored');assert.equal(ctx.fVisible().length,0);
  nodes['failure-filter'].value='active';ctx.fRender();
  ctx.fLoad();const stale=requests.shift();ctx.fLoad();requests.shift().resolve(data([item('a','active')]));await tick();
  ctx.fSetIgnored([item('a','active')],false);const mutation=requests.shift();
  stale.resolve(data([item('wrong','active')]));await tick();assert.equal(ctx.failureItems[0].id,'a','stale GET rejected');
  mutation.reject(new Error('网络错误'));await tick();requests.shift().reject(new Error('读取失败'));await tick();
  assert.equal(nodes['failure-ignore-all'].disabled,true,'failed reload prevents stale actions');
  ctx.fLoad();requests.shift().resolve(data([item('a','active')]));await tick();assert.equal(nodes['failure-ignore-all'].disabled,false);
  console.log('Ignore snapshots, retry protection, duplicate guard, stale reads, restore and error recovery verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
