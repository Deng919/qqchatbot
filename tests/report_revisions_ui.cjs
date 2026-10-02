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
vm.runInThisContext(script.slice(script.indexOf('  function openReportQA()'),script.indexOf('  function appendQAMessage')));
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
  global.featureFlags={report_revisions:false,report_qa:true};
  let revisionCalls=0;global.api=()=>{revisionCalls++;throw Error('history disabled');};
  await loadReportRevisions(reportSelectionToken);
  assert.equal(revisionCalls,0);
  assert.equal(document.getElementById('report-qa-open').disabled,false,'current report QA must work without the version panel');
  openReportQA();
  assert.equal(document.getElementById('report-qa-dialog').hidden,false);
  closeReportQA();featureFlags.report_qa=false;openReportQA();
  assert.equal(document.getElementById('report-qa-dialog').hidden,true);
  const search=fs.readFileSync('qq_digest/web/templates/search.html','utf8');
  vm.runInThisContext(search.slice(search.indexOf('  function renderSearchResult'),search.indexOf('  function loadSearch')));
  featureFlags.review=false;featureFlags.tasks=false;
  let searchResult=renderSearchResult({kind:'knowledge',id:1,url:'/candidates?candidate_id=1',title:'资源',group_name:'test',date:'2026-10-02',snippet:'text'});
  assert.ok(!searchResult.includes('href="/candidates'),'review links must disappear when disabled');
  searchResult=renderSearchResult({kind:'message',id:'m1',group_id:123,title:'消息',group_name:'test',date:'2026-10-02',snippet:'text'});
  assert.ok(!searchResult.includes('加入待办'));
  console.log('Report body/version and historical QA races verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
