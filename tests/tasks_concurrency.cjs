const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('qq_digest/web/templates/tasks.html', 'utf8');
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this.value=''; this.hidden=false; this.disabled=false; this.style={}; }
  set textContent(value) { this.text=String(value); this.children=[]; }
  get textContent() { return this.text || ''; }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(event, callback) { this.listeners[event]=callback; }
  setAttribute(name,value) { this[name]=value; }
  querySelector(tag) { return this.querySelectorAll(tag)[0] || null; }
  querySelectorAll(tag) { return this.children.flatMap(child => [ ...(child.tagName===tag ? [child] : []), ...child.querySelectorAll(tag)]); }
}
const nodes = new Map();
const sandbox = { document: { createElement:tag=>new Element(tag), getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);}}, console, URLSearchParams };
vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf('  var taskSuggestions='), source.indexOf("  document.querySelectorAll('.task-tab')")), sandbox);
let requests=[], loads=0, notices=[];
sandbox.tLoad=()=>{loads++;};
sandbox.toast=(message)=>notices.push(message);
sandbox.api=(method,url,body)=>{requests.push({method,url,body:JSON.parse(JSON.stringify(body))});return Promise.resolve({});};
const task={task_id:5,title:'旧内容',owner:'旧负责人',due_date:'2026-10-01',status:'open',revision:3,group_name:'合成群',source_ids:[]};
const tick=()=>new Promise(setImmediate);
(async()=>{
  if (!process.argv.includes('--edit-only')) {
    sandbox.tSetStatus(task,'completed');
    await tick();
    assert.deepEqual(requests[0].body,{status:'completed'},'status actions must never send stale text, owner or deadline');
  }
  sandbox.taskItems=[task];
  sandbox.tRenderTasks();
  const host=nodes.get('task-list'), form=host.querySelector('form'), fields=form.querySelectorAll('input');
  fields[0].value='我的未保存草稿'; fields[1].value='草稿负责人'; fields[2].value='2026-10-05';
  form.hidden=false;
  requests=[]; loads=0;
  sandbox.api=(method,url,body)=>{requests.push({method,url,body:JSON.parse(JSON.stringify(body))});const error=new Error('待办已被修改，请重新读取');error.status=409;return Promise.reject(error);};
  form.listeners.submit({preventDefault(){}});
  await tick();
  assert.equal(requests[0].body.expected_revision,3,'full edit must include the displayed integer revision');
  assert.equal(requests.length,1,'conflict must not automatically resend the draft');
  assert.equal(loads,0,'conflict must not discard the draft by reloading');
  assert.equal(host.querySelector('form'),form);
  assert.deepEqual(fields.map(input=>input.value),['我的未保存草稿','草稿负责人','2026-10-05']);
  assert.equal(form.hidden,false);
  assert.equal(form.querySelector('button').disabled,false);
  assert.ok(notices.some(message=>message.includes('草稿') && message.includes('重新读取')),'conflict must explain that the draft survives and current data needs reloading');
  console.log('Task status payload and conflict draft retention verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
