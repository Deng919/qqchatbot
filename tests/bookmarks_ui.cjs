const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(){return {value:'',hidden:false,open:false,disabled:false,dataset:{},options:[],children:[],attrs:{},_text:'',get textContent(){return this._text;},set textContent(v){this._text=v;this.children=[];},appendChild(n){this.children.push(n);},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}};}
const elements={},requests=[],get=id=>elements[id]??=node(),tick=()=>new Promise(r=>setImmediate(r));
get('bookmarks-status').value='pending';
const scope={console,URLSearchParams,Date,Number,Array,Promise,String,Math,featureFlags:{bookmarks:true},location:{search:''},history:{replaceState(){}},toast(){},destroyMessageContext(){},mountMessageContext(){},document:{createElement:node,getElementById:get},api(method,url,body){return new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));}};
vm.createContext(scope);
for(const file of ['bookmark_actions.html','bookmarks_script.html'])vm.runInContext(fs.readFileSync('qq_digest/web/templates/'+file,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1],scope);
const empty={items:[],total:0,page:1,page_size:20,counts:{}};
const item=id=>({bookmark_id:id,kind:'message',status:'pending',revision:0,snapshot:{title:'收藏 '+id,text:'<img src=x onerror=bad()>完整正文',group_name:'研发',date:'2026-10-05'},origin_available:false});
(async()=>{
  requests.shift().resolve(empty);await tick();
  scope.bookmarksChange(item(1),false,node());const mutation=requests.shift();
  get('bookmarks-status').value='completed';scope.bookmarksLoad();requests.shift().resolve(empty);await tick();
  mutation.resolve({status:'completed'});await tick();
  assert.equal(requests.length,1,'Successful mutation refreshes the current filter even if filter changed while saving');
  assert.ok(requests[0].url.includes('status=completed'),'Refresh preserves current filters');requests.shift().resolve(empty);await tick();
  const old=scope.bookmarksLoad(),older=requests.shift(),fresh=scope.bookmarksLoad(),newer=requests.shift();
  newer.resolve({...empty,items:[item(2)],total:1});await fresh;older.reject(Error('stale failure'));await old;
  assert.equal(get('bookmarks-list').children[0].children[1].textContent,'收藏 2');
  scope.bookmarksOpen(1);const first=requests.shift();scope.bookmarksOpen(2);const second=requests.shift();
  second.resolve(item(2));await tick();first.resolve(item(1));await tick();assert.equal(get('bookmark-title').textContent,'收藏 2','Late detail cannot replace newer selection');
  assert.equal(get('bookmark-body').textContent,item(2).snapshot.text,'Source content is plain text');
  scope.bookmarksOpen(3);const abandoned=requests.shift();scope.bookmarksClose();abandoned.resolve(item(3));await tick();assert.equal(get('bookmark-dialog').open,false);
  const b=node(),target={kind:'message',group_id:11,msg_id:'m1'};const saving=scope.saveBookmark(target,b);scope.saveBookmark(target,b);assert.equal(requests.length,1,'Double click saves once');
  requests.shift().reject(Error('disk full'));await saving;assert.equal(b.disabled,false,'Failure allows retry');
  const retry=scope.saveBookmark(target,b);requests.shift().resolve({reused:true});await retry;assert.equal(b.textContent,'已收藏');assert.equal(b.disabled,true);
  console.log('Bookmark current-filter refresh, stale reads, cancelled detail, plain text, duplicate submit and retry verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
