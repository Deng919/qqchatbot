const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('qq_digest/web/templates/settings.html','utf8');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',checked:false,disabled:false,hidden:false,textContent:'',dataset:{},listeners:{},addEventListener(type,fn){this.listeners[type]=fn;}});return nodes.get(id);}
let pending=[],submitted=[],choices=[],errors=[];
const context={document:{getElementById:node},toast:message=>errors.push(message),api:(method,path,body)=>{if(path.endsWith('restore-preview'))return new Promise((resolve,reject)=>pending.push({resolve,reject,body}));if(path.endsWith('/restore'))return new Promise((resolve,reject)=>submitted.push({resolve,reject,body}));if(path.endsWith('choose-backup')||path.endsWith('choose-folder'))return new Promise(resolve=>choices.push({resolve,body}));return Promise.resolve({});},restorePreview:null};
vm.createContext(context);
const declarations=source.slice(source.indexOf('  var restorePreview ='),source.indexOf('  function initDesktopSettings()'));
vm.runInContext(declarations,context);
const listeners=source.slice(source.indexOf("  document.getElementById('recent-backups').addEventListener"),source.indexOf("  document.getElementById('export-reports').disabled = true;",source.indexOf("  document.getElementById('recent-backups').addEventListener")));
vm.runInContext(listeners,context);
const fire=(id,type='click')=>node(id).listeners[type].call(node(id));
const flush=()=>new Promise(setImmediate);
(async()=>{
  node('restore-file').value='A.zip';fire('preview-backup');
  node('restore-file').value='B.zip';fire('restore-file','input');
  pending[0].resolve({path:'C:/backups/A.zip',sha256:'a'});await flush();
  assert.equal(context.restorePreview,null,'late A preview must not approve B input');
  assert.equal(node('restore-data').disabled,true);
  fire('preview-backup');pending[1].resolve({path:'C:/backups/B.zip',sha256:'b'});await flush();
  assert.ok(node('restore-preview').textContent.includes('C:/backups/B.zip'),'verified source path must be visible');
  node('restore-destination').value='D:/Restored';node('confirm-restore').checked=true;context.updateRestoreReady();
  // Programmatic input changes without events must still be rejected at submit.
  node('restore-file').value='C.zip';fire('restore-data');
  assert.equal(submitted.length,0,'submit must recheck source snapshot');
  node('restore-file').value='B.zip';fire('preview-backup');pending[2].resolve({path:'C:/backups/B.zip',sha256:'b'});await flush();
  fire('choose-backup');fire('choose-restore-folder');
  node('confirm-restore').checked=true;context.updateRestoreReady();fire('restore-data');
  assert.equal(submitted.length,1);assert.equal(submitted[0].body.path,'C:/backups/B.zip');
  for(const id of ['restore-file','recent-backups','choose-backup','preview-backup','restore-destination','choose-restore-folder','confirm-restore'])assert.equal(node(id).disabled,true,`${id} frozen during restore`);
  choices[0].resolve({path:'C.zip'});choices[1].resolve({path:'D:/Other'});await flush();
  assert.equal(node('restore-file').value,'B.zip','late file chooser must not change an active restore');
  assert.equal(node('restore-destination').value,'D:/Restored');
  assert.equal(node('choose-backup').disabled,true);
  submitted[0].reject(new Error('synthetic failure'));await flush();
  assert.equal(node('restore-file').disabled,false);assert.equal(node('restore-data').disabled,false);
  // Changing and changing back still invalidates in-flight validation.
  fire('preview-backup');node('restore-file').value='C.zip';fire('restore-file','input');node('restore-file').value='B.zip';fire('restore-file','input');
  pending[3].resolve({path:'C:/backups/B.zip',sha256:'b'});await flush();assert.equal(context.restorePreview,null);
  console.log('Restore preview rejects late results and freezes verified selection during submit');
})().catch(e=>{console.error(e);process.exitCode=1;});
