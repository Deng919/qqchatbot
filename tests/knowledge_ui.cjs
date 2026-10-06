const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(){return {value:'',hidden:false,open:false,options:[],children:[],attrs:{},style:{},isConnected:true,_text:'',get textContent(){return this._text;},set textContent(v){this._text=v;this.children=[];},appendChild(n){this.children.push(n);},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},addEventListener(){},focus(){},scrollIntoView(){},showModal(){this.open=true;},close(){this.open=false;}};}
const elements={},requests=[],get=id=>elements[id]??=node(),tick=()=>new Promise(r=>setImmediate(r));
const scope={console,URLSearchParams,Date,Number,Array,Object,Promise,String,featureFlags:{bookmarks:false},location:{search:''},history:{replaceState(){}},window:{scrollY:120,scrollTo(){}},requestAnimationFrame(f){f();},document:{activeElement:node(),getElementById:get,createElement:node},destroyMessageContext(){},mountMessageContext(){},api(method,url,body){return new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));}};
vm.createContext(scope);
function script(name){return fs.readFileSync('qq_digest/web/templates/'+name,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];}
vm.runInContext(script('bookmark_actions.html'),scope);
vm.runInContext(script('knowledge_script.html'),scope);
vm.runInContext(script('knowledge_save.html'),scope);
const list={total:0,items:[],page:1,page_size:20};
const item=id=>({item_id:id,title:'知识 '+id,item_type:'experience',group_name:'研发',date:'2026-10-05',content:'全部正文',reason:'说明',source_context:[],missing_source_count:1});
(async()=>{
 requests.shift().resolve({groups:[]});requests.shift().resolve(list);await tick();
 const old=scope.knowledgeShow(1),r1=requests.shift();const newer=scope.knowledgeShow(2),r2=requests.shift();
 r2.resolve(item(2));await newer;r1.resolve(item(1));await old;
 assert.equal(get('knowledge-title').textContent,'知识 2','Late detail cannot replace newer knowledge');
 const pending=scope.knowledgeShow(3),abandoned=requests.shift();scope.knowledgeReturn();requests.shift().resolve(list);await tick();abandoned.resolve(item(3));await pending;
 assert.equal(get('knowledge-detail').hidden,true,'Return cancels pending detail');
 const loading=scope.knowledgeLoad(),oldList=requests.shift();const viewing=scope.knowledgeShow(4),detail=requests.shift();detail.resolve(item(4));await viewing;oldList.reject(Error('old failure'));await loading;
 assert.equal(get('knowledge-detail').hidden,false);assert.equal(get('knowledge-title').textContent,'知识 4');
 const preview=scope.openKnowledgeSave(11,'m1'),late=requests.shift();scope.closeKnowledgeSave();late.resolve({text:'old',group_name:'研发',date:'2026-10-05'});await preview;assert.equal(scope.knowledgeSaveReady,false);
 const opening=scope.openKnowledgeSave(11,'m2');get('knowledge-save-title').value='用户提前输入的标题';requests.shift().resolve({text:'核对接口\n完整原文',group_name:'研发',date:'2026-10-05'});await opening;
 assert.equal(get('knowledge-save-title').value,'用户提前输入的标题','A delayed preview cannot overwrite a typed title');
 get('knowledge-save-title').value='自定义标题';get('knowledge-save-reason').value='留作参考';
 const saving=scope.submitKnowledgeSave({preventDefault(){}}),failed=requests.shift();scope.submitKnowledgeSave({preventDefault(){}});assert.equal(requests.length,0,'Double submit is blocked');failed.reject(Error('disk full'));await saving;
 assert.equal(get('knowledge-save-title').value,'自定义标题');assert.equal(get('knowledge-save-submit').disabled,false,'Failure keeps input and allows retry');
 const retry=scope.submitKnowledgeSave({preventDefault(){}}),success=requests.shift();success.resolve({item_id:9,reused:false});await retry;assert.equal(get('knowledge-save-result').children[0].href,'/knowledge?item_id=9');assert.equal(get('knowledge-save-submit').disabled,true);
 const again=scope.openKnowledgeSave(11,'m2');requests.shift().resolve({text:'消息',group_name:'研发',date:'2026-10-05'});await again;const closingSave=scope.submitKnowledgeSave({preventDefault(){}}),closed=requests.shift();scope.closeKnowledgeSave();assert.equal(scope.openKnowledgeSave(11,'m3'),undefined,'Cannot switch source while publication is in flight');closed.resolve({item_id:9,reused:true});await closingSave;assert.equal(get('knowledge-save-dialog').open,false);assert.equal(scope.knowledgeSaveBusy,false);
 console.log('Knowledge navigation, late responses, save retry, duplicate submission and close states verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
