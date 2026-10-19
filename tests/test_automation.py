import copy
import json
import tempfile
import unittest
import base64
from pathlib import Path
from automation.parser import ReviewRequired, extract, read_eml
from automation.proposal import draft_payload, matches, prepare, source_key, totals
from automation.worker import StateStore, Worker, apply_discount
from automation.graph import SCOPES, MicrosoftSession

ROOT = Path(__file__).parent / 'fixtures'


class FakeGraph:
    def __init__(self):
        self.calls, self.recovered = [], []
        self.fail_create = False
        self.current = {'id': 'draft-1', 'isDraft': True, 'changeKey': 'version-1', '@odata.etag': 'etag-1'}

    def request(self, method, path, payload=None, headers=None):
        self.calls.append((method, path, payload, headers))
        if method == 'POST':
            if self.fail_create:
                raise TimeoutError('Simulación de respuesta perdida')
            return self.current.copy()
        if method == 'PATCH':
            return {**self.current, 'changeKey': 'version-2'}
        if 'singleValueExtendedProperties' in path:
            return {'value': self.recovered}
        if '/me/mailFolders/' in path:
            return {'value': []}
        return self.current.copy()


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config.demo.json').read_text())
        self.message = read_eml(ROOT / 'renovacion-ficticia.eml')
        self.html = self.message['body']['content']

    def test_pvp_renovacion_no_pvd_ni_pvp_actual(self):
        proposal = prepare(self.message, self.config)
        self.assertEqual(proposal['pvp_cents'], 21960)
        self.assertEqual(proposal['quantity'], 3)
        self.assertEqual(proposal['recipient'], 'cliente@example.com')
        self.assertEqual(proposal['renewal'], '2026-11-09')
        self.assertEqual(totals(proposal, 'unit')['total'], 65880)
        self.assertEqual(totals(proposal, 'total')['total'], 21960)

    def test_descuento_porcentual_y_fijo(self):
        proposal = extract(self.html)
        self.assertEqual(totals(proposal, 'unit', '10')['total'], 59292)
        self.assertEqual(totals(proposal, 'total', '20', 'amount')['total'], 19960)
        for value in ('-1', '101', 'NaN', 'Infinity', 'abc'):
            with self.subTest(value=value), self.assertRaises(ReviewRequired):
                totals(proposal, 'unit', value)

    def test_no_asume_base_de_precio(self):
        with self.assertRaises(ReviewRequired):
            prepare(self.message, {**self.config, 'price_basis': None})

    def test_correo_sin_informacion_privada_del_proveedor(self):
        payload = draft_payload(extract(self.html), self.config, 'origen')
        html = payload['body']['content']
        for forbidden in ('178,59', '163,98', '201,60', 'supplier', 'SubscriptionId', 'identificador-interno', 'privado@example.com', 'Ver pedido'):
            self.assertNotIn(forbidden, html)
        self.assertNotIn('Descuento', html)
        self.assertEqual(payload['toRecipients'][0]['emailAddress']['address'], 'cliente@example.com')
        self.assertIn('219,60 €', html)
        self.assertNotIn('658,80 €', html)
        self.assertNotIn('Mail.Send', SCOPES)

    def test_formato_ambiguo_no_crea_propuesta(self):
        alternatives = [self.html.replace('219,60 €', 'precio pendiente'), self.html.replace('2026-11-09', '2026-11-10'), self.html.replace('09-11-2026', '31-02-2026'), self.html.replace('Email: cliente@example.com', 'Email: cliente@example.com; segundo@example.com'), self.html.replace('PVP (act. → renov.)', 'PVD (act. → renov.)'), self.html.replace('201,60 € →', '201,60 € /')]
        for html in alternatives:
            with self.subTest(html=html[:80]), self.assertRaises(ReviewRequired):
                extract(html)

    def test_dominios_exactos_y_borradores_ignorados(self):
        self.assertTrue(matches(self.message, self.config))
        for sender in ('x@supplier.example.com.ejemplo.com', 'x@falsosupplier.example.com', 'x@example.com'):
            message = {**self.message, 'from': {'emailAddress': {'address': sender}}}
            self.assertFalse(matches(message, self.config))
        self.assertFalse(matches({**self.message, 'isDraft': True}, self.config))

    def test_asunto_y_dominio_observados_en_el_correo_real(self):
        message = {**self.message, 'from': {'emailAddress': {'address': 'servicios@mail.supplier.example.com'}}}
        for subject in ('Licencia Office 365 renovara en 30 dias [12345]', 'Licencia Office 365 renovará en 30 días', 'Renovación de licencia Office 365'):
            with self.subTest(subject=subject):
                self.assertTrue(matches({**message, 'subject': subject}, self.config))
        self.assertFalse(matches({**message, 'subject': 'Oferta de equipos'}, self.config))
        self.assertFalse(matches({**message, 'from': {'emailAddress': {'address': 'servicios@mail.supplier.example.com.example.org'}}}, self.config))

    def test_reenvio_con_firma_y_espaciadores_de_outlook(self):
        prefix = '<p>Persona de prueba</p><p>firma-interna@example.com</p><p>De: Portal Proveedor de ejemplo &lt;servicios@mail.supplier.example.com&gt;</p>'
        html = prefix + self.html.replace('</table>\n</td>', '<tr><td>&nbsp;</td></tr><tr><td></td></tr></table>\n</td>')
        proposal = extract(html)
        payload = draft_payload(proposal, self.config, 'prueba')
        self.assertEqual(proposal['recipient'], 'cliente@example.com')
        self.assertNotIn('Persona de prueba', payload['body']['content'])
        self.assertNotIn('firma-interna@example.com', payload['body']['content'])

    def test_destinatario_proveedor_rechazado(self):
        message = copy.deepcopy(self.message)
        message['body']['content'] = self.html.replace('Email: cliente@example.com', 'Email: agente@supplier.example.com')
        with self.assertRaises(ReviewRequired):
            prepare(message, self.config)

    def test_repeticion_no_duplica_borrador(self):
        with tempfile.TemporaryDirectory() as directory:
            store, graph = StateStore(directory), FakeGraph()
            worker = Worker(graph, self.config, store, 'cuenta', log=lambda text: None)
            state = store.read()
            worker.process(self.message, state)
            worker.process(self.message, store.read())
            self.assertEqual(sum(c[0] == 'POST' for c in graph.calls), 1)

    def test_borrador_de_prueba_dirigido_solo_a_cuenta_de_prueba(self):
        with tempfile.TemporaryDirectory() as directory:
            store, graph = StateStore(directory), FakeGraph()
            config = {**self.config, 'expected_mailbox': 'prueba@example.com'}
            worker = Worker(graph, config, store, 'cuenta', log=lambda text: None)
            worker.process(self.message, store.read(), test_mode=True)
            worker.process(self.message, store.read(), test_mode=True)
            posts = [call for call in graph.calls if call[0] == 'POST']
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0][2]['toRecipients'], [{'emailAddress': {'address': 'prueba@example.com'}}])
            self.assertTrue(posts[0][2]['subject'].startswith('[PRUEBA] '))
            self.assertIn('219,60 €', posts[0][2]['body']['content'])
            self.assertNotIn(source_key(self.message), store.read()['messages'])

    def test_post_incierto_no_se_repite_y_se_recupera(self):
        with tempfile.TemporaryDirectory() as directory:
            store, graph = StateStore(directory), FakeGraph()
            worker = Worker(graph, self.config, store, 'cuenta', log=lambda text: None)
            graph.fail_create = True
            with self.assertRaises(TimeoutError):
                worker.process(self.message, store.read())
            worker.process(self.message, store.read())
            self.assertEqual(sum(c[0] == 'POST' for c in graph.calls), 1)
            graph.recovered = [graph.current]
            worker.process(self.message, store.read())
            self.assertEqual(store.read()['messages'][source_key(self.message)]['status'], 'draft')

    def test_descuento_actualiza_solo_borrador_sin_ediciones(self):
        with tempfile.TemporaryDirectory() as directory:
            store, graph = StateStore(directory), FakeGraph()
            state = store.read()
            state['mailbox_id'] = 'cuenta'
            Worker(graph, self.config, store, 'cuenta', log=lambda text: None).process(self.message, state)
            key = source_key(self.message)
            apply_discount(graph, store, self.config, 'cuenta', key[:12], '10', 'percent')
            patches = [c for c in graph.calls if c[0] == 'PATCH']
            self.assertIn('197,64 €', patches[0][2]['body']['content'])
            self.assertEqual(patches[0][3]['If-Match'], 'etag-1')
            graph.current['isDraft'] = False
            with self.assertRaisesRegex(ValueError, 'ya no es un borrador'):
                apply_discount(graph, store, self.config, 'cuenta', key[:12], '20', 'percent')
            graph.current['isDraft'] = True
            graph.current['changeKey'] = 'edicion-en-outlook'
            with self.assertRaisesRegex(ValueError, 'ha cambiado'):
                apply_discount(graph, store, self.config, 'cuenta', key[:12], '20', 'percent')

    def test_contenido_html_escapado(self):
        proposal = extract(self.html)
        proposal['product'] = '<script>alert(1)</script>'
        html = draft_payload(proposal, self.config, 'origen')['body']['content']
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_no_hay_operacion_de_envio(self):
        graph = MicrosoftSession({'tenant_id': 'organizations'})
        for path in ('/me/sendMail', '/me/messages/id/send'):
            with self.assertRaisesRegex(ValueError, 'Solo se permite'):
                graph.request('POST', path, {})
        with self.assertRaisesRegex(ValueError, 'Destino Graph'):
            graph.request('GET', 'https://example.com/v1.0/me')

    def test_tabla_con_columnas_reordenadas_y_miles(self):
        html = self.html.replace('<th>PVD (act. → renov.)</th><th>PVP (act. → renov.)</th>', '<th>PVP (act. → renov.)</th><th>PVD (act. → renov.)</th>')
        html = html.replace('163,98 € → <strong>178,59 €', '1.163,98 € → <strong>1.178,59 €')
        self.assertEqual(extract(html)['pvp_cents'], 117859)

    def test_logotipo_incrustado_en_borrador(self):
        with tempfile.TemporaryDirectory() as directory:
            logo = Path(directory) / 'logo.png'
            logo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH7kAAAAASUVORK5CYII='))
            config = copy.deepcopy(self.config)
            config['company']['logo_path'] = str(logo)
            payload = draft_payload(extract(self.html), config, 'origen')
            self.assertIn('cid:company-logo', payload['body']['content'])
            self.assertTrue(payload['attachments'][0]['isInline'])
            self.assertEqual(base64.b64decode(payload['attachments'][0]['contentBytes']), logo.read_bytes())

    def test_ciclo_completo_paginado_y_sin_duplicados(self):
        message = self.message
        class Pages(FakeGraph):
            def request(inner, method, path, payload=None, headers=None):
                if method == 'GET' and '/me/mailFolders/' in path:
                    return {'value': [message], '@odata.nextLink': 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?page=2'} if 'page=2' not in path else {'value': [message]}
                if method == 'GET' and '$select=id,subject,from,body,isDraft' in path:
                    return message
                return super(Pages, inner).request(method, path, payload, headers)
        with tempfile.TemporaryDirectory() as directory:
            store, graph = StateStore(directory), Pages()
            worker = Worker(graph, self.config, store, 'cuenta', log=lambda text: None)
            worker.tick()
            worker.tick()
            self.assertEqual(sum(c[0] == 'POST' for c in graph.calls), 1)
            self.assertIsNotNone(store.read()['last_scan'])
            self.assertEqual(store.read()['mailbox_id'], 'cuenta')

    def test_fallo_de_lectura_no_avanza_el_punto_de_recuperacion(self):
        class FailingGraph(FakeGraph):
            def request(inner, *args, **kwargs):
                raise TimeoutError('Sin conexión')
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(directory)
            state = store.read()
            state['started_at'] = '2026-01-01T00:00:00Z'
            state['last_scan'] = '2026-01-02T00:00:00Z'
            store.save(state)
            with self.assertRaises(TimeoutError):
                Worker(FailingGraph(), self.config, store, 'cuenta').tick()
            self.assertEqual(store.read()['last_scan'], '2026-01-02T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
