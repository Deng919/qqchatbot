const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes=new Map(),list={scrollTop:640},reading={scrollTop:320};let desktop=true,saved;
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:true,children:[],options:[],classList:{add(){},remove(){},toggle(){}},appendChild(n){this.children.push(n);this.options.push(n);},setAttribute(){},removeAttribute(){},focus(){},querySelector(){return node(id+'-child');},scrollIntoView(){}});return nodes.get(id);}
node('summary-reading-section').parentElement=list;node('report-layout').parentElement=reading;
Object.assign(global,{document:{getElementById:node,activeElement:node('opener'),createElement:()=>node('new-'+nodes.size),querySelectorAll:()=>[],addEventListener(){}},window:{scrollY:120,matchMedia:()=>({matches:desktop}),scrollTo({top}){this.scrollY=top;},addEventListener(){}},sessionStorage:{setItem(_,v){saved=JSON.parse(v);}},location:{href:'http://localhost/reports',search:''},history:{replaceState(){}},requestAnimationFrame:fn=>fn(),api:()=>new Promise(()=>{}),refreshDateHints(){},featureFlags:{report_qa:false,report_revisions:false}});
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/reports.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1]);
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/summary_reading_script.html','utf8').replace(/<\/?script>/g,'').replace('  summaryInit();',''));
node('report-from').value='2026-10-22';node('report-to').value='2026-10-23';node('report-kind').value='all';node('summary-read-filter').value='all';
summarySave();assert.equal(saved.scroll,640,'desktop reading saves the list column, not window.scrollY');
list.scrollTop=0;summaryPendingScroll=640;summaryRestoreScroll();assert.equal(list.scrollTop,640,'restoration targets the desktop list column');assert.equal(window.scrollY,120);
viewReport('daily',1);assert.equal(summaryBrowseScroll,640,'opening a report captures the list position');
summaryApplyView=()=>{};summaryDetailActive=true;list.scrollTop=0;summaryLeaveDetail();assert.equal(list.scrollTop,640,'closing a report restores the desktop list');
list.clientHeight=0;list.scrollTop=0;summarySave();assert.equal(saved.scroll,640,'hidden desktop list retains its captured position');delete list.clientHeight;list.scrollTop=640;
summaryView='manage';summarySave();assert.equal(saved.scroll,320,'report management saves its own scrolling column');reading.scrollTop=0;summaryPendingScroll=320;summaryRestoreScroll();assert.equal(reading.scrollTop,320);
desktop=false;summaryView='points';summarySave();assert.equal(saved.scroll,120,'mobile retains document scrolling');summaryPendingScroll=450;summaryRestoreScroll();assert.equal(window.scrollY,450);assert.equal(list.scrollTop,640);
const navigation=fs.readFileSync('qq_digest/web/templates/section_navigation.html','utf8');vm.runInThisContext(navigation.slice(navigation.indexOf('  function navigateSummaryReading('),navigation.indexOf('  function updateWorkspaceTools(')));
desktop=true;location.pathname='/reports';summarySetView=()=>{};navigateSummaryReading({button:0,preventDefault(){}});assert.equal(list.scrollTop,0,'the reading home action returns the desktop column to its beginning');
console.log('Desktop reading/management scroll, full-report return and mobile document scroll verified');
