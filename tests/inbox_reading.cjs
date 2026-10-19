const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map();
function make(){const classes=new Set();return {hidden:false,value:'',children:[],dataset:{},attributes:{},classList:{toggle(k,v){if(v===undefined)v=!classes.has(k);v?classes.add(k):classes.delete(k);},contains:k=>classes.has(k)},appendChild(n){this.children.push(n);return n;},setAttribute(k,v){this.attributes[k]=v;},focus(){},set textContent(v){this.text=String(v);this.children=[];},get textContent(){return this.text||'';}};}
function node(id){if(!nodes.has(id))nodes.set(id,make());return nodes.get(id);}
global.document={getElementById:node,createElement:make};global.window={matchMedia:()=>({matches:false})};
node('inbox-point-detail').parentElement={scrollTop:400};
global.summaryView='points';global.summaryDetailActive=false;
global.summaryNode=(tag,cls,text)=>{const n=make();n.className=cls;if(text!==undefined)n.textContent=text;return n;};
global.summaryCleanText=String;global.summaryButton=(text,fn)=>{const n=summaryNode('button','',text);n.onclick=fn;return n;};
let reportCloses=0;global.closeReport=()=>{reportCloses++;summaryDetailActive=false;};global.summaryOpenSource=()=>{};global.viewReport=()=>{};global.appendBookmarkAction=()=>{};
global.summarySelectGroups=ids=>node('report-group').value=ids.join(',');global.filterReports=()=>{};global.summaryEmpty=host=>host.textContent='empty';
global.summaryLoadPoints=()=>{throw Error('a delayed save must not reload a newer selection');};global.summaryShowResult=()=>{};
const requests=[];global.api=(method,url,body)=>new Promise(resolve=>requests.push({method,url,body,resolve}));
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/inbox_reading_script.html','utf8').replace(/<\/?script>/g,''));
const item=(key,text)=>({key,text,group_name:'群 <img>',group_id:11,report_id:1,report_kind:'range',section:'讨论',date_label:'2026-10-18',read:false,status:'legacy',source_ids:[]});
const settle=()=>new Promise(setImmediate);
(async()=>{
 inboxRenderPoints([item('a','标题：第一条'),item('b','<script>第二条</script>')]);
 assert.equal(inboxSelectedKey,'a','desktop selects the first point');assert.equal(requests.length,0,'selecting never writes read state');
 assert.equal(node('inbox-detail-meta').textContent,'群 <img> · 2026-10-18 · 未读');
 const read=node('inbox-detail-actions').children.at(-1);read.onclick();
 inboxSelectPoint('b',true);requests[0].resolve({});await settle();
 assert.equal(node('inbox-point-detail').parentElement.scrollTop,0,'new point starts at the top of the reading pane');
 assert.equal(inboxSelectedKey,'b','late read-save cannot replace a newer point');
 assert.equal(node('inbox-detail-body').textContent,'<script>第二条</script>','user text stays literal');
 summaryDetailActive=true;const closes=reportCloses;inboxRenderPoints([item('a','第一条'),item('b','第二条')]);
 assert.equal(reportCloses,closes,'background refresh cannot close or cancel full report');summaryDetailActive=false;
 inboxFilterPage('missing');assert.equal(node('inbox-point-detail').hidden,true,'no matches cannot leave stale detail');
 inboxClearSelection();assert.equal(inboxSelectedKey,null);assert.equal(node('inbox-workspace').classList.contains('inbox-detail-open'),false);
 window.matchMedia=()=>({matches:true});inboxRenderPoints([item('a','标题：第一条')]);
 assert.equal(inboxSelectedKey,null,'mobile starts at the list');inboxSelectPoint('a',true);
 assert.equal(node('inbox-workspace').classList.contains('inbox-detail-open'),true);
 inboxRenderPoints([item('a','更新后的第一条')]);assert.equal(node('inbox-workspace').classList.contains('inbox-detail-open'),true,'refresh preserves mobile detail');
 inboxBackToList();assert.equal(node('inbox-workspace').classList.contains('inbox-detail-open'),false);
 node('report-group').value='__empty__';inboxRenderGroups([{group_id:11,name:'群 <img>',enabled:true}]);
 assert.equal(node('report-group').value,'__empty__','group options do not expand explicit empty scope');
 node('inbox-group-list').children[1].onclick();assert.equal(node('report-group').value,'11');
 console.log('Inbox desktop/mobile selection, delayed save, exact scope and literal text verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
