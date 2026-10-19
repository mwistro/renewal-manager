import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from .parser import ReviewRequired
from .proposal import PROPERTY_ID, draft_payload, matches, prepare, source_key, render


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'estado.json'

    @contextmanager
    def lock(self):
        with open(self.directory / 'proceso.lock', 'a+b') as handle:
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError('Hay otra operación en curso. Vuelve a intentarlo en unos segundos.') from None
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self):
        if not self.path.exists():
            return {'started_at': utcnow(), 'last_scan': None, 'messages': {}}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def save(self, state):
        temporary = self.path.with_suffix('.tmp')
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


def recover(graph, key):
    query = urlencode({'$filter': f"singleValueExtendedProperties/Any(ep: ep/id eq '{PROPERTY_ID}' and ep/value eq '{key}')", '$select': 'id,isDraft,changeKey,webLink', '$top': '2'})
    found = graph.request('GET', '/me/messages?' + query)['value']
    if len(found) > 1:
        raise RuntimeError('Se han encontrado varios mensajes para la misma renovación. Revisión manual necesaria.')
    return found[0] if found else None


class Worker:
    def __init__(self, graph, config, store, mailbox_id, log=print):
        self.graph, self.config, self.store, self.mailbox_id, self.log = graph, config, store, mailbox_id, log

    def process(self, message, state, test_mode=False):
        if not matches(message, self.config):
            return
        if test_mode:
            if not self.config.get('expected_mailbox'):
                raise ValueError('Falta la cuenta de destino para la prueba.')
            message = {**message, 'id': 'prueba-local:' + message['id']}
        key = source_key(message)
        record = state['messages'].get(key)
        if record and record['status'] not in ('creating', 'uncertain'):
            return
        if record:
            found = recover(self.graph, key)
            if found:
                record.update(status='draft' if found['isDraft'] else 'sent', draft_id=found['id'], change_key=found.get('changeKey'), web_link=found.get('webLink'))
                self.store.save(state)
            # No se repite un POST cuyo resultado sea desconocido.
            return
        try:
            proposal = prepare(message, self.config)
        except ReviewRequired as error:
            state['messages'][key] = {'status': 'review', 'reason': str(error), 'source_id': message['id']}
            self.store.save(state)
            self.log(f'Revisión necesaria [{key[:12]}]: {error}')
            return
        payload = draft_payload(proposal, self.config, key)
        if test_mode:
            payload['toRecipients'] = [{'emailAddress': {'address': self.config['expected_mailbox']}}]
            payload['subject'] = '[PRUEBA] ' + payload['subject']
        record = {'status': 'creating', 'proposal': proposal, 'source_id': message['id'], 'price_basis': self.config['price_basis'], 'discount': '0', 'discount_kind': 'percent', 'company': {'name': self.config['company']['name'], 'email': self.config['company']['email']}, 'tax': self.config['tax'], 'has_logo': bool(payload.get('attachments'))}
        state['messages'][key] = record
        self.store.save(state)
        try:
            draft = self.graph.request('POST', '/me/messages', payload)
        except Exception:
            record['status'] = 'uncertain'
            self.store.save(state)
            self.log(f'Resultado pendiente de comprobar [{key[:12]}]. No se repetirá la creación automáticamente.')
            raise
        record.update(status='draft', draft_id=draft['id'], change_key=draft.get('changeKey'), web_link=draft.get('webLink'))
        self.store.save(state)
        self.log(f'Borrador creado en Outlook [{key[:12]}], con el PVP de renovación y sin descuento.')

    def tick(self):
        with self.store.lock():
            state = self.store.read()
            if state.get('mailbox_id', self.mailbox_id) != self.mailbox_id:
                raise RuntimeError('El estado pertenece a otra cuenta de correo.')
            state['mailbox_id'] = self.mailbox_id
            self.store.save(state)
            # Revisa también creaciones inciertas fuera del intervalo de consulta.
            for key, record in state['messages'].items():
                if record['status'] in ('creating', 'uncertain'):
                    found = recover(self.graph, key)
                    if found:
                        record.update(status='draft' if found['isDraft'] else 'sent', draft_id=found['id'], change_key=found.get('changeKey'), web_link=found.get('webLink'))
                        self.store.save(state)
            scan_started = utcnow()
            since = datetime.fromisoformat(state['last_scan'] or state['started_at']) - timedelta(minutes=5)
            # Evita tratar el historial anterior a la primera activación.
            since = max(since, datetime.fromisoformat(state['started_at']))
            query = urlencode({'$filter': 'receivedDateTime ge ' + since.isoformat().replace('+00:00', 'Z'), '$select': 'id,subject,from,receivedDateTime,isDraft', '$top': '50'})
            path = '/me/mailFolders/inbox/messages?' + query
            while path:
                page = self.graph.request('GET', path)
                for summary in page['value']:
                    if not matches(summary, self.config):
                        continue
                    key = source_key(summary)
                    if key in state['messages']:
                        continue
                    message = self.graph.request('GET', '/me/messages/' + quote(summary['id'], safe='') + '?$select=id,subject,from,body,isDraft')
                    self.process(message, state)
                path = page.get('@odata.nextLink')
            state['last_scan'] = scan_started
            self.store.save(state)

    def run(self):
        self.log('Automatización activa. Se comprobará la bandeja de entrada cada ' + str(self.config['poll_seconds']) + ' segundos. Pulsa Ctrl+C para detenerla.')
        while True:
            try:
                self.tick()
            except Exception as error:
                self.log('No se ha completado la comprobación: ' + str(error))
            time.sleep(self.config['poll_seconds'])


def apply_discount(graph, store, config, mailbox_id, reference, value, kind):
    with store.lock():
        state = store.read()
        if state.get('mailbox_id') != mailbox_id:
            raise RuntimeError('El estado pertenece a otra cuenta.')
        keys = [key for key in state['messages'] if key.startswith(reference)]
        if not reference or len(keys) != 1:
            raise ValueError('Referencia ausente o ambigua. Consulta el comando lista.')
        key = keys[0]
        record = state['messages'][key]
        if record['status'] != 'draft':
            raise ValueError('La referencia no corresponde a un borrador preparado.')
        path = '/me/messages/' + quote(record['draft_id'], safe='')
        current = graph.request('GET', path + '?$select=id,isDraft,changeKey,body')
        if not current.get('isDraft'):
            raise ValueError('El mensaje ya no es un borrador. No se ha modificado.')
        if not record.get('change_key') or current.get('changeKey') != record['change_key']:
            raise ValueError('El borrador ha cambiado en Outlook. Aplica el descuento allí para conservar tus cambios.')
        if not current.get('@odata.etag'):
            raise ValueError('No se puede comprobar la versión del borrador. No se ha modificado.')
        frozen = {**config, 'price_basis': record['price_basis'], 'company': record['company'], 'tax': record['tax']}
        body = {'contentType': 'HTML', 'content': render(record['proposal'], frozen, value, kind, record['has_logo'])}
        updated = graph.request('PATCH', path, {'body': body}, headers={'If-Match': current['@odata.etag']})
        record.update(discount=str(value), discount_kind=kind, change_key=updated.get('changeKey'))
        store.save(state)
