const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('qq_digest/web/templates/group_details.html','utf8').split('<script>')[1].split('</script>')[0];
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',checked:false,hidden:true,disabled:false,textContent:'',innerHTML:'',focus(){},scrollIntoView(){},toggleAttribute(){}});return nodes.get(id);}
const groups=[{group_id:123,name:'A',daily_summary:true,category:'tech',keywords:[],collection_window_days:30,important_candidates:true}];
let calls=[];
const context={document:{getElementById:node,querySelector:()=>node('advanced'),addEventListener(){}},configuredGroups:groups,formatNumber:String,repairUrl:()=>'',escapeHtml:String,renderConfiguredGroups(){},api:async(method,path,body)=>{calls.push(body);return{};},URLSearchParams};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
  context.openGroupDetail(123);
  assert.equal(node('group-read-images').disabled,true,'unknown flags must not be editable');
  node('group-read-images').checked=true;
  node('group-keywords').value='changed';
  context.saveGroupDetail({preventDefault(){}});await new Promise(setImmediate);
  assert.equal(Object.hasOwn(calls[0],'read_images'),false,'unknown state must not be submitted');
  assert.equal(Object.hasOwn(calls[0],'read_files'),false);
  Object.assign(groups[0],{read_images:true,read_files:false});context.openGroupDetail(123);
  assert.equal(node('group-read-images').disabled,false);
  assert.equal(node('group-read-images').checked,true);
  node('group-read-files').checked=true;context.saveGroupDetail({preventDefault(){}});await new Promise(setImmediate);
  assert.equal(Object.hasOwn(calls[1],'read_images'),false);assert.equal(calls[1].read_files,true);
  console.log('Group attachment flags preserve unknown and saved values');
})().catch(e=>{console.error(e);process.exitCode=1;});
