const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(){return {value:'',checked:false,hidden:false,open:false,disabled:false,children:[],options:[],selectedOptions:[],attrs:{},_text:'',get textContent(){return this._text},set textContent(v){this._text=v;this.children=[]},appendChild(n){this.children.push(n);if(n.tag==='option')this.options.push(n)},setAttribute(k,v){this.attrs[k]=v},addEventListener(){},focus(){},showModal(){this.open=true},close(){this.open=false}}}
const elements={},requests=[],get=id=>elements[id]??=node(),tick=()=>new Promise(r=>setImmediate(r));
const scope={console,URLSearchParams,Date,Number,Array,Object,Promise,String,Math,location:{search:''},history:{replaceState(){}},document:{getElementById:get,querySelectorAll(){return []},createElement(tag){let n=node();n.tag=tag;return n}},api(method,url,body){return new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))}};
vm.createContext(scope);
vm.runInContext(fs.readFileSync('qq_digest/web/templates/reminders_script.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1],scope);
const list={items:[],total:0,unread:0,queued:0,page:1,page_size:20};
(async()=>{
 scope.remindersEdit();assert.equal(get('reminder-dialog').open,false,'Cannot edit before group scope options arrive');
 for(const req of requests.splice(0))req.resolve(req.url.includes('groups')?{groups:[]}:req.url.includes('capabilities')?{windows:false,timezone:'Asia/Shanghai'}:req.url.includes('rules')?{rules:[]}:list);await tick();
 let p1=scope.remindersLoad(1),r1=requests.shift(),p2=scope.remindersLoad(2),r2=requests.shift();r2.resolve({...list,page:2,total:25});await p2;r1.resolve({...list,page:1,total:25});await p1;
 assert.match(get('reminders-page-label').textContent,/2/,'Late response must not replace newer page');
 scope.remindersEdit();get('reminder-name').value='保留名称';get('reminder-keywords').value='教程';
 let preview=scope.remindersPreview(),pending=requests.shift();get('reminder-keywords').value='新词';scope.remindersChanged();pending.resolve({items:[{title:'旧预览',body:'旧内容'}],total:1,quiet:false});await preview;
 assert.doesNotMatch(get('reminder-preview').textContent,/旧预览/,'Changed conditions invalidate a late preview');
 preview=scope.remindersPreview();requests.shift().resolve({items:[{title:'匹配',body:'内容',group_name:'合成群',source_url:'/tasks'}],total:1,quiet:false});await preview;
 assert.match(get('reminder-preview').children[0].textContent,/合成群/);assert.equal(get('reminder-preview').children[1].attrs?.href||get('reminder-preview').children[1].href,'/tasks');
 let save=scope.remindersSave({preventDefault(){}}),failed=requests.shift();assert.equal(get('reminder-form').inert,true,'Freeze editor while submitted snapshot is saving');scope.remindersSave({preventDefault(){}});assert.equal(requests.length,0,'Repeated save blocked');failed.reject(Error('保存失败'));await save;
 assert.equal(get('reminder-form').inert,false,'Form becomes editable after failure');
 assert.equal(get('reminder-name').value,'保留名称');assert.equal(get('reminder-save').disabled,false);
 let retry=scope.remindersSave({preventDefault(){}}),conflict=requests.shift();let err=Error('版本已变化');err.status=409;conflict.reject(err);await retry;
 assert.match(get('reminder-error').textContent,/版本已变化/);assert.equal(get('reminder-dialog').open,true);
 console.log('Reminder stale lists, changed preview, duplicate submits, failed edits and conflict retention verified');
})().catch(err=>{console.error(err);process.exitCode=1});
