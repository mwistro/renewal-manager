import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from .graph import MicrosoftSession
from .parser import read_eml
from .proposal import draft_payload, prepare, totals, matches, source_key
from .worker import StateStore, Worker, apply_discount


def configuration(path, live=False, connection_only=False):
    config_path = Path(path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if live:
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', config.get('client_id', '')):
            raise ValueError('Falta client_id del registro de aplicación de Microsoft Entra.')
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', config.get('expected_mailbox', '')):
            raise ValueError('Falta expected_mailbox: dirección completa de la cuenta que recibe las renovaciones.')
    if connection_only:
        return config
    if config.get('price_basis') not in ('unit', 'total'):
        raise ValueError('Configura price_basis: unit si el PVP es por licencia, total si es por conjunto. Debe confirmarlo el proveedor.')
    if config.get('tax') not in ('included', 'excluded', 'unspecified'):
        raise ValueError('Configura tax: included, excluded o unspecified.')
    if not isinstance(config.get('poll_seconds'), int) or not 15 <= config['poll_seconds'] <= 300:
        raise ValueError('poll_seconds debe estar entre 15 y 300 segundos.')
    if not config.get('supplier_domains') or any(not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}', d) for d in config['supplier_domains']):
        raise ValueError('Configura los dominios exactos del proveedor en supplier_domains.')
    company = config.get('company', {})
    if not company.get('name') or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', company.get('email', '')):
        raise ValueError('Faltan el nombre y el email de contacto de la empresa.')
    if company.get('logo_path'):
        company['logo_path'] = str((config_path.parent / company['logo_path']).resolve())
    return config


def main():
    cli = argparse.ArgumentParser(description='Renovaciones de Outlook: avisos, revisión, descuentos y envío confirmado desde el panel.')
    cli.add_argument('--config', default='automation/config.local.json', help='Archivo de configuración local')
    cli.add_argument('--state-dir', default='.local/automatizacion', help='Directorio local de estado')
    commands = cli.add_subparsers(dest='command', required=True)
    preview = commands.add_parser('vista', help='Extraer un email .eml y generar una vista previa sin conectar a Outlook')
    preview.add_argument('eml')
    preview.add_argument('--output', default='.local/vista-renovacion.html')
    preview.add_argument('--discount', default='0')
    preview.add_argument('--kind', choices=['percent', 'amount'], default='percent')
    commands.add_parser('iniciar', help='Abrir el panel de revisión y confirmación de renovaciones')
    panel_parser = commands.add_parser('panel', help='Abrir el panel con avisos, descuentos, vista previa y envío confirmado')
    panel_parser.add_argument('--port', type=int, default=8765)
    panel_parser.add_argument('--open', action='store_true', help='Abrir el panel en el navegador')
    connection = commands.add_parser('conectar', help='Comprobar el inicio de sesión y el acceso al buzón sin crear borradores')
    connection.add_argument('--verificar-envio', action='store_true', help='Solicitar y comprobar Mail.Send sin enviar mensajes')
    test_draft = commands.add_parser('borrador-prueba', help='Crear un borrador de prueba dirigido a tu propia cuenta a partir de un .eml')
    test_draft.add_argument('eml')
    commands.add_parser('lista', help='Consultar las referencias de los borradores y las incidencias')
    discount = commands.add_parser('descuento', help='Recalcular un borrador que aún no se ha editado en Outlook')
    discount.add_argument('reference')
    discount.add_argument('value', help='Porcentaje o euros, con punto decimal')
    discount.add_argument('--kind', choices=['percent', 'amount'], default='percent')
    args = cli.parse_args()
    config = configuration(args.config, live=args.command in ('iniciar', 'panel', 'descuento', 'conectar', 'borrador-prueba'), connection_only=args.command == 'conectar')
    if args.command in ('panel', 'iniciar'):
        from .panel import serve
        from .organization import require_organization
        require_organization(config)
        config['restrict_organization'] = True
        serve(config, Path(__file__).resolve().parent.parent, port=getattr(args, 'port', 8765), open_browser=getattr(args, 'open', args.command == 'iniciar'))
        return
    if args.command == 'conectar':
        graph = MicrosoftSession(config, enable_send=args.verificar_envio)
        graph.login()
        graph.verify_mailbox()
        graph.request('GET', '/me/mailFolders/inbox?$select=id')
        graph.request('GET', '/me/mailFolders/drafts?$select=id')
        print('Conexión verificada: ' + config['expected_mailbox'])
        print('Acceso a Bandeja de entrada y Borradores confirmado. No se han creado ni enviado mensajes.')
        if args.verificar_envio:
            if not graph.can_send:
                raise RuntimeError('Microsoft no ha incluido Mail.Send en esta sesión. Comprueba el permiso delegado y el consentimiento de Renovaciones.')
            print('Mail.Send confirmado en la sesión de Microsoft. El envío sigue requiriendo vista previa y confirmación en la aplicación.')
        print('Prueba finalizada. La automatización permanece detenida; al iniciarla se solicitará de nuevo el acceso.')
        return
    identity = hashlib.sha256(config.get('expected_mailbox', '').lower().encode()).hexdigest()[:20]
    store = StateStore(Path(args.state_dir) / identity)
    if args.command == 'vista':
        message = read_eml(args.eml)
        proposal = prepare(message, config)
        draft = draft_payload(proposal, config, 'vista-local', args.discount, args.kind)
        html = draft['body']['content']
        for attachment in draft.get('attachments', []):
            html = html.replace('cid:company-logo', 'data:' + attachment['contentType'] + ';base64,' + attachment['contentBytes'])
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding='utf-8')
        print('Vista previa creada: ' + str(output.resolve()))
        print('PVP de renovación: ' + str(proposal['pvp_cents'] / 100) + ' EUR. Total: ' + str(totals(proposal, config['price_basis'], args.discount, args.kind)['total'] / 100) + ' EUR.')
        return
    if args.command == 'lista':
        with store.lock():
            state = store.read()
        if not state['messages']:
            print('Todavía no hay renovaciones procesadas.')
        for key, record in state['messages'].items():
            print(key[:12] + ' | ' + record['status'] + ' | ' + record.get('reason', record.get('proposal', {}).get('customer', '')))
        return
    if args.command == 'borrador-prueba':
        message = read_eml(args.eml)
        if not matches(message, config):
            raise ValueError('El archivo no coincide con el remitente y asunto configurados. Usa la copia de prueba con remitente simulado.')
        prepare(message, config)
    graph = MicrosoftSession(config)
    graph.login()
    mailbox_id = graph.verify_mailbox()
    if args.command == 'borrador-prueba':
        with store.lock():
            state = store.read()
            if state.get('mailbox_id', mailbox_id) != mailbox_id:
                raise ValueError('El estado de prueba pertenece a otra cuenta.')
            state['mailbox_id'] = mailbox_id
            store.save(state)
            Worker(graph, config, store, mailbox_id).process(message, state, test_mode=True)
            key = source_key({**message, 'id': 'prueba-local:' + message['id']})
            record = state['messages'].get(key, {})
        if record.get('status') != 'draft':
            raise RuntimeError('El borrador necesita revisión: ' + record.get('reason', record.get('status', 'sin resultado')))
        from urllib.parse import quote
        current = graph.request('GET', '/me/messages/' + quote(record['draft_id'], safe='') + '?$select=id,isDraft,webLink')
        if not current.get('isDraft'):
            raise RuntimeError('El mensaje de prueba ya no está en estado de borrador.')
        print('Borrador de prueba disponible en Borradores. Destinatario: ' + config['expected_mailbox'])
        if current.get('webLink'):
            print('Abrir en Outlook: ' + current['webLink'])
        print('No se ha enviado ningún mensaje. La supervisión automática de la bandeja de entrada permanece detenida.')
    else:
        apply_discount(graph, store, config, mailbox_id, args.reference, args.value, args.kind)
        print('Descuento aplicado al borrador de Outlook. El mensaje sigue pendiente de revisión y envío.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nAutomatización detenida.')
    except Exception as error:
        print('No se ha completado la operación: ' + str(error), file=sys.stderr)
        sys.exit(1)
