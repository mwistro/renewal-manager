'use strict';
const $ = id => document.getElementById(id);
const desktop=window.renovacionesDesktop;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = cents => new Intl.NumberFormat('es-ES', {style:'currency',currency:'EUR'}).format(cents / 100);
const labels = {pending:'Pendiente',review:'Revisar datos',creating:'Preparando envío',sending:'Enviando',submitted:'Envío aceptado',sent:'Enviado',uncertain:'Comprobar envío',send_failed:'Error de envío'};
let csrf='', state=null, selected=null, filter='pending', preview=null, rendered='', sending=false, polling=false, seen=null, toastTimer;

async function api(path, data){
  const response = await fetch(path, {method:data===undefined?'GET':'POST', headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:data===undefined?undefined:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw Error(result.error||'No se ha podido completar la operación.');
  return result;
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500);}
function item(){return state?.items.find(entry=>entry.key===selected);}
function totalFor(entry, value='0', kind='percent'){
  const subtotal=entry.proposal.pvp_cents*(state.price_basis==='unit'?entry.proposal.quantity:1);
  const n=Number(value), reduction=kind==='none'?0:kind==='amount'?Math.round(n*100):Math.round(subtotal*n/100);
  if(!Number.isFinite(n)||n<0||reduction>subtotal)throw Error('Revisa el descuento.');
  return {subtotal,discount:reduction,total:subtotal-reduction};
}
function safeLink(value){try{const url=new URL(value);return url.protocol==='https:'?url.href:'';}catch{return '';}}
function fmtDate(value){return value?value.split('-').reverse().join('/'):'—';}
function renderQueue(){
  if(!state)return;
  const pending=state.items.filter(entry=>entry.status==='pending').length;
  const review=state.items.filter(entry=>entry.status==='review'||entry.status==='uncertain'||entry.status==='send_failed').length;
  const sent=state.items.filter(entry=>['sent','submitted','sending','creating'].includes(entry.status)).length;
  $('pending-count').textContent=pending;$('review-count').textContent=review;$('sent-count').textContent=sent;
  $('summary-pending').textContent=pending;$('summary-sent').textContent=sent;
  document.title=(pending?'('+pending+') ':'')+'Renovaciones · Renewal Manager';
  const search=$('search').value.toLocaleLowerCase('es-ES');
  const entries=state.items.filter(entry=>{
    const inFilter=filter==='all'||(filter==='pending'&&entry.status==='pending')||(filter==='review'&&['review','uncertain','send_failed'].includes(entry.status))||(filter==='sent'&&['sent','submitted','creating','sending'].includes(entry.status));
    return inFilter&&[entry.proposal?.customer,entry.proposal?.product,entry.subject].join(' ').toLocaleLowerCase('es-ES').includes(search);
  });
  $('list-title').textContent=({pending:'Pendientes',review:'Revisar datos',sent:'Envíos',all:'Todas las renovaciones'})[filter];
  $('list-count').textContent=entries.length;
  $('queue').innerHTML=entries.length?entries.map(entry=>`<button class="queue-item ${entry.key===selected?'active':''}" data-key="${entry.key}" aria-pressed="${entry.key===selected}"><div class="item-top"><span class="item-company">${escapeHtml(entry.proposal?.customer||'Revisión necesaria')}</span>${entry.test?'<span class="badge test">PRUEBA</span>':''}</div><h3>${escapeHtml(entry.proposal?.product||entry.subject)}</h3><div class="item-bottom"><span class="badge ${escapeHtml(entry.status)}">${escapeHtml(labels[entry.status]||entry.status)}</span><span class="item-amount">${entry.amounts?money(entry.amounts.total):'—'}</span></div></button>`).join(''):'<div class="queue-empty">No hay renovaciones en esta vista.<br>Las nuevas aparecerán aquí cuando conectes Outlook. También puedes cargar el correo de prueba.</div>';
  $('queue').querySelectorAll('[data-key]').forEach(button=>button.addEventListener('click',()=>{selected=button.dataset.key;rendered='';renderQueue();renderDetail();}));
}
function renderConnection(){
  $('account').textContent=state.account;
  $('connection-dot').classList.toggle('connected',state.connected);
  $('connection-label').textContent=state.last_error?'Conexión pendiente':state.connected?'Supervisión activa':state.auth.status==='restoring'?'Restaurando sesión…':state.auth.status==='needs_login'?'Inicia sesión de nuevo':'Sin conexión';
  const busy=['starting','waiting','restoring'].includes(state.auth.status);
  $('connect').disabled=busy;
  $('connect').textContent=busy?'Conectando…':!state.connected?'Conectar Outlook':'Reconectar Outlook';
  const accessReady=state.connected&&!busy&&state.auth.status!=='needs_login';
  $('login-screen').hidden=accessReady;
  $('application').hidden=!accessReady;
  if(!accessReady)$('confirm-dialog').close();
  $('login-account').textContent=state.account;
  $('login-connect').disabled=busy;
  $('login-connect').hidden=busy;
  $('login-description').textContent=state.auth.status==='restoring'?'Recuperando tu sesión de Microsoft…':state.auth.status==='reconnecting'?'Esperando conexión para recuperar tu sesión. Se reintentará automáticamente.':state.auth.status==='waiting'?'Introduce el código que aparece abajo en la página de Microsoft. El panel se abrirá al terminar.':state.auth.status==='starting'?'Preparando el acceso a Microsoft…':state.auth.status==='needs_login'?'Microsoft necesita que vuelvas a iniciar sesión. Tus renovaciones se conservan.':'Inicia sesión con tu cuenta de trabajo para abrir el panel. En los siguientes accesos recuperaremos tu sesión automáticamente.';
  $('sync').disabled=!state.connected||busy;
  $('sync-label').textContent=state.last_scan?'Última comprobación: '+new Date(state.last_scan).toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'})+' · Cada '+state.poll_seconds+' s':state.connected?'Esperando la primera comprobación…':'Conecta Outlook para recibir las nuevas renovaciones.';
  $('auth-box').hidden=!busy;
  $('auth-code-area').hidden=state.auth.status!=='waiting';
  $('auth-description').textContent=state.auth.status==='waiting'?'Abre Microsoft, introduce este código e inicia sesión con '+state.account+'.':'Preparando el inicio de sesión…';
  if(state.auth.status==='waiting'){$('auth-code').textContent=state.auth.user_code;$('auth-link').href=safeLink(state.auth.verification_uri);}
  const error=['error','needs_login'].includes(state.auth.status)?state.auth.message:state.last_error;
  $('connection-error').hidden=!error;$('connection-error').textContent=error||'';
  $('login-error').hidden=!error;$('login-error').textContent=error||'';
  updateSendButton();
}
function renderDetail(){
  const entry=item();if(!entry)return;
  const signature=entry.key+':'+entry.status;
  if(rendered===signature)return;
  rendered=signature;preview=null;
  const p=entry.proposal;
  const header=`<div class="detail-header"><div><h2>${escapeHtml(p?.customer||'Revisión del correo')}</h2><p>${escapeHtml(p?.product||entry.subject)}</p></div><span class="badge ${escapeHtml(entry.status)}">${escapeHtml(labels[entry.status]||entry.status)}</span></div>`;
  if(entry.status!=='pending'||!p){
    const link=safeLink(entry.web_link);
    $('detail').innerHTML=header+`<div class="card"><h3>${escapeHtml(labels[entry.status]||entry.status)}</h3><p class="discount-note">${escapeHtml(entry.issue||(entry.status==='sent'?'El mensaje figura como enviado en Outlook.':'Esta renovación ya tiene una operación de envío registrada.'))}</p>${p?`<p>Total de la propuesta: <strong>${money(entry.amounts.total)}</strong></p><p class="discount-note">Destinatario: ${escapeHtml(entry.recipient)}</p>`:''}${link?`<a class="button secondary" target="_blank" rel="noreferrer" href="${escapeHtml(link)}">Revisar en Outlook ↗</a>`:''}${entry.status==='review'?'<a class="button secondary" href="https://outlook.office.com/mail/inbox" target="_blank" rel="noreferrer">Abrir la bandeja de entrada ↗</a>':''}</div>`;
    return;
  }
  const multiplier=state.price_basis==='unit'?p.quantity:1;
  const initialKind=Number(entry.discount)?entry.discount_kind:'none';
  $('detail').innerHTML=header+`
    ${entry.reception_test?'<p class="notice">Prueba recibida por Outlook. El envío está bloqueado permanentemente.</p>':entry.test?'<p class="notice">Prueba local. El destinatario está fijado a tu propia cuenta; puedes recorrer todo el proceso antes de usarlo con clientes.</p>':''}
    <section class="card"><div class="card-heading"><span class="step">01</span><h3>Revisa los precios</h3><small>INFORMACIÓN INTERNA</small></div><div class="price-grid"><div><span>Coste de renovación · PVD</span><strong>${p.pvd_cents!==undefined?money(p.pvd_cents*multiplier):'—'}</strong></div><div><span>PVP anterior</span><strong>${p.pvp_current_cents!==undefined?money(p.pvp_current_cents*multiplier):'—'}</strong></div><div class="highlight"><span>PVP de renovación</span><strong>${money(p.pvp_cents*multiplier)}</strong></div></div><div class="meta-row"><span>Licencias: <b>${p.quantity}</b></span><span>Renovación: <b>${fmtDate(p.renewal)}</b></span><span>Importes del conjunto</span></div></section>
    <form id="prepare-form" class="card"><div class="card-heading"><span class="step">02</span><h3>Prepara la propuesta</h3></div><div class="fields"><label>Descuento<select id="discount-kind"><option value="none">Sin descuento</option><option value="percent">Porcentaje (%)</option><option value="amount">Importe total (€)</option></select></label><label id="discount-label">Valor del descuento<input id="discount-value" type="number" min="0" step="0.01" value="${escapeHtml(entry.discount)}" required></label><label class="wide">Destinatario<input id="recipient" type="email" required maxlength="200" value="${escapeHtml(entry.recipient)}" ${entry.test?'readonly':''}></label></div><div class="final-price"><span>Total para el cliente</span><strong id="live-total">${money(entry.amounts.total)}</strong></div><p id="discount-summary" class="discount-note">El coste del proveedor se queda en el panel.</p><button class="button primary prepare-button" id="prepare" type="submit">Generar vista previa →</button></form>
    <section class="card preview-card"><div class="preview-label"><strong>03 · Vista previa del correo</strong><span>LO QUE VERÁ EL CLIENTE</span></div><div id="preview-placeholder" class="preview-placeholder">Genera la vista previa para revisar el correo completo<br>con la marca de tu empresa y el precio final.</div><iframe id="email-preview" title="Vista previa del correo para el cliente" sandbox="" hidden></iframe></section><div class="send-area"><p id="send-help">Primero revisa la vista previa. Después podrás confirmar el envío.</p><button class="button primary" id="review-send" disabled>Revisar y enviar ↗</button></div>`;
  $('discount-kind').value=initialKind;
  let formVersion=0;
  function changed(){
    formVersion++;
    preview=null;
    const kind=$('discount-kind').value;
    $('discount-value').disabled=kind==='none';
    $('discount-value').max=kind==='percent'?'100':String(p.pvp_cents*multiplier/100);
    try{const amount=totalFor(entry,$('discount-value').value,kind);$('live-total').textContent=money(amount.total);$('discount-summary').textContent=amount.discount?'Descuento: '+money(amount.discount)+' sobre '+money(amount.subtotal)+'. El coste del proveedor se queda en el panel.':'Sin descuento. El coste del proveedor se queda en el panel.';}catch{$('live-total').textContent='—';$('discount-summary').textContent='El descuento debe ser válido y no superar el total.';}
    $('email-preview').hidden=true;$('preview-placeholder').hidden=false;updateSendButton();
  }
  $('discount-kind').addEventListener('change',changed);$('discount-value').addEventListener('input',changed);$('recipient').addEventListener('input',changed);changed();
  $('prepare-form').addEventListener('submit',async event=>{
    event.preventDefault();const button=$('prepare');button.disabled=true;
    const key=selected, version=formVersion;
    try{
      const result=await api('/api/items/'+key+'/preview',{discount:$('discount-value').value,kind:$('discount-kind').value,recipient:$('recipient').value});
      if(selected!==key)return;
      if(formVersion!==version){toast('Los datos han cambiado. Vuelve a generar la vista previa.');return;}
      preview=result;$('email-preview').srcdoc=result.html;$('email-preview').hidden=false;$('preview-placeholder').hidden=true;
      $('live-total').textContent=money(result.amounts.total);updateSendButton();await refresh();
    }catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}
  });
  $('review-send').addEventListener('click',()=>{
    if(!preview)return;
    $('confirm-from').textContent=state.account;$('confirm-to').textContent=preview.to;$('confirm-subject').textContent=preview.subject;$('confirm-total').textContent=money(preview.amounts.total);$('confirm-test').hidden=!preview.test;$('confirm-dialog').showModal();
  });
  updateSendButton();
}
function updateSendButton(){
  if(!$('review-send'))return;
  if(item()?.send_blocked){$('review-send').disabled=true;$('send-help').textContent='Prueba de recepción: el envío está bloqueado, incluso después de finalizar la prueba. Puedes revisar precios, descuento y vista previa.';return;}
  $('review-send').disabled=!preview||!state.connected||!state.can_send||sending;
  $('send-help').textContent=!preview?'Genera y revisa la vista previa antes de enviar.':!state.connected?'La propuesta está lista para revisar. Conecta Outlook para enviarla.':!state.can_send?'Microsoft no ha concedido el permiso de envío en esta sesión. Pulsa «Reconectar Outlook»; si persiste, consulta al administrador.':'El correo solo se enviará cuando pulses «Confirmar y enviar» en el siguiente paso.';
}
async function refresh(){
  if(polling)return;polling=true;
  try{
    const next=await api('/api/state');
    if(seen){
      const added=next.items.filter(entry=>!seen.has(entry.key));
      if(added.length){toast(added.length===1?'Nueva renovación: '+(added[0].proposal?.customer||'Revisar datos'):added.length+' renovaciones nuevas.');
        if(!desktop&&'Notification' in window&&Notification.permission==='granted'&&document.hidden){const notification=new Notification('Nueva renovación',{body:added.length===1?(added[0].proposal?.customer||'Revisar datos')+' · '+(added[0].amounts?money(added[0].amounts.total):'Datos pendientes'):added.length+' correos pendientes de revisar',tag:'renovaciones'});notification.onclick=()=>{window.focus();selected=added[0].key;rendered='';renderQueue();renderDetail();notification.close();};}
      }
    }
    seen=new Set(next.items.map(entry=>entry.key));state=next;renderConnection();renderQueue();renderDetail();
  }catch(error){const message=desktop?'Se está recuperando la conexión con el servicio. Tus renovaciones se conservan.':'No se puede acceder al proceso local. Comprueba que «Abrir Renovaciones.cmd» sigue abierto.';for(const id of ['connection-error','login-error']){$(id).hidden=false;$(id).textContent=message;}}finally{polling=false;}
}
async function connectOutlook(){
  $('login-connect').disabled=true;$('connect').disabled=true;
  let connectionError='';
  try{await api('/api/connect',{});await refresh();}
  catch(error){connectionError=error.message;toast(error.message);}
  finally{if(state)renderConnection();if(connectionError){$('login-error').hidden=false;$('login-error').textContent=connectionError;}}
}
$('connect').addEventListener('click',connectOutlook);
$('login-connect').addEventListener('click',connectOutlook);
$('load-test').addEventListener('click',async()=>{const button=$('load-test');button.disabled=true;try{const result=await api('/api/import-test',{});selected=result.key;filter='all';document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.filter===filter));rendered='';await refresh();}catch(error){toast(error.message);}finally{button.disabled=false;}});
$('sync').addEventListener('click',async()=>{const button=$('sync');button.disabled=true;try{await api('/api/sync',{});await refresh();toast('Comprobación solicitada.');}catch(error){toast(error.message);}finally{button.disabled=!state?.connected;}});
$('notify').addEventListener('click',async()=>{if(desktop){try{const enabled=await desktop.notifications(true);$('notify').textContent=enabled?'Avisos activados':'Activar avisos';toast('Los avisos funcionan aunque cierres esta ventana. Puedes desactivarlos desde el icono junto al reloj.');}catch{toast('No se han podido activar los avisos.');}return;}if(!('Notification' in window)){toast('Este navegador no admite avisos de escritorio. Las novedades aparecerán en el panel.');return;}const permission=await Notification.requestPermission();$('notify').textContent=permission==='granted'?'Avisos activados':'Activar avisos';toast(permission==='granted'?'Recibirás avisos con el panel abierto, aunque estés en otra pestaña.':'Puedes permitir los avisos desde los ajustes del navegador.');});
$('search').addEventListener('input',renderQueue);
document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el===button));renderQueue();}));
for(const id of ['cancel-x','cancel-send'])$(id).addEventListener('click',()=>$('confirm-dialog').close());
$('confirm-form').addEventListener('submit',async event=>{
  event.preventDefault();if(sending||!preview)return;sending=true;$('confirm-send').disabled=true;
  try{await api('/api/items/'+selected+'/send',{approval_id:preview.approval_id,confirmed:true});$('confirm-dialog').close();preview=null;toast('Microsoft ha aceptado la solicitud de envío. El panel comprobará su estado.');}
  catch(error){$('confirm-dialog').close();toast(error.message);}
  finally{sending=false;$('confirm-send').disabled=false;rendered='';await refresh();}
});
async function boot(){
  if(desktop){document.querySelector('footer span').textContent='Cerrar la ventana mantiene la supervisión activa. Para cerrar la aplicación, usa «Salir» en el icono junto al reloj.';try{const settings=await desktop.settings();$('app-version').textContent='Versi\u00f3n '+settings.version;$('app-version').hidden=!settings.version;$('notify').textContent=settings.notifications?'Avisos activados':'Activar avisos';}catch{}}
  try{csrf=(await api('/api/session')).csrf;await refresh();setInterval(refresh,4000);if('Notification' in window&&Notification.permission==='granted')$('notify').textContent='Avisos activados';}
  catch{$('login-error').hidden=false;$('login-error').textContent='Abre «Abrir Renovaciones.cmd» para usar el panel con su conexión local.';$('login-description').textContent='No se ha podido conectar con el servicio local. Vuelve a abrir la aplicación.';}
}
boot();
