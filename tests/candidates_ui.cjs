const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('qq_digest/web/templates/candidates.html','utf8');
function node(){return {value:'',hidden:false,disabled:false,children:[],listeners:{},dataset:{},_text:'',_html:'',get textContent(){return this._text},set textContent(v){this._text=v;this.children=[]},get innerHTML(){return this._html},set innerHTML(v){this._html=v;this.children=[]},appendChild(n){this.children.push(n)},addEventListener(k,v){this.listeners[k]=v},focus(){}}}
const elements={},requests=[],notices=[],listeners={},get=id=>elements[id]??=node(),submit=node();
const scope={console,URLSearchParams,formatNumber:String,escapeHtml:v=>String(v??''),toast:(m,type)=>notices.push({m,type}),appendBookmarkAction(){},confirm:()=>true,location:{search:''},document:{getElementById:get,createElement:node,querySelector:()=>submit,querySelectorAll:()=>[],addEventListener(k,fn){listeners[k]=fn;}},GroupSelector:{params(p,n){if(n.value)p.append('group_ids',n.value)},error(){}},api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))};
vm.createContext(scope);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],scope);
const tick=()=>new Promise(setImmediate),item=(id=1)=>({candidate_id:id,candidate_type:'resource',status:'pending',title:'标题 '+id,link:'',reason:'理由',excerpt:'片段',group_name:'合成群',message_ids:[],source_context:[]});
function editFields(){get('edit-type').value='resource';get('edit-title').value='修改标题';get('edit-reason').value='修改理由';}
async function pendingSwitch(){scope.currentCandidate=item();scope.viewCandidate(2);const loading=requests.shift();assert.equal(scope.currentCandidate,null,'a pending target switch must invalidate the old editable candidate');scope.saveCandidateEdit({preventDefault(){}});assert.equal(requests.length,0,'old candidate cannot save under a new view token');loading.resolve(item(2));await tick();assert.equal(scope.currentCandidate.candidate_id,2);}
async function pendingRefresh(){scope.currentCandidate=item();editFields();get('candidate-edit-form').hidden=false;scope.saveCandidateEdit({preventDefault(){}});requests.shift().resolve(item());await tick();assert.equal(get('candidate-edit-form').hidden,true,'save response must not leave an editable form while its detail refresh is pending');requests.splice(0).forEach(r=>r.resolve(r.url.includes('?')?{candidates:[]} : item()));await tick();}
(async()=>{
  requests.shift().resolve({groups:[]});requests.shift().resolve({candidates:[]});await tick();
  if(process.argv.includes('--switch-only')){await pendingSwitch();return;}
  if(process.argv.includes('--refresh-only')){await pendingRefresh();return;}
  if(!process.argv.includes('--save-only')&&!process.argv.includes('--close-only')){
    scope.viewCandidate(1);requests.shift().resolve(item());await tick();
    assert.match(get('candidate-detail-actions').innerHTML,/编辑候选/,'editing must not imply publication');
    assert.doesNotMatch(get('candidate-detail-actions').innerHTML,/编辑后入库/);
    assert.match(get('candidate-detail-actions').innerHTML,/入库/,'detail must have a separate explicit confirm action');
  }
  scope.currentCandidate=item();editFields();scope.saveCandidateEdit({preventDefault(){}});const saved=requests.shift();
  if(!process.argv.includes('--close-only')){scope.saveCandidateEdit({preventDefault(){}});assert.equal(requests.length,0,'duplicate saves must be blocked');}scope.closeCandidateDetail();saved.resolve(item());await tick();
  assert.equal(requests.filter(r=>r.url==='/api/candidates/1').length,0,'closed detail must not reopen after saving');
  assert.ok(!notices.some(n=>n.m.includes('保存失败')),'successful save after closure cannot be reported as a failure');
  requests.splice(0).forEach(r=>r.resolve({candidates:[]}));await tick();
  scope.currentCandidate=item();editFields();scope.saveCandidateEdit({preventDefault(){}});const oldSave=requests.shift();
  scope.viewCandidate(2);requests.shift().resolve(item(2));await tick();scope.editCandidate();get('edit-title').value='另一候选草稿';
  get('candidate-detail-feedback').textContent='候选 2 当前状态';oldSave.resolve(item());await tick();
  assert.equal(scope.currentCandidate.candidate_id,2);assert.equal(get('edit-title').value,'另一候选草稿');assert.equal(get('candidate-edit-form').hidden,false);
  assert.equal(get('candidate-detail-feedback').textContent,'候选 2 当前状态','Detached success cannot offer publication for a different candidate');
  requests.splice(0).forEach(r=>r.resolve({candidates:[]}));await tick();
  scope.currentCandidate=item();editFields();scope.saveCandidateEdit({preventDefault(){}});const detachedFailure=requests.shift();
  scope.viewCandidate(2);requests.shift().resolve(item(2));await tick();get('candidate-detail-feedback').textContent='候选 2 当前状态';
  detachedFailure.reject(Error('候选 1 保存失败'));await tick();
  assert.equal(get('candidate-detail-feedback').textContent,'候选 2 当前状态','Detached errors must not claim the current candidate draft was preserved');
  scope.currentCandidate=item();const detachedAction=scope.candidateAction(1,'confirm'),detachedActionRequest=requests.shift();
  scope.viewCandidate(2);requests.shift().resolve(item(2));await tick();get('candidate-detail-feedback').textContent='候选 2 当前状态';detachedActionRequest.reject(Error('候选 1 操作失败'));await detachedAction;
  assert.equal(get('candidate-detail-feedback').textContent,'候选 2 当前状态','Detached action errors must not enter a different candidate detail');
  scope.editCandidate();let prevented=false;listeners['qq-digest-check-unsaved']?.({preventDefault(){prevented=true;}});assert.equal(prevented,false,'Opening an unchanged candidate editor is not dirty');
  get('edit-title').value='未保存草稿';listeners['qq-digest-check-unsaved']?.({preventDefault(){prevented=true;}});assert.equal(prevented,true,'Changed candidate draft participates in shared navigation guard');
  scope.cancelCandidateEdit();prevented=false;listeners['qq-digest-check-unsaved']?.({preventDefault(){prevented=true;}});assert.equal(prevented,false,'Canceled candidate editor does not block navigation');
  get('candidate-status').value='pending';get('candidate-group').value='123';get('candidate-type').value='resource';get('candidate-query').value='教程';get('candidate-date-from').value='2026-10-01';get('candidate-date-to').value='2026-10-14';
  assert.ok(!scope.renderCandidate(item()).includes('<form'),'status actions must not navigate via a form');
  const action=scope.candidateAction(1,'later');const mutation=requests.shift();scope.candidateAction(1,'later');assert.equal(requests.length,0,'duplicate status action blocked');
  assert.equal(mutation.url,'/api/candidates/1/later');mutation.resolve({status:'later'});await tick();
  const reload=requests.find(r=>r.url.startsWith('/api/candidates?'));assert.ok(reload);const query=new URLSearchParams(reload.url.split('?')[1]);assert.equal(query.get('group_ids'),'123');assert.equal(query.get('q'),'教程');assert.equal(query.get('status'),'pending');assert.equal(query.get('date_from'),'2026-10-01');
  requests.splice(0).forEach(r=>r.resolve(r.url.startsWith('/api/candidates?')?{candidates:[]}:{...item(2)}));await action;await tick();
  const failed=scope.candidateAction(1,'confirm');const failure=requests.shift();const e=Error('后台任务正在运行');e.status=409;failure.reject(e);await failed;
  assert.match(get('candidate-feedback').textContent,/后台任务正在运行/);assert.equal(get('candidate-group').value,'123');
  scope.candidateAction(1,'confirm');assert.equal(requests.length,1,'failed action allows retry');requests.shift().reject(Error('重试失败'));await tick();
  scope.loadCandidates();const oldList=requests.shift();get('candidate-query').value='最新';scope.loadCandidates();requests.shift().resolve({candidates:[item(3)]});await tick();oldList.resolve({candidates:[item(4)]});await tick();assert.match(get('candidate-list').innerHTML,/标题 3/);assert.doesNotMatch(get('candidate-list').innerHTML,/标题 4/);
  await pendingSwitch();await pendingRefresh();
  scope.currentCandidate=item();get('candidate-detail').hidden=false;scope.editCandidate();let discardPrompts=0;scope.confirm=()=>{discardPrompts++;return false;};
  get('edit-title').value='必须保留的候选草稿';scope.closeCandidateDetail();
  assert.equal(get('candidate-detail').hidden,false,'Canceling local close preserves the candidate editor');assert.equal(scope.currentCandidate.candidate_id,1);assert.equal(get('edit-title').value,'必须保留的候选草稿');assert.equal(discardPrompts,1);
  listeners.keydown({key:'Escape'});assert.equal(discardPrompts,2,'Escape asks once');assert.equal(get('candidate-edit-form').hidden,false);
  await scope.viewCandidate(2);assert.equal(discardPrompts,3);assert.equal(requests.length,0,'Canceled candidate switch must not issue another detail request');assert.equal(scope.currentCandidate.candidate_id,1);
  scope.cancelCandidateEdit();assert.equal(discardPrompts,4);assert.equal(get('candidate-edit-form').hidden,false,'Canceling edit discard keeps its draft visible');
  scope.editCandidate();assert.equal(discardPrompts,5);assert.equal(get('edit-title').value,'必须保留的候选草稿','Reopening editor cannot overwrite a canceled draft');
  scope.confirm=()=>{discardPrompts++;return true;};scope.closeCandidateDetail();assert.equal(discardPrompts,6);assert.equal(get('candidate-detail').hidden,true);
  scope.currentCandidate=item();scope.editCandidate();const promptsBeforeSave=discardPrompts;get('edit-title').value='已保存的草稿';const noDiscardSave=scope.saveCandidateEdit({preventDefault(){}});requests.shift().resolve({});await tick();assert.equal(discardPrompts,promptsBeforeSave,'A successful save refresh must not ask to discard the saved snapshot');requests.splice(0).forEach(r=>r.resolve(r.url.includes('?')?{candidates:[]}:item()));await noDiscardSave;await tick();
  console.log('Candidate edit semantics, detached save, async filters, failure and duplicate protections verified');
})().catch(e=>{console.error(e);process.exitCode=1});
