const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('qq_digest/web/templates/base.html','utf8');
const code=html.slice(html.indexOf('function api('),html.indexOf('function showStorageHandoff('));
let opened=0;const scope={window:{location:{href:'/candidates?status=later'}},SessionLogin:{open(){opened++}},toast(){},fetch:()=>Promise.resolve({status:401}),Error};
vm.createContext(scope);vm.runInContext(code,scope);
(async()=>{await assert.rejects(scope.api('PATCH','/api/candidates/1',{title:'draft'}),error=>error.status===401,'expiry must remain an HTTP error so mutations do not enter network retry');assert.equal(scope.window.location.href,'/candidates?status=later','expiry must preserve current DOM and original address');assert.equal(opened,1,'expiry offers inline reauthentication');console.log('Expired login keeps drafts/URL and does not replay mutation');})().catch(e=>{console.error(e);process.exitCode=1});
