const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node{
 constructor(tag){this.tagName=tag;this.children=[];this._text='';this.attrs={};this.listeners={};this.offsetHeight=50;this.clientHeight=100;this.scrollTop=0;}
 set textContent(v){this._text=String(v);this.children=[];}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
 appendChild(n){n.offsetTop=this.children.length*50;this.children.push(n);return n;}
 setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,v){this.listeners[k]=v;}
 get scrollHeight(){return this.children.length*50;}querySelector(selector){return this.children.find(n=>n.className.includes(selector.slice(1)));}
}
const requests=[],scope={appendBookmarkAction:()=>{},console,URLSearchParams,Date,String,Math,Set,document:{createElement:tag=>new Node(tag)},api:(method,url,body)=>new Promise(resolve=>requests.push({method,url,body,resolve}))};
vm.createContext(scope);vm.runInContext(fs.readFileSync('qq_digest/web/templates/message_context_script.html','utf8').replace(/<style>[\s\S]*?<\/style>/,'').replace(/<\/?script>/g,''),scope);
const row=(msg_id,minute)=>({msg_id,text:'<img> 合成正文 '+msg_id,sender_name:'合成昵称',timestamp:new Date(Date.UTC(2026,9,7,0,minute)).toISOString()});
const ids=['a','b','c','d','e'],rows=ids.map((id,i)=>row(id,[1,10,30,60,90][i]));
function all(node){return [node,...node.children.flatMap(all)];}
const settle=()=>new Promise(setImmediate);
const button=(host,name)=>all(host).find(n=>n.tagName==='button'&&n.textContent===name);
(async()=>{
 const host=new Node('div'),controller=scope.mountRelatedMessages(host,{groupId:11,url:'/related',sourceIds:ids,version:0});
 const request=requests.shift();assert.equal(request.method,'POST');assert.deepEqual(Array.from(request.body.source_ids),ids);
 request.resolve({group_id:11,current_version:0,related_ids:ids,messages:rows});await settle();
 assert.equal(requests.length,1,'Opening related messages automatically loads continuous context');
 let context=requests.shift();assert.equal(context.body.anchor_id,'a');assert.equal(context.body.expected_version,0);
 context.resolve({related_ids:ids,messages:[row('u0',0),rows[0],row('u2',2)],first_id:'u0',last_id:'u2',has_before:false,has_after:true});await controller.ready;
 assert.equal(all(host).filter(n=>n.className?.includes('message-context-row')&&n.className.includes('is-cited')).length,1,'Only related rows are highlighted');
 assert.ok(host.textContent.includes('<img> 合成正文'),'Message text is literal');
 assert.ok(!host.textContent.includes('查看前后讨论')&&!host.textContent.includes('原消息 1'));
 assert.ok(button(host,'上方没有关联消息').disabled);
 button(host,'下方还有 4 条关联消息').onclick();await settle();
 context=requests.shift();assert.equal(context.body.anchor_id,'b');assert.equal(context.body.expected_version,0);
 context.resolve({related_ids:ids,messages:[row('u9',9),rows[1],row('u11',11)],first_id:'u9',last_id:'u11',has_before:true,has_after:true});await settle();
 assert.ok(button(host,'上方还有 1 条关联消息'));
 assert.ok(button(host,'下方还有 3 条关联消息'));
 const scroll=all(host).find(n=>n.className==='message-context-scroll');
 const nested=all(host).find(n=>n.className==='related-message-context')._messageContext;
 const page=nested.load('after');context=requests.shift();assert.equal(context.body.cursor,'u11');
 context.resolve({related_ids:ids,messages:[rows[2],row('u31',31)],first_id:'c',last_id:'u31',has_before:true,has_after:true});await page;
 scroll.scrollTop=100;scroll.listeners.scroll({});
 assert.ok(button(host,'上方还有 2 条关联消息'),'Counters include rows above viewport, even when loaded');
 assert.ok(button(host,'下方还有 2 条关联消息'));
 assert.equal(all(host).filter(n=>n.className?.includes('message-context-row')&&n.className.includes('is-cited')).length,2,'All loaded related rows keep their marks');
 button(host,'上方还有 2 条关联消息').onclick();await settle();context=requests.shift();assert.equal(context.body.anchor_id,'b');
 controller.destroy();host.textContent='关闭后内容';context.resolve({related_ids:ids,messages:[rows[1]],first_id:'b',last_id:'b',has_before:false,has_after:false});await settle();
 assert.equal(host.textContent,'关闭后内容','Closing ignores pending context navigation');
 const late=scope.mountRelatedMessages(host,{groupId:11,url:'/related',sourceIds:ids});const pending=requests.shift();late.destroy();host.textContent='关闭后内容';pending.resolve({group_id:11,related_ids:ids,messages:rows});await late.ready;
 assert.equal(host.textContent,'关闭后内容');assert.equal(requests.length,0,'Closing bootstrap prevents context request');
 console.log('Continuous context, viewport-related counts, jumps, marking, literal text and cancellation verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
