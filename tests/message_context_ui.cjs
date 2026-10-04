const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const file='qq_digest/web/templates/message_context_script.html';assert.ok(fs.existsSync(file),'Shared scroll context component exists');
class Node {constructor(){this.children=[];this.scrollTop=0;this.clientHeight=100;this.offsetHeight=50;this.offsetTop=0;this.listeners={};this.attrs={};this.disabled=false;this._text='';}
  set textContent(value){this._text=value;this.children=[];}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  appendChild(n){n.offsetTop=this.children.length*50;this.children.push(n);return n;}
  setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,v){this.listeners[k]=v;}
  get scrollHeight(){return this.children.length*50;}querySelector(){return this.children.find(n=>n.className.includes('is-cited'));}
}
const requests=[],scope={console,URLSearchParams,Date,String,Math,document:{createElement:()=>new Node()},api:(method,url)=>new Promise((resolve,reject)=>requests.push({url,resolve,reject}))};vm.createContext(scope);vm.runInContext(fs.readFileSync(file,'utf8').replace(/<style>[\s\S]*?<\/style>/,'').replace(/<\/?script>/g,''),scope);
const rows=(start,count)=>Array.from({length:count},(_,i)=>({msg_id:String(start+i),text:'纯文本 <img>',sender_qq:1,timestamp:'2026-10-04T00:00:00Z'}));
(async()=>{
  const host=new Node(),controller=scope.mountMessageContext(host,{url:'/context',citedId:'30'});
  requests.shift().resolve({messages:rows(28,5),first_id:'28',last_id:'32',has_before:true,has_after:true});await controller.ready;
  const scroll=host.children[0].children.find(n=>n.className==='message-context-scroll'),oldTop=scroll.scrollTop;
  const before=controller.load('before');assert.ok(requests[0].url.includes('cursor=28'));
  requests.shift().resolve({messages:rows(8,20),first_id:'8',last_id:'27',has_before:true,has_after:true});await before;
  assert.equal(scroll.children.length,25);assert.equal(scroll.scrollTop,oldTop+1000,'Prepending preserves existing view');
  const failure=controller.load('after');requests.shift().reject(Error('retry me'));await failure;assert.ok(host.textContent.includes('retry me'));
  const retry=controller.load('after');assert.ok(requests[0].url.includes('cursor=32'),'Failure does not advance cursor');
  requests.shift().resolve({messages:rows(33,2),first_id:'33',last_id:'34',has_before:true,has_after:false});await retry;
  assert.equal(scroll.children.length,27);
  assert.equal(typeof scroll.listeners.pointerdown,'function','Dragging the scrollbar enables boundary loading');
  scroll.listeners.pointerdown({});scroll.scrollTop=0;scroll.listeners.scroll({});const pending=requests.shift();assert.ok(pending);controller.destroy();
  pending.resolve({messages:rows(0,8),first_id:'0',last_id:'7',has_before:false,has_after:true});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(scroll.children.length,27,'Destroyed views ignore late pages');
  console.log('Context paging, prepend view, retry cursor and cancellation verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
