const test=require('node:test');
const assert=require('node:assert/strict');
const {allowedExternal,NotificationTracker,closeToTray}=require('../desktop/policy.cjs');

test('solo abre enlaces HTTPS de Microsoft, sin esquemas ejecutables',()=>{
  assert.equal(allowedExternal('https://microsoft.com/devicelogin'),true);
  assert.equal(allowedExternal('https://outlook.office.com/mail/inbox'),true);
  for(const url of ['file:///C:/Windows/system32/cmd.exe','javascript:alert(1)','https://microsoft.com.ejemplo.com','https://microsoft.com@ejemplo.com','http://microsoft.com','https://microsoft.com:444/test'])assert.equal(allowedExternal(url),false);
});
test('cerrar oculta la ventana; Salir permite finalizar',()=>{
  let prevented=0,hidden=0;
  const event={preventDefault(){prevented++;}},window={hide(){hidden++;}};
  closeToTray(event,window,false);
  assert.equal(prevented,1);assert.equal(hidden,1);
  closeToTray(event,window,true);
  assert.equal(prevented,1);assert.equal(hidden,1);
});
test('un fallo al confirmar el aviso no duplica la notificación en el proceso',()=>{
  const tracker=new NotificationTracker(),events=[{key:'renovacion-1'}];
  assert.equal(tracker.fresh(events).length,1);
  tracker.mark(events);
  assert.equal(tracker.fresh(events).length,0);
  assert.equal(tracker.has('renovacion-1'),true);
  assert.equal(tracker.fresh([...events,{key:'renovacion-2'}]).length,1);
});
