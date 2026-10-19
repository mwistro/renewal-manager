(function(root){
  'use strict';
  const money = value => new Intl.NumberFormat('es-ES', {style:'currency',currency:'EUR'}).format(value / 100);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function calculate(data){
    const price=Number(data.price), quantity=Number(data.quantity), discount=Number(data.discount);
    if(!['unit','total'].includes(data.basis)) throw Error('Indica si el precio es por licencia o por el conjunto.');
    if(data.price==='' || !Number.isFinite(price) || price<0 || price>10000000) throw Error('Introduce un precio de venta válido.');
    if(!Number.isInteger(quantity) || quantity<1 || quantity>100000) throw Error('La cantidad de licencias debe ser un número entero positivo.');
    if(!Number.isFinite(discount) || discount<0) throw Error('El descuento debe ser un número positivo o cero.');
    const subtotal=Math.round(price*100)*(data.basis==='unit'?quantity:1);
    if(data.discountType==='percent' && discount>100) throw Error('El descuento no puede superar el 100 %.');
    const reduction=data.discountType==='percent'?Math.round(subtotal*discount/100):Math.round(discount*100);
    if(reduction>subtotal) throw Error('El descuento no puede superar el importe de la propuesta.');
    return {subtotal,reduction,total:subtotal-reduction};
  }
  function date(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Fecha pendiente'; return value.split('-').reverse().join('/');}
  function email(data, logo=''){
    let totals; try{totals=calculate(data);}catch{totals=null;}
    const e=escape, nl=v=>e(v).replace(/\n/g,'<br>');
    const company=e(data.company||'Tu empresa');
    const logoTag=logo?'<img src="'+e(logo)+'" alt="'+company+'" style="max-width:190px;max-height:70px;display:block;margin-bottom:16px">':'';
    const tax=({excluded:'IVA no incluido',included:'IVA incluido',unspecified:''})[data.tax]||'';
    return '<!doctype html><html lang="es-ES"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body style="margin:0;background:#fff;color:#35443a;font-family:Arial,sans-serif"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td style="padding:32px"><div style="border-bottom:2px solid #27674c;padding-bottom:20px">'+logoTag+'<strong style="font-size:20px;color:#24543b">'+company+'</strong><div style="font-size:10px;letter-spacing:1.8px;color:#8a958a;margin-top:7px">PROPUESTA DE RENOVACIÓN</div></div><h1 style="font-size:25px;line-height:1.3;font-weight:normal;margin:29px 0 20px;color:#294936">Tus licencias, listas<br>para seguir trabajando.</h1><p style="font-size:13px;line-height:1.8">Hola'+(data.contact?' '+e(data.contact):'')+':</p><p style="font-size:13px;line-height:1.8;color:#6c796c">'+nl(data.message)+'</p><div style="padding:17px 0;font-size:11px;color:#85907f">CLIENTE<br><strong style="color:#50634c;font-size:13px;line-height:2">'+e(data.customer||'Empresa cliente')+'</strong></div><table width="100%" cellpadding="14" cellspacing="0" style="background:#f5f7f1;border:1px solid #e7ebdf;border-radius:6px;font-size:12px"><tr><td colspan="2"><strong style="font-size:14px;color:#36533a">'+e(data.product||'Nombre de la licencia')+'</strong><div style="color:#89917d;font-size:11px;margin-top:7px">'+e(data.term)+'</div></td></tr><tr><td style="color:#839079">Licencias</td><td align="right">'+e(data.quantity)+'</td></tr><tr><td style="color:#839079">Fecha de renovación</td><td align="right">'+date(data.renewal)+'</td></tr>'+(totals&&totals.reduction>0?'<tr><td>Importe inicial</td><td align="right">'+money(totals.subtotal)+'</td></tr><tr><td>Descuento'+(data.discountType==='percent'?' ('+e(data.discount)+' %)':'')+'</td><td align="right">−'+money(totals.reduction)+'</td></tr>':'')+'<tr><td style="border-top:1px solid #dfe6d7;font-weight:bold;color:#355639">Total renovación</td><td align="right" style="border-top:1px solid #dfe6d7;font-size:23px;color:#246744;font-weight:bold">'+(totals?money(totals.total):'—')+'</td></tr></table><p style="text-align:right;font-size:10px;color:#8b947f">'+tax+'</p><p style="font-size:12px;color:#7e8876;line-height:1.8;margin-top:25px">'+nl(data.closing)+'</p><p style="font-size:12px;line-height:1.8;margin-top:22px">Un saludo,<br><strong>'+company+'</strong><br>'+e(data.sender)+'</p></td></tr></table></body></html>';
  }
  const api={calculate,money,escape,email,date};
  if(typeof module!=='undefined'&&module.exports) module.exports=api;
  else root.Proposal=api;
})(typeof globalThis!=='undefined'?globalThis:this);
