const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const {Updates}=require('../desktop/updater.cjs');
function fixture(version='0.4.0'){
  const updater=new EventEmitter(),calls=[];
  updater.checkForUpdates=async()=>{calls.push('check');return null;};updater.quitAndInstall=()=>calls.push('install');
  const options={updater,version,dialog:{showMessageBox:async()=>({response:1})},notify:()=>calls.push('notify'),stopEngine:async()=>calls.push('stop'),resumeEngine:async()=>calls.push('resume'),changed(){},setInstalling:value=>calls.push('installing:'+value)};
  return {updates:new Updates(options),updater,calls,options};
}
test('estable no recibe beta, no admite downgrade y Salir no instala',()=>{
  const {updater}=fixture();assert.equal(updater.allowPrerelease,false);assert.equal(updater.channel,'latest');assert.equal(updater.allowDowngrade,false);assert.equal(updater.autoInstallOnAppQuit,false);assert.equal(updater.disableWebInstaller,true);
  assert.equal(fixture('0.4.0-beta.1').updater.channel,'beta');
});
test('Más tarde mantiene el motor activo',async()=>{
  const {updates,updater,calls}=fixture();updater.emit('update-downloaded',{version:'0.4.1'});await new Promise(r=>setImmediate(r));
  assert.equal(updates.ready,true);assert.ok(!calls.includes('stop'));assert.ok(!calls.includes('install'));
});
test('instala solo después de terminar el motor y evita doble confirmación',async()=>{
  const {updates,calls}=fixture();updates.ready=true;let finish;
  updates.stopEngine=()=>new Promise(resolve=>{finish=()=>{calls.push('stop');resolve();};});
  const pending=updates.install();await updates.install();assert.deepEqual(calls,[]);
  finish();await pending;assert.deepEqual(calls,['stop','installing:true','install']);
});
test('si el motor no se puede cerrar no inicia el instalador',async()=>{
  const {updates,calls}=fixture();updates.ready=true;updates.stopEngine=async()=>{throw Error('Envío en curso');};
  await updates.install();assert.ok(!calls.includes('install'));assert.ok(calls.includes('resume'));
});
test('fallo de red o hash mantiene la versión actual operativa',async()=>{
  const {updates,updater,calls}=fixture();updater.checkForUpdates=async()=>{throw Error('SHA512 no coincide');};
  await updates.check();assert.equal(updates.busy,false);assert.ok(!calls.includes('stop'));assert.equal(updates.ready,false);
});
test('fallo del instalador restaura el servicio',async()=>{
  const {updates,updater,calls}=fixture();updates.ready=true;updater.quitAndInstall=()=>updater.emit('error',Error('No se puede ejecutar'));
  await updates.install();assert.ok(calls.includes('resume'));assert.equal(updates.installing,false);
});
