import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from automation.__main__ import main


class ConnectionTests(unittest.TestCase):
    def verify(self, granted):
        graph = Mock(can_send=granted)
        config = {'expected_mailbox': 'prueba@example.com'}
        with patch('sys.argv', ['automation', 'conectar', '--verificar-envio']), patch('automation.__main__.configuration', return_value=config), patch('automation.__main__.MicrosoftSession', return_value=graph) as session, redirect_stdout(io.StringIO()) as output:
            if granted:
                main()
                self.assertIn('Mail.Send confirmado', output.getvalue())
            else:
                with self.assertRaisesRegex(RuntimeError, 'no ha incluido Mail.Send'):
                    main()
                self.assertNotIn('Mail.Send confirmado', output.getvalue())
            session.assert_called_once_with(config, enable_send=True)
        graph.login.assert_called_once_with()
        graph.verify_mailbox.assert_called_once_with()
        self.assertEqual(len(graph.request.call_args_list), 2)
        self.assertTrue(all(call.args[0] == 'GET' for call in graph.request.call_args_list))

    def test_comprueba_permiso_sin_crear_ni_enviar(self):
        self.verify(True)

    def test_no_declara_permiso_si_microsoft_no_lo_concede(self):
        self.verify(False)
