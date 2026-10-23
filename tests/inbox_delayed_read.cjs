const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map(),requests=[];
function make(){const classes=new Set();return {children:[],events:{},dataset:{},hidden:false,value:'',open:false,scrollTop:0,isConnected:true,classList:{toggle(k,v){if(v===undefined)v=!classes.has(k);v?classes.add(k):classes.delete(k);return v;}},appendChild(n){n.parentElement=this;this.children.push(n);return n;},remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);},addEventListener(k,fn){this.events[k]=fn;},setAttribute(){},removeAttribute(){},focus(){},set textContent(v){this._text=String(v);this.children=[];},get textContent(){return (this._text||'')+this.children.map(n=>n.textContent).join('');}};}
const n=id=>{if(!nodes.has(id))nodes.set(id,make());return nodes.get(id);},settle=()=>new Promise(setImmediate),find=(node,p)=>p(node)?node:node.children.map(c=>find(c,p)).find(Boolean);
Object.assign(global,{document:{getElementById:n,createElement:make,querySelectorAll:()=>[],addEventListener(){}},window:{scrollY:0,scrollTo(){},matchMedia:()=>({matches:false}),addEventListener(){}},requestAnimationFrame:fn=>fn(),recentWeekDates:()=>({start:'2026-10-19',end:'2026-10-20'}),reportPage:1,closeReport(){},summaryOpenSource(){},viewReport(){},appendBookmarkAction(){},api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))});
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/summary_reading_script.html','utf8').replace(/<\/?script>/g,'').replace('  summaryInit();',''));
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/inbox_reading_script.html','utf8').replace(/<\/?script>/g,''));
summarySave=()=>{};summaryShowResult=()=>{};n('inbox-point-detail').parentElement=make();n('report-from').value='2026-10-19';n('report-to').value='2026-10-20';
const a={key:'a',text:'第一条：正文',group_id:11,group_name:'研发群',section:'讨论',report_id:5,report_kind:'daily',date_label:'2026-10-20',read:false,status:'legacy',source_ids:[]},b={...a,key:'b',text:'第二条：正文'};
const group={group_id:11,group_name:'研发群',total:2,unread:2,report_count:1,preview:[]};
async function scenario(filter){
 inboxClearSelection();summaryExpanded={};n('summary-read-filter').value=filter;
 inboxRenderDashboard({total:1,point_total:2,unread:2,items:[{...group}]});
 const card=find(n('summary-points'),x=>x.tagName==='details'||x.className==='summary-group-stack');card.open=true;card.events.toggle();
 requests.shift().resolve({items:[{...a},{...b}],total:2,page:1,page_size:30});await settle();
 inboxSelectPoint('a',true);n('inbox-detail-actions').children.at(-1).onclick();const save=requests.shift();
 inboxBackToList();inboxSelectPoint('b',true);save.resolve({});await settle();
 assert.equal(inboxSelectedKey,'b','delayed read save must preserve the newer selection');
 const refresh=requests.shift();assert.ok(refresh,'a delayed save must refresh unread counts even after selecting another point');
 assert.match(refresh.url,/grouped=true/);assert.equal(new URLSearchParams(refresh.url.split('?')[1]).get('read_filter'),filter,'refresh retains the current read filter');
 const total=filter==='unread'?1:2;refresh.resolve({grouped:true,total:1,point_total:total,unread:1,page:1,page_size:30,items:[{...group,total,unread:1}]});await settle();
 assert.equal(inboxSelectedKey,'b');assert.match(n('inbox-detail-title').textContent,/第二条/);assert.match(n('summary-points').textContent,/有 1 条未读/,'group count reflects the saved state');
 const expanded=requests.shift();assert.ok(expanded,'expanded group reloads in the current scope');assert.equal(new URLSearchParams(expanded.url.split('?')[1]).get('read_filter'),filter);
 expanded.resolve({items:filter==='unread'?[{...b}]:[{...a,read:true},{...b}],total,page:1,page_size:30});await settle();
 assert.equal(inboxSelectedKey,'b');assert.equal(inboxMobileOpen,true,'background refresh must keep the newer detail open');
 assert.equal(inboxDashboardButtons.some(entry=>entry.key==='a'),filter!=='unread','saved point follows the active unread filter');
 if(filter==='all')assert.match(inboxDashboardButtons.find(entry=>entry.key==='a').button.textContent,/已读/);
}
(async()=>{await scenario('all');await scenario('unread');console.log('Delayed read save refreshes dashboard counts/filter while preserving a newer selection');})().catch(e=>{console.error(e);process.exitCode=1;});
