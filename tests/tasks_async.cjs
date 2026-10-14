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
