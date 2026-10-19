// Prueba las conexiones del proceso principal con Electron simulado. La prueba
// No inicia Electron ni modifica el inicio de Windows durante las pruebas.
const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const {EventEmitter}=require('node:events');
const source=fs.readFileSync(path.join(__dirname,'../desktop/main.cjs'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

async function harness(background=false, shutdownResponseLost=false){
  const intervals=[],calls=[],notices=[],ipc=new Map(),login=[],windows=[],menus=[],files=new Map();
  const app=new EventEmitter();
  Object.assign(app,{isPackaged:true,getVersion(){return '0.4.0';},setAppUserModelId(){},commandLine:{appendSwitch(){}},setPath(){},getPath(){return 'C:/prueba/Renovaciones';},requestSingleInstanceLock(){return true;},whenReady(){return Promise.resolve();},setLoginItemSettings(data){login.push(data);},quit(){app.emit('before-quit',{preventDefault(){}});},exit(code){app.exitCode=code;}});
  class Window extends EventEmitter {
    constructor(options){super();this.options=options;this.visible=false;this.webContents=new EventEmitter();this.webContents.id=1;this.webContents.mainFrame={url:'http://127.0.0.1:12345/'};this.webContents.setWindowOpenHandler=fn=>this.open=fn;this.webContents.session={setPermissionRequestHandler:fn=>this.permission=fn,setPermissionCheckHandler(){},webRequest:{onBeforeSendHeaders:fn=>this.headers=fn}};windows.push(this);}
    removeMenu(){}isDestroyed(){return false;}isMinimized(){return false;}show(){this.visible=true;}hide(){this.visible=false;}focus(){}async loadURL(url){this.url=url;}
  }
  class Tray extends EventEmitter {setToolTip(value){this.tooltip=value;}setContextMenu(menu){menus.push(menu);}destroy(){this.destroyed=true;}}
  class Notification extends EventEmitter {static isSupported(){return true;}constructor(data){super();this.data=data;}show(){notices.push(this.data);}}
  const powerMonitor=new EventEmitter();let connected=false;
  const electron={app,BrowserWindow:Window,Tray,Notification,powerMonitor,Menu:{buildFromTemplate:items=>items},nativeImage:{createFromPath:()=>({})},ipcMain:{handle:(key,fn)=>ipc.set(key,fn)},net:{isOnline:()=>connected},shell:{openExternal:url=>calls.push({external:url})},dialog:{showErrorBox:()=>{throw Error('Error de inicio');}}};
  const engine=new EventEmitter();engine.exitCode=null;engine.stdout=new EventEmitter();engine.stderr=new EventEmitter();engine.stdin=new EventEmitter();
  engine.stdin.write=text=>{engine.options=JSON.parse(text);setImmediate(()=>engine.stdout.emit('data',Buffer.from('{"ready":"http://127.0.0.1:12345"}\n')));};
  engine.stdin.end=()=>setImmediate(()=>{engine.exitCode=0;engine.emit('exit',0);});
  engine.kill=()=>{engine.exitCode=1;engine.emit('exit',1);};
  let state={items:[],auth:{status:'connected'},connected:true,can_send:false,last_error:''},events=[];
  const fetch=async(url,options)=>{
    const endpoint=new URL(url).pathname;calls.push({endpoint,options});
    if(endpoint==='/api/shutdown'&&shutdownResponseLost)throw Error('Respuesta perdida después de la barrera');
    const data=endpoint==='/api/prepare-shutdown'?{ready:true}:endpoint==='/api/session'?{csrf:'csrf-prueba'}:endpoint==='/api/state'?state:endpoint==='/api/notifications'?{items:events}:{};
    return {ok:true,json:async()=>data};
  };
  const updater=new EventEmitter();updater.checkForUpdates=async()=>null;
  class MockUpdates extends require('../desktop/updater.cjs').Updates {start(){void this.check();}}
  const context={require:name=>name==='electron'?electron:name==='electron-updater'?{autoUpdater:updater}:name==='./updater.cjs'?{Updates:MockUpdates}:name==='node:child_process'?{spawn:()=>engine}:name==='node:fs'?{existsSync:()=>true,mkdirSync(){},writeFileSync:(key,value)=>files.set(key,value),renameSync:(a,b)=>{files.set(b,files.get(a));},readFileSync:()=>{throw Error('Primera apertura');}}:name==='./policy.cjs'?require('../desktop/policy.cjs'):require(name),__dirname:path.join(__dirname,'../desktop'),process:{resourcesPath:'C:/app/resources',execPath:'C:/app/Renovaciones.exe',env:{},argv:background?['--background']:[]},fetch,URL,AbortSignal,Buffer,console,setTimeout:()=>1,clearTimeout(){},setInterval:(fn,ms)=>{intervals.push({fn,ms});return intervals.length;},clearInterval(){}};
  vm.runInNewContext(source,context);
  for(let i=0;i<5;i++)await settle();
  return {app,engine,windows,login,calls,notices,menus,ipc,files,powerMonitor,intervals,setState:value=>state=value,setEvents:value=>events=value,setOnline:value=>connected=value};
}

test('Electron registra inicio automático, oculta al cerrar y Salir detiene el motor',async()=>{
  const h=await harness();
  assert.equal(h.login[0].openAtLogin,true);
  assert.deepEqual(Array.from(h.login[0].args),['--background']);
  const window=h.windows[0];
  assert.equal(window.visible,true);
  assert.equal(window.options.webPreferences.nodeIntegration,false);
  assert.equal(window.options.webPreferences.sandbox,true);
  let prevented=false;window.emit('close',{preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(window.visible,false);assert.equal(h.engine.exitCode,null);
  h.menus.at(-1).find(item=>item.label==='Abrir Renovaciones').click();
  assert.equal(window.visible,true);
  h.menus.at(-1).find(item=>item.label==='Salir').click();
  for(let i=0;i<5;i++)await settle();
  assert.equal(h.engine.exitCode,0);assert.equal(h.app.exitCode,0);
});

test('el inicio de Windows arranca oculto y una segunda apertura muestra la ventana',async()=>{
  const h=await harness(true);
  assert.equal(h.windows[0].visible,false);
  assert.ok(h.calls.some(call=>call.endpoint==='/api/state'));
  h.app.emit('second-instance');
  assert.equal(h.windows[0].visible,true);
});

test('una respuesta de cierre perdida después de la barrera no deja el motor detenido y abierto',async()=>{
  const h=await harness(false,true);
  h.menus.at(-1).find(item=>item.label==='Salir').click();
  for(let i=0;i<5;i++)await settle();
  assert.ok(h.calls.some(call=>call.endpoint==='/api/prepare-shutdown'));
  assert.equal(h.engine.exitCode,0);assert.equal(h.app.exitCode,0);
});

test('reanudar y recuperar la red solicitan sincronización aunque la ventana esté oculta',async()=>{
  const h=await harness();h.windows[0].hide();
  const count=()=>h.calls.filter(call=>call.endpoint==='/api/sync').length;
  const before=count();h.powerMonitor.emit('resume');await settle();
  assert.equal(count(),before+1);
  h.setOnline(true);h.intervals.find(entry=>entry.ms===5000).fn();await settle();
  assert.equal(count(),before+2);
  h.intervals.find(entry=>entry.ms===5000).fn();await settle();
  assert.equal(count(),before+2);
});

test('avisos nativos sin ventana, sin repetir; sesión caducada genera aviso de login',async()=>{
  const h=await harness();h.windows[0].hide();
  h.setEvents([{key:'una',customer:'Cliente ficticio'}]);
  const poll=h.intervals.find(entry=>entry.ms===3000).fn;
  poll();await settle();poll();await settle();
  assert.equal(h.notices.filter(item=>item.title==='Nueva renovación de Proveedor de ejemplo').length,1);
  assert.ok(h.calls.some(call=>call.endpoint==='/api/notifications/ack'));
  h.setState({items:[],connected:false,auth:{status:'needs_login'},last_error:'Caducada'});
  poll();await settle();poll();await settle();
  assert.equal(h.notices.filter(item=>item.title==='Vuelve a conectar Outlook').length,1);
});

test('la clave privada no se envía fuera del motor ni se expone en IPC',async()=>{
  const h=await harness(),window=h.windows[0];
  let headers;
  window.headers({url:'https://example.com',webContentsId:1,requestHeaders:{}},result=>headers=result.requestHeaders);
  assert.equal(headers['X-Desktop-Key'],undefined);
  window.headers({url:'http://127.0.0.1:12345/api/state',webContentsId:1,requestHeaders:{}},result=>headers=result.requestHeaders);
  assert.equal(headers['X-Desktop-Key'],h.engine.options.key);
  const settings=h.ipc.get('desktop:settings');
  assert.throws(()=>settings({sender:{},senderFrame:{url:'https://example.com'}}));
  const result=settings({sender:window.webContents,senderFrame:window.webContents.mainFrame});
  assert.equal(result.key,undefined);assert.equal(result.refresh_token,undefined);
});

test('el aviso de recepción identifica la prueba y no se repite',async()=>{
  const h=await harness();h.windows[0].hide();
  h.setEvents([{key:'prueba-entrada',customer:'Cliente ficticio',reception_test:true}]);
  const poll=h.intervals.find(entry=>entry.ms===3000).fn;
  poll();await settle();poll();await settle();
  assert.equal(h.notices.filter(item=>item.title==='Prueba de recepción recibida').length,1);
  assert.ok(h.notices.at(-1).body.includes('envío está bloqueado'));
});
