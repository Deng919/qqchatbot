const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=[];function node(tag){return {tag,children:[],dataset:{},appendChild(n){this.children.push(n);return n;},setAttribute(k,v){this[k]=v;},set textContent(v){this.text=String(v);},get textContent(){return this.text||'';}};}
const scope={Date,Math,Number,String,Map,Set,URLSearchParams,document:{createElement:node,querySelectorAll:()=>nodes}};vm.createContext(scope);vm.runInContext(fs.readFileSync('qq_digest/web/templates/secondhand_script.html','utf8').replace(/<\/?script>/g,'').replace('marketInit();',''),scope);
const now=Date.parse('2026-10-26T12:00:00+08:00');
for(const [seconds,label] of [[0,'刚刚'],[59,'刚刚'],[60,'1分钟前'],[3599,'59分钟前'],[3600,'1小时前'],[86400,'1天前'],[259200,'3天前'],[2592000,'1个月前'],[31536000,'1年前'],[-10,'刚刚']])assert.equal(scope.marketRelativeTime(new Date(now-seconds*1000).toISOString(),now),label);
assert.equal(scope.marketRelativeTime('invalid',now),'时间未注明');assert.equal(scope.marketRelativeTime('',now),'时间未注明');
const item={item_id:'a',title:'床上桌',category:'other',lane:'sale',status:'unknown',published_at:'2026-10-26T11:55:00+08:00',price_text:'10r',group_count:1,seller_name:'卖家'};
const card=scope.marketCard(item),top=card.children[0],time=top.children[1];assert.equal(top.className,'market-item-topline');assert.equal(time.tag,'time');assert.equal(time.dataset.marketPublished,item.published_at);assert.equal(time.dateTime,item.published_at);assert.ok(card.children.at(-1).children[0].textContent.includes(scope.marketDate(item.published_at)),'exact footer time remains');
nodes.push(time);scope.marketRefreshAges(now);assert.equal(time.textContent,'5分钟前');scope.marketRefreshAges(now+60000);assert.equal(time.textContent,'6分钟前');
const unknown=scope.marketCard({...item,original_time_unknown:true}).children[0].children[1];assert.equal(unknown.textContent,'时间待确认');assert.equal(unknown.dataset.marketPublished,undefined,'unknown original time is never presented as a recent publication');
console.log('Relative publication ages, exact timestamps, live updates and unknown originals verified');
