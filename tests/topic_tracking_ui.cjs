const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(){return {value:'',hidden:false,open:false,disabled:false,children:[],options:[],attrs:{},style:{},isConnected:true,_text:'',get textContent(){return this._text;},set textContent(v){this._text=v;this.children=[];this.options=[];},appendChild(n){this.children.push(n);if(n.tag==='option')this.options.push(n);},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},addEventListener(){},focus(){},showModal(){this.open=true;},close(){this.open=false;},scrollIntoView(){}};}
const elements={},requests=[],get=id=>elements[id]??=node(),tick=()=>new Promise(r=>setImmediate(r));
const scope={console,URLSearchParams,Date,Number,Array,Object,Promise,String,Math,location:{search:''},history:{replaceState(){}},window:{scrollY:0,scrollTo(){}},requestAnimationFrame(f){f();},document:{activeElement:node(),getElementById:get,createElement(tag){let n=node();n.tag=tag;return n;}},destroyMessageContext(){},mountMessageContext(){},api(method,url,body){return new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));}};
vm.createContext(scope);
const script=fs.readFileSync('qq_digest/web/templates/topics_script.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
vm.runInContext(script,scope);
const groupsSource=fs.readFileSync('qq_digest/web/templates/groups.html','utf8');
vm.runInContext(groupsSource.slice(groupsSource.indexOf('  function syncCell('),groupsSource.indexOf('  function toggleControl(')),scope);
scope.escapeHtml=String;
const syncStatus=scope.syncCell({sync_status:'report_collection_completed'});
assert.doesNotMatch(syncStatus,/采集失败/,'A completed limited report collection must not be classified as failure');
const topic=id=>({topic_id:id,title:'项目 '+id,status:'tracking',revision:1,discussion_count:2,groups:[],first_date:'2026-10-04',last_date:'2026-10-05',latest_progress:'进展',latest_conclusions:[],open_questions:[],discussions:[],page:1,page_size:20,total:0});
(async()=>{
 requests.shift().resolve({groups:[]});requests.shift().resolve({reports_processed:0,skipped_reports:0});await tick();requests.shift().resolve({topics:[],total:0,page:1,page_size:20});await tick();
 let p1=scope.topicsShow(1),r1=requests.shift(),p2=scope.topicsShow(2),r2=requests.shift();r2.resolve(topic(2));await p2;r1.resolve(topic(1));await p1;
 assert.equal(get('topics-title').textContent,'项目 2','Old detail cannot replace newer topic');
 const loading=scope.topicsShow(3),old=requests.shift();scope.topicsReturn();requests.shift().resolve({topics:[],total:0,page:1,page_size:20});await tick();old.resolve(topic(3));await loading;assert.equal(get('topics-detail').hidden,true);
 const showing=scope.topicsShow(2);requests.shift().resolve(topic(2));await showing;
 scope.topicsOpenEdit('rename');get('topics-edit-title').value='保留用户输入';const saving=scope.topicsSubmit({preventDefault(){}}),failed=requests.shift();scope.topicsSubmit({preventDefault(){}});assert.equal(requests.length,0,'Repeated submit is blocked');failed.reject(Error('存储失败'));await saving;assert.equal(get('topics-edit-title').value,'保留用户输入');assert.equal(get('topics-edit-submit').disabled,false);
 const retry=scope.topicsSubmit({preventDefault(){}}),ok=requests.shift();scope.topicsCloseEdit();assert.equal(scope.topicsOpenEdit('rename'),undefined);ok.resolve({...topic(2),title:'保留用户输入',revision:2});await retry;requests.shift().resolve({...topic(2),title:'保留用户输入',revision:2});await tick();assert.equal(get('topics-edit-dialog').open,false);
 const refresh=scope.topicsRefresh(),pending=requests.shift();scope.topicsRefresh();assert.equal(requests.length,0,'Repeated refresh is blocked');pending.reject(Error('正在运行'));await refresh;assert.equal(get('topics-title').textContent,'保留用户输入','Refresh error preserves current content');
 scope.topicsOpenEdit('rename');get('topics-edit-title').value='过期修改';const conflict=scope.topicsSubmit({preventDefault(){}}),c=requests.shift();let error=Error('版本已变化');error.status=409;c.reject(error);await conflict;assert.match(get('topics-edit-error').textContent,/版本已变化/);assert.equal(get('topics-edit-title').value,'过期修改');assert.equal(get('topics-edit-dialog').open,true);
 scope.topicsCloseEdit();scope.topicsOpenEdit('merge');requests.shift().resolve({topics:[topic(1)],page:1,page_size:100,total:1});await tick();
 const lookup=scope.topicsTargets(1),search=requests.shift();search.reject(Error('查找失败'));await lookup;
 scope.topicsSubmit({preventDefault(){}});assert.equal(requests.length,0,'Failed target lookup cannot submit a stale selection with Enter');
 console.log('Topic delayed navigation, duplicate submit, failed edits, close while saving, target lookup and refresh retention verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
