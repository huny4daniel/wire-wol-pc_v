"""여러 화면이 같이 쓰는 UI 부품 — 색상/스타일, 비동기 실행 도우미, 안드로이드
Toast/Snackbar에 대응하는 창 안 알림."""
import threading

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

# android res/values/colors.xml과 동일한 색.
BACKGROUND = '#0e0e0e'
SURFACE = '#1a1a1a'
TEXT = '#ffffff'
HINT = '#8a8a8a'
ACCENT = '#4d7fd6'
ACCENT_GREEN = '#3f9d5a'
ACCENT_RED = '#c44d4d'

STYLESHEET = f"""
QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-family: 'Malgun Gothic'; font-size: 10pt; }}
QDialog {{ background: {BACKGROUND}; }}
QLabel#hint {{ color: {HINT}; }}
QLabel#sectionTitle {{ font-weight: bold; font-size: 11pt; }}
QFrame#card {{ background: {SURFACE}; border-radius: 6px; }}
QFrame#card QLabel {{ background: transparent; }}
QPushButton {{
    background: {SURFACE}; color: {TEXT}; border: none; border-radius: 6px;
    padding: 8px 12px;
}}
QPushButton:hover {{ background: #262626; }}
QPushButton:pressed {{ background: #303030; }}
QPushButton:disabled {{ color: {HINT}; }}
QPushButton#flat {{ background: transparent; color: {HINT}; }}
QPushButton#flat:hover {{ color: {TEXT}; }}
QPushButton#green {{ background: {ACCENT_GREEN}; font-weight: bold; font-size: 12pt; }}
QPushButton#green:hover {{ background: #47ad65; }}
QPushButton#red {{ background: {ACCENT_RED}; font-weight: bold; font-size: 11pt; }}
QPushButton#red:hover {{ background: #d45a5a; }}
QPushButton#accent {{ background: {ACCENT}; }}
QPushButton#accent:hover {{ background: #5a8ce0; }}
QLineEdit, QSpinBox {{
    background: {SURFACE}; border: 1px solid #333; border-radius: 4px; padding: 6px;
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QMessageBox QLabel {{ min-width: 260px; }}
"""


class _Task(QObject):
    done = pyqtSignal(object, object)  # (result, error)


_running_tasks = set()


def run_async(fn, callback=None):
    """스레드에서 fn()을 실행하고, 끝나면 GUI 스레드에서 callback(result, error)을
    호출한다 — 네트워크/서비스 호출이 창을 멈추지 않게 한다(android의
    Thread { ... handler.post { ... } } 패턴에 대응)."""
    task = _Task()
    _running_tasks.add(task)  # emit 전에 GC되지 않게 붙잡아둔다

    def finish(result, error):
        _running_tasks.discard(task)
        if callback is not None:
            callback(result, error)

    task.done.connect(finish)

    def worker():
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 - 원인 불문 UI에서 처리
            task.done.emit(None, e)
            return
        task.done.emit(result, None)

    threading.Thread(target=worker, daemon=True).start()


class ClickableLabel(QLabel):
    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


TOAST_SHORT_MS = 2000
TOAST_LONG_MS = 3500


class Toast(QLabel):
    """android Toast — 창 아래쪽에 잠깐 떴다 사라지는 안내 문구. 새 문구가 오면
    이전 것을 바로 대체한다."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f'background: #2b2b2b; color: {TEXT}; border-radius: 14px; padding: 8px 14px;')
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, duration_ms: int = TOAST_SHORT_MS):
        parent = self.parentWidget()
        self.setText(text)
        max_width = parent.width() - 40
        self.setFixedWidth(max_width)
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 24)
        self.raise_()
        self.show()
        self._timer.start(duration_ms)


class Snackbar(QFrame):
    """android Snackbar — 창 맨 아래에 문구와 동작 버튼 하나를 정해진 시간 동안
    보여준다(PC 끄기 직후 10초간 "취소"를 누를 수 있게 하는 용도)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(f'QFrame {{ background: #2b2b2b; border-radius: 6px; }}'
                           f'QLabel {{ background: transparent; }}'
                           f'QPushButton {{ background: transparent; color: {ACCENT}; font-weight: bold; }}')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 6, 6)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._button = QPushButton()
        self._button.clicked.connect(self._on_action)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._button)
        self._action = None
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, action_text: str, action, duration_ms: int):
        parent = self.parentWidget()
        self._label.setText(text)
        self._button.setText(action_text)
        self._action = action
        self.setFixedWidth(parent.width() - 24)
        self.adjustSize()
        self.move(12, parent.height() - self.height() - 12)
        self.raise_()
        self.show()
        self._timer.start(duration_ms)

    def _on_action(self):
        self._timer.stop()
        self.hide()
        if self._action is not None:
            self._action()
