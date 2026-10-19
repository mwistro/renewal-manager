"""Extracción conservadora: nunca sustituye el PVP por el PVD."""
import re
from datetime import date
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from html.parser import HTMLParser
from decimal import Decimal


class ReviewRequired(ValueError):
    pass


class EmailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.text, self.hidden = [], [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if self.hidden:
            return
        if tag == 'table':
            table = {'rows': [], 'row': None, 'cell': None}
            self.tables.append(table)
            self.stack.append(table)
        elif self.stack and tag == 'tr':
            self.stack[-1]['row'] = []
        elif self.stack and tag in ('td', 'th'):
            self.stack[-1]['cell'] = []
        if tag in ('br', 'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'):
            self._text('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if self.stack:
            table = self.stack[-1]
            if tag in ('td', 'th') and table['cell'] is not None:
                if table['row'] is not None:
                    table['row'].append(' '.join(''.join(table['cell']).split()))
                table['cell'] = None
                self.text.append(' ')
            elif tag == 'tr' and table['row'] is not None:
                table['rows'].append(table['row'])
                table['row'] = None
            elif tag == 'table':
                self.stack.pop()
        if tag in ('p', 'div', 'tr', 'li', 'h1', 'h2', 'h3', 'table'):
            self._text('\n')

    def _text(self, data):
        self.text.append(data)
        for table in self.stack:
            if table['cell'] is not None:
                table['cell'].append(data)

    def handle_data(self, data):
        if not self.hidden:
            self._text(data)


def amount(value):
    value = value.strip().replace('\xa0', '').replace(' ', '')
    if not re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}', value):
        raise ReviewRequired('Formato de precio no reconocido. Revisa el PVP. ')
    return int(Decimal(value.replace('.', '').replace(',', '.')) * 100)


def renewal_pvp(cell):
    return price_pair(cell)[1]


def price_pair(cell):
    values = re.findall(r'(?<![\d.,-])((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\s*(?:€|EUR)', cell, re.I)
    if len(values) == 2 and re.search(r'→|->|➜|⇒', cell):
        after = re.split(r'→|->|➜|⇒', cell)[-1]
        if len(re.findall(r'\d+,\d{2}\s*(?:€|EUR)', after, re.I)) != 1:
            raise ReviewRequired('No se distingue el PVP de renovación.')
        return amount(values[0]), amount(values[-1])
    raise ReviewRequired('Se esperaba PVP actual → PVP de renovación. Revisa el formato del proveedor.')


def extract(html):
    parser = EmailHTML()
    parser.feed(html)
    candidates = []
    for table in parser.tables:
        rows = table['rows']
        for index, header in enumerate(rows):
            names = [re.sub(r'\s+', ' ', s).strip().lower() for s in header]
            pvp = [i for i, s in enumerate(names) if re.match(r'^pvp\b', s)]
            pvd = [i for i, s in enumerate(names) if re.match(r'^pvd\b', s)]
            title = [i for i, s in enumerate(names) if s in ('título', 'titulo', 'producto')]
            quantity = [i for i, s in enumerate(names) if s in ('ud.', 'ud', 'uds.', 'cantidad')]
            if len(pvp) == len(title) == len(quantity) == 1:
                for row in rows[index + 1:]:
                    if not row or not any(row):
                        continue
                    if len(row) != len(header):
                        raise ReviewRequired('La tabla contiene filas con un formato no reconocido.')
                    qty = row[quantity[0]].strip()
                    if not qty.isdigit() or not 1 <= int(qty) <= 100000:
                        raise ReviewRequired('Cantidad de licencias no válida.')
                    product = re.sub(r'\s*Renovaci[oó]n autom[aá]tica\s*', '', row[title[0]], flags=re.I).strip()
                    if not product:
                        raise ReviewRequired('Falta el nombre de la licencia.')
                    current_pvp, next_pvp = price_pair(row[pvp[0]])
                    item = {'product': product, 'quantity': int(qty), 'pvp_cents': next_pvp, 'pvp_current_cents': current_pvp}
                    if len(pvd) == 1:
                        try:
                            item['pvd_current_cents'], item['pvd_cents'] = price_pair(row[pvd[0]])
                        except ReviewRequired:
                            pass
                    candidates.append(item)
                break
    if len(candidates) != 1:
        raise ReviewRequired('Se necesita exactamente una licencia en la tabla PVP. Revisa el correo original.')
    text = ''.join(parser.text)
    client = re.split(r'Datos\s+del\s+cliente', text, flags=re.I)
    if len(client) != 2:
        raise ReviewRequired('No se encuentra un único bloque de datos del cliente.')
    # Solo se aceptan campos identificados dentro del bloque del cliente.
    block = client[1].split('Es el momento')[0]
    def field(label, required=True):
        found = re.findall(r'(?:^|\n)\s*' + label + r'\s*:\s*([^\r\n]+)', block, re.I)
        if len(found) != 1:
            if not required and not found:
                return ''
            raise ReviewRequired('Campo de cliente ausente o ambiguo: ' + label)
        return found[0].strip()
    recipient = field('Email')
    if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', recipient):
        raise ReviewRequired('La dirección del cliente no es válida.')
    dates = re.findall(r'Fecha\s+de\s+expiraci[oó]n\s*:\s*(\d{2}-\d{2}-\d{4})', text, re.I)
    iso_dates = re.findall(r'FechaCotermino\s*:\s*(\d{4}-\d{2}-\d{2})', text, re.I)
    dates = ['-'.join(v.split('-')[::-1]) for v in dates] + iso_dates
    try:
        if len(set(dates)) != 1:
            raise ValueError()
        renewal = date.fromisoformat(dates[0]).isoformat()
    except (ValueError, IndexError):
        raise ReviewRequired('Fecha de renovación ausente, no válida o contradictoria.') from None
    return {**candidates[0], 'customer': field('Nombre cliente'), 'contact': field('Persona de contacto', False), 'recipient': recipient, 'renewal': renewal}


def read_eml(path):
    with open(path, 'rb') as handle:
        message = BytesParser(policy=policy.default).parse(handle)
    body = message.get_body(preferencelist=('html',))
    if body is None:
        raise ReviewRequired('El correo no contiene HTML. Se necesita el email original, no una captura o reenvío en texto.')
    return {'id': message.get('Message-ID', ''), 'subject': str(message.get('Subject', '')), 'from': {'emailAddress': {'address': parseaddr(message.get('From', ''))[1]}}, 'body': {'contentType': 'html', 'content': body.get_content()}}
