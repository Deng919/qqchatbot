const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2] || 'qq_digest/web/templates/summary_reading_script.html','utf8');
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.text='';}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
  appendChild(node){this.children.push(node);return node;}
}
const host = new Node('div');
global.document={createElement:tag=>new Node(tag),getElementById:()=>host};
vm.runInThisContext(source.slice(source.indexOf('  function summaryNode('),source.indexOf('  function summaryButton(')));
vm.runInThisContext(source.slice(source.indexOf('  function summaryRenderMarkdown('),source.indexOf('  function summaryReadDailyResults(')));
summaryRenderMarkdown('- **路混色器**：建议修改。 〔原消息：7692858474141681191、m2〕\n- **接口**：20口比16口快。 [原消息: 123, 456]\n\n```\n**代码** 〔原消息：123〕\n```');
assert.equal(host.children[0].children[0].textContent,'路混色器：建议修改。');
assert.equal(host.children[0].children[1].textContent,'接口：20口比16口快。');
assert.equal(host.children[1].textContent,'**代码** 〔原消息：123〕\n','code blocks are preserved');
assert.equal(summaryCleanText('计算 2*3=6，**结论**'), '计算 2*3=6，结论');
assert.equal(summaryCleanText('讨论原消息：内容本身'), '讨论原消息：内容本身');
summaryRenderMarkdown('## 主要话题\n- **公司“夏休”与年假调休**：每年八月中旬放九天假。 〔原消息：7693506880749127285、7693506971074568292〕');
assert.equal(host.children[1].children[0].textContent,'公司“夏休”与年假调休：每年八月中旬放九天假。');
console.log('Summary display: citations hidden, paired bold markers removed, code preserved');
