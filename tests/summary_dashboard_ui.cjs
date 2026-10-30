const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map();function make(){const classes=new Set();return {children:[],events:{},dataset:{},attrs:{},hidden:false,value:'',open:false,classList:{toggle(k,v){if(v===undefined)v=!classes.has(k);v?classes.add(k):classes.delete(k);return v;},contains:k=>classes.has(k)},appendChild(n){this.children.push(n);return n;},addEventListener(k,fn){this.events[k]=fn;},setAttribute(k,v){this.attrs[k]=v;},focus(){},set textContent(v){this._text=String(v);this.children=[];},get textContent(){return (this._text||'')+this.children.map(n=>n.textContent).join('');}};}
const deckNode=require('./helpers/deck_dom.cjs'),plainMake=make;make=()=>deckNode(plainMake());
const n=id=>{if(!nodes.has(id))nodes.set(id,make());return nodes.get(id);},requests=[];
Object.assign(global,{document:{getElementById:n,createElement:make},window:{matchMedia:()=>({matches:false})},summaryView:'points',summaryDetailActive:false,summaryExpanded:{},summaryPointToken:1,summarySince:null,
 summaryNode:(tag,cls,text)=>{const x=make();x.tag=tag;x.className=cls||'';if(text!==undefined)x.textContent=text;return x;},summaryCleanText:String,summaryButton:(text,fn,cls)=>{const x=summaryNode('button',cls,text);x.onclick=fn;return x;},summarySave:()=>{},summaryValues:()=>({from:'2026-10-19',to:'2026-10-20',read:'unread'}),summaryReportKeyParams:p=>p.append('report_keys','daily:5'),summaryLoadPoints:()=>{},summaryShowResult:()=>{},appendBookmarkAction:()=>{},closeReport:()=>{},viewReport:()=>{},summaryOpenSource:()=>{},api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))});
n('inbox-point-detail').parentElement={scrollTop:0};
Object.assign(global,{summaryPointReference:null,summarySave:global.summarySave||(()=>{}),summaryHistoryPush:()=>{},summaryBackInScope:()=>false});
document.addEventListener=()=>{};document.removeEventListener=()=>{};
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/summary_deck_motion.html','utf8').replace(/<\/?script>/g,''));
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/inbox_reading_script.html','utf8').replace(/<\/?script>/g,''));
const find=(node,p)=>p(node)?node:node.children.map(c=>find(c,p)).find(Boolean),settle=()=>new Promise(setImmediate);
(async()=>{inboxRenderDashboard({total:2,point_total:9,unread:9,items:[{group_id:11,group_name:'研发 <img>',total:8,unread:8,report_count:2,latest_date:'2026-10-20',preview:['讨论一']},{group_id:22,group_name:'产品群',total:1,unread:1,report_count:1,latest_date:'2026-10-20',preview:['讨论二']}]});
 assert.equal(inboxSelectedKey,null);assert.equal(requests.length,0,'collapsed cards do not load every group');
 assert.equal(n('inbox-workspace').classList.contains('inbox-list-only'),true,'dashboard gets the whole width');
 const card=find(n('summary-points'),x=>x.tag==='details');assert.ok(card);card.open=true;card.events.toggle();
 assert.match(requests[0].url,/group_id=11/);assert.match(requests[0].url,/read_filter=unread/);assert.match(requests[0].url,/report_keys=daily%3A5/);
 requests.shift().resolve({items:[{key:'a',text:'标题：详情',group_id:11,group_name:'研发 <img>',section:'讨论',report_id:5,report_kind:'daily',date_label:'2026-10-20',read:false,status:'legacy',source_ids:[]}],total:1,page:1,page_size:30});await settle();
 assert.equal(requests.length,0,'expanding never writes read state');const point=find(n('summary-points'),x=>x.className==='inbox-row');point.onclick();assert.equal(inboxSelectedKey,'a');assert.equal(n('inbox-workspace').classList.contains('inbox-dashboard-detail'),true);
 inboxBackToList();assert.equal(n('inbox-workspace').classList.contains('inbox-list-only'),true);assert.equal(card.open,true,'back preserves the expanded deck');
 inboxFilterPage('产品');assert.equal(card.hidden,true);inboxFilterPage('');assert.equal(card.hidden,false);
 card.open=false;card.events.toggle();card.open=true;card.events.toggle();assert.equal(requests.length,1,'reopening reuses loaded points while the automatic read save is pending');assert.equal(requests[0].method,'POST');
 inboxSelectPoint('a',true);n('inbox-detail-actions').children.at(-1).onclick();assert.equal(requests[0].body.read,true);requests.shift().resolve({});await settle();
 inboxRenderDashboard({total:1,point_total:1,unread:0,items:[{group_id:11,group_name:'研发',total:1,unread:0,report_count:1,preview:[]}]});
 requests.shift().resolve({items:[{...inboxItems[0],read:true}],total:1,page:1,page_size:30});await settle();
 n('inbox-detail-actions').children.at(-1).onclick();assert.equal(requests[0].body.read,false);requests.shift().resolve({});await settle();
 assert.match(n('inbox-detail-meta').textContent,/未读/,'a second toggle updates the current item after lazy-loading replaced its response object');
 console.log('Group dashboard, opt-in lazy expansion, scope, literal names, reading/back and filtering verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
