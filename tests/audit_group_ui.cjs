const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function node(){return {value:'',checked:false,disabled:false,hidden:false,textContent:'',innerHTML:'',options:[],style:{},dataset:{},children:[],appendChild(n){this.children.push(n)},addEventListener(){},focus(){},scrollIntoView(){},toggleAttribute(){}}}
const nodes={},get=id=>nodes[id]??=node(),requests=[];
const group={group_id:123,name:'test',category:'general',keywords:[],daily_summary:true,important_candidates:true,read_images:false,read_files:false,collection_window_days:30,message_count:2,last_sync:'2026-08-01T00:00:00+08:00',revision:4};
const scope={console,Date,URLSearchParams,Set,Number,Array,formatNumber:String,escapeHtml:String,configuredGroups:[group],repairUrl:()=>'/collect',renderConfiguredGroups(){},document:{getElementById:get,querySelector:()=>node(),addEventListener(){}},api:(method,url,body)=>new Promise(resolve=>requests.push({method,url,body,resolve}))};
vm.createContext(scope);vm.runInContext(fs.readFileSync('qq_digest/web/templates/group_details.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1],scope);
scope.openGroupDetail(123);get('group-keywords').value='changed';scope.saveGroupDetail({preventDefault(){}});
assert.equal(requests[0].body.expected_revision,4,'group editor must send the original revision');
assert.equal(requests[0].body.keywords[0],'changed');
assert.ok(!Object.hasOwn(requests[0].body,'read_images'),'unchanged permissions must not be resent');
assert.ok(!Object.hasOwn(requests[0].body,'category'),'unchanged category must not be resent');
assert.match(get('group-detail-links').innerHTML,/browse=1/,'view messages must open browsing');
assert.ok(new URLSearchParams(scope.updateUrl(group).split('?')[1]).get('start')<='2026-08-01','update must include prior sync gap');
const attachScope={console,document:{getElementById:get,addEventListener(){},createElement:node},configuredGroups:[{...group,enabled:false}],destroyMessageContext(){}};
vm.createContext(attachScope);vm.runInContext(fs.readFileSync('qq_digest/web/templates/attachment_controls.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1],attachScope);
attachScope.attachmentRender({enabled:false,read_images:true,read_files:false,items:[],counts:{}});
assert.equal(get('attachment-read').disabled,true,'disabled groups cannot imply attachment reading is available');
assert.match(get('attachment-status').textContent,/关注|停用/,'disabled reading must explain its actual reason');
console.log('Group changed-field/revision, browsing, gap window and disabled attachment regressions verified');
