"""Recursos permitidos del instalador: interfaz, configuración, logo y muestra ficticia."""
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from automation.organization import require_organization


def icon_png(size):
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            px, py = (x + .5) / size, (y + .5) / size
            colour = (33, 43, 57, 255)
            # Neutral envelope icon drawn with simple geometry.
            if .2 < px < .8 and .37 < py < .74:
                colour = (250, 250, 252, 255)
                if abs(py - (.39 + .5 * min(px-.2, .8-px))) < .025:
                    colour = (36, 123, 131, 255)
            pixels.extend(colour)
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(bytes(pixels)))+chunk(b'IEND',b'')


def prepare():
    local = ROOT / 'desktop/config.local.json'
    if not local.exists():
        raise SystemExit('Copia desktop/public-config.json a desktop/config.local.json y configura tus propios tenant_id y client_id. Para la demo sin cuenta: npm run demo.')
    config = require_organization(json.loads(local.read_text(encoding='utf-8-sig')))
    destination = ROOT / '.build/app-data'
    # Carpeta exclusivamente generada: no conservar residuos de builds privados.
    if destination.exists():
        if destination.is_symlink() or destination.resolve() != ROOT.resolve() / '.build/app-data':
            raise ValueError('Directorio de compilación no válido.')
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'panel').mkdir(exist_ok=True)
    for name in ('index.html','app.js','styles.css'):
        shutil.copy2(ROOT / 'panel' / name, destination / 'panel' / name)
    # Lista explícita: no copiar credenciales ni el estado local al instalador.
    public = {key: config[key] for key in ('tenant_id','client_id','expected_mailbox','supplier_domains','price_basis','tax','poll_seconds')}
    public['company'] = {key:config['company'][key] for key in ('name','email')}
    public['expected_mailbox'] = ''
    public['company']['email'] = ''
    public['company']['logo_path'] = None
    (destination / 'desktop-config.json').write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')
    assets = destination / 'assets'
    assets.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'tests/fixtures/renovacion-ficticia.eml', destination / 'demo.eml')
    icons = ROOT / 'desktop/assets'
    icons.mkdir(parents=True, exist_ok=True)
    png = icon_png(256)
    (icons / 'icon.png').write_bytes(png)
    (icons / 'icon.ico').write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(png),22)+png)
    print('Recursos públicos preparados a partir de la lista permitida. Falta revisar el paquete compilado.')


if __name__ == '__main__':
    prepare()
