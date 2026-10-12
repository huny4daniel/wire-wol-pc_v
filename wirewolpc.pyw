"""WireWOL PC 진입점 — wire-wol_v 안드로이드 리모컨 앱의 윈도우판.

PC 켜기/끄기 명령을 "보내는" 쪽이다. 명령을 "받는" 쪽은 대상 PC에 설치하는
wire-wol_v의 windows 컴패니언(wirewol.pyw)으로, 별개의 프로그램이다.

와이어가드 켜기/끄기는 내장 터널 서비스를 만들고 지우는 일이라 관리자 권한이
필요하다 — 매번 UAC 승인을 띄우는 대신 앱 시작 시 한 번만 관리자 권한으로
실행한다. 빌드된 exe는 PyInstaller --uac-admin 매니페스트로 실행 즉시 상승되므로
아래 재실행 로직은 바로 통과하고, `python wirewolpc.pyw`로 띄우는 개발 환경에서만
실제로 재실행이 일어난다.
"""
import ctypes
import os
import subprocess
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from app.main_window import MainWindow
from app.ui_common import STYLESHEET

_MUTEX_NAME = 'WireWOLPC_SingleInstance'


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> bool:
    args = sys.argv[1:] if getattr(sys, 'frozen', False) else [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    result = ctypes.windll.shell32.ShellExecuteW(
        None, 'runas', sys.executable, subprocess.list2cmdline(args), None, 1)
    return result > 32


def _icon_path() -> str:
    base = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'assets', 'app_icon.ico')


def main():
    if not _is_admin():
        # UAC를 거절하면 그냥 끝낸다 — 관리자 권한 없이는 와이어가드를 켤 수 없다.
        _relaunch_as_admin()
        sys.exit(0)

    # 같은 터널 서비스를 두 창이 동시에 다루지 않게 하나만 실행한다.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(None, 'WireWOL이 이미 실행 중입니다.', 'WireWOL', 0x40)
        sys.exit(0)

    # 작업 표시줄이 python.exe 아이콘 대신 앱 아이콘으로 묶이게 한다.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('WireWOL.PC')
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    icon = QIcon(_icon_path())
    app.setWindowIcon(icon)
    window = MainWindow(icon)
    window.show()
    code = app.exec_()
    ctypes.windll.kernel32.CloseHandle(mutex)
    sys.exit(code)


if __name__ == '__main__':
    main()
