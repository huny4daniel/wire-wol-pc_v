"""WireWOL 윈도우 컴패니언(wire-wol_v의 windows/wirewol.pyw)의 최소 API 호출.

android CompanionClient.kt와 동일한 계약이다 — 토큰은 X-WireWOL-Token 헤더로
싣고, TLS는 쓰지 않는다(같은 LAN 또는 WireGuard 터널 안의 사설 주소로만
통신한다고 전제).
"""
from dataclasses import dataclass

import requests

_TOKEN_HEADER = 'X-WireWOL-Token'
# 와이어가드를 막 켠 직후의 첫 요청은 핸드셰이크(유실 시 5초 뒤 재시도)가
# 끝나야 나가므로, 짧은 타임아웃은 터널이 멀쩡해도 실패로 끝난다.
_TIMEOUT = 8


@dataclass
class CompanionConfig:
    host: str
    port: str
    token: str

    @property
    def base_url(self) -> str:
        return f'http://{self.host}:{self.port}'

    @classmethod
    def from_pairing(cls, pairing: dict) -> 'CompanionConfig':
        return cls(pairing['host'], str(pairing['port']), pairing['token'])


class CompanionError(Exception):
    pass


def ping(config: CompanionConfig) -> str:
    """도달 가능하면 PC의 MAC을 돌려준다. 도달 불가/오류 시 CompanionError."""
    try:
        resp = requests.get(f'{config.base_url}/api/ping',
                            headers={_TOKEN_HEADER: config.token}, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise CompanionError(str(e) or '연결할 수 없습니다') from e
    if resp.status_code != 200:
        raise CompanionError(f'HTTP {resp.status_code}')
    try:
        return resp.json().get('mac', '')
    except ValueError:
        return ''


def shutdown(config: CompanionConfig, delay_seconds: int | None = None):
    """delay_seconds가 None이면 컴패니언 기본 지연(즉시 끄기 버튼용)을 쓴다."""
    body = {'delay_seconds': delay_seconds} if delay_seconds is not None else None
    _post(config, '/api/shutdown', body)


def cancel_shutdown(config: CompanionConfig):
    _post(config, '/api/shutdown/cancel', None)


def _post(config: CompanionConfig, path: str, body: dict | None):
    try:
        resp = requests.post(f'{config.base_url}{path}', json=body,
                             headers={_TOKEN_HEADER: config.token}, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise CompanionError(str(e) or '연결할 수 없습니다') from e
    if resp.status_code != 200:
        try:
            message = resp.json().get('error') or ''
        except ValueError:
            message = ''
        raise CompanionError(message or f'HTTP {resp.status_code}')
