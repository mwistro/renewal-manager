"""Sesión cifrada con DPAPI para el usuario actual de Windows; nunca texto plano."""
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('La sesión protegida requiere Windows.')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    # CRYPTPROTECT_UI_FORBIDDEN, sin LOCAL_MACHINE: ligado al usuario actual.
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = operation(ctypes.byref(source), 'Renovaciones Microsoft', None, None, None, 1, ctypes.byref(output))
    if not ok:
        code = ctypes.get_last_error()
        raise OSError(code, 'No se ha podido acceder a la sesión protegida de Windows. Comprueba que el perfil de usuario está cargado.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class SessionVault:
    def __init__(self, directory, config):
        self.path = Path(directory) / 'sesion.dpapi'
        self.identity = {k: config[k] for k in ('tenant_id', 'client_id', 'expected_mailbox')}

    def save(self, session):
        if not session.refresh:
            raise RuntimeError('Microsoft no ha proporcionado una sesión renovable. Vuelve a conectar Outlook.')
        payload = {**self.identity, 'refresh_token': session.refresh, 'enable_send': session.enable_send}
        encrypted = protect(json.dumps(payload).encode('utf-8'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def load(self):
        if not self.path.exists():
            return None
        data = json.loads(protect(self.path.read_bytes(), decrypt=True))
        if any(data.get(k) != value for k, value in self.identity.items()):
            raise ValueError('La sesión guardada pertenece a otra configuración de cuenta.')
        if not isinstance(data.get('refresh_token'), str) or not data['refresh_token']:
            raise ValueError('La sesión guardada no es válida.')
        return data

    def clear(self):
        self.path.unlink(missing_ok=True)
