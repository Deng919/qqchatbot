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
  console.log('Report warnings, own input notes, separate daily context and plain-text rendering verified');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
