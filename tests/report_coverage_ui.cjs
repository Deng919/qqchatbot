const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('fs');
(async()=>{
 const source=fs.readFileSync('qq_digest/web/templates/reports.html','utf8');
 const functions=source.slice(source.indexOf('  function coverageBadge('),source.indexOf('  function renderReportEvidence('));
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage();
  await page.setContent('<summary id="summary-coverage-label"></summary><section id="report-completeness"></section>');
  await page.addScriptTag({content:"function escapeHtml(value){var n=document.createElement('span');n.textContent=value;return n.innerHTML;}\n"+functions});
  const normal={status:'normal',label:'输入已覆盖',source_messages:20,included_messages:20,notes:[],daily:{status:'limited',participating_groups:3,report_groups:2,failed_groups:0,notes:['一个群没有消息','另一个群消息较少']}};
  assert.equal(await page.evaluate(data=>coverageBadge(data),normal),'');
  await page.evaluate(data=>renderReportCompleteness(data),normal);
  assert.equal(await page.locator('#report-completeness > ul').count(),0);
  assert.equal(await page.locator('.report-daily-coverage li').count(),2);
  assert.equal(await page.locator('#summary-coverage-label').innerText(),'消息范围与统计 · 输入已覆盖');
  const limited={...normal,status:'limited',label:'消息较少',notes:['实际纳入 3 条消息。<img src=x onerror=alert(1)>']};
  await page.evaluate(data=>renderReportCompleteness(data),limited);
  assert.equal(await page.locator('#report-completeness > ul li').count(),1);
  assert.equal(await page.locator('.report-daily-coverage li').count(),2);
  assert.equal(await page.locator('#report-completeness img').count(),0);
  assert.ok((await page.evaluate(data=>coverageBadge(data),limited)).includes('消息较少'));
  await page.evaluate(()=>renderReportCompleteness({status:'unknown',daily:{participating_groups:0,notes:['范围尚未确定']}}));
  assert.equal(await page.locator('.report-daily-coverage li').innerText(),'范围尚未确定');
  const dashboard=fs.readFileSync('qq_digest/web/templates/dashboard.html','utf8');
  assert.ok(!dashboard.includes('id="daily-coverage-scope"'),'overview omits the removed scope disclaimer');
  await page.setContent(dashboard.match(/<section class="health-strip"[\s\S]*?<\/section>/)[0]+dashboard.match(/<section id="daily-coverage"[\s\S]*?<\/section>/)[0]);
  await page.addScriptTag({content:"var statusLabels={success:'成功',failed:'失败'};var formatNumber=String;\n"+dashboard.slice(dashboard.indexOf('  function loadHealth('),dashboard.indexOf('  function loadStats('))});
  const daily={report_date:'2026-10-25',status:'normal',label:'生成成功，2 个群无消息跳过',participating_groups:14,report_groups:12,failed_groups:0,no_message_groups:2,notes:['2 个群因本时间窗无归档消息而跳过，未生成日报。'],scope:'不应显示这行说明'};
  await page.evaluate(daily=>{window.health={latest_daily_status:'success',latest_job_status:'success',failed_notifications:0,daily_coverage:daily};window.api=()=>Promise.resolve(health);loadHealth();},daily);
  await page.waitForFunction(()=>document.getElementById('health-service').textContent==='服务运行正常');
  assert.equal(await page.locator('#daily-coverage').getAttribute('data-status'),'normal');assert.match(await page.locator('#daily-coverage-title').textContent(),/生成成功，2 个群无消息跳过/);assert.ok(!(await page.locator('body').textContent()).includes('不应显示这行说明'));
  await page.evaluate(()=>{health.daily_coverage={...health.daily_coverage,status:'limited',label:'日报存在限制',failed_groups:1};loadHealth();});
  await page.waitForFunction(()=>document.getElementById('health-service').textContent.includes('有待处理异常'));assert.equal(await page.locator('#health-dot').getAttribute('class'),'health-dot is-warning');
  console.log('Report warnings, own input notes, separate daily context and plain-text rendering verified');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
