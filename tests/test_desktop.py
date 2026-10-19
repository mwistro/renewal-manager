import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from automation.graph import MicrosoftSession, LoginRequired
from automation.panel import Panel
from automation.parser import read_eml
from automation.vault import SessionVault, protect
from test_panel import Mailbox

ROOT = Path(__file__).resolve().parent.parent


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = json.loads((ROOT/'tests/fixtures/config.demo.json').read_text())
        self.config.update(client_id='client',expected_mailbox='comercial@example.com')
        self.panel = Panel(self.config, self.directory.name, ROOT)
        self.panel.state['monitor_started_at'] = '2026-10-01T00:00:00Z'
        self.panel.state['last_scan'] = '2026-10-02T12:00:00Z'
        self.message = read_eml(ROOT/'tests/fixtures/renovacion-ficticia.eml')
        self.graph = Mailbox()
        self.graph.message = self.message
        self.graph.incoming = [self.message]
        self.panel.graph = self.graph
        self.panel.save()

    def test_reinicio_y_reconexion_recuperan_desde_ultima_consulta_sin_duplicar(self):
        self.panel.tick()
        restarted = Panel(self.config, self.directory.name, ROOT)
        restarted.graph = self.graph
        restarted.synchronize()
        self.assertEqual(len(restarted.state['items']),1)
        self.assertTrue(all(method=='GET' for method,_,_ in self.graph.calls))
        self.assertIn('2026-10-02T11%3A55%3A00Z',self.graph.calls[0][1])

    def test_caida_en_segunda_pagina_no_adelanta_marca_y_recupera_el_resto(self):
        original = self.graph.request
        second = {**self.message,'id':'otro-correo'}
        failed = True
        def request(method,path,*args,**kwargs):
            if path=='https://graph.microsoft.com/v1.0/page2':
                if failed: raise OSError('Sin red')
                return {'value':[second]}
            if '/inbox/messages?' in path:
                result=original(method,path,*args,**kwargs)
                return {**result,'@odata.nextLink':'https://graph.microsoft.com/v1.0/page2'}
            if '/me/messages/otro-correo?' in path:return second
            return original(method,path,*args,**kwargs)
        self.graph.request=request
        self.panel.synchronize()
        self.assertEqual(self.panel.state['last_scan'],'2026-10-02T12:00:00Z')
        self.assertEqual(len(self.panel.state['items']),1)
        failed=False
        self.panel.synchronize()
        self.assertEqual(len(self.panel.state['items']),2)
        self.assertEqual(self.panel.last_error,'')

    def test_avisos_persisten_y_no_se_repiten_tras_confirmar(self):
        self.panel.tick()
        events=self.panel.notifications()
        self.assertEqual(len(events),1)
        self.panel.acknowledge([events[0]['key']])
        restarted=Panel(self.config,self.directory.name,ROOT)
        self.assertEqual(restarted.notifications(),[])

    def test_muestra_local_no_genera_aviso_de_renovacion_real(self):
        self.panel.ingest(self.message,test=True)
        self.assertEqual(self.panel.notifications(),[])

    def test_sesion_revocada_pide_login_sin_borrar_pendientes(self):
        self.panel.tick()
        self.graph.request=lambda *a,**k: (_ for _ in ()).throw(LoginRequired('Vuelve a conectar'))
        self.panel.synchronize()
        self.assertIsNone(self.panel.graph)
        self.assertEqual(self.panel.auth['status'],'needs_login')
        self.assertEqual(len(self.panel.state['items']),1)

    def test_caida_de_red_no_borra_sesion_ni_pide_login(self):
        self.graph.request=lambda *a,**k: (_ for _ in ()).throw(OSError('Sin red'))
        self.panel.synchronize()
        self.assertIs(self.panel.graph,self.graph)
        self.assertNotEqual(self.panel.auth['status'],'needs_login')
        self.assertEqual(self.panel.state['last_scan'],'2026-10-02T12:00:00Z')

    def test_restaura_y_guarda_rotacion_de_refresh_token_tras_verificar_cuenta(self):
        class Vault:
            def __init__(self):self.saved=[]
            def load(self):return {'refresh_token':'guardado','enable_send':False}
            def save(self,session):self.saved.append(session.refresh)
        vault=Vault()
        panel=Panel(self.config,self.directory.name,ROOT,vault=vault)
        session=MicrosoftSession(self.config)
        def verify():
            self.assertEqual(session.refresh,'guardado')
            session.refresh='rotado'
            return 'mailbox'
        session.verify_mailbox=verify
        with patch('automation.panel.MicrosoftSession',return_value=session):
            panel.restore_session()
        self.assertIs(panel.graph,session)
        self.assertEqual(vault.saved,['rotado'])
        self.assertFalse(panel.public_state()['can_send'])

    def test_sesion_restaurada_sin_red_se_conserva_para_reintentar(self):
        class Vault:
            def load(self):return {'refresh_token':'guardado','enable_send':False}
            def clear(self):raise AssertionError('No debe borrar la sesión por una caída de red')
        panel=Panel(self.config,self.directory.name,ROOT,vault=Vault())
        with patch('automation.graph.MicrosoftSession.verify_mailbox',side_effect=OSError('Sin red')):
            panel.synchronize()
        self.assertEqual(panel.auth['status'],'reconnecting')
        self.assertEqual(panel.state['last_scan'],'2026-10-02T12:00:00Z')


@unittest.skipUnless(os.name=='nt','DPAPI requiere Windows')
class VaultTests(unittest.TestCase):
    def test_dpapi_real_cifra_y_recupera_sesion_sin_guardar_token_en_claro(self):
        config={'tenant_id':'organizacion','client_id':'aplicacion','expected_mailbox':'test@example.com'}
        with tempfile.TemporaryDirectory() as folder:
            vault=SessionVault(folder,config)
            session=MicrosoftSession(config)
            session.refresh='token-FICTICIO-de-prueba'
            vault.save(session)
            self.assertNotIn(session.refresh.encode(),vault.path.read_bytes())
            self.assertEqual(vault.load()['refresh_token'],session.refresh)
            with self.assertRaises(ValueError):
                SessionVault(folder,{**config,'expected_mailbox':'otra@example.com'}).load()
            vault.clear()
            self.assertIsNone(vault.load())

    def test_datos_modificados_no_se_descifran(self):
        data=bytearray(protect(b'contenido ficticio'))
        data[-1]^=1
        with self.assertRaises(OSError):protect(bytes(data),decrypt=True)


class RefreshTests(unittest.TestCase):
    def test_revocacion_exige_login(self):
        graph=MicrosoftSession({'tenant_id':'organizations'})
        graph.refresh='ficticio'
        with patch.object(graph,'oauth',return_value={'error':'invalid_grant'}):
            with self.assertRaises(LoginRequired):graph.access_token()

    def test_rotacion_persiste_token_nuevo(self):
        graph=MicrosoftSession({'tenant_id':'organizations'})
        graph.refresh='anterior'
        records=[]
        graph.on_refresh=lambda session:records.append(session.refresh)
        with patch.object(graph,'oauth',return_value={'access_token':'ficticio','refresh_token':'nuevo','expires_in':3600,'scope':'User.Read Mail.ReadWrite'}):
            self.assertEqual(graph.access_token(),'ficticio')
        self.assertEqual(records,['nuevo'])
        self.assertFalse(graph.can_send)

    def test_revocacion_mail_send_al_refrescar_no_envia(self):
        graph=MicrosoftSession({'tenant_id':'organizations'},enable_send=True)
        graph.refresh='ficticio';graph.can_send=True
        with patch.object(graph,'oauth',return_value={'access_token':'ficticio','expires_in':3600,'scope':'Mail.ReadWrite'}), patch('automation.graph.urlopen') as request:
            with self.assertRaises(ValueError):graph.request('POST','/me/messages/draft/send')
            request.assert_not_called()


if __name__=='__main__':unittest.main()
