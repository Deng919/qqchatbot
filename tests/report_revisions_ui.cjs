// Exercise actual template functions with delayed responses, without browser globals.
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('qq_digest/web/templates/reports.html','utf8');
const script = html.slice(html.indexOf('<script>')+8, html.lastIndexOf('</script>'));
const nodes = new Map();
class TestNode {
  constructor(tag='div'){this.tagName=tag;this.value='';this.hidden=true;this.disabled=false;this.innerHTML='';this.children=[];this.text='';}
  get textContent(){return this.text+this.children.map(child=>child.textContent).join('');}
  set textContent(value){this.text=String(value);this.children=[];}
  appendChild(child){this.children.push(child);return child;}
  focus(){}
}
global.document = {activeElement:null,createElement(tag){return new TestNode(tag);},getElementById(id){if(!nodes.has(id))nodes.set(id,new TestNode());return nodes.get(id);},querySelectorAll(){return [];}};
global.escapeHtml=v=>String(v??'');global.toast=()=>{};
const reading = fs.readFileSync('qq_digest/web/templates/summary_reading_script.html','utf8');
vm.runInThisContext(reading.slice(reading.indexOf('  function summaryNode('),reading.indexOf('  function summaryButton(')));
vm.runInThisContext(reading.slice(reading.indexOf('  function summaryRenderMarkdown('),reading.indexOf('  function summaryReadDailyResults(')));
vm.runInThisContext(script.slice(0,script.indexOf('  function reportParams()')));
vm.runInThisContext(script.slice(script.indexOf('  function openReportQA()'),script.indexOf('  document.addEventListener')));
(async()=>{
  qaReport={kind:'daily',id:1,current_version:1,group_name:'test',start:'2026-10-01',end:'2026-10-01'};
  document.getElementById('report-content').textContent='v1 body';
  let reloads=0;
  global.viewReport=()=>{reloads++;};
  global.api=(method,url)=>Promise.resolve(url.endsWith('/corrections')?{corrections:[]}:
    {current_version:2,total:2,page:1,page_size:10,versions:[{version:2,created_at:'2026-10-01T10:00:00Z',reason:'new'}]});
  await loadReportRevisions(reportSelectionToken);
  assert.equal(reloads,1,'new metadata must reload the body, not relabel old content');
  assert.equal(document.getElementById('correction-fields').disabled,true);
  qaReport.current_version=2;
  revisionReport={kind:'daily',id:1,current_version:2,viewing_version:2};
  let release;
  global.api=()=>new Promise(resolve=>{release=resolve;});
  document.getElementById('revision-select').value='1';
  selectReportVersion();
  assert.equal(document.getElementById('report-qa-open').disabled,true,'QA disabled while loading historical body');
  openReportQA();
  assert.equal(document.getElementById('report-qa-dialog').hidden,true);
  release({markdown:'v1 historical',created_at:'2026-10-01T09:00:00Z',reason:'older',content_available:true});
  await new Promise(setImmediate);
  assert.equal(document.getElementById('report-content').textContent,'v1 historical');
  assert.equal(document.getElementById('report-qa-dialog').hidden,true);
  assert.equal(document.getElementById('correction-fields').disabled,true);
  assert.equal(document.getElementById('report-evidence').hidden,true);
  revisionReport.viewing_version=2;revisionReport.current_version=2;document.getElementById('report-content').textContent='current body';
  let rejectHistory;global.api=()=>new Promise((resolve,reject)=>{rejectHistory=reject;});
  document.getElementById('revision-select').value='1';selectReportVersion();
  rejectHistory(new Error('历史服务不可用'));await new Promise(setImmediate);
  assert.match(document.getElementById('report-content').textContent,/历史版本读取失败：历史服务不可用/);
  assert.match(document.getElementById('revision-notice').textContent,/重试/);
  const retry=document.getElementById('revision-notice').children.find(child=>child.tagName==='button');
  assert.ok(retry,'history failure must offer a direct retry action');
  let releaseRetry;global.api=()=>new Promise(resolve=>{releaseRetry=resolve;});retry.onclick();
  assert.match(document.getElementById('report-content').textContent,/正在读取历史版本/);
  releaseRetry({markdown:'retried history',created_at:'2026-10-01',reason:'retry',content_available:true});await new Promise(setImmediate);
  assert.match(document.getElementById('report-content').textContent,/retried history/);
  featureFlags={report_revisions:true,report_qa:true};qaReport={kind:'daily',id:1,current_version:2};revisionReport={kind:'daily',id:1,current_version:2,viewing_version:2};qaRequestToken=0;qaHistory=[];qaBusy=false;
  let releaseQuestion;global.api=()=>new Promise(resolve=>{releaseQuestion=resolve;});
  document.getElementById('report-qa-question').value='问题 A';askReport({preventDefault(){}});
  document.getElementById('report-qa-question').value='问题 B';
  releaseQuestion({answer:'回答 A',context_message_count:1,sources:[]});await new Promise(setImmediate);
  assert.equal(document.getElementById('report-qa-question').value,'问题 B','an answer must not clear a newer draft');
  global.featureFlags={report_revisions:false,report_qa:true};
  let revisionCalls=0;global.api=()=>{revisionCalls++;throw Error('history disabled');};
  await loadReportRevisions(reportSelectionToken);
  assert.equal(revisionCalls,0);
  assert.equal(document.getElementById('report-qa-open').disabled,false,'current report QA must work without the version panel');
  openReportQA();
  assert.equal(document.getElementById('report-qa-dialog').hidden,false);
  closeReportQA();featureFlags.report_qa=false;openReportQA();
  assert.equal(document.getElementById('report-qa-dialog').hidden,true);
  vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/find_helpers.html','utf8').replace(/<\/?script>/g,''));
  const search=fs.readFileSync('qq_digest/web/templates/find_script.html','utf8');
  global.findKindLabels={message:'聊天消息',report:'摘要',knowledge:'知识'};
  vm.runInThisContext(search.slice(search.indexOf('function findHitMarkup'),search.indexOf('function loadSearch')));
  featureFlags.review=false;featureFlags.tasks=false;
  let searchResult=renderFindGroup({kind:'knowledge',items:[{kind:'knowledge',id:1,url:'/knowledge?item_id=1',title:'资源',group_name:'test',date:'2026-10-02',snippet:'text'}]},'资源');
  assert.ok(searchResult.includes('href="/knowledge?item_id=1'),'Saved knowledge stays readable when candidate review is disabled');
  searchResult=renderFindGroup({kind:'message',items:[{kind:'message',id:'m1',group_id:123,title:'消息',group_name:'test',date:'2026-10-02',snippet:'text'}]},'消息');
  assert.ok(!searchResult.includes('加入待办'));
  console.log('Report body/version and historical QA races verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
