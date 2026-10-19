'use strict';
const $=id=>document.getElementById(id);
let logoData='';
const ids=['product','term','quantity','renewal','customer','contact','recipient','price','basis','discount','discount-type','tax','cost','company','sender','subject','message','closing'];
function data(){return Object.fromEntries(ids.map(id=>[id==='discount-type'?'discountType':id,$(id).value]));}
function status(message){$('status').textContent=message;}
function render(){
  const d=data();
  $('preview').srcdoc=Proposal.email(d,logoData);
  $('preview-to').textContent=d.recipient||'Email del cliente';
  $('preview-subject').textContent=d.subject;
  $('price-error').textContent=''; $('profit-row').hidden=true;
  try{
    const t=Proposal.calculate(d);
    $('subtotal').textContent=Proposal.money(t.subtotal);$('discount-total').textContent='−'+Proposal.money(t.reduction);$('total').textContent=Proposal.money(t.total);
    if(d.cost!=='' && Number.isFinite(Number(d.cost)) && Number(d.cost)>=0){$('profit-row').hidden=false;$('profit').textContent=Proposal.money(t.total-Math.round(Number(d.cost)*100)*(d.basis==='unit'?Number(d.quantity):1));}
  }catch(error){for(const id of ['subtotal','discount-total','total']) $(id).textContent='—'; if(d.price!=='') $('price-error').textContent=error.message;}
}
$('proposal-form').addEventListener('input',render);
$('proposal-form').addEventListener('submit',e=>e.preventDefault());
$('sample').addEventListener('click',()=>{
  if(ids.some(id=>['product','customer','company','price'].includes(id)&&$(id).value) && !confirm('¿Sustituir los datos de la propuesta por un ejemplo ficticio?')) return;
  const sample={product:'Microsoft Entra ID P1',term:'Anual, pago anual',quantity:'3',renewal:'2026-11-09',customer:'Empresa de ejemplo, S. L.',contact:'María',recipient:'cliente@example.com',price:'219.60',basis:'unit',discount:'10','discount-type':'percent',cost:'178.59',company:'Tu empresa · Ejemplo',sender:'comercial@example.com',tax:'excluded',subject:'Propuesta de renovación de licencias'};
  for(const [key,value] of Object.entries(sample)) $(key).value=value;
  logoData='';$('logo').value='';$('remove-logo').hidden=true;
  render();status('Ejemplo ficticio cargado. Se ha supuesto precio por licencia. Comprueba esta base con el proveedor antes de usar datos reales.');
});
$('logo').addEventListener('change',async e=>{
  const file=e.target.files[0]; if(!file)return;
  if(!['image/png','image/jpeg'].includes(file.type)||file.size>1024*1024){status('Selecciona un PNG o JPG de hasta 1 MB.');e.target.value='';return;}
  const reader=new FileReader();
  reader.onload=()=>{const picture=new Image();picture.onload=()=>{logoData=reader.result;$('remove-logo').hidden=false;render();status('Logotipo añadido.');};picture.onerror=()=>{status('No se ha podido leer la imagen.');e.target.value='';};picture.src=reader.result;};
  reader.onerror=()=>status('No se ha podido leer el archivo.');reader.readAsDataURL(file);
});
$('remove-logo').addEventListener('click',()=>{logoData='';$('logo').value='';$('remove-logo').hidden=true;render();});
function ready(){if(!$('proposal-form').reportValidity())return null;try{Proposal.calculate(data());return data();}catch(error){status(error.message);$('basis').focus();return null;}}
function base64(value){const bytes=new TextEncoder().encode(value);let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return btoa(binary);}
function folded(value){return value.match(/.{1,76}/g)?.join('\r\n')||'';}
function encodedHeader(value){const chunks=[];let chunk='';for(const c of value.replace(/[\r\n]/g,' ')){if(new TextEncoder().encode(chunk+c).length>42){chunks.push('=?UTF-8?B?'+base64(chunk)+'?=');chunk='';}chunk+=c;}if(chunk)chunks.push('=?UTF-8?B?'+base64(chunk)+'?=');return chunks.join('\r\n ');}
$('download').addEventListener('click',()=>{
  const d=ready();if(!d)return;
  const boundary='renovacion_'+crypto.randomUUID().replace(/-/g,'');
  const headers=['MIME-Version: 1.0','X-Unsent: 1','From: '+d.sender.replace(/[\r\n]/g,''),'To: '+d.recipient.replace(/[\r\n]/g,''),'Subject: '+encodedHeader(d.subject),'Content-Type: multipart/related; boundary="'+boundary+'"'];
  const parts=['--'+boundary,'Content-Type: text/html; charset=UTF-8','Content-Transfer-Encoding: base64','',folded(base64(Proposal.email(d,logoData?'cid:company-logo':'')))];
  if(logoData){const match=logoData.match(/^data:(image\/(?:png|jpeg));base64,(.+)$/);if(match)parts.push('--'+boundary,'Content-Type: '+match[1],'Content-ID: <company-logo>','Content-Disposition: inline; filename="logo.'+(match[1]==='image/png'?'png':'jpg')+'"','Content-Transfer-Encoding: base64','',folded(match[2]));}
  parts.push('--'+boundary+'--','');
  const blob=new Blob([headers.join('\r\n')+'\r\n\r\n'+parts.join('\r\n')],{type:'message/rfc822'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='propuesta-renovacion.eml';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status('Correo descargado. Ábrelo en tu cliente de correo, revisa el destinatario y envíalo cuando esté listo.');
});
$('copy').addEventListener('click',async()=>{
  const d=ready();if(!d)return;
  try{const html=Proposal.email(d,logoData),plain=new DOMParser().parseFromString(html,'text/html').body.textContent;
    if(!navigator.clipboard?.write||typeof ClipboardItem==='undefined')throw Error();
    await navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([html],{type:'text/html'}),'text/plain':new Blob([plain],{type:'text/plain'})})]);
    status('Correo copiado. Pégalo en el cuerpo del mensaje. Comprueba que tu cliente conserva el logotipo; para incluirlo como imagen adjunta usa el archivo .eml.');
  }catch{status('El navegador no permite copiar con formato aquí. Descarga el archivo .eml para abrirlo en tu cliente de correo.');}
});
render();
