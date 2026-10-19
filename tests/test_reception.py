import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from automation.panel import Panel
from automation.parser import read_eml
from test_panel import Mailbox

ROOT = Path(__file__).resolve().parent.parent


class ReceptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = json.loads((ROOT/'tests/fixtures/config.demo.json').read_text())
        self.config['expected_mailbox'] = 'prueba@example.com'
        self.panel = Panel(self.config, self.temp.name, ROOT)
        self.panel.graph = Mailbox()
        self.panel.state['monitor_started_at'] = '2026-01-01T00:00:00+00:00'
        trial = self.panel.start_reception_test()
        self.message = read_eml(ROOT/'tests/fixtures/renovacion-ficticia.eml')
        self.message.update(subject=trial['subject'], receivedDateTime=datetime.now(timezone.utc).isoformat())
        self.message['from'] = {'emailAddress': {'address': self.config['expected_mailbox']}}

    def test_no_activa_sin_conexion_y_no_cambia_codigo_activo(self):
        code = self.panel.reception_test_state()['subject']
        self.assertEqual(self.panel.start_reception_test()['subject'], code)
        self.panel.graph = None
        with self.assertRaises(ValueError): self.panel.start_reception_test()

    def test_llega_por_consulta_y_avisa_sin_escribir_en_graph(self):
        graph = self.panel.graph
        graph.incoming = [self.message]; graph.message = self.message
        self.panel.tick()
        event, = self.panel.notifications()
        self.assertTrue(event['reception_test'])
        item = self.panel.public_state()['items'][0]
        self.assertTrue(item['send_blocked'])
        self.assertEqual(item['proposal']['pvp_cents'], 21960)
        self.assertTrue(all(method == 'GET' for method, _, _ in graph.calls))

    def test_rechaza_otro_remitente_asunto_fecha_y_borrador(self):
        for change in (
            {'from': {'emailAddress': {'address': 'otro@example.com'}}},
            {'from': {'emailAddress': {'address': 'otro@supplier.example.com'}}},
            {'subject': 'RV: ' + self.message['subject']},
            {'subject': 'Renovación de licencia sin código'},
            {'receivedDateTime': '2020-01-01T00:00:00Z'},
            {'receivedDateTime': (datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()},
            {'receivedDateTime': ''}, {'isDraft': True},
        ):
            with self.subTest(change=change):
                self.assertIsNone(self.panel.ingest({**self.message, **change}))
        self.assertEqual(self.panel.notifications(), [])

    def test_recupera_recibido_en_plazo_despues_de_caducar_y_reiniciar(self):
        self.panel.save()
        restored = Panel(self.config, self.temp.name, ROOT)
        future = datetime.now(timezone.utc).timestamp()+3*3600
        with patch('automation.panel.time.time', return_value=future):
            self.assertFalse(restored.reception_test_state()['active'])
            key = restored.ingest(self.message)
            self.assertIsNotNone(key)
        restored.acknowledge([key])
        again = Panel(self.config, self.temp.name, ROOT)
        again.ingest(self.message)
        self.assertEqual(len(again.state['items']), 1)
        self.assertEqual(again.notifications(), [])

    def test_finalizar_revoca_codigo_y_no_reutiliza_al_reactivar(self):
        original = self.message['subject']
        self.panel.stop_reception_test()
        self.assertIsNone(self.panel.ingest(self.message))
        self.assertNotEqual(self.panel.start_reception_test()['subject'], original)
        self.assertIsNone(self.panel.ingest(self.message))

    def test_envio_bloqueado_con_permiso_confirmacion_y_despues_de_reiniciar(self):
        key = self.panel.ingest(self.message)
        for panel in (self.panel, Panel(self.config, self.temp.name, ROOT)):
            panel.graph = Mailbox()
            panel.stop_reception_test()
            preview = panel.preview(key, {'kind':'percent', 'discount':'10', 'recipient':'cliente@example.com'})
            self.assertEqual(preview['amounts']['total'], 19764)
            self.assertEqual(preview['to'], self.config['expected_mailbox'])
            with self.assertRaisesRegex(ValueError, 'bloqueado permanentemente'):
                panel.send_confirmed(key, {'confirmed':True, 'approval_id':preview['approval_id']})
            self.assertEqual(panel.graph.calls, [])

    def test_no_relaja_filtro_de_proveedor_y_conserva_demostracion_local(self):
        supplier = read_eml(ROOT/'tests/fixtures/renovacion-ficticia.eml')
        key = self.panel.ingest(supplier)
        self.assertFalse(self.panel.state['items'][key].get('send_blocked', False))
        local = self.panel.ingest({**supplier, 'id':'local'}, test=True)
        self.assertNotIn(local, [n['key'] for n in self.panel.notifications()])

    def test_correo_sin_tabla_queda_para_revisar_y_avisa(self):
        message = copy.deepcopy(self.message)
        message['body'] = {'contentType':'text', 'content':'Ejemplo sin tabla'}
        key = self.panel.ingest(message)
        self.assertEqual(self.panel.state['items'][key]['status'], 'review')
        self.assertTrue(self.panel.state['items'][key]['send_blocked'])
        self.assertEqual(len(self.panel.notifications()), 1)
