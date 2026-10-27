"""Panel local: detectar, revisar precios, previsualizar y confirmar cada envío."""
import copy
import hashlib
import json
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from .graph import MicrosoftSession, GraphError, LoginRequired
from .parser import read_eml, ReviewRequired
from .proposal import draft_payload, prepare, matches, source_key, totals
from .worker import StateStore, utcnow, recover

EDITABLE = {'pending'}
PENDING_NETWORK = {'creating', 'sending', 'uncertain', 'submitted', 'send_failed'}


class Panel:
    def __init__(self, config, directory, root, vault=None):
        self.config, self.root = config, Path(root)
        self.store = StateStore(directory)
        self.lock, self.graph_lock = threading.RLock(), threading.RLock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.vault = vault
        self.csrf = secrets.token_urlsafe(32)
        self.graph = None
        self.auth = {'status': 'disconnected'}
        self.last_error = ''
        self.previews = {}
        self.draining = False
        self.state = self.store.read()
        self.state.setdefault('items', {})
        self.state.setdefault('monitor_started_at', None)
        for item in self.state['items'].values():
            if item['status'] in ('creating', 'sending'):
                item['status'] = 'uncertain'
                item['issue'] = 'El proceso se interrumpió. Se comprobará el resultado en Outlook; no se repetirá el envío.'

    def save(self):
        self.store.save(self.state)

    def public_state(self):
        with self.lock:
            items = []
            for key, entry in self.state['items'].items():
                item = {k: copy.deepcopy(entry[k]) for k in ('status', 'subject', 'received_at', 'proposal', 'discount', 'discount_kind', 'recipient', 'test', 'reception_test', 'send_blocked', 'issue', 'web_link', 'revision') if k in entry}
                item['key'] = key
                if entry.get('proposal'):
                    item['amounts'] = totals(entry['proposal'], self.config['price_basis'], entry.get('discount', '0'), entry.get('discount_kind', 'percent'))
                items.append(item)
            items.sort(key=lambda item: item.get('received_at', ''), reverse=True)
            return {'account': self.config['expected_mailbox'], 'company': self.config['company']['name'], 'price_basis': self.config['price_basis'], 'connected': self.graph is not None, 'can_send': bool(self.graph and self.graph.can_send), 'auth': copy.deepcopy(self.auth), 'items': items, 'last_scan': self.state.get('last_scan'), 'last_error': self.last_error, 'poll_seconds': self.config['poll_seconds'], 'reception_test': self.reception_test_state()}

    def reception_test_state(self):
        with self.lock:
            trial = self.state.get('reception_test') or {}
            active = bool(trial.get('enabled') and time.time() < trial.get('expires_epoch', 0))
            return {'active': active, 'subject': trial.get('subject', '') if active else '',
                    'expires_at': trial.get('expires_at'),
                    'received': sum(bool(item.get('reception_test')) for item in self.state['items'].values())}

    def start_reception_test(self):
        with self.graph_lock, self.lock:
            if self.draining or not self.graph:
                raise ValueError('Conecta Outlook antes de activar la prueba.')
            if self.reception_test_state()['active']:
                return self.reception_test_state()
            now = datetime.now(timezone.utc)
            expires = now + timedelta(hours=2)
            self.state['reception_test'] = {'enabled': True, 'started_at': now.isoformat(),
                'expires_at': expires.isoformat(), 'expires_epoch': expires.timestamp(),
                'subject': '[PRUEBA RENOVACIONES ' + secrets.token_hex(12).upper() + ']'}
            self.save()
            return self.reception_test_state()

    def stop_reception_test(self):
        with self.lock:
            if self.state.get('reception_test'):
                self.state['reception_test']['enabled'] = False
                self.save()
            return self.reception_test_state()

    def matches_reception_test(self, message):
        with self.lock:
            trial = self.state.get('reception_test') or {}
            sender = message.get('from', {}).get('emailAddress', {}).get('address', '').strip().lower()
            if (not trial.get('enabled') or message.get('isDraft', False)
                    or sender != self.config['expected_mailbox'].strip().lower()
                    or message.get('subject', '').strip() != trial.get('subject', '')):
                return False
            try:
                received = datetime.fromisoformat(message.get('receivedDateTime', '').replace('Z', '+00:00'))
                # Una consulta posterior recupera lo recibido durante la ventana,
                # incluso si la conexión volvió después de caducar la prueba.
                return datetime.fromisoformat(trial['started_at']) <= received <= datetime.fromisoformat(trial['expires_at'])
            except (ValueError, TypeError):
                return False

    def start_auth(self):
        with self.lock:
            if self.auth['status'] in ('starting', 'waiting', 'restoring'):
                raise ValueError('Ya hay un inicio de sesión pendiente.')
            self.auth = {'status': 'starting'}
        def connect():
            try:
                session = MicrosoftSession(self.config, enable_send=True)
                code = session.begin_login()
                with self.lock:
                    self.auth = {'status': 'waiting', **code}
                session.complete_login()
                mailbox_id = session.verify_mailbox()
                session.request('GET', '/me/mailFolders/inbox?$select=id')
                with self.graph_lock, self.lock:
                    if self.state.get('mailbox_id', mailbox_id) != mailbox_id:
                        raise ValueError('Los datos del panel pertenecen a otra cuenta.')
                    self.state['mailbox_id'] = mailbox_id
                    if not self.state['monitor_started_at']:
                        self.state['monitor_started_at'] = utcnow()
                    if self.vault:
                        self.vault.save(session)
                        session.on_refresh = self.vault.save
                    self.graph = session
                    self.auth = {'status': 'connected'}
                    self.last_error = ''
                    self.save()
                self.synchronize()
            except Exception as error:
                with self.lock:
                    self.auth = {'status': 'error', 'message': str(error)}
        threading.Thread(target=connect, daemon=True).start()
        return {'started': True}

    def restore_session(self):
        with self.lock:
            if not self.vault or self.graph or self.auth['status'] in ('starting', 'waiting', 'needs_login'):
                return
            try:
                stored = self.vault.load()
            except (OSError, ValueError):
                self.auth = {'status': 'needs_login', 'message': 'No se puede abrir la sesión protegida. Vuelve a conectar Outlook.'}
                return
            if not stored:
                return
            self.auth = {'status': 'restoring'}
        # Recupera también sesiones antiguas de lectura con los permisos ya
        # concedidos. Microsoft decide si hace falta una nueva interacción.
        session = MicrosoftSession(self.config, enable_send=True)
        session.refresh = stored['refresh_token']
        mailbox_id = session.verify_mailbox()
        with self.lock:
            if self.state.get('mailbox_id', mailbox_id) != mailbox_id:
                raise LoginRequired('La sesión guardada no corresponde al buzón de este historial.')
            self.state['mailbox_id'] = mailbox_id
            self.state['monitor_started_at'] = self.state['monitor_started_at'] or utcnow()
            self.vault.save(session)
            session.on_refresh = self.vault.save
            self.graph = session
            self.auth = {'status': 'connected'}
            self.save()

    def synchronize(self):
        with self.graph_lock:
            try:
                self.restore_session()
                self.tick()
            except LoginRequired as error:
                with self.lock:
                    self.graph = None
                    self.auth = {'status': 'needs_login', 'message': str(error)}
                    self.last_error = str(error)
                    if self.vault:
                        try:
                            self.vault.clear()
                        except OSError:
                            self.last_error += ' No se ha podido eliminar la sesión caducada del disco.'
            except Exception as error:
                with self.lock:
                    self.last_error = 'No se ha podido comprobar Outlook. Se reintentará automáticamente. ' + str(error)
                    if self.auth['status'] == 'restoring':
                        self.auth = {'status': 'reconnecting'}

    def notifications(self):
        with self.lock:
            return [{'key': key, 'customer': item.get('proposal', {}).get('customer', 'Datos pendientes de revisar'), 'reception_test': bool(item.get('reception_test'))}
                    for key, item in self.state['items'].items()
                    if not item.get('notified') and (not item.get('test') or item.get('reception_test')) and item['status'] in ('pending', 'review')]

    def acknowledge(self, keys):
        with self.lock:
            for key in keys:
                if key in self.state['items']:
                    self.state['items'][key]['notified'] = True
            self.save()

    def ingest(self, message, test=False):
        reception_test = not test and self.matches_reception_test(message)
        if not test and message.get('subject', '').strip().startswith('[PRUEBA RENOVACIONES ') and not reception_test:
            return None
        if not test and not reception_test and not matches(message, self.config):
            return None
        identity = {**message, 'id': ('panel-test:' if test else '') + message['id']}
        key = source_key(identity)
        with self.lock:
            if key in self.state['items']:
                return key
        item = {'status': 'pending', 'source_id': message['id'], 'subject': message.get('subject', ''), 'received_at': message.get('receivedDateTime') or utcnow(), 'discount': '0', 'discount_kind': 'percent', 'test': test, 'revision': 0}
        if reception_test:
            item.update(test=True, reception_test=True, send_blocked=True)
        try:
            item['proposal'] = prepare(message, self.config)
            item['recipient'] = self.config['expected_mailbox'] if item['test'] else item['proposal']['recipient']
        except ReviewRequired as error:
            item.update(status='review', issue=str(error))
        with self.lock:
            self.state['items'].setdefault(key, item)
            self.save()
        return key

    def import_test(self):
        path = self.root / '.local/pruebas/entrada-remitente-simulado.eml'
        if (self.root / 'demo.eml').exists():
            path = self.root / 'demo.eml'
        if not path.exists():
            raise ValueError('No se encuentra el archivo de prueba local.')
        return {'key': self.ingest(read_eml(path), test=True)}

    def tick(self):
        with self.graph_lock:
            graph = self.graph
            if graph is None:
                return
            self.reconcile(graph)
            with self.lock:
                started = self.state.get('monitor_started_at')
                if not started:
                    return
                since = max(datetime.fromisoformat(started), datetime.fromisoformat(self.state.get('last_scan') or started) - timedelta(minutes=5))
            scan_started = utcnow()
            query = urlencode({'$filter': 'receivedDateTime ge ' + since.isoformat().replace('+00:00', 'Z'), '$select': 'id,subject,from,receivedDateTime,isDraft', '$top': '50'})
            path = '/me/mailFolders/inbox/messages?' + query
            while path:
                page = graph.request('GET', path)
                for summary in page['value']:
                    if not matches(summary, self.config) and not self.matches_reception_test(summary):
                        continue
                    with self.lock:
                        known = source_key(summary) in self.state['items']
                    if known:
                        continue
                    message = graph.request('GET', '/me/messages/' + quote(summary['id'], safe='') + '?$select=id,subject,from,body,isDraft,receivedDateTime')
                    self.ingest(message)
                path = page.get('@odata.nextLink')
            with self.lock:
                self.state['last_scan'] = scan_started
                self.last_error = ''
                self.save()

    def reconcile(self, graph):
        with self.lock:
            pending = [(key, copy.deepcopy(item)) for key, item in self.state['items'].items() if item['status'] in PENDING_NETWORK]
        for key, item in pending:
            draft_id = item.get('draft_id')
            if not draft_id and item.get('marker'):
                found = recover(graph, item['marker'])
                draft_id = found['id'] if found else None
            if not draft_id:
                continue
            try:
                message = graph.request('GET', '/me/messages/' + quote(draft_id, safe='') + '?$select=id,isDraft,sentDateTime,webLink')
            except GraphError as error:
                if error.status == 404:
                    continue
                raise
            with self.lock:
                current = self.state['items'][key]
                current.update(draft_id=draft_id, web_link=message.get('webLink'))
                if not message.get('isDraft', True) and message.get('sentDateTime'):
                    current.update(status='sent', sent_at=message['sentDateTime'], issue='')
                elif current['status'] == 'uncertain':
                    current['issue'] = 'Se ha localizado el borrador. Revisa su estado en Outlook antes de cualquier otro envío.'
                self.save()

    def preview(self, key, fields):
        with self.lock:
            if key not in self.state['items']:
                raise ValueError('No se encuentra la renovación.')
            item = self.state['items'][key]
            if item['status'] not in EDITABLE:
                raise ValueError('Esta renovación ya no está pendiente de preparar.')
            kind = fields.get('kind', 'percent')
            value = str(fields.get('discount', '0'))
            if kind == 'none':
                kind, value = 'percent', '0'
            amount = totals(item['proposal'], self.config['price_basis'], value, kind)
            recipient = self.config['expected_mailbox'] if item['test'] else str(fields.get('recipient', item['recipient'])).strip()
            if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', recipient):
                raise ValueError('Introduce una dirección de destinatario válida.')
            item.update(discount=value, discount_kind=kind, recipient=recipient, revision=item['revision'] + 1)
            marker = 'panel:' + key + ':' + str(item['revision'])
            payload = draft_payload(item['proposal'], self.config, marker, value, kind)
            payload['toRecipients'] = [{'emailAddress': {'address': recipient}}]
            if item['test']:
                payload['subject'] = '[PRUEBA] ' + payload['subject']
            html = payload['body']['content']
            for attachment in payload.get('attachments', []):
                html = html.replace('cid:' + attachment['contentId'], 'data:' + attachment['contentType'] + ';base64,' + attachment['contentBytes'])
            self.previews = {token: record for token, record in self.previews.items() if record['key'] != key and record['expires'] > time.time()}
            token = secrets.token_urlsafe(32)
            self.previews[token] = {'key': key, 'revision': item['revision'], 'expires': time.time() + 600, 'payload': payload, 'marker': marker}
            self.save()
            return {'approval_id': token, 'revision': item['revision'], 'html': html, 'to': recipient, 'subject': payload['subject'], 'amounts': amount, 'test': item['test']}

    def change_recipient(self, key, fields):
        """Conserva el contenido revisado y emite una confirmación nueva sin Graph."""
        with self.lock:
            item = self.state['items'].get(key)
            old_token = fields.get('approval_id', '')
            previous = self.previews.get(old_token)
            if (self.draining or not item or item['status'] != 'pending' or not previous
                    or previous['key'] != key or previous['expires'] < time.time()
                    or previous['revision'] != item['revision']):
                raise ValueError('La vista previa ha caducado o ha cambiado. Genera otra vista previa.')
            if item.get('test') or item.get('send_blocked'):
                raise ValueError('El destinatario de una prueba no se puede modificar.')
            recipient = str(fields.get('recipient', '')).strip()
            if len(recipient) > 200 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', recipient):
                raise ValueError('Introduce una dirección de destinatario válida.')
            record = copy.deepcopy(previous)
            record['payload']['toRecipients'] = [{'emailAddress': {'address': recipient}}]
            item.update(recipient=recipient, revision=item['revision'] + 1)
            record['revision'] = item['revision']
            self.save()
            # El token anterior deja de ser válido; no se prolonga su caducidad.
            self.previews = {token: entry for token, entry in self.previews.items() if entry['key'] != key}
            token = secrets.token_urlsafe(32)
            self.previews[token] = record
            return {'approval_id': token, 'revision': item['revision'], 'to': recipient}

    def send_confirmed(self, key, fields):
        if fields.get('confirmed') is not True:
            raise ValueError('Debes confirmar el envío después de revisar la vista previa.')
        with self.graph_lock:
            with self.lock:
                if self.draining:
                    raise ValueError('La aplicación se está cerrando o actualizando. No se ha iniciado el envío.')
                if self.state['items'].get(key, {}).get('send_blocked'):
                    raise ValueError('El envío está bloqueado permanentemente para esta prueba de recepción.')
                if not self.graph or not self.graph.can_send:
                    raise ValueError('Vuelve a conectar Outlook. Microsoft debe conceder el permiso de envío en esa sesión.')
                token = fields.get('approval_id', '')
                preview = self.previews.get(token)
                item = self.state['items'].get(key)
                if not preview or preview['key'] != key or preview['expires'] < time.time() or not item or item['status'] != 'pending' or preview['revision'] != item['revision']:
                    raise ValueError('La confirmación ha caducado o la propuesta ha cambiado. Genera otra vista previa.')
                self.previews.pop(token)
                item.update(status='creating', marker=preview['marker'], issue='')
                self.save()
                payload = copy.deepcopy(preview['payload'])
            stage = 'creating'
            try:
                draft = self.graph.request('POST', '/me/messages', payload)
                with self.lock:
                    item.update(status='sending', draft_id=draft['id'], web_link=draft.get('webLink'))
                    self.save()
                stage = 'sending'
                result = self.graph.request('POST', '/me/messages/' + quote(draft['id'], safe='') + '/send')
                if not result.get('accepted'):
                    raise RuntimeError('Microsoft no ha confirmado la aceptación del envío.')
                with self.lock:
                    item.update(status='submitted', issue='Microsoft ha aceptado la solicitud de envío. Se comprobará su estado en Outlook.')
                    self.save()
                return {'status': 'submitted'}
            except Exception as error:
                with self.lock:
                    rejected = isinstance(error, GraphError) and 400 <= error.status < 500 and error.status != 408
                    item['status'] = 'send_failed' if rejected else 'uncertain'
                    item['issue'] = ('No se ha completado el envío. ' if rejected else 'El resultado es incierto; no se repetirá automáticamente. ') + str(error)
                    if stage == 'creating' and rejected:
                        item['issue'] += ' No se ha creado el borrador.'
                    self.save()
                raise

    def prepare_shutdown(self):
        # graph_lock cubre toda creación/envío y la consulta: la barrera solo
        # se confirma después de terminar la operación activa y guardar estado.
        if not self.graph_lock.acquire(blocking=False):
            return {'ready': False}
        try:
            with self.lock:
                self.draining = True
                self.save()
                self.stop.set()
                self.wake.set()
            return {'ready': True}
        finally:
            self.graph_lock.release()

    def run_monitor(self):
        while not self.stop.is_set():
            self.wake.clear()
            self.synchronize()
            self.wake.wait(self.config['poll_seconds'])


def serve(config, root, port=8765, open_browser=False, data_dir=None, desktop_key=None, ready=None):
    root = Path(root)
    identity = hashlib.sha256(config['expected_mailbox'].lower().encode()).hexdigest()[:20]
    directory = Path(data_dir) / 'panel' / identity if data_dir else root / '.local/panel' / identity
    from .vault import SessionVault
    app = Panel(config, directory, root, vault=SessionVault(directory, config))
    origin = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, data, content_type='application/json; charset=utf-8'):
            body = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{server.server_port}' and (not desktop_key or secrets.compare_digest(self.headers.get('X-Desktop-Key', ''), desktop_key))

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {'error': 'Abre el panel desde la dirección local indicada.'})
            path = urlparse(self.path).path
            if path == '/api/state':
                return self.reply(200, app.public_state())
            if path == '/api/session':
                return self.reply(200, {'csrf': app.csrf})
            if path == '/api/notifications' and desktop_key:
                return self.reply(200, {'items': app.notifications()})
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.reply(404, {'error': 'No encontrado.'})
            name, content_type = files[path]
            return self.reply(200, (root / 'panel' / name).read_bytes(), content_type)

        def do_POST(self):
            if not self.valid_host() or self.headers.get('Origin') not in (None, origin) or not secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), app.csrf):
                return self.reply(403, {'error': 'Vuelve a abrir el panel para continuar.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 65536:
                    raise ValueError('La solicitud es demasiado grande.')
                fields = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(fields, dict):
                    raise ValueError('Solicitud no válida.')
                path = urlparse(self.path).path
                if path == '/api/connect':
                    result = app.start_auth()
                elif path == '/api/import-test':
                    result = app.import_test()
                elif path == '/api/reception-test/start':
                    result = app.start_reception_test()
                elif path == '/api/reception-test/stop':
                    result = app.stop_reception_test()
                elif path == '/api/sync':
                    app.wake.set()
                    result = {'checked': True}
                elif path == '/api/notifications/ack' and desktop_key:
                    keys = fields.get('keys', [])
                    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
                        raise ValueError('Referencias no válidas.')
                    app.acknowledge(keys)
                    result = {'acknowledged': True}
                elif path == '/api/shutdown' and desktop_key:
                    if not app.prepare_shutdown()['ready']:
                        return self.reply(409, {'error': 'Hay una operación en curso. Espera a que termine.'})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    result = {'stopping': True}
                elif path == '/api/prepare-shutdown' and desktop_key:
                    result = app.prepare_shutdown()
                else:
                    action = re.fullmatch(r'/api/items/([a-f0-9]{64})/(preview|recipient|send)', path)
                    if not action:
                        return self.reply(404, {'error': 'No encontrado.'})
                    key, command = action.groups()
                    handlers = {'preview': app.preview, 'recipient': app.change_recipient, 'send': app.send_confirmed}
                    result = handlers[command](key, fields)
                self.reply(200, result)
            except Exception as error:
                self.reply(400, {'error': str(error)})

    with app.store.lock():
        server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        origin = f'http://127.0.0.1:{server.server_port}'
        server.daemon_threads = True
        threading.Thread(target=app.run_monitor, daemon=True).start()
        if ready:
            ready(origin)
        else:
            print('Panel de renovaciones: ' + origin, flush=True)
            print('Los correos nuevos quedan pendientes de revisión. Solo se envía después de confirmar en el panel.', flush=True)
        if open_browser:
            webbrowser.open(origin)
        try:
            server.serve_forever()
        finally:
            app.stop.set()
            app.wake.set()
            server.server_close()
