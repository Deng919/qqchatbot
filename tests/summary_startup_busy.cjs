const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const base=fs.readFileSync('qq_digest/web/templates/base.html','utf8');
const apiCode=base.slice(base.indexOf('        function api('),base.indexOf('        function showStorageHandoff('));
const settle=()=>new Promise(setImmediate);
async function exercise(statuses,options){
 let calls=0,toasts=0,login=0,handoffs=0,delays=0;
 const scope={Promise,Error,Array,JSON,SessionLogin:{open(){login++;}},showStorageHandoff(){handoffs++;},toast(){toasts++;},setTimeout(fn){delays++;fn();},fetch(){let status=statuses[Math.min(calls++,statuses.length-1)];return Promise.resolve({status:status.code,ok:status.code===200,json:()=>Promise.resolve(status.body||{detail:'busy'})});}};
 vm.createContext(scope);vm.runInContext(apiCode,scope);
 let error;try{await scope.api('POST','/api/summary-reading/visit',{},options);}catch(value){error=value;}
 return {calls,toasts,login,handoffs,delays,error};
}
function reader(){
 const nodes=new Map(),requests=[],errors=[];let loads=0,saves=0;
 function node(id){if(!nodes.has(id))nodes.set(id,{value:'',options:[],hidden:false,disabled:false,querySelector(){return node(id+'-new');},classList:{toggle(){}},setAttribute(){},appendChild(){}});return nodes.get(id);}
 const scope={console,Promise,URLSearchParams,Number,Array,Object,JSON,Date,Math,sessionStorage:{getItem(){return null;}},location:{search:''},window:{addEventListener(){}},document:{getElementById:node,addEventListener(){}},performance:{getEntriesByType(){return[];}},recentWeekDates:()=>({start:'2026-10-18',end:'2026-10-24'}),reportPage:1,refreshDateHints(){},api(method,url,body,options){return new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));}};
 vm.createContext(scope);vm.runInContext(fs.readFileSync('qq_digest/web/templates/summary_reading_script.html','utf8').replace(/<\/?script>/g,'').replace('  summaryInit();',''),scope);
 Object.assign(scope,{summarySelectGroups(){},summaryApplyView(){},summaryShowResult(message){errors.push(message);},loadReports(){loads++;},summarySave(){saves++;},summaryLoadPoints(){loads++;}});
 return {scope,node,requests,errors,loads:()=>loads,saves:()=>saves};
}
(async()=>{
 let result=await exercise([{code:409},{code:409},{code:200}],{retryBusy:true});
 assert.equal(result.calls,3,'startup visit retries maintenance admission');assert.equal(result.toasts,0);assert.equal(result.error,undefined);
 result=await exercise([{code:409}],{});assert.equal(result.calls,1);assert.equal(result.toasts,1,'ordinary write conflicts still notify');
 result=await exercise([{code:409,body:{detail:'restart',restart_required:true}}],{retryBusy:true});assert.equal(result.calls,1);assert.equal(result.handoffs,1);assert.equal(result.error.status,409);
 result=await exercise([{code:401}],{retryBusy:true});assert.equal(result.calls,1);assert.equal(result.login,1);
 result=await exercise([{code:500}],{retryBusy:true});assert.equal(result.calls,1);assert.equal(result.error.status,500);
 result=await exercise([{code:409}],{retryBusy:true});assert.equal(result.calls,31,'maintenance retries have a finite budget');assert.equal(result.toasts,0);assert.equal(result.error.status,409);
 const r=reader(),init=r.scope.summaryInit();
 const visit=r.requests.find(x=>x.url.endsWith('/visit'));
 assert.equal(visit.options.retryBusy,true);
 r.requests.find(x=>x.url==='/api/groups?scope=summary').resolve({groups:[]});await settle();
 assert.equal(r.loads(),1,'normal summary reading does not wait for visit write admission');
 assert.equal(r.node('summary-read-filter-new').disabled,true,'new-since-open waits for its baseline');
 assert.equal(Object.hasOwn(JSON.parse(JSON.stringify(r.scope.summaryValues())),'since'),false,'pending baseline must not be saved as a real first visit');
 visit.resolve({previous_viewed_at:'2026-10-23T10:00:00+08:00'});await init;await settle();
 assert.equal(r.scope.summarySince,'2026-10-23T10:00:00+08:00');assert.equal(r.node('summary-read-filter-new').disabled,false);assert.ok(r.saves()>0,'late visit baseline is persisted');
 const n=reader();n.scope.location.search='?read=new';const pending=n.scope.summaryInit();
 n.requests.find(x=>x.url==='/api/groups?scope=summary').resolve({groups:[]});await settle();assert.equal(n.loads(),0,'new filter must wait for the actual previous visit');
 n.requests.find(x=>x.url.endsWith('/visit')).resolve({previous_viewed_at:'2026-10-23T10:00:00+08:00'});await pending;assert.equal(n.loads(),1);
 const reload=reader();reload.scope.sessionStorage.getItem=()=>JSON.stringify({since:'2026-10-22T10:00:00+08:00'});reload.scope.performance.getEntriesByType=()=>[{type:'reload'}];
 const awaitReload=reload.scope.summaryInit();assert.equal(reload.scope.summarySince,'2026-10-22T10:00:00+08:00','reload keeps a known baseline during maintenance');
 reload.requests.find(x=>x.url==='/api/groups?scope=summary').resolve({groups:[]});reload.requests.find(x=>x.url.endsWith('/visit')).resolve({previous_viewed_at:'2026-10-24T10:00:00+08:00'});await awaitReload;await settle();assert.equal(reload.scope.summarySince,'2026-10-22T10:00:00+08:00');
 const failed=reader();failed.scope.location.search='?read=new';const failure=failed.scope.summaryInit();failed.requests.find(x=>x.url==='/api/groups?scope=summary').resolve({groups:[]});failed.requests.find(x=>x.url.endsWith('/visit')).reject(Object.assign(Error('maintenance deadline'),{status:409}));await failure;
 assert.equal(failed.loads(),1);assert.equal(failed.node('summary-read-filter').value,'all');assert.equal(failed.node('summary-read-filter-new').disabled,true);assert.equal(failed.errors.length,1,'an exhausted visit still has visible local feedback');
 const stale=reader(),staleInit=stale.scope.summaryInit();stale.scope.summarySinceRevision++;stale.scope.summarySince='restored-baseline';stale.requests.find(x=>x.url==='/api/groups?scope=summary').resolve({groups:[]});stale.requests.find(x=>x.url.endsWith('/visit')).resolve({previous_viewed_at:'old-response'});await staleInit;await settle();assert.equal(stale.scope.summarySince,'restored-baseline');assert.equal(stale.errors.length,0);
 console.log('Startup maintenance retries, prompt reading, baseline restoration, bounded failure and auth/handoff boundaries verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
