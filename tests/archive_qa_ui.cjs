const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes={},requests=[];
function element(){return {value:'',textContent:'',hidden:true,options:[],children:[],appendChild(n){this.children.push(n);},scrollIntoView(){},addEventListener(){}};}
const get=id=>nodes[id]??=element();
const scope={console,Array,String,Date,Number,encodeURIComponent,document:{getElementById:get,createElement:element},api(method,url,payload){return new Promise((resolve,reject)=>requests.push({method,url,payload,resolve,reject}));}};
vm.createContext(scope);
const script=fs.readFileSync('qq_digest/web/templates/archive_qa_script.html','utf8').replace(/<\/?script>/g,'');
vm.runInContext(script.slice(0,script.indexOf('\n(function(){')),scope);
scope.archiveQAGroupsReady=true;get('qa-question').value='接口配置？';get('qa-from').value='2026-10-06';get('qa-to').value='2026-10-06';get('qa-group').options=[{value:'',textContent:'全部群'}];
const data={ticket:'ticket-test',scope:{group_id:null,date_from:'2026-10-06',date_to:'2026-10-06',timezone:'Asia/Shanghai'},matched_count:1,messages:[{group_name:'合成群',timestamp:'2026-10-06',excerpt:'<svg onload=alert(1)>'}]};
(async()=>{
 let pending=scope.archiveQAPreview({preventDefault(){}}),request=requests.shift();scope.qaInvalidate();request.resolve(data);await pending;
 assert.equal(scope.archiveQATicket,null,'changed inputs discard delayed preview');
 pending=scope.archiveQAPreview({preventDefault(){}});requests.shift().resolve(data);await pending;
 assert.equal(scope.archiveQATicket,data.ticket);assert.equal(get('qa-send').disabled,false);
 pending=scope.archiveQAAsk();request=requests.shift();scope.qaInvalidate();request.resolve({answer:'旧答案',sources:[],context_message_count:1});await pending;
 assert.equal(get('qa-result').hidden,true,'changed inputs discard delayed answers');
 scope.archiveQATicket=data.ticket;pending=scope.archiveQASource(data.ticket,'s1');request=requests.shift();scope.qaInvalidate();request.resolve({group_name:'群',timestamp:'2026-10-06',text:'旧来源',scope:data.scope});await pending;
 assert.equal(get('qa-original').hidden,true,'changed inputs discard delayed sources');
 scope.archiveQAGroupsReady=false;scope.qaControls();assert.equal(get('qa-preview-button').disabled,true,'group lookup failure cannot silently widen scope');
 assert.equal(scope.archiveQAPreview({preventDefault(){}}),undefined);assert.equal(requests.length,0);
 assert.match(scope.qaTime('2026-10-05T12:00:00Z','Pacific/Kiritimati'),/2026\/10\/6/);
 console.log('Archive QA stale preview/answer/source and unavailable groups verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
