import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from automation.panel import Panel
from automation.parser import read_eml
from automation.graph import MicrosoftSession, GraphError

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'tests/fixtures'


class Mailbox:
    def __init__(self):
        self.can_send = True
        self.calls = []
        self.fail = None
        self.sent = False
        self.incoming = []
        self.message = None

    def request(self, method, path, payload=None, headers=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if method == 'POST' and path == '/me/messages':
            if self.fail == 'create':
                raise TimeoutError('Respuesta de creación perdida')
            return {'id': 'draft-123', 'isDraft': True, 'webLink': 'https://outlook.office.com/mail/draft-123'}
        if method == 'POST' and path.endswith('/send'):
            if self.fail == 'send':
                raise TimeoutError('Respuesta de envío perdida')
            if self.fail == 'forbidden':
                raise GraphError(403, 'AccessDenied')
            return {'accepted': True}
        if '/me/mailFolders/inbox/messages?' in path:
            return {'value': self.incoming}
        if 'singleValueExtendedProperties' in path:
            return {'value': []}
        if '$select=id,subject,from,body' in path:
            return self.message
        if method == 'GET' and path.startswith('/me/messages/draft-123'):
            return {'id': 'draft-123', 'isDraft': not self.sent, 'sentDateTime': '2026-10-13T10:00:00Z' if self.sent else None, 'webLink': 'https://outlook.office.com/mail/draft-123'}
        raise AssertionError((method, path))


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = json.loads((FIXTURES / 'config.demo.json').read_text())
        self.config['expected_mailbox'] = 'comercial@example.com'
        self.panel = Panel(self.config, self.directory.name, ROOT)
        self.graph = Mailbox()
        self.panel.graph = self.graph
        self.message = read_eml(FIXTURES / 'renovacion-ficticia.eml')

    def pending(self, test=False):
        return self.panel.ingest(self.message, test=test)

    def preview(self, key, **values):
        return self.panel.preview(key, {'discount': '10', 'kind': 'percent', **values})

    def confirm(self, key, preview):
        return self.panel.send_confirmed(key, {'confirmed': True, 'approval_id': preview['approval_id']})

    def writes(self):
        return [call for call in self.graph.calls if call[0] != 'GET']

    def test_llegada_solo_registra_pendiente_y_no_crea_correo(self):
        key = self.pending()
        self.assertEqual(self.panel.state['items'][key]['status'], 'pending')
        self.assertEqual(self.panel.state['items'][key]['proposal']['pvp_cents'], 21960)
        self.assertEqual(self.panel.state['items'][key]['proposal']['pvd_cents'], 17859)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.pending(), key)
        self.assertEqual(len(self.panel.state['items']), 1)

    def test_vista_previa_no_es_envio_y_oculta_el_coste(self):
        key = self.pending()
        result = self.preview(key)
        self.assertEqual(result['amounts']['total'], 19764)
        self.assertEqual(result['to'], 'cliente@example.com')
        self.assertIn('197,64 €', result['html'])
        for forbidden in ('178,59', '163,98', 'Proveedor de ejemplo', 'supplier', 'SubscriptionId'):
            self.assertNotIn(forbidden, result['html'])
        self.assertEqual(self.writes(), [])

    def test_no_envia_sin_confirmacion_o_sin_permiso(self):
        key = self.pending()
        result = self.preview(key)
        with self.assertRaisesRegex(ValueError, 'confirmar'):
            self.panel.send_confirmed(key, {'approval_id': result['approval_id']})
        self.graph.can_send = False
        with self.assertRaisesRegex(ValueError, 'permiso de envío'):
            self.confirm(key, result)
        self.assertEqual(self.writes(), [])

    def test_envia_exactamente_la_propuesta_confirmada_una_sola_vez(self):
        key = self.pending()
        result = self.preview(key, recipient='nuevo@example.com')
        answer = self.confirm(key, result)
        self.assertEqual(answer['status'], 'submitted')
        self.assertEqual(len(self.writes()), 2)
        draft = self.writes()[0][2]
        self.assertEqual(draft['body']['content'], result['html'])
        self.assertEqual(draft['toRecipients'][0]['emailAddress']['address'], result['to'])
        self.assertEqual(draft['subject'], result['subject'])
        self.assertEqual(self.writes()[1][1], '/me/messages/draft-123/send')
        with self.assertRaises(ValueError):
            self.confirm(key, result)
        self.assertEqual(len(self.writes()), 2)

    def test_cambio_de_descuento_invalida_la_confirmacion_anterior(self):
        key = self.pending()
        old = self.preview(key)
        new = self.preview(key, discount='20', kind='amount')
        with self.assertRaises(ValueError):
            self.confirm(key, old)
        self.assertEqual(self.writes(), [])
        self.assertEqual(new['amounts']['total'], 19960)
        self.confirm(key, new)

    def test_cambio_destinatario_conserva_contenido_y_revoca_token_anterior(self):
        key = self.pending()
        old = self.preview(key)
        record = copy.deepcopy(self.panel.previews[old['approval_id']])
        with patch('automation.panel.draft_payload', side_effect=AssertionError('No regenerar el correo')):
            new = self.panel.change_recipient(key, {'approval_id': old['approval_id'], 'recipient': 'nuevo@example.com'})
        updated = self.panel.previews[new['approval_id']]
        self.assertEqual(updated['expires'], record['expires'])
        expected = copy.deepcopy(record['payload'])
        expected['toRecipients'] = [{'emailAddress': {'address': 'nuevo@example.com'}}]
        self.assertEqual(updated['payload'], expected)
        self.assertEqual(self.panel.store.read()['items'][key]['recipient'], 'nuevo@example.com')
        self.assertEqual(self.writes(), [])
        with self.assertRaises(ValueError): self.confirm(key, old)
        self.confirm(key, new)
        self.assertEqual(self.writes()[0][2], expected)
        self.assertEqual(len(self.writes()), 2)

    def test_cambio_destinatario_rechaza_pruebas_caducidad_y_direcciones_invalidas(self):
        key = self.pending()
        preview = self.preview(key)
        for recipient in ('', 'incorrecto', 'a@example.com;b@example.com', 'x'*201+'@example.com'):
            with self.subTest(recipient=recipient), self.assertRaises(ValueError):
                self.panel.change_recipient(key, {'approval_id': preview['approval_id'], 'recipient': recipient})
        self.panel.previews[preview['approval_id']]['expires'] = 0
        with self.assertRaises(ValueError):
            self.panel.change_recipient(key, {'approval_id': preview['approval_id'], 'recipient': 'otro@example.com'})
        test_key = self.pending(test=True)
        test_preview = self.preview(test_key)
        with self.assertRaises(ValueError):
            self.panel.change_recipient(test_key, {'approval_id': test_preview['approval_id'], 'recipient': 'otro@example.com'})
        self.assertEqual(self.writes(), [])

    def test_cambio_destinatario_no_reutiliza_preview_de_otro_item_o_envio_iniciado(self):
        key = self.pending()
        preview = self.preview(key)
        other = self.panel.ingest({**self.message, 'id': 'otra-renovacion'})
        with self.assertRaises(ValueError):
            self.panel.change_recipient(other, {'approval_id': preview['approval_id'], 'recipient': 'otro@example.com'})
        self.panel.state['items'][key]['status'] = 'creating'
        with self.assertRaises(ValueError):
            self.panel.change_recipient(key, {'approval_id': preview['approval_id'], 'recipient': 'otro@example.com'})
        self.assertEqual(self.writes(), [])

    def test_confirmacion_caducada_no_envia(self):
        key = self.pending()
        result = self.preview(key)
        self.panel.previews[result['approval_id']]['expires'] = 0
        with self.assertRaises(ValueError):
            self.confirm(key, result)
        self.assertEqual(self.writes(), [])

    def test_prueba_no_admite_enviar_a_un_cliente_real(self):
        key = self.pending(test=True)
        result = self.preview(key, recipient='cliente-real@example.com')
        self.assertEqual(result['to'], self.config['expected_mailbox'])
        self.assertTrue(result['subject'].startswith('[PRUEBA]'))
        self.confirm(key, result)
        self.assertEqual(self.writes()[0][2]['toRecipients'][0]['emailAddress']['address'], self.config['expected_mailbox'])

    def test_resultado_incierto_no_reintenta(self):
        for stage in ('create', 'send'):
            with self.subTest(stage=stage):
                with tempfile.TemporaryDirectory() as directory:
                    panel = Panel(self.config, directory, ROOT)
                    graph = Mailbox()
                    graph.fail = stage
                    panel.graph = graph
                    key = panel.ingest(self.message)
                    result = panel.preview(key, {'kind': 'none'})
                    with self.assertRaises(TimeoutError):
                        panel.send_confirmed(key, {'confirmed': True, 'approval_id': result['approval_id']})
                    self.assertEqual(panel.state['items'][key]['status'], 'uncertain')
                    before = len(graph.calls)
                    with self.assertRaises(ValueError):
                        panel.send_confirmed(key, {'confirmed': True, 'approval_id': result['approval_id']})
                    self.assertEqual(len(graph.calls), before)

    def test_aceptado_no_significa_enviado_hasta_verificar_outlook(self):
        key = self.pending()
        self.confirm(key, self.preview(key))
        self.panel.reconcile(self.graph)
        self.assertEqual(self.panel.state['items'][key]['status'], 'submitted')
        self.graph.sent = True
        self.panel.reconcile(self.graph)
        self.assertEqual(self.panel.state['items'][key]['status'], 'sent')

    def test_reinicio_no_repite_una_operacion_interrumpida(self):
        key = self.pending()
        self.panel.state['items'][key]['status'] = 'sending'
        self.panel.save()
        restarted = Panel(self.config, self.directory.name, ROOT)
        self.assertEqual(restarted.state['items'][key]['status'], 'uncertain')
        self.assertEqual(restarted.previews, {})

    def test_monitor_detecta_y_avisa_sin_crear_borradores(self):
        self.panel.state['monitor_started_at'] = '2026-10-01T00:00:00Z'
        self.graph.message = self.message
        self.graph.incoming = [self.message]
        self.panel.tick()
        self.assertEqual(len(self.panel.state['items']), 1)
        self.assertEqual(self.writes(), [])
        self.assertIsNotNone(self.panel.state['last_scan'])

    def test_descuento_invalido_no_prepara_envio(self):
        key = self.pending()
        for fields in ({'discount':'101','kind':'percent'}, {'discount':'300','kind':'amount'}, {'discount':'NaN','kind':'percent'}, {'recipient':'uno@example.com;dos@example.com'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.preview(key, **fields)
        self.assertEqual(self.writes(), [])


class SendTransportTests(unittest.TestCase):
    def test_mail_send_es_optativo_y_se_comprueba_el_scope_concedido(self):
        graph = MicrosoftSession({'tenant_id':'organizations'}, enable_send=True)
        graph.remember({'access_token':'ficticio','expires_in':3600,'scope':'User.Read Mail.ReadWrite'})
        self.assertFalse(graph.can_send)
        with self.assertRaises(ValueError):
            graph.request('POST', '/me/messages/test/send')
        graph.remember({'access_token':'ficticio','expires_in':3600,'scope':'User.Read Mail.ReadWrite Mail.Send'})
        self.assertTrue(graph.can_send)

    def test_respuesta_202_sin_cuerpo_se_acepta(self):
        graph = MicrosoftSession({'tenant_id':'organizations'}, enable_send=True)
        graph.remember({'access_token':'ficticio','expires_in':3600,'scope':'Mail.Send'})
        class EmptyResponse:
            status = 202
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b''
        with patch('automation.graph.urlopen', return_value=EmptyResponse()) as request:
            self.assertEqual(graph.request('POST', '/me/messages/test/send'), {'accepted':True})
            self.assertEqual(request.call_args.args[0].data, b'')


if __name__ == '__main__':
    unittest.main()
