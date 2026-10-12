"""설정 전체(PC 등록/WireGuard/원격 WOL/자동 종료)를 비밀번호로 암호화한 파일
하나로 내보내고 다시 불러온다.

**wire-wol_v 안드로이드 앱의 SettingsBackup.kt와 같은 형식**이라 어느 쪽에서
내보낸 파일이든 서로 불러올 수 있다 — 형식이나 내용 필드를 바꾸면 양쪽을 함께
고칠 것.

파일(겉): {"format":"wirewol-settings","version":1,
  "kdf":{"name":"PBKDF2-HMAC-SHA256","iterations":N,"salt":b64},
  "cipher":{"name":"AES-256-GCM","nonce":b64},"data":b64(암호문+태그)}
내용(속, UTF-8 JSON): {"pairing":{host,port,token,mac}|null,
  "wireguard_conf":str|null,"router_wol":{host,port,id,password}|null,
  "auto_shutdown":{"enabled":bool,"delay_minutes":int}}

비밀번호는 UTF-8로 바꿔 PBKDF2에 넣는다(안드로이드 쪽도 같은 방식으로 직접 계산).
공유기 인증서 지문(TOFU)과 예약된 종료 시각처럼 그 기기에서만 의미 있는 값은
담지 않는다.
"""
import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import DEFAULT_AUTO_SHUTDOWN_MINUTES, ClientConfig

FILE_NAME = 'wirewol_settings.json'
_FORMAT = 'wirewol-settings'
_VERSION = 1
_KDF_NAME = 'PBKDF2-HMAC-SHA256'
_CIPHER_NAME = 'AES-256-GCM'
_ITERATIONS = 200_000
_MAX_ITERATIONS = 10_000_000
_MAX_DELAY_MINUTES = 23 * 60 + 59


class InvalidFileError(Exception):
    pass


class WrongPasswordError(Exception):
    pass


def collect(config: ClientConfig) -> dict:
    pairing = config.load_pairing()
    router = config.load_router_wol()
    enabled, delay_minutes = config.load_auto_shutdown()
    return {
        'pairing': {
            'host': pairing['host'], 'port': str(pairing['port']),
            'token': pairing['token'], 'mac': pairing.get('mac', '') or '',
        } if pairing else None,
        'wireguard_conf': config.load_wireguard_conf(),
        'router_wol': {
            'host': router['host'], 'port': str(router['port']),
            'id': router['id'], 'password': router['password'],
        } if router else None,
        'auto_shutdown': {'enabled': enabled, 'delay_minutes': delay_minutes},
    }


def apply(contents: dict, config: ClientConfig):
    """불러온 내용으로 현재 설정을 통째로 바꾼다 — 파일에 없는 항목은 지운다."""
    pairing = contents['pairing']
    if pairing:
        config.save_pairing(pairing['host'], pairing['port'], pairing['token'], pairing['mac'])
    else:
        config.clear_pairing()
    if contents['wireguard_conf']:
        config.save_wireguard_conf(contents['wireguard_conf'])
    else:
        config.clear_wireguard_conf()
    router = contents['router_wol']
    if router:
        config.save_router_wol(router['host'], router['port'], router['id'], router['password'])
    else:
        config.clear_router_wol()
    auto = contents['auto_shutdown']
    config.save_auto_shutdown(auto['enabled'], auto['delay_minutes'])


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, 32)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def export_text(contents: dict, password: str) -> str:
    """키 유도가 수십만 번 반복되므로 run_async로 백그라운드에서 호출할 것."""
    plain = json.dumps(contents, ensure_ascii=False).encode('utf-8')
    salt = os.urandom(16)
    nonce = os.urandom(12)
    encrypted = AESGCM(_derive_key(password, salt, _ITERATIONS)).encrypt(nonce, plain, None)
    envelope = {
        'format': _FORMAT,
        'version': _VERSION,
        'kdf': {'name': _KDF_NAME, 'iterations': _ITERATIONS, 'salt': _b64(salt)},
        'cipher': {'name': _CIPHER_NAME, 'nonce': _b64(nonce)},
        'data': _b64(encrypted),
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2)


def import_text(file_text: str, password: str) -> dict:
    """백그라운드에서 호출할 것. 형식이 다르면 InvalidFileError, 비밀번호가
    틀리면(GCM 태그 불일치) WrongPasswordError."""
    try:
        envelope = json.loads(file_text)
        if envelope.get('format') != _FORMAT or envelope.get('version') != _VERSION:
            raise InvalidFileError()
        kdf, cipher = envelope['kdf'], envelope['cipher']
        if kdf['name'] != _KDF_NAME or cipher['name'] != _CIPHER_NAME:
            raise InvalidFileError()
        iterations = int(kdf['iterations'])
        salt = base64.b64decode(kdf['salt'])
        nonce = base64.b64decode(cipher['nonce'])
        encrypted = base64.b64decode(envelope['data'])
    except InvalidFileError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidFileError()
    if not 1 <= iterations <= _MAX_ITERATIONS:
        raise InvalidFileError()
    try:
        plain = AESGCM(_derive_key(password, salt, iterations)).decrypt(nonce, encrypted, None)
    except InvalidTag:
        raise WrongPasswordError()
    except ValueError:
        raise InvalidFileError()
    try:
        return _parse_contents(json.loads(plain.decode('utf-8')))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InvalidFileError()


def _parse_contents(data: dict) -> dict:
    # port는 문자열/숫자 어느 쪽이 와도 받아준다(페어링 QR은 숫자로 담는다).
    pairing = data.get('pairing')
    if pairing:
        pairing = {
            'host': str(pairing['host']), 'port': str(pairing['port']),
            'token': str(pairing['token']), 'mac': str(pairing.get('mac') or ''),
        }
        if not (pairing['host'] and pairing['port'] and pairing['token']):
            pairing = None
    router = data.get('router_wol')
    if router:
        router = {
            'host': str(router['host']), 'port': str(router['port']),
            'id': str(router['id']), 'password': str(router['password']),
        }
    auto = data.get('auto_shutdown') or {}
    delay = auto.get('delay_minutes')
    if not isinstance(delay, int) or not 1 <= delay <= _MAX_DELAY_MINUTES:
        delay = DEFAULT_AUTO_SHUTDOWN_MINUTES
    return {
        'pairing': pairing or None,
        'wireguard_conf': data.get('wireguard_conf') or None,
        'router_wol': router or None,
        'auto_shutdown': {'enabled': bool(auto.get('enabled', False)), 'delay_minutes': delay},
    }
