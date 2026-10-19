import json
import unittest
from pathlib import Path
from automation.organization import require_organization
from automation.parser import read_eml
from automation.proposal import prepare, render

ROOT = Path(__file__).resolve().parent.parent


class PortfolioTests(unittest.TestCase):
    def test_organization_accepts_user_supplied_ids_and_rejects_placeholders(self):
        config = {'tenant_id': '11111111-1111-4111-8111-111111111111',
                  'client_id': '22222222-2222-4222-8222-222222222222'}
        self.assertEqual(require_organization(config), config)
        for field in config:
            for invalid in ('', 'common', 'organizations', 'consumers', 'not-a-uuid', '00000000-0000-0000-0000-000000000000'):
                with self.subTest(field=field, value=invalid), self.assertRaises(ValueError):
                    require_organization({**config, field: invalid})

    def test_public_configuration_has_no_account_or_application_registration(self):
        for name in ('desktop/public-config.json', 'automation/config.example.json'):
            config = json.loads((ROOT / name).read_text(encoding='utf-8'))
            for key in ('tenant_id', 'client_id', 'expected_mailbox'):
                self.assertEqual(config[key], '')
            self.assertEqual(config['company']['email'], '')
            self.assertIsNone(config['company']['logo_path'])

    def test_email_contact_comes_from_configuration_and_is_escaped(self):
        config = json.loads((ROOT / 'tests/fixtures/config.demo.json').read_text(encoding='utf-8'))
        proposal = prepare(read_eml(ROOT / 'tests/fixtures/renovacion-ficticia.eml'), config)
        config['company']['email'] = 'support@example.org'
        html = render(proposal, config)
        self.assertIn('mailto:support@example.org', html)
        self.assertNotIn('comercial@example.com', html)
        config['company']['email'] = 'contact" onclick="bad@example.org'
        html = render(proposal, config)
        self.assertNotIn('" onclick="', html)
