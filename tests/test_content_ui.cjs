// Behavioral DOM regressions; real template scripts, synthetic endpoint promises.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'qq_digest', 'web', 'templates');
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this.style={}; this.value=''; this.hidden=false; this.options=[]; this.open=false; this.dataset={}; this.classList={toggle(){},add(){}}; }
  set textContent(v) { this.text=String(v); this.children=[]; this.options=[]; }
  get textContent() { return (this.text||'')+this.children.map(c=>c.textContent).join(''); }
  appendChild(n) { this.children.push(n); if(n.tagName==='option')this.options.push(n); return n; }
  addEventListener(k,f) { this.listeners[k]=f; }
  setAttribute(k,v) { this[k]=v; }
  focus() {} close() { this.open=false; } showModal() { this.open=true; }
  scrollIntoView() { this.scrolled=true; }
  querySelectorAll(tag) { return this.children.flatMap(c=>[...(c.tagName===tag?[c]:[]),...c.querySelectorAll(tag)]); }
  querySelector(tag) { return this.querySelectorAll(tag)[0]||null; }
}
const tick=()=>new Promise(setImmediate);
function surface(search='') {
  const nodes=new Map(), requests=[];
  const get=id=>nodes.get(id)||nodes.set(id,new Element('div')).get(id);
  const scope={console,URLSearchParams,location:{search},history:{replaceState(){}},window:{scrollY:0,scrollTo(){}},
    requestAnimationFrame:f=>f(),formatNumber:String,toast(){},destroyMessageContext(){},mountMessageContext(){},
    GroupSelector:{fromParams(){return null;},error(){},params(){}},
    document:{createElement:t=>new Element(t),getElementById:get,querySelectorAll(){return [];},addEventListener(){}},
    api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))};
  vm.createContext(scope);
  return {scope,get,requests};
}
const task=id=>({task_id:id,title:'saved task '+id,owner:'owner',status:'open',revision:0,source_ids:[],group_name:'synthetic'});
const taskHtml=fs.readFileSync(path.join(root,'tasks.html'),'utf8');
function taskScope(search='') {
  const ctx=surface(search);
  vm.runInContext(taskHtml.slice(taskHtml.indexOf('  var taskSuggestions='),taskHtml.indexOf("  document.querySelectorAll('.task-tab')")),ctx.scope);
  return ctx;
}
async function formalDraft() {
  const {scope,get,requests}=taskScope();
  scope.taskItems=[task(1),task(2)];scope.tRenderTasks();
  const card=get('task-list').children[1], form=card.querySelector('form');
  card.querySelectorAll('button').find(n=>n.textContent==='编辑').listeners.click();
  form.querySelector('input').value='UNSAVED HUMAN DRAFT';if(form.listeners.input)form.listeners.input();
  scope.tSetStatus(scope.taskItems[0],'completed');requests.shift().resolve({});await tick();
  requests.shift().resolve({items:[]});requests.shift().resolve({items:[{...task(2),revision:1}],counts:{open:1,today:0,overdue:0}});await tick();
  const after=get('task-list').querySelector('form');
  assert.equal(after.querySelector('input').value,'UNSAVED HUMAN DRAFT','other task reload preserves draft');
  assert.equal(after.hidden,false,'open editor remains open');
  after.listeners.submit({preventDefault(){}});
  assert.equal(requests[0].body.expected_revision,0,'draft retains original revision instead of rebasing');
  requests.shift().reject(Object.assign(Error('待办已被修改'),{status:409}));await tick();
  assert.equal(after.querySelector('input').value,'UNSAVED HUMAN DRAFT');
  const reconcile=get('task-list').querySelectorAll('button').find(n=>n.textContent==='核对最新版本并保留草稿');
  assert.ok(reconcile,'conflict offers explicit recovery action');assert.equal(reconcile.hidden,false);
  let inspected='';scope.window.confirm=message=>{inspected=message;return false;};
  reconcile.listeners.click();requests.shift().resolve({items:[{...task(2),title:'latest stored',owner:'latest owner',due_date:'2026-10-15',revision:1}]});await tick();
  assert.ok(inspected.includes('latest stored')&&inspected.includes('latest owner')&&inspected.includes('2026-10-15'));
  assert.equal(scope.taskDrafts.get(2).revision,0,'cancel does not rebase draft');
  scope.window.confirm=()=>true;
  reconcile.listeners.click();requests.shift().resolve({items:[{...task(2),title:'latest stored',owner:'latest owner',due_date:'2026-10-15',revision:1}]});await tick();
  assert.equal(scope.taskDrafts.get(2).revision,1,'explicit acceptance records inspected version');
  assert.equal(requests.length,0,'acceptance never submits automatically');
  assert.equal(after.querySelector('input').value,'UNSAVED HUMAN DRAFT');
  after.listeners.submit({preventDefault(){}});assert.equal(requests[0].body.expected_revision,1);
  requests.shift().resolve({task:{...task(2),revision:2}});await tick();
  requests.shift().resolve({items:[]});requests.shift().resolve({items:[{...task(2),title:'UNSAVED HUMAN DRAFT',revision:2}],counts:{open:1,today:0,overdue:0}});await tick();
  assert.equal(scope.taskDrafts.has(2),false,'successful manual save clears only saved draft');
}
async function topicsReadFirst(search='') {
  const {scope,get,requests}=surface(search);
  vm.runInContext(fs.readFileSync(path.join(root,'topics_script.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1],scope);
  assert.equal(requests[0].url,'/api/groups');requests.shift().resolve({groups:[]});
  assert.equal(requests[0].method,'GET','read archived topics before attempting refresh');
  const detail=search.includes('topic_id');
  assert.ok(requests[0].url.startsWith(detail?'/api/topics/8':'/api/topics?'));
  if(detail)requests.shift().resolve({topic_id:8,title:'existing detail',status:'tracking',revision:1,groups:[],discussion_count:0,discussions:[],page:1,page_size:20,total:0});
  else requests.shift().resolve({topics:[{topic_id:8,title:'existing topic',status:'tracking',groups:[],discussion_count:1}],page:1,page_size:20,total:1});
  await tick();assert.equal(requests[0].url,'/api/topics/refresh');
  requests.shift().reject(Error('busy refresh'));await tick();
  assert.ok(get(detail?'topics-title':'topics-list').textContent.includes(detail?'existing detail':'existing topic'),'refresh failure retains existing read view');
  assert.ok(get('topics-status').textContent.includes('busy refresh'));
}
async function taskDeepLink() {
  const {scope,get,requests}=taskScope('?task_id=2');
  assert.equal(typeof scope.tOpenLinkedTask,'function','deep link consumer is available');
  scope.tOpenLinkedTask();
  assert.equal(get('task-status').value,'all');assert.equal(get('task-list-view').hidden,false);
  const pending=scope.tLoad();
  requests.shift().resolve({items:[]});requests.shift().resolve({items:[task(1),{...task(2),status:'completed'}],counts:{open:1,today:0,overdue:0}});await pending;
  assert.equal(get('task-list').children[1].scrolled,true,'scrolls to the exact completed task');
}
async function editsDuringOwnSave() {
  const {scope,get,requests}=taskScope();scope.taskItems=[task(1)];scope.tRenderTasks();
  const card=get('task-list').children[0],form=card.querySelector('form');
  card.querySelectorAll('button').find(n=>n.textContent==='编辑').listeners.click();
  const input=form.querySelector('input');input.value='submitted A';form.listeners.input();
  form.listeners.submit({preventDefault(){}});const saving=requests.shift();
  input.value='newer unsaved B';form.listeners.input();
  saving.resolve({task:{...task(1),title:'submitted A',revision:1}});await tick();
  requests.shift().resolve({items:[]});requests.shift().resolve({items:[{...task(1),title:'submitted A',revision:1}],counts:{open:1,today:0,overdue:0}});await tick();
  const after=get('task-list').querySelector('form');
  assert.equal(after.querySelector('input').value,'newer unsaved B','save of A cannot erase B typed while waiting');
  assert.equal(after.hidden,false);after.listeners.submit({preventDefault(){}});
  assert.equal(requests.at(-1).body.expected_revision,1,'newer draft uses the version returned by its own successful save');
}
async function composeReplyAfterDifferentPanelOpened() {
  const {scope,get,requests}=taskScope('?group_id=123&msg_id=m1');scope.tOpenCompose();
  const context=requests.shift();context.resolve({messages:[]});await tick();
  const form=get('task-panel-content').querySelector('form');form.querySelector('input').value='my task';
  form.listeners.submit({preventDefault(){}});const saving=requests.shift();
  scope.tClosePanel();scope.tOpenSource(456,'m2','other source',null);const latestToken=scope.taskPanelToken;
  saving.resolve({task:task(1)});await tick();
  assert.equal(get('task-panel').hidden,false,'old create reply must not close a newly opened source panel');
  assert.equal(scope.taskPanelToken,latestToken,'old create reply does not invalidate current source request');
}
async function reloadDuringSaveDoesNotPermitSecondSubmission() {
  const {scope,get,requests}=taskScope();scope.taskItems=[task(1)];scope.tRenderTasks();
  let card=get('task-list').children[0],form=card.querySelector('form');
  card.querySelectorAll('button').find(n=>n.textContent==='编辑').listeners.click();
  form.querySelector('input').value='submitted A';form.listeners.input();form.listeners.submit({preventDefault(){}});
  scope.tRenderTasks();form=get('task-list').querySelector('form');
  assert.equal(form.querySelectorAll('button')[0].disabled,true,'reloaded editor reflects the same task save still running');
  form.listeners.submit({preventDefault(){}});assert.equal(requests.length,1,'only one write may be in flight for this task');
}
async function staleConflictCannotOverwriteReloadedDraft() {
  const {scope,get,requests}=taskScope();scope.taskItems=[task(1)];scope.tRenderTasks();
  let card=get('task-list').children[0],form=card.querySelector('form');
  card.querySelectorAll('button').find(n=>n.textContent==='编辑').listeners.click();
  form.querySelector('input').value='submitted A';form.listeners.input();form.listeners.submit({preventDefault(){}});const saving=requests.shift();
  scope.tRenderTasks();form=get('task-list').querySelector('form');
  form.querySelector('input').value='newest C after reload';form.listeners.input();
  saving.reject(Object.assign(Error('待办已被修改'),{status:409}));await tick();scope.tRenderTasks();
  assert.equal(get('task-list').querySelector('form').querySelector('input').value,'newest C after reload','late conflict must preserve draft from the current editor');
  assert.equal(scope.taskDrafts.get(1).conflict,true);
}
(async()=>{let failed=0;for(const test of [formalDraft,topicsReadFirst,()=>topicsReadFirst('?topic_id=8'),taskDeepLink,editsDuringOwnSave,composeReplyAfterDifferentPanelOpened,reloadDuringSaveDoesNotPermitSecondSubmission,staleConflictCannotOverwriteReloadedDraft]){try{await test();}catch(error){failed++;console.error(error);}}assert.equal(failed,0);console.log('PASS: task drafts, save races, sources, topics read-first and deep links');})().catch(e=>{console.error(e);process.exitCode=1;});
