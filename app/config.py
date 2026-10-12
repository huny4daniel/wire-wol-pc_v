"""윈도우 로그인 계정에 묶인(DPAPI) 암호화 저장소.

안드로이드 앱은 페어링 정보/원격 WOL 자격 증명/WireGuard 개인 키가 담긴
설정을 EncryptedSharedPreferences(AES256)에 저장한다. 데스크톱에는 그런
표준 API가 없어 대신 Windows DPAPI(CryptProtectData/CryptUnprotectData)를
쓴다 — 같은 윈도우 계정으로 로그인한 사용자만 복호화할 수 있어 목적이
동일하다.

안드로이드는 pairing_prefs/router_wol_prefs/wireguard_prefs/shutdown_prefs로
파일을 나누지만, 여기선 하나의 암호화 파일에 같이 담는다 — 전부 같은 사용자
하나만 접근하는 로컬 파일이라 나눠서 얻는 격리 이점이 없다.
"""
import json
import os

import win32crypt

# 프로그램 폴더는 exe를 옮기거나 새 버전을 받으면서 쉽게 바뀌므로, 실제 저장
# 위치는 사용자별 숨김 폴더(AppData\Local)에 둔다.
DATA_DIR = os.path.join(os.environ['LOCALAPPDATA'], 'WireWOL PC')
os.makedirs(DATA_DIR, exist_ok=True)
SECRETS_FILE = os.path.join(DATA_DIR, 'wirewolpc_secrets.dat')

DEFAULT_ROUTER_PORT = '443'
DEFAULT_AUTO_SHUTDOWN_MINUTES = 60


class ClientConfig:
    def __init__(self):
        self._data = {}
        self.load()

    # ---- 저장/불러오기 ----

    def load(self):
        if not os.path.exists(SECRETS_FILE):
            self._data = {}
            return
        try:
            with open(SECRETS_FILE, 'rb') as f:
                encrypted = f.read()
            _, plain = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
            self._data = json.loads(plain.decode('utf-8'))
        except Exception:
            self._data = {}

    def _save(self):
        plain = json.dumps(self._data, ensure_ascii=False).encode('utf-8')
        encrypted = win32crypt.CryptProtectData(plain, 'WireWOL PC secrets', None, None, None, 0)
        tmp_path = SECRETS_FILE + '.tmp'
        with open(tmp_path, 'wb') as f:
            f.write(encrypted)
        os.replace(tmp_path, SECRETS_FILE)

    # ---- PC 등록(컴패니언 연결 정보 + MAC) ----
    # android PairingConfig.kt와 대응. MAC은 PC 쪽 getmac 결과를 QR로 받아오지만
    # 어댑터가 여러 개라 잘못 잡힌 경우를 위해 직접 입력으로 고칠 수 있다.

    def load_pairing(self) -> dict | None:
        pairing = self._data.get('pairing')
        if not pairing or not all(pairing.get(k) for k in ('host', 'port', 'token')):
            return None
        return pairing

    def load_mac(self) -> str:
        return (self._data.get('pairing') or {}).get('mac', '') or ''

    def save_pairing(self, host: str, port: str, token: str, mac: str):
        self._data['pairing'] = {'host': host, 'port': str(port), 'token': token, 'mac': mac}
        self._save()

    # ---- 원격 WOL(공유기 API) ----

    def load_router_wol(self) -> dict | None:
        router = self._data.get('router_wol')
        if not router or not all(router.get(k) for k in ('host', 'port', 'id', 'password')):
            return None
        return router

    def save_router_wol(self, host: str, port: str, user_id: str, password: str):
        self._data['router_wol'] = {'host': host, 'port': port, 'id': user_id, 'password': password}
        self._save()

    def clear_router_wol(self):
        self._data.pop('router_wol', None)
        self._save()

    def get_pinned_cert_fingerprint(self, host: str) -> str | None:
        return self._data.get('router_cert_fp', {}).get(host)

    def set_pinned_cert_fingerprint(self, host: str, fingerprint: str):
        self._data.setdefault('router_cert_fp', {})[host] = fingerprint
        self._save()

    # ---- WireGuard 설정(.conf 원문) ----
    # QR에서 읽은 원문(개인키 포함)을 그대로 저장한다 — 터널을 올릴 때마다
    # 이 원문으로 서비스를 새로 만들어 설정이 바뀌어도 항상 최신 내용으로 붙는다.

    def load_wireguard_conf(self) -> str | None:
        return self._data.get('wireguard_conf') or None

    def save_wireguard_conf(self, conf_text: str):
        self._data['wireguard_conf'] = conf_text
        self._save()

    def clear_wireguard_conf(self):
        self._data.pop('wireguard_conf', None)
        self._save()

    # ---- 예약된 종료 시각 / 종료 확인 후 와이어가드 끄기 감시 ----
    # android shutdownPrefs(KEY_PENDING_SHUTDOWN / KEY_WIREGUARD_OFF_WATCH_FROM)에
    # 대응 — 둘 다 epoch 초. 앱을 다시 켜도 상태 표시와 감시를 이어간다.

    def load_pending_shutdown_at(self) -> float:
        return self._data.get('pending_shutdown_at') or 0

    def save_pending_shutdown_at(self, target: float):
        self._data['pending_shutdown_at'] = target
        self._save()

    def clear_pending_shutdown_at(self):
        if self._data.pop('pending_shutdown_at', None) is not None:
            self._save()

    def load_wireguard_off_watch_from(self) -> float:
        return self._data.get('wireguard_off_watch_from') or 0

    def save_wireguard_off_watch_from(self, target: float):
        self._data['wireguard_off_watch_from'] = target
        self._save()

    def clear_wireguard_off_watch_from(self):
        if self._data.pop('wireguard_off_watch_from', None) is not None:
            self._save()

    # ---- PC 켜기 후 자동 종료 ----
    # android AutoShutdownConfig.kt에 대응 — 기본 사용 안 함. auto_shutdown_wait_from은
    # android shutdownPrefs의 KEY_AUTO_SHUTDOWN_WAIT_FROM(PC 켜기를 누른 시각, epoch 초).

    def load_auto_shutdown(self) -> tuple[bool, int]:
        data = self._data.get('auto_shutdown') or {}
        return bool(data.get('enabled', False)), int(data.get('delay_minutes') or DEFAULT_AUTO_SHUTDOWN_MINUTES)

    def save_auto_shutdown(self, enabled: bool, delay_minutes: int):
        self._data['auto_shutdown'] = {'enabled': enabled, 'delay_minutes': delay_minutes}
        self._save()

    def load_auto_shutdown_wait_from(self) -> float:
        return self._data.get('auto_shutdown_wait_from') or 0

    def save_auto_shutdown_wait_from(self, started: float):
        self._data['auto_shutdown_wait_from'] = started
        self._save()

    def clear_auto_shutdown_wait_from(self):
        if self._data.pop('auto_shutdown_wait_from', None) is not None:
            self._save()

    # ---- 설정 파일 내보내기/불러오기(app/settings_backup.py) ----

    def clear_pairing(self):
        self._data.pop('pairing', None)
        self._save()

    # ---- 전체 초기화 ----
    # android의 "저장된 정보 초기화"와 동일하게 연결 정보/WireGuard/원격 WOL/자동
    # 종료 설정만 지운다(예약 표시는 실제 PC 쪽 예약이 남아있을 수 있으니 그대로 둔다).

    def clear_all(self):
        for key in ('pairing', 'wireguard_conf', 'router_wol', 'router_cert_fp', 'auto_shutdown'):
            self._data.pop(key, None)
        self._save()
