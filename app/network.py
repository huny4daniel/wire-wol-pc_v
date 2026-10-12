"""네트워크 유틸 — 매직 패킷 전송(android MainActivity.sendWakeOnLan),
직접 도달 여부 탐지(android NetworkProbe), 네트워크 연결 유무(android
hasAnyNetwork)."""
import ctypes
import re
import socket

_MAC_SPLIT_RE = re.compile(r'[:\-]')
_PROBE_TIMEOUT_SECONDS = 1.5


def is_reachable_directly(host: str, port, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """"지금 집 안인가 밖인가"를 SSID 비교 대신 실제 도달 여부로 판단한다 —
    컴패니언 포트로 짧게 TCP 연결을 찔러봐서 열리면 같은 LAN(또는 이미 다른
    경로로 도달 가능한 상태)이라 "내부", 실패하면 "외부"로 본다. 반드시
    백그라운드 스레드에서 호출할 것."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def has_any_network() -> bool:
    """폰의 activeNetwork 확인에 대응 — 랜선/와이파이 연결이 하나도 없으면
    "PC 꺼짐"이 아니라 "네트워크 연결 없음"으로 구분해 보여주기 위함."""
    try:
        flags = ctypes.c_ulong()
        return bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    except Exception:
        return True


def _wol_targets(host: str | None) -> list[str]:
    """255.255.255.255(제한 브로드캐스트)는 라우터를 넘지 못해 와이어가드
    터널로는 절대 집 LAN에 닿지 않는다 — 그래서 PC 주소 기준 /24 대역의 지정
    브로드캐스트(x.x.x.255)와 PC 주소 자체(유니캐스트, 공유기에 ARP가 남아있는
    동안 유효)로도 함께 보낸다. 어느 쪽이 통할지는 공유기 설정에 달려 있어
    전부 보내는 편이 낫다."""
    targets = ['255.255.255.255']
    parts = (host or '').split('.')
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        targets.append('.'.join(parts[:3] + ['255']))
        targets.append(host)
    return targets


def parse_mac(mac: str) -> bytes:
    try:
        mac_bytes = bytes(int(part, 16) for part in _MAC_SPLIT_RE.split(mac.strip()) if part)
    except ValueError:
        mac_bytes = b''
    if len(mac_bytes) != 6:
        raise ValueError('MAC 주소 형식이 올바르지 않습니다')
    return mac_bytes


def send_magic_packet(mac: str, host: str | None = None):
    """매직 패킷 전송. MAC 형식이 틀리면 ValueError, 개별 대상 전송 실패는
    무시한다(어느 경로가 통할지 모르니 가능한 것만 보낸다)."""
    packet = b'\xff' * 6 + parse_mac(mac) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in _wol_targets(host):
            try:
                sock.sendto(packet, (target, 9))
            except OSError:
                pass
