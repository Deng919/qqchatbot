const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const nodes={};function node(id){return nodes[id]||=( {disabled:false,textContent:'',value:'',hidden:true,children:[],appendChild(n){this.children.push(n);}} );}
const s={console,Set,Promise,configuredGroups:[{group_id:1}],discoveredBatchGroups:[],renderConfiguredGroups(){},escapeHtml:String,formatNumber:String,toast(){},document:{getElementById:node,createElement(){return node('new-'+Math.random());}},api(){return Promise.resolve({groups:[{group_id:1,name:'已有群'},{group_id:2,name:'新增甲'},{group_id:3,name:'新增乙'}]});}};
vm.createContext(s);const source=fs.readFileSync('qq_digest/web/templates/groups.html','utf8');vm.runInContext(source.slice(source.indexOf('  function discoverGroups()'),source.indexOf("  document.getElementById('discover-list').addEventListener")),s);
s.discoverGroups();setImmediate(()=>{assert.equal(s.discoveredBatchGroups.map(g=>g.group_id).join(','),'2,3');assert.equal(node('discover-batch-select').children.length,2);assert.match(node('discover-status').textContent,/扫描完成/);console.log('Successful discovery populates only new groups for batch addition');});

const batchSource=fs.readFileSync('qq_digest/web/templates/group_batch_controls.html','utf8');
const batch={GroupSelector:{value(){return [888,999];}},document:{getElementById(){return {}; }},discoveredBatchGroups:[{group_id:999,name:'仍在列表'}],toast(message){batch.warning=message;},executeGroupBatch(url,payload){batch.payload=payload;return Promise.resolve();}};
vm.createContext(batch);vm.runInContext(batchSource.match(/function addSelectedDiscoveredGroups\(\)[\s\S]*?(?=<\/script>)/)[0],batch);
batch.addSelectedDiscoveredGroups();
assert.equal(batch.payload,undefined,'Unavailable selected groups must not be silently omitted');
assert.match(batch.warning,/888/);
