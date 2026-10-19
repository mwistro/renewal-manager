import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from automation.graph import MicrosoftSession, LoginRequired
from automation.panel import Panel, serve
from automation.vault import SessionVault

ROOT = Path(__file__).resolve().parent.parent


class InlineThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class LoginTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = json.loads((ROOT/'tests/fixtures/config.demo.json').read_text())
        self.config.update(client_id='app-ficticia', expected_mailbox='prueba@example.com')
        self.vault = Mock()
        self.panel = Panel(self.config, directory.name, ROOT, vault=self.vault)
        self.panel.synchronize = Mock()

    def test_panel_de_navegador_usa_boveda_protegida_tambien_sin_electron(self):
        with patch('automation.panel.Panel') as factory, patch('automation.panel.ThreadingHTTPServer'), patch('automation.panel.threading.Thread'):
            serve(self.config,ROOT,data_dir=self.panel.store.directory,ready=Mock())
        self.assertIsInstance(factory.call_args.kwargs['vault'],SessionVault)

    def test_un_login_solicita_envio_y_guarda_sesion_solo_despues_de_verificar_buzon(self):
        session = MicrosoftSession(self.config, enable_send=True)
        self.assertIn('Mail.Send', session.scopes)
        self.assertIn('offline_access', session.scopes)
        session.begin_login = Mock(return_value={'verification_uri':'https://login.microsoft.com/device', 'user_code':'FICTICIO'})
        session.complete_login = Mock()
        session.verify_mailbox = Mock(return_value='buzon-ficticio')
        session.request = Mock(return_value={})
        session.refresh = 'refresh-ficticio'
        session.can_send = True
        self.vault.save.side_effect = lambda value: session.verify_mailbox.assert_called_once()
        with patch('automation.panel.MicrosoftSession', return_value=session) as factory, patch('automation.panel.threading.Thread', InlineThread):
            self.panel.start_auth()
        factory.assert_called_once_with(self.config, enable_send=True)
        self.vault.save.assert_called_once_with(session)
        self.assertTrue(self.panel.public_state()['can_send'])
        self.assertNotIn('refresh-ficticio', json.dumps(self.panel.public_state()))
        self.assertTrue(all(call.args[0]=='GET' for call in session.request.call_args_list))

    def test_cuenta_distinta_no_abre_panel_ni_guarda_sesion(self):
        session = Mock()
        session.begin_login.return_value = {}
        session.verify_mailbox.side_effect = LoginRequired('La cuenta no coincide')
        with patch('automation.panel.MicrosoftSession', return_value=session), patch('automation.panel.threading.Thread', InlineThread):
            self.panel.start_auth()
        self.assertFalse(self.panel.public_state()['connected'])
        self.vault.save.assert_not_called()
        session.request.assert_not_called()

    def test_restaura_sesion_antigua_con_envio_sin_nuevo_codigo(self):
        self.vault.load.return_value = {'refresh_token':'anterior-ficticio','enable_send':False}
        session = Mock(refresh=None,can_send=True)
        session.verify_mailbox.return_value = 'buzon-ficticio'
        with patch('automation.panel.MicrosoftSession', return_value=session) as factory:
            self.panel.restore_session()
        factory.assert_called_once_with(self.config, enable_send=True)
        self.assertEqual(session.refresh,'anterior-ficticio')
        session.begin_login.assert_not_called()
        session.verify_mailbox.assert_called_once()
        self.vault.save.assert_called_once_with(session)
        self.assertTrue(self.panel.public_state()['can_send'])

    def test_fallo_de_proteccion_no_abre_panel_ni_guarda_en_claro(self):
        session = Mock()
        session.begin_login.return_value = {}
        session.verify_mailbox.return_value = 'buzon-ficticio'
        self.vault.save.side_effect = OSError('No se puede proteger la sesión')
        with patch('automation.panel.MicrosoftSession', return_value=session), patch('automation.panel.threading.Thread', InlineThread):
            self.panel.start_auth()
        self.assertFalse(self.panel.public_state()['connected'])
        self.assertEqual(self.panel.auth['status'],'error')
