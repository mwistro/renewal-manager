// Interfaz real en Edge; datos y transporte ficticios. Nunca contacta Microsoft.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const entry={key:'a'.repeat(64),status:'pending',test:false,discount:'0',discount_kind:'percent',recipient:'original@example.com',proposal:{customer:'Empresa ficticia',product:'Office 365',quantity:3,pvp_cents:21960,renewal:'2036-11-09'},amounts:{total:21960}};
    const state={account:'comercial@example.com',company:'Empresa ficticia',price_basis:'total',connected:true,can_send:true,auth:{status:'connected'},items:[entry],last_scan:null,last_error:'',poll_seconds:30};
    let previews=0,recipients=0,sends=0,approval='',holdRecipient=null;
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!=='http://renovaciones.test')return route.abort();
      if(url.pathname==='/api/state')return route.fulfill({json:state});
      if(url.pathname==='/api/session')return route.fulfill({json:{csrf:'ficticio'}});
      if(url.pathname.endsWith('/preview')){
        previews++;approval='preview-'+previews;
        entry.recipient=route.request().postDataJSON().recipient;
        return route.fulfill({json:{approval_id:approval,html:'<p>Contenido revisado: 219,60 EUR</p>',to:entry.recipient,subject:'Renovacion ficticia',test:false,amounts:{total:21960}}});
      }
      if(url.pathname.endsWith('/recipient')){
        const fields=route.request().postDataJSON();assert.equal(fields.approval_id,approval);
        recipients++;approval='recipient-'+recipients;entry.recipient=fields.recipient;
        if(holdRecipient)await holdRecipient;
        return route.fulfill({json:{approval_id:approval,to:fields.recipient}});
      }
      if(url.pathname.endsWith('/send')){
        const fields=route.request().postDataJSON();assert.equal(fields.approval_id,approval);assert.equal(fields.confirmed,true);
        sends++;entry.status='submitted';return route.fulfill({json:{status:'submitted'}});
      }
      const assets={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css'};
      if(assets[url.pathname])return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':url.pathname.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,'panel',assets[url.pathname]))});
      return route.fulfill({status:404,body:''});
    });
    await page.goto('http://renovaciones.test/');
    await page.locator('[data-key]').click();
    assert.equal(await page.locator('#preview-refresh-hint').isHidden(),true);
    assert.equal(await page.locator('#recipient').isEditable(),true);
    await page.locator('#prepare').click();
    await page.locator('#email-preview').waitFor({state:'visible'});
    assert.equal(await page.locator('#recipient').isEditable(),true);
    await page.locator('#recipient').fill('nuevo@example.com');
    assert.equal(await page.locator('#email-preview').isVisible(),true);
    assert.equal(await page.locator('#preview-refresh-hint').isHidden(),true);
    await page.locator('#review-send').click();
    await page.locator('#confirm-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#confirm-to').innerText(),'nuevo@example.com');
    assert.equal(previews,1);assert.equal(recipients,1);assert.equal(sends,0);
    await page.locator('#cancel-send').click();
    await page.locator('#recipient').fill('invalido');
    assert.equal(await page.locator('#review-send').isDisabled(),true);
    assert.equal(await page.locator('#email-preview').isVisible(),true);
    let releaseRecipient;holdRecipient=new Promise(resolve=>{releaseRecipient=resolve;});
    await page.locator('#recipient').fill('segundo@example.com');
    await page.locator('#review-send').click();
    await page.waitForFunction(()=>document.querySelector('#review-send').disabled);
    await page.locator('#recipient').fill('tercero@example.com');
    holdRecipient=null;releaseRecipient();
    await page.waitForFunction(()=>!document.querySelector('#review-send').disabled);
    assert.equal(await page.locator('#confirm-dialog').isVisible(),false);
    await page.locator('#review-send').click();
    await page.locator('#confirm-dialog').waitFor({state:'visible'});
    assert.equal(await page.locator('#confirm-to').innerText(),'tercero@example.com');
    assert.equal(previews,1);assert.equal(sends,0);
    await page.locator('#cancel-send').click();
    await page.locator('#discount-kind').selectOption('percent');
    await page.locator('#discount-value').fill('10');
    assert.equal(await page.locator('#preview-refresh-hint').isVisible(),true);
    assert.match(await page.locator('#preview-refresh-hint').innerText(),/Has cambiado el descuento/);
    assert.ok(await page.evaluate(()=>document.querySelector('#preview-refresh-hint').getBoundingClientRect().top>=document.querySelector('#prepare').getBoundingClientRect().bottom));
    assert.equal(await page.locator('#email-preview').isHidden(),true);
    assert.equal(await page.locator('#review-send').isDisabled(),true);
    await page.locator('#prepare').click();
    await page.locator('#email-preview').waitFor({state:'visible'});
    assert.equal(await page.locator('#preview-refresh-hint').isHidden(),true);
    await page.locator('#review-send').click();
    await page.locator('#confirm-dialog').waitFor({state:'visible'});
    await page.locator('#confirm-send').click();
    await page.waitForFunction(()=>document.querySelector('#prepare')===null);
    assert.equal(sends,1);assert.deepEqual(errors,[]);
    console.log('Destinatario editable sin regenerar HTML, confirmacion actualizada, direcciones invalidas y carrera verificados. Envio simulado.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
