const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('qq_digest/web/templates/first_use_script.html','utf8').replace(/{% if (not )?public_distribution\(\) %}([\s\S]*?)(?:{% else %}([\s\S]*?))?{% endif %}/g,(_m,not,yes,no)=>not?yes:(no||''));
function node(){return {value:'',hidden:false,disabled:false,children:[],dataset:{},attrs:{},_text:'',get textContent(){return this._text},set textContent(v){this._text=v;this.children=[]},appendChild(n){this.children.push(n)},setAttribute(k,v){this.attrs[k]=v},removeAttribute(k){delete this.attrs[k]},toggleAttribute(k,v){this.attrs[k]=v}}}
const nodes={},get=id=>nodes['setup-'+id]??=node(),requests=[];
const panels=[3,4,6].map(n=>Object.assign(node(),{dataset:{setupPanel:String(n)}}));
const data={available:true,active:true,completed:false,step:3,revision:'synthetic',selected_groups:[100],group_names:{100:'synthetic group'},imports:{100:{status:'success',total:1,inserted:1}},start_date:'2026-10-05',end_date:'2026-10-05',ai_ready:false,ai:{key_configured:false,bridge_available:false,provider:'compatible',model:'synthetic',base_url:'https://example.com'},ntqq:{db_dir:'',qq_number:0},schedule:{interval_minutes:20,hour:22,minute:0,auto_collection:false,auto_daily:false}};
const scope={console,URLSearchParams,setTimeout,clearTimeout,document:{getElementById:id=>nodes[id]??=node(),createElement:node,querySelectorAll:selector=>selector==='[data-setup-panel]'?panels:[]},api:async(method,url,body)=>{requests.push({method,url,body});return data}};
vm.createContext(scope);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],scope);
const tick=()=>new Promise(setImmediate);
(async()=>{
  await tick();requests.length=0;
  get('start').value='2026-10-04';
  if(get('start').oninput)get('start').oninput();
  get('finish').onclick();await tick();
  assert.equal(requests.filter(r=>r.url==='/api/setup/finish').length,0,'changed dates cannot finish using the previous range');
  get('import-next').onclick();await tick();
  assert.equal(panels.find(p=>p.dataset.setupPanel==='4').hidden,true,'changed dates must require another import before continuing');
  assert.match(get('status').textContent,/导入/);
  get('start').value=data.start_date;
  if(get('start').oninput)get('start').oninput();
  get('import-next').onclick();await tick();
  assert.equal(panels.find(p=>p.dataset.setupPanel==='4').hidden,false,'returning to the imported dates can continue');
  console.log('first use date-range regressions passed');
})().catch(error=>{console.error(error);process.exitCode=1});
