const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const test=require('node:test');
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.text='';}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(node=>node.textContent).join('');}
  appendChild(node){this.children.push(node);return node;}
}
const file=__dirname+'/../qq_digest/web/templates/group_batch_feedback.html';
assert.ok(fs.existsSync(file),'shared batch feedback must exist');
const code=fs.readFileSync(file,'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const c={console,document:{createElement:tag=>new Node(tag)},formatNumber:String};vm.createContext(c);vm.runInContext(code,c);
const payload={group_ids:[1,2,3],start:'2026-10-01',end:'2026-10-07',refresh:true};
const retry=c.groupBatchRetryPayload(payload,[{group_id:1,status:'success'},{group_id:2,status:'failed'},{group_id:3,status:'success'}]);
assert.equal(retry.group_ids.join(','),'2');assert.equal(retry.start,payload.start);assert.equal(retry.end,payload.end);assert.equal(payload.group_ids.join(','),'1,2,3');
const add=c.groupBatchRetryPayload({groups:[{group_id:1,name:'A'},{group_id:2,name:'B'}]},[{group_id:1,status:'skipped'},{group_id:2,status:'failed'}]);assert.equal(add.groups.length,1);assert.equal(add.groups[0].group_id,2);
console.log('Batch retry preserves submitted dates and retries only failed groups');

const row={group_id:2,group_name:'学习群',status:'success',total:8,inserted:2,
  missing_days:[{date:'2026-10-01',count:2}]};
function render(result,submitted,retryAction){
  const host=new Node('div');
  c.renderGroupBatchFeedback(host,{results:[result]},submitted,retryAction);
  return host;
}
test('preview labels pre-ingest missing messages as pending gaps',()=>{
  const host=render({...row,inserted:0},{...payload,dry_run:true});
  assert.equal(host.children[1].children[1].textContent,'待补缺口：2026-10-01（2 条）');
  assert.equal(host.children[1].children[0].textContent,'学习群 · 完成 · 读取 8 条 · 新增 0 条');
});
test('successful actual update labels pre-ingest missing messages as filled this time',()=>{
  for(const submitted of [{...payload,dry_run:false},payload]){
    const host=render(row,submitted);
    assert.equal(host.children[1].children[1].textContent,'本次补齐：2026-10-01（2 条）');
    assert.equal(host.children[1].children[0].textContent,'学习群 · 完成 · 读取 8 条 · 新增 2 条');
  }
});
test('non-successful actual update never claims gaps were filled',()=>{
  for(const status of ['failed','skipped']){
    const host=render({...row,status,error:'采集失败',inserted:0},{...payload,dry_run:false});
    assert.equal(host.children[1].children[1].textContent,'更新前缺口：2026-10-01（2 条）');
    assert.ok(!host.textContent.includes('本次补齐'));
  }
});
test('repeated update with no missing days renders counts without a gap line',()=>{
  for(const missing_days of [[],undefined]){
    const host=render({...row,inserted:0,missing_days},{...payload,dry_run:false});
    assert.equal(host.children[1].children.length,1);
    assert.equal(host.children[1].textContent,'学习群 · 完成 · 读取 8 条 · 新增 0 条');
  }
});
test('rendered retry freezes submitted dates, group ids and preview mode',()=>{
  const submitted={...payload,group_ids:[1,2,3],dry_run:true};
  let received;
  const host=render({...row,status:'failed'},submitted,next=>{received=next;});
  submitted.start='2026-10-10';submitted.end='2026-10-11';submitted.group_ids.push(4);submitted.dry_run=false;
  const button=host.children[2];button.onclick();
  assert.equal(button.disabled,true);
  assert.equal(received.group_ids.join(','),'2');
  assert.equal(received.start,payload.start);assert.equal(received.end,payload.end);
  assert.equal(received.dry_run,true);
});
