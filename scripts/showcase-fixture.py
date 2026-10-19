"""Synthetic showcase data, using the application's real email renderer."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from automation.proposal import render, totals

config = {
    'price_basis': 'total', 'tax': 'excluded',
    'company': {'name': 'Renewal Manager · Demo', 'email': 'equipo@example.com', 'logo_path': None},
}


def entry(key, customer, product, quantity, cost, previous, price, status='pending'):
    proposal = {
        'customer': customer, 'product': product, 'quantity': quantity,
        'pvd_cents': cost, 'pvp_current_cents': previous, 'pvp_cents': price,
        'renewal': '2030-11-09', 'recipient': f'{key}@example.com',
    }
    return {
        'key': key, 'status': status, 'discount': '0', 'discount_kind': 'percent',
        'recipient': proposal['recipient'], 'proposal': proposal,
        'amounts': totals(proposal, 'total', '0'), 'test': False,
        'subject': f'Renovación de licencias — {customer}',
    }


items = [
    entry('atlas', 'Cliente Atlas · Demo', 'Microsoft 365 Business Standard', 12, 138000, 168000, 180000),
    entry('horizonte', 'Cliente Horizonte · Demo', 'Microsoft Entra ID P1', 8, 42000, 54000, 57600),
    entry('norte', 'Cliente Norte · Demo', 'Microsoft 365 Business Premium', 5, 96000, 120000, 126000),
    entry('lumen', 'Cliente Lumen · Demo', 'Exchange Online Plan 1', 20, 72000, 90000, 96000),
    entry('orion', 'Cliente Orión · Demo', 'Microsoft 365 Business Basic', 4, 22000, 27000, 28800, 'sent'),
    entry('aurora', 'Cliente Aurora · Demo', 'Microsoft Entra ID P1', 6, 31500, 40500, 43200, 'sent'),
    {'key': 'revision', 'status': 'review', 'subject': 'Renovación con datos incompletos',
     'issue': 'La fecha de renovación no está clara. Revisa el mensaje antes de preparar una propuesta.'},
]
proposal = items[0]['proposal']
preview = {
    'approval_id': 'showcase-only', 'html': render(proposal, config, '10'),
    'to': proposal['recipient'], 'subject': 'Propuesta de renovación · Microsoft 365',
    'test': False, 'amounts': totals(proposal, 'total', '10'),
}
state = {
    'account': 'equipo@example.com', 'company': config['company']['name'],
    'price_basis': 'total', 'connected': True, 'can_send': True,
    'auth': {'status': 'connected'}, 'items': items,
    'last_scan': '2030-10-19T08:30:00Z', 'last_error': '', 'poll_seconds': 30,
}
print(json.dumps({'state': state, 'preview': preview}, ensure_ascii=False))
