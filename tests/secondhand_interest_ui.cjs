const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),nodes=new Map();
const n=id=>{if(!nodes.has(id))nodes.set(id,{children:[],dataset:{},value:'',appendChild(x){this.children.push(x);return x;},setAttribute(){},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;},set textContent(v){this._text=v;this.children=[];},get textContent(){return this._text||'';}});return nodes.get(id);};
const requests=[];Object.assign(global,{document:{getElementById:n,createElement:()=>n('node'+nodes.size)},window:{addEventListener(){}},location:{search:''},history:{replaceState(){}},setTimeout:()=>1,clearTimeout(){},destroyMessageContext(){},api:(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}))});
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/secondhand_script.html','utf8').replace(/<\/?script>/g,'').replace('marketInit();',''));
(async()=>{const button=n('dismiss'),item={item_id:'chosen',lane:'promotion',publications:[{item_id:'chosen'},{item_id:'other-product'}]};
 const pending=marketDismiss(item,button);assert.deepEqual(requests[0].body,{item_ids:['chosen']},'a single product preference cannot hide the other products in a promotion deck');
 marketDismiss(item,button);assert.equal(requests.length,1,'duplicate taps share one mutation');requests.shift().reject(Error('offline'));await pending;assert.equal(button.disabled,false,'failure preserves retry');
 console.log('Single-product dismissal and retry verified');
})().catch(e=>{console.error(e);process.exitCode=1;});
