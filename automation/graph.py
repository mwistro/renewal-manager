"""Cliente Microsoft Graph: lectura, creación y edición de borradores."""
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

SCOPES = 'https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.ReadWrite offline_access'


class GraphError(RuntimeError):
    def __init__(self, status, code):
        super().__init__(f'Microsoft Graph: {status} ({code}).')
        self.status = status


class LoginRequired(RuntimeError):
    pass


class MicrosoftSession:
    def __init__(self, config, log=print, enable_send=False):
        if config.get('restrict_organization'):
            from .organization import require_organization
            require_organization(config)
        self.config, self.log = config, log
        self.token, self.refresh, self.expires = None, None, 0
        self.enable_send, self.can_send = enable_send, False
        self.scopes = SCOPES + (' https://graph.microsoft.com/Mail.Send' if enable_send else '')
        self.device = None
        self.on_refresh = None
        tenant = config['tenant_id']
        if not re.fullmatch(r'[a-zA-Z0-9.-]+', tenant):
            raise ValueError('Identificador de organización no válido.')
        self.authority = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/'

    def oauth(self, endpoint, fields):
        request = Request(self.authority + endpoint, data=urlencode({'client_id': self.config['client_id'], **fields}).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            return json.loads(error.read())

    def remember(self, result):
        if 'access_token' not in result:
            if result.get('error') in ('invalid_grant', 'interaction_required', 'login_required', 'consent_required'):
                raise LoginRequired('Microsoft necesita que vuelvas a iniciar sesión. Pulsa «Conectar Outlook».')
            raise RuntimeError('No se ha podido iniciar sesión: ' + result.get('error', 'respuesta no válida'))
        self.token = result['access_token']
        self.refresh = result.get('refresh_token', self.refresh)
        self.expires = time.time() + int(result['expires_in']) - 90
        if 'scope' in result:
            self.can_send = self.enable_send and any(scope.split('/')[-1].lower() == 'mail.send' for scope in result['scope'].split())
        if self.on_refresh:
            self.on_refresh(self)

    def begin_login(self):
        result = self.oauth('devicecode?mkt=es-ES', {'scope': self.scopes})
        if 'device_code' not in result:
            raise RuntimeError('No se puede iniciar la conexión: ' + result.get('error', 'configuración no válida'))
        self.device = {**result, 'deadline': time.time() + result['expires_in']}
        return {key: result[key] for key in ('verification_uri', 'user_code', 'expires_in')}

    def complete_login(self):
        if not self.device:
            raise RuntimeError('Primero inicia la conexión.')
        result = self.device
        deadline = result['deadline']
        interval = result.get('interval', 5)
        while time.time() < deadline:
            time.sleep(interval)
            answer = self.oauth('token', {'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'device_code': result['device_code']})
            if answer.get('error') == 'authorization_pending':
                continue
            if answer.get('error') == 'slow_down':
                interval += 5
                continue
            self.remember(answer)
            return
        raise RuntimeError('El código de conexión ha caducado. Vuelve a ejecutar el comando.')

    def login(self):
        result = self.begin_login()
        self.log(f'Abre {result["verification_uri"]} e introduce el código {result["user_code"]}. Inicia sesión con la cuenta que recibirá las renovaciones.')
        self.complete_login()

    def access_token(self):
        if self.token and time.time() < self.expires:
            return self.token
        if not self.refresh:
            raise RuntimeError('Inicia sesión antes de acceder al correo.')
        self.remember(self.oauth('token', {'grant_type': 'refresh_token', 'refresh_token': self.refresh, 'scope': self.scopes}))
        return self.token

    def request(self, method, path, payload=None, headers=None, _retried=False):
        url = path if path.startswith('https://') else 'https://graph.microsoft.com/v1.0' + path
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com' or not parsed.path.startswith('/v1.0/'):
            raise ValueError('Destino Graph no válido.')
        sending = method == 'POST' and bool(re.fullmatch(r'/v1\.0/me/messages/[^/]+/send', parsed.path))
        if sending and not (self.enable_send and self.can_send):
            raise ValueError('Solo se permite enviar con Mail.Send autorizado y desde la confirmación del panel.')
        if method == 'POST' and parsed.path != '/v1.0/me/messages' and not sending:
            raise ValueError('Solo se permite crear un borrador.')
        if method == 'PATCH' and not re.fullmatch(r'/v1\.0/me/messages/[^/]+', parsed.path):
            raise ValueError('Solo se permite editar un borrador.')
        if method not in ('GET', 'POST', 'PATCH'):
            raise ValueError('Operación no permitida.')
        token = self.access_token()
        if sending and not self.can_send:
            raise ValueError('Microsoft ya no concede Mail.Send. Vuelve a autorizar el envío.')
        request = Request(url, method=method, data=json.dumps(payload).encode() if payload is not None else (b'' if method == 'POST' else None), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Prefer': 'IdType="ImmutableId"', **(headers or {})})
        try:
            with urlopen(request, timeout=45) as response:
                raw = response.read()
                return json.loads(raw) if raw else {'accepted': response.status == 202}
        except HTTPError as error:
            if error.code == 401:
                self.expires = 0
                if method == 'GET' and not _retried:
                    return self.request(method, path, payload, headers, _retried=True)
                raise LoginRequired('La sesión de Microsoft ha caducado. Vuelve a conectar Outlook.') from None
            try:
                code = json.loads(error.read()).get('error', {}).get('code', 'Error')
            except (ValueError, AttributeError):
                code = 'Error'
            raise GraphError(error.code, code) from None

    def verify_mailbox(self):
        profile = self.request('GET', '/me?$select=id,mail,userPrincipalName')
        expected = self.config['expected_mailbox'].lower()
        addresses = {str(profile.get(k) or '').lower() for k in ('mail', 'userPrincipalName')}
        if expected not in addresses:
            raise LoginRequired('La cuenta conectada no coincide con la cuenta configurada. Vuelve a conectar Outlook con la cuenta indicada. No se ha accedido a los mensajes.')
        return profile['id']
