import base64
import json

import pytest

import native_session


def store(tmp_path, value=None, active='owner', signed_in=True, version='0.70.2'):
    key_blob = base64.b64encode(b'DPAPIencrypted-key').decode()
    token_blob = base64.b64encode(b'v10' + b'n' * 12 + b'encrypted-token-and-tag').decode()
    (tmp_path / 'Local State').write_text(json.dumps({'os_crypt': {'encrypted_key': key_blob}}))
    (tmp_path / 'desktop-status.json').write_text(json.dumps({'appVersion': version, 'signedIn': signed_in}))
    accounts = {'active': active, 'accounts': {'owner': {'cursor-access-token': value or token_blob,
                                                      'cursor-refresh-token': 'never-read'}}}
    (tmp_path / 'sand-secrets.json').write_text(json.dumps({'cursor-accounts': json.dumps(accounts)}))


def test_native_session_passes_only_active_token_and_actual_version(monkeypatch, tmp_path):
    monkeypatch.setattr(native_session.sys, 'platform', 'win32')
    store(tmp_path)
    unprotected = []
    decrypted = []
    def unprotect(value):
        unprotected.append(value)
        return b'k' * 32
    def decrypt(key, nonce, ciphertext):
        decrypted.append((key, nonce, ciphertext))
        return b'fake-active-token'
    source = {'APPDATA': str(tmp_path), 'OTHER': 'preserved'}
    result = native_session.build_cli_environment(source, root=tmp_path, unprotect=unprotect, decrypt=decrypt)
    assert result['CURSOR_ACCESS_TOKEN'] == 'fake-active-token'
    assert result['SAND_CLIENT_VERSION'] == '0.70.2'
    assert result['OTHER'] == 'preserved'
    assert 'CURSOR_ACCESS_TOKEN' not in source
    assert unprotected == [b'encrypted-key']
    assert decrypted == [(b'k' * 32, b'n' * 12, b'encrypted-token-and-tag')]
    assert all('REFRESH' not in key.upper() for key in result)


@pytest.mark.parametrize('name', ['CURSOR_ACCESS_TOKEN', 'GROK_BOT_GATEWAY_TOKEN'])
def test_explicit_auth_does_not_touch_native_files(monkeypatch, tmp_path, name):
    monkeypatch.setattr(native_session.sys, 'platform', 'win32')
    environment = {name: 'fake-configured', 'SAND_CLIENT_VERSION': 'custom'}
    assert native_session.build_cli_environment(environment, root=tmp_path) == environment


def test_scoped_storage_prefix_is_supported(monkeypatch, tmp_path):
    monkeypatch.setattr(native_session.sys, 'platform', 'win32')
    blob = base64.b64encode(b'v10' + b'n' * 12 + b'ciphertext-and-tag').decode()
    store(tmp_path, value='scoped:v1:owner:' + blob)
    result = native_session.build_cli_environment({}, root=tmp_path,
             unprotect=lambda value: b'k' * 32, decrypt=lambda *args: b'fake-token')
    assert result['CURSOR_ACCESS_TOKEN'] == 'fake-token'


@pytest.mark.parametrize('change', ['signed_out', 'no_active', 'plaintext', 'wrong_version', 'bad_dpapi', 'bad_cipher'])
def test_invalid_native_storage_returns_generic_error(monkeypatch, tmp_path, change):
    monkeypatch.setattr(native_session.sys, 'platform', 'win32')
    store(tmp_path, signed_in=change != 'signed_out', active='' if change == 'no_active' else 'owner',
          value='plaintext-private-token' if change == 'plaintext' else None,
          version='private-version' if change == 'wrong_version' else '0.70.2')
    if change == 'bad_dpapi':
        (tmp_path / 'Local State').write_text(json.dumps({'os_crypt': {'encrypted_key': base64.b64encode(b'other-private').decode()}}))
    def decrypt(*args):
        if change == 'bad_cipher':
            raise RuntimeError('private-secret-in-underlying-error')
        return b'fake-token'
    with pytest.raises(native_session.NativeSessionError) as error:
        native_session.build_cli_environment({}, root=tmp_path, unprotect=lambda value: b'k' * 32, decrypt=decrypt)
    assert 'private' not in str(error.value)
    assert 'plaintext' not in str(error.value)
    assert 'refresh' not in str(error.value)


def test_non_windows_has_no_credential_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(native_session.sys, 'platform', 'linux')
    assert native_session.build_cli_environment({'OTHER': 'ok'}, root=tmp_path) == {'OTHER': 'ok'}
