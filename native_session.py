"""Read the signed-in Windows Grok Bot account for a local CLI subprocess.

Native safeStorage is decrypted with CurrentUser DPAPI plus AES-GCM. Only the
active access token is used; no refresh tokens, auth writes or refresh requests.
Tokens remain in memory and in the child environment, never in application
settings, returned web state, command arguments or diagnostics.
"""
from __future__ import annotations

import base64
import ctypes
import json
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class NativeSessionError(RuntimeError):
    """Safe failure without underlying secret values or decoder diagnostics."""


def _dpapi_unprotect(value: bytes) -> bytes:
    if sys.platform != 'win32':
        raise RuntimeError('Windows DPAPI unavailable')

    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = Blob(len(value), buffer)
    output = Blob()
    # CRYPTPROTECT_UI_FORBIDDEN: no prompts; CurrentUser is implicit, with no
    # machine-wide flag or alternative-user credential supplied.
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('DPAPI decryption unavailable')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def _aes_decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def _json_file(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Native store too large')
    result = json.loads(path.read_text('utf-8'))
    if not isinstance(result, dict):
        raise ValueError('Native store must be an object')
    return result


def build_cli_environment(environment: Mapping[str, str], *, root: Path | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
        decrypt: Callable[[bytes, bytes, bytes], bytes] | None = None) -> dict[str, str]:
    """Copy environment; use native login only when no explicit auth exists.

    Dependency injection is for deterministic tests and never accepts a browser
    supplied path, key or token. Callers must keep the returned mapping internal.
    """
    result = dict(environment)
    if (result.get('CURSOR_ACCESS_TOKEN', '').strip()
            or result.get('GROK_BOT_GATEWAY_TOKEN', '').strip()
            or sys.platform != 'win32'):
        return result
    try:
        folder = root if root is not None else Path(result['APPDATA']) / 'Grok Bot'
        status = _json_file(folder / 'desktop-status.json')
        version = status.get('appVersion')
        if status.get('signedIn') is not True or not isinstance(version, str) or not re.fullmatch(
                r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
            raise ValueError('Not a supported signed-in desktop session')
        local_state = _json_file(folder / 'Local State')
        encrypted_key = base64.b64decode(local_state['os_crypt']['encrypted_key'], validate=True)
        if not encrypted_key.startswith(b'DPAPI') or len(encrypted_key) <= 5:
            raise ValueError('DPAPI key prefix is required')
        key = (unprotect or _dpapi_unprotect)(encrypted_key[5:])
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError('Invalid AES key')
        store = _json_file(folder / 'sand-secrets.json')
        encoded_accounts = store['cursor-accounts']
        if not isinstance(encoded_accounts, str):
            raise ValueError('Account store must use the native string format')
        accounts = json.loads(encoded_accounts)
        active = accounts.get('active')
        if not isinstance(active, str) or not active:
            raise ValueError('Active account is missing')
        # Select exactly the active account and exactly its access-token field.
        # Do not iterate other identities, select a refresh token or fall back.
        encoded_token = accounts['accounts'][active]['cursor-access-token']
        if not isinstance(encoded_token, str):
            raise ValueError('Native token must be encrypted')
        if encoded_token.startswith('scoped:v1:'):
            fields = encoded_token.split(':', 3)
            if len(fields) != 4 or not fields[2]:
                raise ValueError('Invalid scoped token')
            encoded_token = fields[3]
        encrypted_token = base64.b64decode(encoded_token, validate=True)
        if not encrypted_token.startswith(b'v10') or len(encrypted_token) < 32:
            raise ValueError('Only safeStorage v10 is supported')
        token = (decrypt or _aes_decrypt)(key, encrypted_token[3:15], encrypted_token[15:]).decode('utf-8')
        if not token or len(token) > 16384 or any(character.isspace() or ord(character) < 32 for character in token):
            raise ValueError('Invalid decrypted access token')
        result['CURSOR_ACCESS_TOKEN'] = token
        result.setdefault('SAND_CLIENT_VERSION', version)
        return result
    except Exception:
        raise NativeSessionError('无法复用本机 Grok Bot 登录，请打开应用确认当前账号已登录，或显式配置 CLI 认证。') from None
