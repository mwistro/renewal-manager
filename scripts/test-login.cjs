// Navegador real; Microsoft y el transporte del panel están simulados.
// No se leen credenciales, no se consulta Microsoft y no se envían correos.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:850}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    let state={account:'prueba@example.com',company:'Empresa ficticia',price_basis:'total',connected:false,can_send:false,auth:{status:'disconnected'},items:[],last_scan:null,last_error:'',poll_seconds:30};
    let connects=0;
    await page.route('http://renovaciones.test/**',async route=>{
      const url=new URL(route.request().url());
      if(url.pathname==='/api/state')return route.fulfill({json:state});
      if(url.pathname==='/api/session')return route.fulfill({json:{csrf:'csrf-ficticio'}});
      if(url.pathname.endsWith('/preview'))return route.fulfill({json:{approval_id:'ficticio',html:'<p>197,64 €</p>',to:'prueba@example.com',subject:'[PRUEBA]',test:true,amounts:{total:19764}}});
      if(url.pathname==='/api/connect'){
        connects++;assert.deepEqual(route.request().postDataJSON(),{});
        state={...state,auth:{status:'waiting',verification_uri:'https://login.microsoft.com/device',user_code:'FICTICIO'}};
        return route.fulfill({json:{started:true}});
      }
      const assets={'/':['index.html','text/html'],'/app.js':['app.js','text/javascript'],'/styles.css':['styles.css','text/css']};
      const file=assets[url.pathname];
      if(!file)return route.fulfill({status:404,body:''});
      return route.fulfill({contentType:file[1],body:fs.readFileSync(path.join(root,'panel',file[0]))});
    });
    await page.goto('http://renovaciones.test/');
    await page.waitForFunction(()=>!document.getElementById('login-connect').disabled);
    assert.equal(await page.locator('#application').isHidden(),true);
    assert.equal(connects,0);
    await page.getByRole('button',{name:'Iniciar sesión con Microsoft'}).click();
    await page.locator('#auth-code').waitFor({state:'visible'});
    assert.equal(await page.locator('#auth-code').innerText(),'FICTICIO');
    assert.equal(await page.locator('#application').isHidden(),true);
    state={...state,connected:true,can_send:true,auth:{status:'connected'}};
    await page.evaluate(()=>refresh());
    await page.locator('#application').waitFor({state:'visible'});
    assert.equal(await page.locator('#login-screen').isHidden(),true);
    assert.equal(connects,1);
    assert.equal(await page.locator('.reception-test, #reception-start, #reception-stop').count(),0);
    // Los elementos de pruebas anteriores conservan el bloqueo de envio.
    state.items=[{key:'ficticio',status:'pending',test:true,reception_test:true,send_blocked:true,discount:'0',discount_kind:'percent',recipient:'prueba@example.com',proposal:{customer:'Empresa de ejemplo',product:'Office 365',quantity:3,pvp_cents:21960,renewal:'2036-11-09'},amounts:{total:21960}}];
    await page.evaluate(()=>refresh());
    await page.locator('[data-key="ficticio"]').click();
    await page.locator('#discount-kind').selectOption('percent');
    await page.locator('#discount-value').fill('10');
    await page.locator('#prepare').click();
    await page.frameLocator('#email-preview').getByText('197,64 €').waitFor();
    assert.equal(await page.locator('#review-send').isDisabled(),true);
    assert.match(await page.locator('#send-help').innerText(),/bloqueado/);
    assert.equal(await page.locator('#review-send').isDisabled(),true);
    // Una sesión recuperada entra sin iniciar otro flujo de Microsoft.
    await page.reload();
    await page.locator('#application').waitFor({state:'visible'});
    assert.equal(connects,1);
    // Si Microsoft exige nuevo acceso, se vuelve a la pantalla de entrada.
    state={...state,connected:false,can_send:false,auth:{status:'needs_login',message:'Vuelve a iniciar sesión.'}};
    await page.evaluate(()=>refresh());
    assert.equal(await page.locator('#application').isHidden(),true);
    await page.locator('#login-screen').waitFor({state:'visible'});
    await page.setViewportSize({width:375,height:850});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    state={...state,auth:{status:'disconnected'}};
    await page.evaluate(()=>refresh());
    await page.setViewportSize({width:1280,height:850});
    fs.mkdirSync(path.join(root,'.local/pruebas'),{recursive:true});
    await page.screenshot({path:path.join(root,'.local/pruebas/acceso-outlook.png')});
    assert.deepEqual(errors,[]);
    console.log('Pantalla de acceso verificada en Edge: entrada única, panel oculto, recuperación sin otro login y sesión caducada. Transporte Microsoft simulado.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
