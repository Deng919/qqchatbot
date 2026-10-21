const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Node {constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.attrs={};this.scrollTop=0;this.clientHeight=100;this.offsetHeight=50;this.offsetTop=0;this._text='';}
 set textContent(v){this._text=v;this.children=[];}get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
 appendChild(n){n.offsetTop=this.children.length*50;this.children.push(n);return n;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,v){this.listeners[k]=v;}
 get scrollHeight(){return this.children.length*50;}querySelector(s){return this.children.find(n=>n.className.includes(s.slice(1)));}
 remove(){this.removed=true;}}
const requests=[],scope={console,URLSearchParams,Date,String,Math,document:{createElement:tag=>new Node(tag)},appendBookmarkAction:()=>{},api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))};vm.createContext(scope);
vm.runInContext(fs.readFileSync('qq_digest/web/templates/message_context_script.html','utf8').replace(/<style>[\s\S]*?<\/style>/,'').replace(/<\/?script>/g,''),scope);
const find=(n,tag)=>n.tag===tag?n:n.children.map(c=>find(c,tag)).find(Boolean);
(async()=>{const host=new Node('main'),controller=scope.mountMessageContext(host,{url:'/context',groupId:123,citedId:'a'});
 const item={msg_id:'a',timestamp:'2026-10-20T00:00:00Z',text:'出售商品',images_enabled:true,read_images:false,images:[{key:'p',name:'商品图',status:'disabled',url:'/api/groups/123/messages/a/images/original?key=p',read_url:'/api/groups/123/messages/a/images/p/read',text:''}]};
 item.images.push({...item.images[0],key:'p2',name:'第二张图'});
 requests.shift().resolve({messages:[item],first_id:'a',last_id:'a'});await controller.ready;
 const row=find(host,'article'),image=find(row,'img');assert.ok(image,'Original is rendered inside its own message');
 assert.equal(row.children[1].textContent,'出售商品');assert.equal(image.loading,'lazy');assert.ok(image.src.startsWith('/api/groups/'));
 const button=find(row,'button');assert.match(button.textContent,/开启本群/);const read=button.onclick();
 assert.equal(requests[0].method,'PATCH');requests.shift().resolve({});await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].url,item.images[0].read_url);requests.shift().resolve({items:[{...item.images[0],status:'success',text:'<img onerror=alert(1)> 显示器800元'}]});await read;
 assert.ok(host.textContent.includes('<img onerror=alert(1)> 显示器800元'),'OCR is literal text');
 assert.ok(!host.textContent.includes('图片识别未开启'),'Enabling group recognition updates all visible cards');
 assert.equal(requests.length,0,'Viewing never starts recognition');
 const retry=find(row,'button');const pending=retry.onclick();await new Promise(r=>setImmediate(r));controller.destroy();requests.shift().resolve({items:[]});await pending;
 assert.ok(host.textContent.includes('显示器800元'),'Late recognition does not replace a destroyed view');
 console.log('Inline image placement, opt-in recognition, literal OCR and stale results verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
