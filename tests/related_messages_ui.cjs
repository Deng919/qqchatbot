const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag){this.tagName=tag;this.children=[];this._text='';this.attrs={};this.listeners={};this.offsetHeight=50;this.clientHeight=100;this.scrollTop=0;}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
 appendChild(n){n.offsetTop=this.children.length*50;this.children.push(n);return n;}
 setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,v){this.listeners[k]=v;}
 get scrollHeight(){return this.children.length*50;}querySelector(){return this.children.find(n=>n.className.includes('is-cited'));}
}
const requests=[],scope={appendBookmarkAction:()=>{},console,URLSearchParams,Date,String,Math,Set,document:{createElement:tag=>new Node(tag)},api:(method,url,body)=>new Promise(resolve=>requests.push({method,url,body,resolve}))};
vm.createContext(scope);vm.runInContext(fs.readFileSync('qq_digest/web/templates/message_context_script.html','utf8').replace(/<style>[\s\S]*?<\/style>/,'').replace(/<\/?script>/g,''),scope);
const ids=['a','b','c','d','e'],rows=ids.map(msg_id=>({msg_id,text:'<img> 正文',sender_name:'合成昵称',timestamp:'2026-10-07T00:00:00Z'}));
function all(node){return [node,...node.children.flatMap(all)];}
(async()=>{
 const host=new Node('div'),controller=scope.mountRelatedMessages(host,{groupId:11,url:'/related',sourceIds:ids});
 const request=requests.shift();assert.equal(request.method,'POST');assert.deepEqual(Array.from(request.body.source_ids),ids);
 request.resolve({group_id:11,current_version:0,related_ids:ids,messages:rows});await controller.ready;
 assert.equal(all(host).filter(n=>n.className==='message-context-row is-cited').length,5);
 assert.ok(!host.textContent.includes('原消息 1')&&!host.textContent.includes(' · 原消息'));
 const open=all(host).find(n=>n.tagName==='button'&&n.textContent==='查看前后讨论');open.onclick();
 const context=requests.shift();assert.equal(context.body.anchor_id,'a');assert.equal(context.body.expected_version,0);
 context.resolve({related_ids:ids,messages:[rows[0],{...rows[0],msg_id:'unrelated'},rows[1]],first_id:'a',last_id:'b',has_before:false,has_after:false});await new Promise(setImmediate);
 const nested=all(host).find(n=>n.className==='related-message-context');
 assert.equal(all(nested).filter(n=>n.className?.includes('message-context-row')&&n.className.includes('is-cited')).length,2);
 const late=scope.mountRelatedMessages(host,{groupId:11,url:'/related',sourceIds:ids});const pending=requests.shift();late.destroy();host.textContent='关闭后内容';pending.resolve({group_id:11,related_ids:ids,messages:rows});await late.ready;
 assert.equal(host.textContent,'关闭后内容');
 console.log('All related messages, context marking, literal text and cancelled responses verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
