import base64
import hashlib
import re
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from .parser import ReviewRequired, extract

PROPERTY_ID = 'String {0eae67ec-3da7-42e3-8e70-c05068e60da1} Name RenovacionOrigen'


def euros(cents):
    return f'{cents / 100:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.') + ' €'


def totals(proposal, basis, discount='0', kind='percent'):
    if basis not in ('unit', 'total'):
        raise ReviewRequired('Confirma si el PVP del proveedor es por licencia (unit) o por conjunto (total).')
    subtotal = proposal['pvp_cents'] * (proposal['quantity'] if basis == 'unit' else 1)
    try:
        value = Decimal(str(discount))
        if not value.is_finite() or value < 0 or kind not in ('percent', 'amount'):
            raise ValueError()
        if kind == 'percent' and value > 100:
            raise ValueError()
        reduction = int((Decimal(subtotal) * value / 100 if kind == 'percent' else value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        if reduction > subtotal:
            raise ValueError()
    except (ValueError, ArithmeticError):
        raise ReviewRequired('El descuento no es válido o supera el importe de la propuesta.') from None
    return {'subtotal': subtotal, 'discount': reduction, 'total': subtotal - reduction}


def source_key(message):
    return hashlib.sha256(message['id'].encode('utf-8')).hexdigest()


def matches(message, config):
    sender = message.get('from', {}).get('emailAddress', {}).get('address', '').lower()
    domains = config['supplier_domains']
    renewal_subject = re.search(r'\b(?:renovaci[oó]n(?:es)?|renovar[aá]n?)\b', message.get('subject', ''), re.I)
    return bool(not message.get('isDraft', False) and sender.count('@') == 1 and sender.split('@')[1] in domains and renewal_subject)


def prepare(message, config):
    if message.get('body', {}).get('contentType', '').lower() != 'html':
        raise ReviewRequired('El mensaje no tiene una tabla HTML reconocible.')
    proposal = extract(message['body']['content'])
    if proposal['recipient'].lower().split('@')[-1] in config['supplier_domains']:
        raise ReviewRequired('El destinatario extraído pertenece al proveedor. Revisa el cliente.')
    totals(proposal, config['price_basis'])
    return proposal


def render(proposal, config, discount='0', kind='percent', with_logo=False):
    amount = totals(proposal, config['price_basis'], discount, kind)
    e = lambda value: escape(str(value), quote=True)
    company = config['company']
    logo = '<img src="cid:company-logo" alt="' + e(company['name']) + '" width="180" style="display:block;width:180px;max-width:100%;height:auto;border:0;margin:0 auto">' if with_logo else ''

    def row(label, value, total=False):
        border = 'border-top:1px solid #dddddd;' if total else ''
        return f'<tr><td width="55%" style="padding:12px 20px;{border}color:{"#333333" if total else "#666666"};font-weight:{"bold" if total else "normal"}">{e(label)}</td><td align="right" style="padding:12px 20px;{border}white-space:nowrap;font-size:{"22px" if total else "14px"};font-weight:{"bold" if total else "normal"};color:#222222">{e(value)}</td></tr>'

    reduction = row('Importe inicial', euros(amount['subtotal'])) + row('Descuento', '−' + euros(amount['discount'])) if amount['discount'] else ''
    tax = {'excluded': 'El importe indicado no incluye impuestos.', 'included': 'IVA incluido', 'unspecified': 'El importe indicado no incluye impuestos.'}[config['tax']]
    tax_note = f'<p style="margin:10px 0 0;text-align:right;font-size:12px;line-height:1.5;color:#666666">{tax}</p>'
    return f'''<!doctype html><html lang="es-ES"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background-color:#ffffff;font-family:Arial,sans-serif;color:#333333">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="border-collapse:collapse"><tr><td align="center" style="padding:24px 12px">
<!--[if mso]><table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;border-collapse:collapse"><tr><td style="padding:0 12px;font-family:Arial,sans-serif;font-size:14px;line-height:1.7;color:#333333">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="padding:0 0 22px;border-bottom:1px solid #e5e5e5">{logo}</td></tr></table>
<h1 align="center" style="margin:26px 0 28px;text-align:center;font-family:Arial,sans-serif;font-size:24px;line-height:1.35;font-weight:bold;color:#222222">Renovación de licencia Office 365</h1>
<p style="margin:0 0 12px">Hola!</p>
<p style="margin:0 0 22px">Próximamente se renovarán automáticamente la siguiente licencia de Office 365:</p>
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f7f7f7" style="border-collapse:collapse;background-color:#f7f7f7;font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#333333">
<tr><td colspan="2" style="padding:18px 20px;border-bottom:1px solid #e5e5e5;font-size:16px;line-height:1.5"><strong>{e(proposal['product'])}</strong></td></tr>
{row('Licencias', proposal['quantity'])}
{row('Fecha de renovación', '/'.join(proposal['renewal'].split('-')[::-1]))}
{reduction}{row('Total renovación', euros(amount['total']), total=True)}</table>{tax_note}
<p style="margin:24px 0 14px">Es el momento de confirmar si los clientes continúan con las licencias que tienen o si quieren hacer modificaciones, tanto en las unidades como en la modalidad de facturación.</p>
<p style="margin:0 0 22px">De no obtener respuesta, la renovación se realizará automáticamente en la misma cantidad y modalidad de facturación vigente.</p>
<p style="margin:0;padding:18px 0 0;border-top:1px solid #e5e5e5">En caso de necesitar hacer alguna modificación, por favor, contactad a la mayor brevedad posible con <a href="mailto:{e(company['email'])}" style="color:#333333;text-decoration:underline">{e(company['email'])}</a></p>
</td></tr></table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>'''


def logo_attachment(config):
    path = config['company'].get('logo_path')
    if not path:
        return []
    image = Path(path).read_bytes()
    if len(image) > 1024 * 1024:
        raise ValueError('El logotipo debe ocupar menos de 1 MB.')
    mime = 'image/png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if image.startswith(b'\xff\xd8\xff') else None
    if not mime:
        raise ValueError('El logotipo debe ser PNG o JPG.')
    return [{'@odata.type': '#microsoft.graph.fileAttachment', 'name': 'logo.png' if mime == 'image/png' else 'logo.jpg', 'contentType': mime, 'isInline': True, 'contentId': 'company-logo', 'contentBytes': base64.b64encode(image).decode('ascii')}]


def draft_payload(proposal, config, key, discount='0', kind='percent'):
    attachments = logo_attachment(config)
    result = {'subject': 'Propuesta de renovación — ' + proposal['product'], 'body': {'contentType': 'HTML', 'content': render(proposal, config, discount, kind, bool(attachments))}, 'toRecipients': [{'emailAddress': {'address': proposal['recipient']}}], 'singleValueExtendedProperties': [{'id': PROPERTY_ID, 'value': key}]}
    if attachments:
        result['attachments'] = attachments
    return result
