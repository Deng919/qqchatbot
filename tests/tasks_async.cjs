const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.style={};this.value='';this.hidden=false;}
  set textContent(v){this.text=String(v);this.children=[];} get textContent(){return (this.text||'')+this.children.map(c=>c.textContent).join('');}
  appendChild(n){this.children.push(n);return n;} addEventListener(name,fn){this.listeners[name]=fn;} setAttribute(k,v){this[k]=v;}
  querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tagName===tag?[c]:[]),...c.querySelectorAll(tag)]);}
  querySelector(tag){return this.querySelectorAll(tag)[0]||null;}
}
const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);},requests=[],notices=[];
const scope={console,URLSearchParams,formatNumber:String,toast:m=>notices.push(m),document:{createElement:tag=>new Element(tag),getElementById:node},api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))};
const html=fs.readFileSync('qq_digest/web/templates/tasks.html','utf8');vm.createContext(scope);
vm.runInContext(html.slice(html.indexOf('  var taskSuggestions='),html.indexOf("  document.querySelectorAll('.task-tab')")),scope);
const tick=()=>new Promise(setImmediate),counts={open:1,today:0,overdue:0};
const suggestion=key=>({key,title:'建议 '+key,owner:'',due_date:null,group_name:'合成群',report_date:'2026-10-14',source_ids:[]});
function respond(suggestions,tasks=[]){requests.shift().resolve({items:suggestions});requests.shift().resolve({items:tasks,counts});}
(async()=>{
  if(!process.argv.includes('--draft-only')){
    node('task-status').value='open';scope.tLoad();const old=requests.splice(0);
    node('task-status').value='completed';scope.tLoad();respond([],[{task_id:2,title:'已完成任务',status:'completed',group_name:'合成群',source_ids:[]}]);await tick();
    old[0].resolve({items:[]});old[1].resolve({items:[{task_id:1,title:'旧进行中任务',status:'open',group_name:'合成群',source_ids:[]}],counts});await tick();
    assert.equal(scope.taskItems[0].task_id,2,'older filter response must not replace the completed list');
    scope.tLoad();const failing=requests.splice(0);scope.tLoad();respond([]);await tick();failing[0].reject(Error('旧加载失败'));failing[1].resolve({items:[],counts});await tick();
    assert.equal(notices.length,0,'stale failures must not show errors for the current filter');
  }
  const savedTask={task_id:8,title:'独立可读待办',owner:'',due_date:null,status:'open',revision:0,group_name:'合成群',source_ids:[]};
  const partial=scope.tLoad(),partialRequests=requests.splice(0);
  partialRequests[0].reject(Error('建议暂时不可用'));
  partialRequests[1].resolve({items:[savedTask],counts});await partial;
  assert.match(node('task-list').textContent,/独立可读待办/,'a failed suggestion read cannot hide successfully loaded tasks');
  assert.equal(node('task-count-open').textContent,'1');
  assert.match(node('task-suggestions').textContent,/读取失败.*建议暂时不可用/,'the failed view keeps a visible error after any toast expires');
  assert.equal(node('task-count-suggestions').textContent,'—','a failed count must not look like zero or a stale total');
  const retry=node('task-suggestions').querySelector('button');assert.ok(retry,'the failed view offers an explicit retry');
  retry.listeners.click();respond([suggestion('draft')],[savedTask]);await tick();
  const retainedForm=node('task-suggestions').querySelector('form'),retainedInput=retainedForm.querySelector('input');
  retainedInput.value='故障前建议草稿';retainedForm.listeners.input({});
  const failedAgain=scope.tLoad(),failedRequests=requests.splice(0);
  failedRequests[0].reject(Error('建议再次不可用'));failedRequests[1].resolve({items:[savedTask],counts});await failedAgain;
  node('task-suggestions').querySelector('button').listeners.click();respond([suggestion('draft')],[savedTask]);await tick();
  assert.equal(node('task-suggestions').querySelector('input').value,'故障前建议草稿','suggestion drafts survive a failed read and retry');
  node('task-list').querySelectorAll('button').find(button=>button.textContent==='编辑').listeners.click();
  const retainedTaskForm=node('task-list').querySelector('form');retainedTaskForm.querySelector('input').value='故障前待办草稿';retainedTaskForm.listeners.input({});
  const inverse=scope.tLoad(),inverseRequests=requests.splice(0);
  inverseRequests[0].resolve({items:[suggestion('draft'),suggestion('extra')]});await tick();
  assert.equal(node('task-count-suggestions').textContent,'2','one completed endpoint can render before the other settles');
  inverseRequests[1].reject(Error('待办暂时不可用'));await inverse;
  assert.match(node('task-suggestions').textContent,/建议 draft/,'a failed task read cannot hide successfully loaded suggestions');
  assert.match(node('task-list').textContent,/读取失败.*待办暂时不可用/);
  ['open','today','overdue'].forEach(key=>assert.equal(node('task-count-'+key).textContent,'—'));
  node('task-list').querySelector('button').listeners.click();respond([suggestion('draft')],[savedTask]);await tick();
  assert.match(node('task-list').textContent,/独立可读待办/,'retry recovers the failed task view');
  assert.equal(node('task-list').querySelector('input').value,'故障前待办草稿','task drafts survive a failed read and retry');
  assert.equal(node('task-list').querySelector('form').hidden,false,'retry keeps an edited task open');
  scope.tLoad();respond([suggestion('A'),suggestion('B')]);await tick();
  let forms=node('task-suggestions').querySelectorAll('form'),draft=forms[1].querySelectorAll('input');
  draft[0].value='B 未保存内容';draft[1].value='B 负责人';draft[2].value='2026-10-20';forms[1].listeners.input?.({});
  forms[0].listeners.submit({preventDefault(){}});requests.shift().resolve({});await tick();
  // Edit again while the successful A decision's refresh is still pending.
  draft[0].value='B 最后输入';forms[1].listeners.input?.({});
  respond([suggestion('B')]);await tick();
  forms=node('task-suggestions').querySelectorAll('form');
  assert.equal(forms.length,1);assert.deepEqual(forms[0].querySelectorAll('input').map(n=>n.value),['B 最后输入','B 负责人','2026-10-20'],'processing A must retain all of B draft fields');
  forms[0].listeners.submit({preventDefault(){}});const submission=requests.shift();
  assert.equal(submission.body.title,'B 最后输入');submission.resolve({});await tick();respond([]);await tick();
  scope.tLoad();respond([suggestion('B')]);await tick();
  assert.equal(node('task-suggestions').querySelector('input').value,'建议 B','removed suggestion drafts must not reappear');
  console.log('Task filter races, late errors and independent suggestion drafts verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
