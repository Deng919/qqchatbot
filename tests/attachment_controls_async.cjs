const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('qq_digest/web/templates/attachment_controls.html','utf8');
class Node{constructor(){this.children=[];this.disabled=false;this.open=false;}set textContent(value){this.text=value;this.children=[];}get textContent(){return this.text;}appendChild(n){this.children.push(n);}addEventListener(){}showModal(){this.open=true;}close(){this.open=false;}focus(){}}
const nodes=new Map();global.document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);},createElement:()=>new Node(),activeElement:new Node()};
global.destroyMessageContext=()=>{};global.mountMessageContext=()=>{};
global.configuredGroups=[{group_id:1,name:'群一'},{group_id:2,name:'群二'}];
let calls=[];global.api=(method,url)=>new Promise((resolve,reject)=>calls.push({method,url,resolve,reject}));
vm.runInThisContext(source.split('<script>')[1].split('</script>')[0]);
const data=name=>({counts:{success:1},read_files:true,items:[{name,text:'<script>不执行</script>',kind:'file',status:'success',at:new Date().toISOString()}]});
(async()=>{
 let old=attachmentOpen(1),newer=attachmentOpen(2);calls[0].resolve(data('过期'));await old;assert.equal(document.getElementById('attachment-items').children.length,0);
 calls[1].resolve(data('<b>新附件</b>'));await newer;assert.equal(document.getElementById('attachment-items').children[0].children[0].textContent,'<b>新附件</b>');
 let read=attachmentRead();attachmentRead();assert.equal(calls.filter(c=>c.method==='POST').length,1);
 attachmentClose();calls[2].resolve(data('关闭后结果'));await read;assert.equal(attachmentGroup,null);assert.equal(document.getElementById('attachment-dialog').open,false);
 let loading=attachmentOpen(1);attachmentClose();calls[3].resolve(data('迟到'));await loading;assert.equal(attachmentGroup,null);
 console.log('Attachment dialog ignores stale responses, blocks duplicate reading, renders plain text');
})().catch(e=>{console.error(e);process.exitCode=1;});
