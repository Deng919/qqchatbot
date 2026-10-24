const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',options:[],appendChild(n){this.options.push(n);},hidden:false});return nodes.get(id);}
global.document={getElementById:node,createElement:()=>({}),addEventListener(){}};
global.window={addEventListener(){}};global.reportPage=1;global.destroyMessageContext=()=>{};
const reading=fs.readFileSync('qq_digest/web/templates/summary_reading_script.html','utf8').replace(/<\/?script>/g,'').replace('  summaryInit();','');
vm.runInThisContext(reading);
global.summarySetView=(v)=>{global.summaryView=v;};global.closeReport=()=>{};global.refreshDateHints=()=>{};global.filterReports=()=>{};global.closeRangeSummary=()=>{};
summaryReadRangeResults('2026-09-28','2026-10-04',[11,22],['daily:1']);
assert.equal(node('report-group').value,'11,22','generation keeps exactly the selected groups');
assert.equal(summaryView,'points');assert.equal(node('report-kind').value,'all');
assert.equal(node('summary-read-filter').value,'all');
const params=new URLSearchParams();summaryGroupParams(params,node('report-group').value);
assert.deepEqual(params.getAll('group_ids'),['11','22']);assert.equal(params.has('group_id'),false);
summarySelectGroups(['11','22']);assert.equal(node('report-group').options.length,1,'restoring selection does not duplicate options');
summarySelectGroups(['11']);const one=new URLSearchParams();summaryGroupParams(one,node('report-group').value);
assert.equal(one.get('group_id'),'11');assert.equal(one.has('group_ids'),false);
console.log('Single/multi-group result reading, restore and parameter encoding verified');

const generation=fs.readFileSync('qq_digest/web/templates/summary_generation_script.html','utf8').replace(/<\/?script>/g,'');
function generationSelection(preferred,groups){
  const elements=new Map(),mounts=[];
  function element(id){
    if(!elements.has(id))elements.set(id,{value:'',hidden:false,textContent:'',focus(){},querySelector(){return null;}});
    return elements.get(id);
  }
  const context={
    document:{getElementById:element,activeElement:{focus(){}},querySelector:()=>element('first-control')},
    refreshDateHints(){},
    GroupSelector:{mount(host,options){
      mounts.push(options);
      let selected=Array.from(options.selected);
      return {ids:()=>selected.slice(),setGroups(){},setSelected(ids){selected=Array.from(ids);},chooseVisible(checked){
        selected=checked?groups.map(group=>Number(group.group_id)):[];
        options.onChange();
      }};
    }}
  };
  vm.createContext(context);vm.runInContext(generation,context);
  context.updateRangeSelectionSummary=()=>{};
  context.rangeGroups=groups;context.rangeGroupsLoaded=true;
  element('report-group').value=preferred;
  context.openRangeSummary();context.renderRangeGroups();
  return {context,mounts,element};
}
const group11={group_id:'11',name:'One',enabled:true},group22={group_id:22,name:'Two',enabled:true};
const sole=generationSelection('',[group11]);
assert.deepEqual(sole.context.selectedRangeGroupIds(),[11],'first mount selects the sole enabled group as a numeric ID');
assert.equal(sole.mounts[0].open,false,'a sole selected group starts collapsed');
assert.equal(sole.mounts[0].allowAll,false,'generation requires explicit groups');
const multiple=generationSelection('11,22',[group11,group22]);
assert.deepEqual(multiple.context.selectedRangeGroupIds(),[11,22],'reading selection is preserved for multiple groups');
assert.equal(multiple.mounts[0].open,false,'existing multi-group selection starts collapsed');
const single=generationSelection('22',[group11,group22]);
assert.deepEqual(single.context.selectedRangeGroupIds(),[22],'an existing single-group selection is preserved');
assert.equal(single.mounts[0].open,false);
const unavailable=generationSelection('99',[group11]);
assert.deepEqual(unavailable.context.selectedRangeGroupIds(),[],'an unavailable preferred group requires a new choice instead of selecting a different group');
assert.equal(unavailable.mounts[0].open,true);
const unselected=generationSelection('',[group11,group22]);
assert.deepEqual(unselected.context.selectedRangeGroupIds(),[],'multiple available groups require a choice');
assert.equal(unselected.mounts[0].open,true,'an unselected picker starts expanded');
const empty=generationSelection('__empty__',[group11]);
assert.deepEqual(empty.context.selectedRangeGroupIds(),[],'an explicitly empty reading selection stays empty');
assert.equal(empty.mounts[0].open,true);
sole.context.selectVisibleRangeGroups(false);
sole.context.renderRangeGroups();sole.context.closeRangeSummary();sole.context.openRangeSummary();sole.context.renderRangeGroups();
assert.deepEqual(sole.context.selectedRangeGroupIds(),[],'clearing survives rerender and reopening');
assert.equal(sole.mounts.length,1,'rerender and reopening reuse the existing selector');
unselected.context.rangeGroups=[group11];unselected.context.renderRangeGroups();
assert.deepEqual(unselected.context.selectedRangeGroupIds(),[],'a later group update does not auto-select');
const noGroups=generationSelection('',[]);
assert.deepEqual(noGroups.context.selectedRangeGroupIds(),[],'an empty group list has no default');
assert.equal(noGroups.mounts[0].open,true);
console.log('Generation defaults, preferred selections, collapse, explicit empty and clear persistence verified');
