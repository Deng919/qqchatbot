const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('qq_digest/web/templates/section_navigation.html','utf8');
const node={textContent:'',toggleAttribute(name,value){this.warning=value;}};
global.document={getElementById:()=>node};
let health;
global.api=()=>Promise.resolve(health);
vm.runInThisContext(source.slice(source.indexOf('  function loadWorkspaceStatus()'),source.lastIndexOf('  loadWorkspaceStatus();')));
(async()=>{
  for(const extra of [{latest_daily_status:'failed'},{latest_daily_status:'partial_success'},{daily_coverage:{status:'limited'}},{latest_job_status:'partial_success'},{failed_notifications:1}]){
    health={latest_job_status:'success',failed_notifications:0,...extra};
    loadWorkspaceStatus();await new Promise(setImmediate);
    assert.equal(node.warning,true,JSON.stringify(extra)+' must remain visible after a successful message update');
  }
  health={latest_job_status:'success',latest_daily_status:'success',daily_coverage:{status:'complete'},failed_notifications:0};
  loadWorkspaceStatus();await new Promise(setImmediate);
  assert.equal(node.warning,false);
  console.log('Independent daily, coverage, job and notification warnings verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
