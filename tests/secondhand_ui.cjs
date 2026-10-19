const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,checked:false,children:[],dataset:{},events:{},textContent:'',appendChild(n){this.children.push(n);return n;},setAttribute(){},focus(){},showModal(){this.open=true;},close(){this.open=false;},addEventListener(name,fn){this.events[name]=fn;}});return nodes.get(id);}
global.document={getElementById:node,createElement:()=>node('new'+nodes.size),addEventListener(){}};
global.window={addEventListener(){}};global.location={search:''};global.history={replaceState(){}};
global.destroyMessageContext=()=>{};global.mountMessageContext=()=>{};global.setTimeout=()=>1;global.clearTimeout=()=>{};
const requests=[];global.api=(method,url,body)=>new Promise((resolve,reject)=>requests.push({method,url,body,resolve,reject}));
vm.runInThisContext(fs.readFileSync('qq_digest/web/templates/secondhand_script.html','utf8').replace(/<\/?script>/g,'').replace('marketInit();',''));
const settle=()=>new Promise(setImmediate);const item=(key,title)=>({item_id:key,title,price_text:'800元',seller_qq:100,source_count:1,group_name:'群',direction:'sale',status:'unknown',published_at:'2026-10-19',description:'<img src=x>',favorite:false});
(async()=>{
 node('market-direction').value='all';node('market-status').value='all';
 marketLoad();const old=requests.at(-1);marketLoad();requests.at(-1).resolve({items:[item('b','新商品')],total:1,page:1,page_size:30,date_from:'2026-10-13',date_to:'2026-10-19',coverage:[]});await settle();
 const length=node('market-list').children.length;old.resolve({items:[item('a','旧商品')],total:1,page:1,page_size:30,coverage:[]});await settle();assert.equal(node('market-list').children.length,length,'old list cannot append stale results');
 marketOpen('a');const detail=requests.at(-1);marketClose();detail.resolve({item:item('a','旧商品'),sources:[]});await settle();assert.notEqual(node('market-title').textContent,'旧商品','closed detail ignores late response');
 marketOpen('b');requests.at(-1).resolve({item:item('b','<script>literal</script>'),sources:[],favorite:false});await settle();assert.equal(node('market-title').textContent,'<script>literal</script>');
 marketFavorite();const save=requests.at(-1);marketFavorite();assert.equal(requests.at(-1),save,'duplicate favorite blocked');save.reject(new Error('failed'));await settle();assert.equal(node('market-favorite').disabled,false,'failed save permits retry');
 node('market-block-qq').value='123';marketBlock();const block=requests.at(-1);marketBlock();assert.equal(requests.at(-1),block,'duplicate block blocked');block.reject(new Error('failed'));await settle();assert.equal(node('market-block-submit').disabled,false);
 marketSource({group_id:11,msg_id:'a'},node('market-context'));const attachment=requests.at(-1),sourceToken=node('market-context')._marketSourceToken;
 marketClose();marketOpen('c');attachment.resolve({items:[{name:'stale A',status:'success',text:'A attachments'}]});await settle();
 assert.ok(node('market-context')._marketSourceToken>sourceToken,'closing or opening detail invalidates source callbacks');
 console.log('Secondhand stale list/detail, literal text, duplicate saves and failed retry verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
