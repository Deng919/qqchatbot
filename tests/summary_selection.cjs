const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',options:[],appendChild(n){this.options.push(n);},hidden:false});return nodes.get(id);}
global.document={getElementById:node,createElement:()=>({}),addEventListener(){}};
global.window={addEventListener(){}};global.reportPage=1;
const reading=fs.readFileSync('qq_digest/web/templates/summary_reading_script.html','utf8').replace(/<\/?script>/g,'').replace('  summaryInit();','');
vm.runInThisContext(reading);
global.summarySetView=(v)=>{global.summaryView=v;};global.closeReport=()=>{};global.refreshDateHints=()=>{};global.filterReports=()=>{};global.closeRangeSummary=()=>{};
summaryReadRangeResults('2026-09-28','2026-10-04',[11,22]);
assert.equal(node('report-group').value,'11,22','generation keeps exactly the selected groups');
assert.equal(summaryView,'points');assert.equal(node('report-kind').value,'all');
assert.equal(node('summary-read-filter').value,'all');
const params=new URLSearchParams();summaryGroupParams(params,node('report-group').value);
assert.deepEqual(params.getAll('group_ids'),['11','22']);assert.equal(params.has('group_id'),false);
summarySelectGroups(['11','22']);assert.equal(node('report-group').options.length,1,'restoring selection does not duplicate options');
summarySelectGroups(['11']);const one=new URLSearchParams();summaryGroupParams(one,node('report-group').value);
assert.equal(one.get('group_id'),'11');assert.equal(one.has('group_ids'),false);
console.log('Single/multi-group result reading, restore and parameter encoding verified');
