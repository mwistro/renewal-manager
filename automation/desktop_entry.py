"""Motor privado del escritorio. La clave efímera llega por stdin, nunca por argv."""
import json
import os
import sys
import threading
from pathlib import Path
from automation.__main__ import configuration
from automation.panel import serve
from automation.organization import require_organization


def main():
    options = json.loads(sys.stdin.readline())
    key = options['key']
    if not isinstance(key, str) or len(key) < 32:
        raise ValueError('Clave de escritorio no válida.')
    root = Path(options['root']).resolve()
    directory = Path(options['data']).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / 'config.json'
    if not config_path.exists():
        raise ValueError('Completa la configuración inicial en la aplicación.')
    # Si el proceso Electron desaparece (también al cerrar sesión en Windows),
    # la tubería se cierra y el motor termina. El estado se guarda antes de enviar.
    def parent_lifetime():
        for _ in sys.stdin:
            pass
        os._exit(0)
    threading.Thread(target=parent_lifetime, daemon=True).start()
    config = require_organization(configuration(config_path, live=True))
    config['restrict_organization'] = True
    serve(config, root, port=0, data_dir=directory,
          desktop_key=key, ready=lambda origin: print(json.dumps({'ready': origin}), flush=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No se escriben respuestas OAuth ni tokens en los registros.
        print(json.dumps({'error': 'No se ha podido iniciar el motor de renovaciones. Revisa la configuración local.'}), flush=True)
        sys.exit(1)
