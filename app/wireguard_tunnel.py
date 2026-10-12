"""별도 WireGuard 앱 없이 이 앱이 직접 VPN 클라이언트로 붙는다 — android의
WireGuardController.kt(공식 임베딩 라이브러리 GoBackend)에 대응한다.

윈도우용 공식 임베딩 방식(wireguard-windows의 embeddable-dll-service)을
그대로 쓴다:
  - tunnel.dll        : wireguard-windows 터널 엔진(scripts/build_tunnel.ps1로 빌드)
  - wireguard.dll     : wireguard-nt 커널 드라이버
  - wirewol-tunnel-host.exe : tunnel.dll을 불러 서비스로 실행해주는 호스트

터널은 반드시 "WireGuardTunnel$<이름>" 윈도우 서비스(LocalSystem) 안에서
돌아야 한다. 서비스 실행 파일로 PyInstaller 단일 exe 자신을 쓰지 않는 이유:
단일 exe는 실행 시 압축을 푼 뒤 자식 프로세스를 하나 더 띄우는데, 서비스
관리자는 자기가 띄운 프로세스가 직접 연결해오길 기대해서 연결이 꼬일 수
있다 — 그래서 세 파일을 고정 위치(%ProgramData%\\WireWOL PC, SYSTEM/관리자만
접근 가능)에 꺼내두고 작은 호스트 exe를 서비스로 등록한다.

켤 때마다 서비스를 새로 만들고(설정이 바뀌었을 수 있으니), 끌 때 서비스를
지운다 — 수동 시작으로 만들어 재부팅해도 저절로 켜지지 않는다(android처럼
사용자가 누른 상태만 따른다).

서비스 생성/삭제에는 관리자 권한이 필요하다 — 앱 자체가 시작 시 관리자
권한으로 실행된다(wirewolpc.pyw 참고).
"""
import filecmp
import os
import shutil
import subprocess
import sys
import threading
import time

import pywintypes
import win32service
import winerror

TUNNEL_NAME = 'wirewolpc'
SERVICE_NAME = f'WireGuardTunnel${TUNNEL_NAME}'
_DISPLAY_NAME = 'WireWOL PC 터널'
_HOST_EXE = 'wirewol-tunnel-host.exe'
_RUNTIME_FILES = ('tunnel.dll', 'wireguard.dll', _HOST_EXE)

INSTALL_DIR = os.path.join(os.environ.get('PROGRAMDATA', r'C:\ProgramData'), 'WireWOL PC')
_CONF_PATH = os.path.join(INSTALL_DIR, f'{TUNNEL_NAME}.conf')

_START_TIMEOUT_SECONDS = 30
_STOP_TIMEOUT_SECONDS = 20
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# bring_up/bring_down이 동시에 들어오지 않게 막는다(android WireGuardController의
# lock과 같은 역할 — 버튼과 자동 연결이 겹치는 경우).
_lock = threading.Lock()


class WireGuardError(Exception):
    pass


def _bundled_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'tunnel')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'amd64')


def _open_scm(access=win32service.SC_MANAGER_ALL_ACCESS):
    try:
        return win32service.OpenSCManager(None, None, access)
    except pywintypes.error as e:
        if e.winerror == winerror.ERROR_ACCESS_DENIED:
            raise WireGuardError('관리자 권한이 필요합니다 — 앱을 관리자 권한으로 실행해주세요') from e
        raise WireGuardError(f'서비스 관리자에 연결할 수 없습니다: {e.strerror}') from e


def _open_service(scm, access):
    try:
        return win32service.OpenService(scm, SERVICE_NAME, access)
    except pywintypes.error as e:
        if e.winerror == winerror.ERROR_SERVICE_DOES_NOT_EXIST:
            return None
        raise


def is_up() -> bool:
    """메인 스레드에서도 부른다 — 상태 조회만 하므로 가볍고 관리자 권한도 필요 없다."""
    try:
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    except pywintypes.error:
        return False
    try:
        service = _open_service(scm, win32service.SERVICE_QUERY_STATUS)
        if service is None:
            return False
        try:
            state = win32service.QueryServiceStatus(service)[1]
            return state == win32service.SERVICE_RUNNING
        finally:
            win32service.CloseServiceHandle(service)
    except pywintypes.error:
        return False
    finally:
        win32service.CloseServiceHandle(scm)


def _prepare_install_dir():
    """런타임 파일을 고정 위치에 꺼내둔다. 폴더는 SYSTEM/Administrators만
    접근할 수 있게 상속을 끊는다 — 서비스(SYSTEM)가 실행하는 exe와 개인키가
    담긴 .conf가 놓이는 곳이라 일반 사용자가 바꿔치기/열람하면 안 된다."""
    os.makedirs(INSTALL_DIR, exist_ok=True)
    subprocess.run(
        ['icacls', INSTALL_DIR, '/inheritance:r',
         '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
        capture_output=True, creationflags=_NO_WINDOW)
    source_dir = _bundled_dir()
    for name in _RUNTIME_FILES:
        src = os.path.join(source_dir, name)
        dst = os.path.join(INSTALL_DIR, name)
        if not os.path.exists(src):
            raise WireGuardError(f'내장 WireGuard 구성 파일이 없습니다: {name}')
        # 새 버전으로 바뀐 경우에만 덮어쓴다(같으면 건드리지 않음).
        if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
            shutil.copyfile(src, dst)


def _wait_for_state(service, wanted: int, timeout: float) -> tuple:
    deadline = time.monotonic() + timeout
    while True:
        status = win32service.QueryServiceStatus(service)
        state = status[1]
        if state == wanted or state == win32service.SERVICE_STOPPED or time.monotonic() > deadline:
            return status
        time.sleep(0.2)


def _remove_service(scm):
    """서비스가 있으면 멈추고 지운 뒤 실제로 사라질 때까지 기다린다 — 지운
    직후 같은 이름으로 다시 만들면 "삭제 대기 중"(1072)으로 실패하기 때문."""
    service = _open_service(scm, win32service.SERVICE_ALL_ACCESS)
    if service is None:
        return
    try:
        try:
            win32service.ControlService(service, win32service.SERVICE_CONTROL_STOP)
        except pywintypes.error as e:
            if e.winerror != winerror.ERROR_SERVICE_NOT_ACTIVE:
                raise
        _wait_for_state(service, win32service.SERVICE_STOPPED, _STOP_TIMEOUT_SECONDS)
        try:
            win32service.DeleteService(service)
        except pywintypes.error as e:
            if e.winerror != winerror.ERROR_SERVICE_MARKED_FOR_DELETE:
                raise
    finally:
        win32service.CloseServiceHandle(service)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        probe = _open_service(scm, win32service.SERVICE_QUERY_STATUS)
        if probe is None:
            return
        win32service.CloseServiceHandle(probe)
        time.sleep(0.2)


def bring_up(conf_text: str):
    """터널 서비스를 만들어 띄운다. 실패 시 WireGuardError. 백그라운드 스레드에서
    호출할 것(서비스 기동을 최대 수십 초 기다린다)."""
    with _lock:
        # 이미 떠 있으면 다시 올리지 않는다(버튼과 자동 연결이 겹치는 경우).
        if is_up():
            return
        scm = _open_scm()
        try:
            _remove_service(scm)
            _prepare_install_dir()
            with open(_CONF_PATH, 'w', encoding='utf-8', newline='\n') as f:
                f.write(conf_text.strip() + '\n')
            host_exe = os.path.join(INSTALL_DIR, _HOST_EXE)
            try:
                service = win32service.CreateService(
                    scm, SERVICE_NAME, _DISPLAY_NAME,
                    win32service.SERVICE_ALL_ACCESS,
                    win32service.SERVICE_WIN32_OWN_PROCESS,
                    win32service.SERVICE_DEMAND_START,
                    win32service.SERVICE_ERROR_NORMAL,
                    f'"{host_exe}" /service "{_CONF_PATH}"',
                    None, False, ['Nsi', 'TcpIp'], None, None)
            except pywintypes.error as e:
                raise WireGuardError(f'터널 서비스를 만들 수 없습니다: {e.strerror}') from e
            try:
                # embeddable-dll-service README: "absolutely essential; do not forget it."
                win32service.ChangeServiceConfig2(
                    service, win32service.SERVICE_CONFIG_SERVICE_SID_INFO,
                    win32service.SERVICE_SID_TYPE_UNRESTRICTED)
                win32service.StartService(service, None)
                status = _wait_for_state(service, win32service.SERVICE_RUNNING, _START_TIMEOUT_SECONDS)
            except pywintypes.error as e:
                win32service.CloseServiceHandle(service)
                _remove_service(scm)
                raise WireGuardError(f'WireGuard 터널을 시작할 수 없습니다: {e.strerror}') from e
            win32service.CloseServiceHandle(service)
            if status[1] != win32service.SERVICE_RUNNING:
                _remove_service(scm)
                # (서비스 종류, 상태, 허용 제어, Win32 종료 코드, 서비스 고유 종료 코드, ...)
                code = status[4] if status[3] == winerror.ERROR_SERVICE_SPECIFIC_ERROR else status[3]
                raise WireGuardError(f'WireGuard 터널을 시작할 수 없습니다 (오류 코드 {code}) — 설정 내용을 확인해주세요')
        finally:
            # 설정은 서비스가 시작할 때 한 번만 읽는다 — 개인키가 담긴 파일을
            # 디스크에 남겨두지 않는다.
            try:
                os.remove(_CONF_PATH)
            except OSError:
                pass
            win32service.CloseServiceHandle(scm)


def bring_down():
    with _lock:
        scm = _open_scm()
        try:
            _remove_service(scm)
        except pywintypes.error as e:
            raise WireGuardError(f'WireGuard 터널을 끌 수 없습니다: {e.strerror}') from e
        finally:
            win32service.CloseServiceHandle(scm)
