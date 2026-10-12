"""집 공유기(iptime 등) 내장 원격 WOL API 직접 호출 — android RouterWol.kt와
동일한 흐름(로그인 → wol/signal)을 그대로 옮긴 것.

로컬 매직 패킷은 PC가 오래 꺼져있으면 공유기 ARP 캐시가 만료돼 실패할 수
있는데, 공유기 자신에게 대신 브로드캐스트하라고 시키면 이 문제가 없다 — 또한
공유기 원격 관리 포트가 열려있다면 집 밖에서 WireGuard 없이도 PC를 켤 수 있다.

공유기가 자체 서명 인증서를 쓰므로 일반 CA 검증 대신 TOFU(최초 연결 지문을
저장해두고 이후 비교)로 처리한다. 문서화되지 않은 공식 앱 내부 API를 재현한
것이라 공유기 펌웨어가 바뀌면 깨질 수 있다.
"""
import hashlib
import http.client
import json
import ssl

from app.config import ClientConfig

_USER_AGENT = 'Mozilla/5.0 (Windows NT) WireWOL'


class RouterWolError(Exception):
    pass


def _fingerprint(der_cert: bytes) -> str:
    return ':'.join(f'{b:02X}' for b in hashlib.sha256(der_cert).digest())


def _build_connection(host: str, port: str) -> http.client.HTTPSConnection:
    ctx = ssl._create_unverified_context()
    # TLS 1.3 협상 시 응답 없이 소켓만 끊어버리는 임베디드 관리 웹서버가
    # 있어(RouterWol.kt에서 겪은 것과 동일한 문제) 1.2로 고정한다.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return http.client.HTTPSConnection(host, int(port), timeout=8, context=ctx)


def _verify_pinned_cert(config: ClientConfig, host: str, conn: http.client.HTTPSConnection):
    der_cert = conn.sock.getpeercert(binary_form=True)
    if not der_cert:
        raise RouterWolError('공유기가 인증서를 제공하지 않았습니다')
    fingerprint = _fingerprint(der_cert)
    pinned = config.get_pinned_cert_fingerprint(host)
    if pinned is None:
        config.set_pinned_cert_fingerprint(host, fingerprint)
    elif pinned != fingerprint:
        raise RouterWolError('공유기 인증서가 이전과 달라 신뢰할 수 없습니다'
                             '(공유기를 교체했다면 원격 WOL 설정을 다시 저장해주세요)')


def _call(config: ClientConfig, host: str, port: str, body: dict, cookie: str | None) -> tuple[dict, str | None]:
    """요청마다 새 연결을 연다 — 서버가 Connection: close로 매번 소켓을 끊어
    커넥션 재사용이 애초에 불가능하다(로그인 세션은 쿠키로만 유지됨)."""
    conn = _build_connection(host, port)
    try:
        conn.connect()
        _verify_pinned_cert(config, host, conn)
        origin = f'https://{host}:{port}'
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': _USER_AGENT,
            'Accept': 'application/json, text/plain, */*',
            'Connection': 'close',
            'Origin': origin,
            'Referer': f'{origin}/',
        }
        if cookie:
            headers['Cookie'] = cookie
        conn.request('POST', '/cgi/service.cgi', body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status >= 400:
            raise RouterWolError(f'공유기 요청 실패 (HTTP {resp.status})')
        set_cookie = resp.getheader('Set-Cookie')
        if set_cookie:
            # "name=value; Path=/; ..." 중 쿠키 본체만 다음 요청에 싣는다.
            set_cookie = set_cookie.split(';', 1)[0]
        try:
            return json.loads(raw.decode('utf-8') or '{}'), (set_cookie or cookie)
        except ValueError:
            return {}, (set_cookie or cookie)
    finally:
        conn.close()


def trigger_remote_wake(config: ClientConfig, router: dict, mac: str):
    """로그인 → wol/signal. 네트워크 호출이므로 백그라운드 스레드에서 호출할 것."""
    host, port = router['host'], router['port']
    try:
        login_body = {'method': 'session/login', 'params': {'id': router['id'], 'pw': router['password']}}
        result, cookie = _call(config, host, port, login_body, None)
        if result.get('result') != 'done':
            raise RouterWolError('공유기 로그인 실패 — ID/비밀번호를 확인해주세요')
        _call(config, host, port, {'method': 'wol/signal', 'params': [mac]}, cookie)
    except ssl.SSLError as e:
        raise RouterWolError(f'공유기에 연결할 수 없습니다: {e}') from e
    except OSError as e:
        raise RouterWolError(f'공유기에 연결할 수 없습니다: {e}') from e
