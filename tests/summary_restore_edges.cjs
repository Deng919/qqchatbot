const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const root=process.cwd()+'/';
const nodes=new Map(), events=new Map(), requests=[], timers=new Map();let next=0;
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,textContent:'',children:[],appendChild(n){this.children.push(n);},focus(){},querySelector(){return null;},setAttribute(){}});return nodes.get(id);}
global.document={getElementById:node,createElement:tag=>({tag,appendChild(){},setAttribute(){},addEventListener(){}}),activeElement:{focus(){}},querySelector:()=>node('visible-modal-button'),querySelectorAll:()=>[],addEventListener:(name,fn)=>events.set(name,fn)};
global.setTimeout=(fn,delay)=>{const id=++next;timers.set(id,{fn,delay});return id;};global.clearTimeout=id=>timers.delete(id);
global.api=(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));
global.summaryNode=(tag,cls,text)=>({tag,textContent:text||'',appendChild(){}});global.summaryButton=summaryNode;global.summaryShowResult=()=>{};global.refreshDateHints=()=>{};global.loadReports=()=>{};global.formatNumber=String;global.toast=()=>{};
for(const file of ['group_selector.html','summary_progress_script.html','summary_generation_script.html']){const text=fs.readFileSync(root+'qq_digest/web/templates/'+file,'utf8');vm.runInThisContext(text.match(/<script>([\s\S]*?)<\/script>/)[1]);}
global.recentWeekDates=()=>({start:'2026-10-08',end:'2026-10-14'});
assert.equal(rangeTimeMode('2026-10-14','2026-10-14',recentWeekDates()),'today');
assert.equal(rangeTimeMode('2026-10-08','2026-10-14',recentWeekDates()),'week');
const state=GroupSelector.createState({allowAll:false,selected:[11]});
global.rangeGroupSelector={ids:state.ids,setSelected:state.setSelected,setGroups:state.setGroups};
rangeGroups=[{group_id:11,name:'Synthetic'}];rangeGroupsLoaded=true;
const base={generation_id:'restore',scope:{group_ids:[22,33],start_date:'2026-10-05',end_date:'2026-10-06'},status:'running',stage:'summary',total:2,completed:0,counts:{created:0,reused:0,skipped:0,failed:0},elapsed_seconds:1};
async function flush(){await new Promise(setImmediate);}
function reset(){summaryGenerationBusy=false;summaryProgressId=null;summaryProgressFinished=null;summaryProgressPolling=null;timers.clear();rangeFormDirty=false;}
(async()=>{
 node('report-from').value='2026-10-14';node('report-to').value='2026-10-14';node('report-group').value='11';openRangeSummary();
 const oldPreview=refreshRangePreview(),oldRequest=requests.at(-1);
 const restore=summaryRestoreGeneration();requests.at(-1).resolve({generation:base});await restore;
 assert.equal(node('range-start-date').value,'2026-10-05');assert.deepEqual(selectedRangeGroupIds(),[22,33]);assert.equal(node('range-selection-summary').textContent,'已选择 2 个群，共 2 天');
 oldRequest.resolve({groups:[],total_messages:99,timezone:'Asia/Shanghai'});await oldPreview;assert.equal(rangePreviewData,null);
 assert.equal(rangeFormDirty,false);assert.equal(requests.filter(r=>r.url==='/api/summaries').length,0);
 console.log('PASS actual range updater: date/groups/count synchronized; pending old preview discarded; no generation POST; programmatic restore stays pristine');
 assert.equal(node('range-time-mode').value,'custom','restored dates must agree with the displayed time preset');
 reset();node('range-time-mode').value='today';const initial=summaryRestoreGeneration();requests.at(-1).reject(Object.assign(Error('expired'),{status:401}));await initial;events.get('qq-digest-session-restored')();requests.at(-1).resolve({generation:{...base,status:'failed',error:'synthetic terminal interruption'}});await flush();
 assert.equal(summaryGenerationBusy,false);assert.equal(summaryProgressId,'restore');assert.equal(node('range-progress-connection').textContent,'','terminal recovery clears obsolete login/connection feedback');
 reset();const old=summaryRestoreGeneration(),oldReq=requests.at(-1);const latest=summaryRestoreGeneration(),latestReq=requests.at(-1);latestReq.resolve({generation:base});await latest;oldReq.reject(Object.assign(Error('expired old'),{status:401}));await old;assert.equal(summaryProgressId,'restore');
 console.log('PASS older restoration error does not overwrite latest task ID');
 reset();const pending=summaryRestoreGeneration(),pendingReq=requests.at(-1);beginSummaryGeneration('new task','user-new',base.scope);pendingReq.resolve({generation:base});await pending;assert.equal(summaryProgressId,'user-new');
 console.log('PASS user new generation wins pending restore');
 reset();const dirty=summaryRestoreGeneration(),dirtyReq=requests.at(-1);rangeFormDirty=true;node('range-start-date').value='2026-10-12';state.setSelected([44]);rangePreviewData={total_messages:5};rangePreviewKey=rangeScopeKey();dirtyReq.resolve({generation:base});await dirty;assert.equal(node('range-start-date').value,'2026-10-12');assert.deepEqual(state.ids(),[44]);assert.equal(rangePreviewData.total_messages,5);
 console.log('PASS edit during pending restore preserves date, group and preview');
})().catch(error=>{console.error(error);process.exitCode=1;});
