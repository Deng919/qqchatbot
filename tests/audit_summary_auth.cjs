const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes={},get=id=>nodes[id]??={value:'',disabled:false,hidden:false,textContent:'',setAttribute(){}};
let opened=0,calls=0,next=0;const timers=new Map(),events=new Map();
const scope={URLSearchParams,Date,Math,Number,Array,Set,Object,Error,
  document:{getElementById:get,addEventListener:(name,handler)=>events.set(name,handler)},
  SessionLogin:{open(){opened++;}},fetch:()=>{calls++;return Promise.resolve({status:401});},toast(){},
  setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),
  summaryShowResult(){},formatNumber:String};
vm.createContext(scope);
const base=fs.readFileSync('qq_digest/web/templates/base.html','utf8');
vm.runInContext(base.slice(base.indexOf('function api('),base.indexOf('function showStorageHandoff(')),scope);
for(const file of ['summary_generation_script.html','summary_progress_script.html'])
  vm.runInContext(fs.readFileSync('qq_digest/web/templates/'+file,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1],scope);
scope.summaryDrawProgress=()=>{};scope.updateGenerationControls=()=>{};scope.generationNextSteps=()=>{};
scope.rangeGroupSelector={ids:()=>[123]};
get('range-start-date').value=get('range-end-date').value='2026-10-14';
scope.rangePreviewData={total_messages:1};scope.rangePreviewKey=scope.rangeScopeKey();
(async()=>{
  scope.submitRangeSummary({preventDefault(){}});await new Promise(setImmediate);
  assert.equal(scope.summaryGenerationBusy,false,'a rejected generation POST releases the form');
  assert.equal(opened,1);assert.equal(calls,1);assert.equal(timers.size,0,'a rejected POST must not start background polling');
  events.get('qq-digest-session-restored')();await new Promise(setImmediate);
  assert.equal(calls,1,'login must not replay rejected generation');
  console.log('Expired generation submission stops once and is never automatically replayed');
})().catch(error=>{console.error(error);process.exitCode=1;});
