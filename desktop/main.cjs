'use strict';
const {app, BrowserWindow, Tray, Menu, Notification, nativeImage, ipcMain, powerMonitor, net, shell, dialog} = require('electron');
const {spawn} = require('node:child_process');
const {randomBytes} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL}=require('node:url');
const {allowedExternal, NotificationTracker, closeToTray} = require('./policy.cjs');

const APP_ID = 'dev.portfolio.renewalmanager';
const ROOT = app.isPackaged ? path.join(process.resourcesPath, 'app-data') : path.resolve(__dirname, '../.build/app-data');
const testing=!!process.env.RENOVACIONES_TEST_DATA;
if (testing || !app.isPackaged) app.setPath('userData', path.resolve(process.env.RENOVACIONES_TEST_DATA || path.join(__dirname, '../.local/electron-desarrollo')));
app.setAppUserModelId(APP_ID);
app.commandLine.appendSwitch('lang', 'es-ES');
const tracker = new NotificationTracker();
let window, tray, child, origin, key, csrf, quitting=false, stopping=false, pollTimer, networkTimer, restartTimer;
let polling=false, retries=0, authStatus='', backendStatus='Iniciando…', settings, settingsPath, online, currentState, presented=false;
let updates, installQuitting=false, pauseRestart=false;

function writeSettings() {
  fs.mkdirSync(path.dirname(settingsPath), {recursive:true});
  fs.writeFileSync(settingsPath+'.tmp', JSON.stringify(settings));
  fs.renameSync(settingsPath+'.tmp', settingsPath);
}
function startup(enabled) {
  if(app.isPackaged && !testing) app.setLoginItemSettings({name:'Renewal Manager',openAtLogin:enabled, path:process.execPath, args:['--background']});
  settings.startAtLogin=enabled; writeSettings();
}
function notify(title, body, onClick=showWindow) {
  if(!settings.notifications || !Notification.isSupported()) return false;
  const notice=new Notification({title, body, icon:path.join(__dirname,'assets/icon.png')});
  notice.on('click',onClick); notice.show();
  return true;
}
function showWindow() {
  if(!window || window.isDestroyed()) return;
  if(window.isMinimized()) window.restore();
  window.show(); window.focus();
}
function updateTray() {
  if(!tray) return;
  const pending=currentState?.items.filter(item=>item.status==='pending').length || 0;
  tray.setToolTip('Renovaciones · '+backendStatus+(pending?' · '+pending+' pendientes':''));
  tray.setContextMenu(Menu.buildFromTemplate([
    {label:'Abrir Renovaciones',click:showWindow},
    {label:backendStatus,enabled:false},
    {label:'Comprobar ahora',click:()=>void wake(),enabled:!!origin},
    {label:'Buscar actualizaciones',click:()=>void updates?.check(true),enabled:!!updates},
    ...(updates?[{label:updates.status,enabled:updates.ready,click:()=>void updates.offer()}]:[]),
    {type:'separator'},
    {label:'Iniciar al entrar en Windows',type:'checkbox',checked:settings.startAtLogin,enabled:app.isPackaged,click:item=>{startup(item.checked);updateTray();}},
    {label:'Mostrar notificaciones',type:'checkbox',checked:settings.notifications,click:item=>{settings.notifications=item.checked;writeSettings();}},
    {type:'separator'},
    {label:'Salir',click:()=>app.quit()}
  ]));
}
async function api(endpoint, fields) {
  if(!origin) throw Error('El motor todavía no está disponible.');
  const response=await fetch(origin+endpoint,{method:fields===undefined?'GET':'POST',headers:{'X-Desktop-Key':key,...(fields===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf})},body:fields===undefined?undefined:JSON.stringify(fields),signal:AbortSignal.timeout(8000)});
  const result=await response.json();
  if(!response.ok) throw Error(result.error || 'No se ha podido completar la operación.');
  return result;
}
async function wake() {
  try {await api('/api/sync',{}); await poll();} catch { /* El temporizador vuelve a intentarlo. */ }
}
async function poll() {
  if(polling || !origin || quitting) return;
  polling=true;
  try {
    currentState=await api('/api/state');
    const state=currentState;
    backendStatus=state.auth.status==='needs_login'?'Inicia sesión de nuevo':state.last_error?'Conexión pendiente · reintentando':state.connected?'Supervisión activa':state.auth.status==='restoring'?'Restaurando sesión…':'Conecta Outlook';
    if(state.auth.status==='needs_login' && authStatus!=='needs_login') notify('Vuelve a conectar Outlook','Microsoft necesita un nuevo inicio de sesión. Tus renovaciones pendientes se conservan.');
    authStatus=state.auth.status;
    const events=await api('/api/notifications');
    const fresh=tracker.fresh(events.items);
    if(fresh.length && settings.notifications && Notification.isSupported()) {
      const onlyTests=fresh.every(item=>item.reception_test);
      notify(onlyTests?'Prueba de recepción recibida':fresh.length===1?'Nueva renovación de Proveedor de ejemplo':fresh.length+' renovaciones nuevas',onlyTests?'El correo de prueba ha llegado por Outlook. El envío está bloqueado.':fresh.length===1?fresh[0].customer:'Tienes nuevas propuestas pendientes de revisar.');
      tracker.mark(fresh);
    }
    // La confirmación de avisos es independiente del envío de correo.
    const shown=events.items.filter(item=>tracker.has(item.key));
    if(shown.length) await api('/api/notifications/ack',{keys:shown.map(item=>item.key)});
    updateTray();
  } catch { backendStatus='Recuperando conexión…';updateTray(); }
  finally {polling=false;}
}
function trusted(event) {
  if(!window || event.sender!==window.webContents || event.senderFrame!==window.webContents.mainFrame || !origin || new URL(event.senderFrame.url).origin!==origin) throw Error('Solicitud de escritorio no válida.');
}
function registerIPC() {
  ipcMain.handle('desktop:configure',async(event,mailbox)=>{
    const expected=pathToFileURL(path.join(__dirname,'setup.html')).href;
    if(event.sender!==window.webContents || event.senderFrame!==window.webContents.mainFrame || event.senderFrame.url!==expected)throw Error('Configuración no permitida.');
    if(typeof mailbox!=='string'||mailbox.length>200||!/^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$/.test(mailbox))throw Error('Correo no válido.');
    const configPath=path.join(app.getPath('userData'),'config.json');
    if(fs.existsSync(configPath))throw Error('La cuenta ya está configurada.');
    const config=JSON.parse(fs.readFileSync(path.join(ROOT,'desktop-config.json'),'utf8'));
    config.expected_mailbox=mailbox;config.company.email=mailbox;config.company.logo_path=null;
    fs.writeFileSync(configPath+'.tmp',JSON.stringify(config));fs.renameSync(configPath+'.tmp',configPath);
    await startBackend();
  });
  ipcMain.handle('desktop:settings',(event)=>{trusted(event);return {...settings,version:app.getVersion(),packaged:app.isPackaged,notificationsAvailable:Notification.isSupported()};});
  ipcMain.handle('desktop:notifications',(event,enabled)=>{trusted(event);settings.notifications=enabled===true;writeSettings();updateTray();if(settings.notifications)notify('Avisos activados','Renovaciones te avisará aunque cierres la ventana.');return settings.notifications;});
  ipcMain.handle('desktop:wake',event=>{trusted(event);void wake();});
}
function createWindow() {
  window=new BrowserWindow({width:1440,height:960,minWidth:900,minHeight:650,show:false,title:'Renovaciones · Renewal Manager',icon:path.join(__dirname,'assets/icon.png'),webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,backgroundThrottling:false,spellcheck:false}});
  window.removeMenu();
  window.on('close',event=>closeToTray(event,window,quitting));
  window.webContents.setWindowOpenHandler(({url})=>{if(allowedExternal(url))void shell.openExternal(url);return {action:'deny'};});
  window.webContents.on('will-navigate',(event,url)=>{if(!origin || new URL(url).origin!==origin){event.preventDefault();if(allowedExternal(url))void shell.openExternal(url);}});
  const session=window.webContents.session;
  session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  session.setPermissionCheckHandler(()=>false);
  session.webRequest.onBeforeSendHeaders((details,callback)=>{
    const headers={...details.requestHeaders};
    delete headers['X-Desktop-Key'];
    if(origin && new URL(details.url).origin===origin && details.webContentsId===window.webContents.id) headers['X-Desktop-Key']=key;
    callback({requestHeaders:headers});
  });
  window.webContents.on('render-process-gone',()=>{if(!quitting && origin)void window.loadURL(origin);});
}
async function startBackend() {
  if(quitting) return;
  key=randomBytes(32).toString('hex');origin=null;csrf=null;
  const executable=app.isPackaged?path.join(process.resourcesPath,'backend','renovaciones-engine.exe'):(process.env.RENOVACIONES_PYTHON || 'python');
  const args=app.isPackaged?[]:['-u','-m','automation.desktop_entry'];
  const engine=spawn(executable,args,{cwd:app.isPackaged?process.resourcesPath:path.resolve(__dirname,'..'),windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
  child=engine;
  // No se registran stdout/stderr del motor: podrían contener información privada.
  engine.stderr.on('data',()=>{});
  engine.stdin.on('error',()=>{});
  engine.stdin.write(JSON.stringify({key,root:ROOT,data:app.getPath('userData')})+'\n');
  let buffer='';
  const startupTimeout=setTimeout(()=>{if(!origin)engine.kill();},30000);
  engine.stdout.on('data',data=>{
    buffer+=data.toString('utf8');
    if(buffer.length>65536){engine.kill();return;}
    const lines=buffer.split('\n');buffer=lines.pop();
    for(const line of lines){
      let message;try{message=JSON.parse(line);}catch{continue;}
      if(message.ready && /^http:\/\/127\.0\.0\.1:\d+$/.test(message.ready)){
        clearTimeout(startupTimeout);origin=message.ready;
        void (async()=>{
          csrf=(await api('/api/session')).csrf;
          retries=0;
          await window.loadURL(origin);
          if(!presented && !process.argv.includes('--background'))showWindow();
          presented=true;
          await poll();
        })().catch(()=>{engine.kill();});
      }
    }
  });
  let handled=false;
  const stopped=()=>{
    if(handled)return;handled=true;clearTimeout(startupTimeout);
    if(child===engine){origin=null;csrf=null;child=null;}
    if(quitting||pauseRestart)return;
    backendStatus='Reiniciando el servicio…';updateTray();
    if(retries++===0)notify('Supervisión interrumpida','Se está recuperando el servicio. Se comprobarán los correos pendientes al reconectar.');
    restartTimer=setTimeout(()=>void startBackend(),Math.min(30000,1000*2**Math.min(retries,5)));
  };
  engine.on('exit',stopped);engine.on('error',stopped);
}
async function stopEngine() {
  pauseRestart=true;clearTimeout(restartTimer);
  const engine=child;
  if(!engine||engine.exitCode!==null)return;
  const deadline=Date.now()+180000;
  while(engine.exitCode===null){
    const result=await api('/api/prepare-shutdown',{});
    if(result.ready)break;
    if(Date.now()>deadline)throw Error('La operación sigue en curso.');
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  if(engine.exitCode!==null)return;
  const exited=new Promise(resolve=>engine.once('exit',resolve));
  // La barrera ya ha guardado el estado y bloqueado nuevos envíos. Si se
  // pierde la respuesta HTTP, el cierre de stdin sigue cerrando ese motor.
  try{await api('/api/shutdown',{});}catch{ /* EOF completa el cierre seguro. */ }
  engine.stdin.end();
  let exitTimeout;
  try{await Promise.race([exited,new Promise((_,reject)=>{exitTimeout=setTimeout(()=>reject(Error('El servicio no se ha cerrado.')),10000);})]);}
  finally{clearTimeout(exitTimeout);}
  if(engine.exitCode===null)throw Error('El servicio sigue activo.');
}
async function resumeEngine(){pauseRestart=false;if(!child&&!quitting)await startBackend();}
async function shutdown() {
  if(stopping)return;stopping=true;
  try{await stopEngine();}catch{stopping=false;await resumeEngine();await dialog.showMessageBox({type:'info',title:'Operación en curso',message:'No se ha cerrado el servicio para proteger la operación en curso. Vuelve a elegir «Salir» cuando termine.',buttons:['Aceptar']});return;}
  quitting=true;updates?.dispose();clearInterval(pollTimer);clearInterval(networkTimer);clearTimeout(restartTimer);
  tray?.destroy();app.exit(0);
}

if(!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance',showWindow);
  app.on('activate',showWindow);
  app.on('window-all-closed',()=>{});
  app.on('before-quit',event=>{if(installQuitting){quitting=true;return;}event.preventDefault();void shutdown();});
  app.whenReady().then(async()=>{
    settingsPath=path.join(app.getPath('userData'),'preferencias.json');
    try{settings=JSON.parse(fs.readFileSync(settingsPath,'utf8'));}catch{settings={startAtLogin:true,notifications:true};}
    startup(settings.startAtLogin!==false);
    registerIPC();createWindow();
    tray=new Tray(nativeImage.createFromPath(path.join(__dirname,'assets/icon.png')));
    tray.on('double-click',showWindow);tray.on('click',showWindow);updateTray();
    powerMonitor.on('resume',()=>void wake());
    powerMonitor.on('unlock-screen',()=>void wake());
    online=net.isOnline();
    networkTimer=setInterval(()=>{const next=net.isOnline();if(next && !online)void wake();online=next;},5000);
    pollTimer=setInterval(()=>void poll(),3000);
    // No update distribution channel is configured for the portfolio edition.
    if(fs.existsSync(path.join(app.getPath('userData'),'config.json')))await startBackend();
    else{await window.loadFile(path.join(__dirname,'setup.html'));showWindow();backendStatus='Configura tu cuenta';updateTray();}
  }).catch(()=>{dialog.showErrorBox('No se puede abrir Renovaciones','No se ha podido iniciar la aplicación. Revisa la instalación.');app.quit();});
}
