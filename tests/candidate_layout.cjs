const {chromium}=require('playwright'),fs=require('fs'),assert=require('node:assert/strict');
(async()=>{const b=await chromium.launch({headless:true});try{
 const p=await b.newPage(),read=n=>fs.readFileSync('qq_digest/web/templates/'+n,'utf8');
 const base=[...read('base.html').matchAll(/<style>[\s\S]*?<\/style>/g)].map(m=>m[0]).join('');
 const style=read('candidates.html').match(/<style>[\s\S]*?<\/style>/)[0];
 const url='https://example.com/'+('long-identifier'.repeat(30));
 await p.setContent(base+read('inbox_workspace_style.html')+style+'<body class="workspace-app"><main id="main-content"><div class="content"><div class="candidate-list"><article class="candidate-item"><div class="candidate-main"><div><strong>'+('长标题'.repeat(50))+'</strong></div><div class="candidate-actions"><button class="btn">详情</button><button class="btn">入库</button></div></div><p class="candidate-reason">'+('LongUnbrokenReason'.repeat(30))+'</p><a href="'+url+'">'+url+'</a></article></div></div></main></body>');
 for(const width of [1440,768,390,320]){await p.setViewportSize({width,height:900});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'candidate content and actions fit '+width);for(const button of await p.locator('.candidate-actions button').all()){const box=await button.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width,'actions remain reachable');}}
 assert.equal(await p.locator('.candidate-item a').getAttribute('href'),url,'wrapping does not truncate the resource URL');console.log('Long candidate titles, reasons, URLs and reachable actions fit desktop/mobile');
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
