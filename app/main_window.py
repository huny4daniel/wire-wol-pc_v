"""WireWOL PC 리모컨 메인 창 — android MainActivity.kt를 그대로 옮긴 것. 버튼
다섯 개(PC 켜기/PC 끄기/예약/와이어가드 켜기/와이어가드 끄기)와 상태 카드가
전부이고, 자주 쓰지 않는 것들은 [SettingsDialog]로 분리했다.

android의 액티비티 수명 주기는 창 상태로 대응시킨다:
  - onResume  ↔ 창이 처음 뜨거나 최소화에서 복귀
  - onPause   ↔ 창 최소화
  - 앱이 백그라운드로 가면 와이어가드 끄기(WireWolApplication) ↔ 창을 닫아
    앱을 끝낼 때만 끈다(PC에서는 최소화해도 그대로 유지).
"""
import time
from datetime import datetime

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app import companion_client, network, wireguard_tunnel
from app.companion_client import CompanionConfig
from app.config import ClientConfig
from app.router_wol import trigger_remote_wake
from app.settings_dialog import SettingsDialog
from app.ui_common import (
    ACCENT_GREEN, ACCENT_RED, HINT, TOAST_LONG_MS, ClickableLabel, Snackbar,
    Toast, run_async,
)
from app.version import VERSION

QUICK_SHUTDOWN_SECONDS = 10
WIREGUARD_OFF_WATCH_INTERVAL_MS = 5_000
WIREGUARD_OFF_WATCH_TIMEOUT_SECONDS = 10 * 60
POWER_ON_POLL_INTERVAL_MS = 5_000
FAST_POWER_ON_POLL_INTERVAL_MS = 1_000
FAST_POWER_ON_POLL_DURATION_SECONDS = 15
AUTO_REFRESH_INTERVAL_MS = 5_000
WAKE_RETRY_POLL_INTERVAL_MS = 1_000
WAKE_RETRY_TIMEOUT_SECONDS = 30
TUNNEL_SETTLE_PROBE_TIMEOUT_SECONDS = 3.0
SHUTDOWN_SNACKBAR_MS = 10_000
AUTO_SHUTDOWN_WAIT_TIMEOUT_SECONDS = 10 * 60
NETWORK_WATCH_INTERVAL_MS = 15_000

STATUS_POWER_CHECKING = 'PC 상태: 확인 중…'
STATUS_POWER_ON = 'PC 상태: 켜짐'
STATUS_POWER_OFF = 'PC 상태: 꺼짐 (또는 응답 없음)'
STATUS_POWER_UNREACHABLE_WG_OFF = 'PC 상태: 응답 없음 (집 밖이라면 와이어가드를 켜야 확인됩니다)'
STATUS_POWER_NO_NETWORK = 'PC 상태: 네트워크 연결 없음'
STATUS_POWER_PAIRING_MISSING = 'PC 상태: 연결 정보가 없습니다'


def _time_label(epoch_seconds: float) -> str:
    dt = datetime.fromtimestamp(epoch_seconds)
    meridiem = '오전' if dt.hour < 12 else '오후'
    hour = dt.hour % 12 or 12
    return f'{meridiem} {hour}:{dt.minute:02d}'


class MainWindow(QWidget):
    def __init__(self, icon: QIcon | None = None):
        super().__init__()
        self.config = ClientConfig()

        # 화면이 앞으로 나온(onResume) 뒤 전원 상태를 실제로 한 번이라도
        # 확인했는지 — 그 첫 확인 때만 "확인 중..."을 보여주고(최소화돼 있던
        # 동안의 낡은 상태를 그대로 보여주지 않도록), 그 이후(자동 재확인/PC
        # 켜기 폴링 등)에는 결과가 올 때까지 직전 상태를 유지한 채 조용히
        # 확인하다가 값이 나오면 그때 갱신한다.
        self._has_checked_power_once = False
        # 자동 재확인이 이전 요청의 타임아웃보다 자주 돌면 요청이 계속 쌓이므로,
        # 아직 응답을 기다리는 중이면 새로 보내지 않는다.
        self._power_check_in_flight = False
        self._resumed = False

        # PC 켜기 후 전원이 켜졌는지 재확인하는 폴링 — 최소화(onPause)하면 멈춘다.
        self._power_poll_timer = self._single_shot_timer(self._poll_power_status)
        self._power_poll_started_at = 0.0
        # 창이 떠 있는 동안 PC 전원 상태를 5초마다 자동으로 재확인한다.
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_tick)
        # 화면을 켜둔 채로 예약 시각이 지나면 "예약된 종료" 표시줄을 스스로 숨긴다.
        self._shutdown_expiry_timer = self._single_shot_timer(self._update_pending_shutdown_status)
        # PC 끄기 성공 후 PC가 실제로 꺼진 것(ping 무응답)이 확인되면 그때
        # 와이어가드를 끄는 감시 — 최소화해도 멈추지 않는다(PC 종료는 보통 창을
        # 내려둔 사이에 일어나므로).
        self._wg_off_watch_timer = self._single_shot_timer(self._wireguard_off_watch_tick)
        self._wg_off_watch_in_flight = False
        self._wg_off_watch_generation = 0
        # 터널이 켜진 채로 망이 바뀌면(예: 집에 들어옴) 터널이 맡을 주소를 다시 맞춘다 —
        # 최소화해도 멈추지 않는다.
        self._network_watch_timer = QTimer(self)
        self._network_watch_timer.timeout.connect(self._network_watch_tick)
        self._network_watch_timer.start(NETWORK_WATCH_INTERVAL_MS)
        self._network_watch_in_flight = False

        self._closing = False

        self.setWindowTitle('WireWOL')
        if icon is not None:
            self.setWindowIcon(icon)
        self.setFixedWidth(400)
        self._build_ui()
        self._toast = Toast(self)
        self._snackbar = Snackbar(self)

    @staticmethod
    def _single_shot_timer(slot) -> QTimer:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(slot)
        return timer

    # ---- 화면 구성 ----

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(0)

        header = QHBoxLayout()
        title = QLabel('WireWOL')
        title.setStyleSheet('font-size: 17pt; font-weight: bold;')
        settings_button = QPushButton('설정')
        settings_button.setObjectName('flat')
        settings_button.setCursor(Qt.PointingHandCursor)
        settings_button.clicked.connect(self._on_settings_clicked)
        header.addWidget(title, 1)
        header.addWidget(settings_button)
        root.addLayout(header)
        root.addSpacing(12)

        card = QFrame()
        card.setObjectName('card')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(6)
        self.status_power_label = QLabel(STATUS_POWER_CHECKING)
        self.status_power_label.setWordWrap(True)
        self.status_host_label = QLabel()
        self.status_mac_label = QLabel()
        self.status_wireguard_label = QLabel()
        self.status_shutdown_label = ClickableLabel(self._cancel_pending_shutdown)
        self.status_shutdown_label.setStyleSheet(f'color: {ACCENT_RED}; font-weight: bold;')
        self.status_shutdown_label.hide()
        for label in (self.status_host_label, self.status_mac_label,
                      self.status_wireguard_label, self.status_shutdown_label):
            label.setWordWrap(True)
        self.status_mac_label.setStyleSheet(f'color: {HINT};')
        self.status_wireguard_label.setStyleSheet(f'color: {HINT};')
        card_layout.addWidget(self.status_power_label)
        card_layout.addWidget(self.status_host_label)
        card_layout.addWidget(self.status_mac_label)
        card_layout.addWidget(self.status_wireguard_label)
        card_layout.addWidget(self.status_shutdown_label)
        root.addWidget(card)
        root.addSpacing(24)

        power_on_button = self._button('PC 켜기', 'green', 64, self._on_power_on_clicked)
        root.addWidget(power_on_button)
        root.addSpacing(16)

        off_row = QHBoxLayout()
        off_row.setSpacing(12)
        off_row.addWidget(self._button('PC 끄기', 'red', 64, self._on_power_off_clicked), 8)
        schedule_button = self._button('예약', None, 64, self._on_schedule_shutdown_clicked)
        schedule_button.setStyleSheet('font-weight: bold;')
        off_row.addWidget(schedule_button, 2)
        root.addLayout(off_row)
        root.addSpacing(16)

        wg_row = QHBoxLayout()
        wg_row.setSpacing(12)
        wg_row.addWidget(self._button('와이어가드 켜기', 'accent', 52, self._on_wireguard_on_clicked), 1)
        wg_row.addWidget(self._button('와이어가드 끄기', None, 52, self._on_wireguard_off_clicked), 1)
        root.addLayout(wg_row)
        root.addSpacing(20)

        version_label = QLabel(f'v{VERSION}' if VERSION[0].isdigit() else VERSION)
        version_label.setAlignment(Qt.AlignCenter)
        version_label.setStyleSheet(f'color: {HINT}; font-size: 8pt;')
        root.addWidget(version_label)

    @staticmethod
    def _button(text, object_name, height, slot) -> QPushButton:
        button = QPushButton(text)
        if object_name:
            button.setObjectName(object_name)
        button.setFixedHeight(height)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _toast_short(self, text: str):
        self._toast.show_message(text)

    def _toast_long(self, text: str):
        self._toast.show_message(text, TOAST_LONG_MS)

    # ---- 수명 주기(onResume/onPause 대응) ----

    def showEvent(self, event):
        super().showEvent(event)
        if not self.isMinimized():
            self._on_resume()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                self._on_pause()
            else:
                self._on_resume()
        super().changeEvent(event)

    def _on_resume(self):
        if self._resumed:
            return
        self._resumed = True
        # 설정 화면에서 연결 정보/WireGuard/MAC을 바꿨을 수 있으니 매번 다시 읽는다.
        self._has_checked_power_once = False
        self._update_status()
        self._auto_refresh_timer.start(AUTO_REFRESH_INTERVAL_MS)
        # 앱을 다시 켠 경우에도 남아있는 "종료 확인 후 와이어가드 끄기" 감시를 이어간다.
        self._resume_wireguard_off_watch()
        self._resume_auto_shutdown_wait()

    def _on_pause(self):
        if not self._resumed:
            return
        self._resumed = False
        self._power_poll_timer.stop()
        self._auto_refresh_timer.stop()
        self._shutdown_expiry_timer.stop()

    def closeEvent(self, event):
        """창을 닫아 앱을 끝낼 때 와이어가드가 켜져 있으면 끄고 나서 종료한다
        (android에서 앱이 백그라운드로 갈 때 끄는 것에 대응)."""
        if self._closing:
            event.accept()
            return
        self._on_pause()
        self._clear_wireguard_off_watch(persist=False)
        if not wireguard_tunnel.is_up():
            event.accept()
            return
        event.ignore()
        self._closing = True
        self.setEnabled(False)
        self._toast.show_message('와이어가드를 끄는 중…', 60_000)

        def on_done(_result, _error):
            self.close()
            QApplication.quit()

        run_async(wireguard_tunnel.bring_down, on_done)

    # ---- 상태 표시 ----

    def _auto_refresh_tick(self):
        # PC 켜기 폴링이 이미 돌고 있으면 같은 요청이 중복되니 건너뛴다.
        if not self._power_poll_timer.isActive():
            self._check_power_status(self.config.load_pairing())

    def _update_status(self):
        pairing = self.config.load_pairing()
        self.status_host_label.setText(
            f'대상: {pairing["host"]}:{pairing["port"]}' if pairing else '연결 정보 없음 — 설정에서 PC를 등록해주세요')
        mac = self.config.load_mac()
        self.status_mac_label.setText(f'MAC: {mac}' if mac else 'MAC 주소 없음 — PC 켜기를 쓰려면 필요합니다')
        self._refresh_wireguard_status()
        self._check_power_status(pairing)
        self._update_pending_shutdown_status()

    def _refresh_wireguard_status(self):
        if not self.config.load_wireguard_conf():
            text = 'WireGuard: 설정 없음'
        elif wireguard_tunnel.is_up():
            text = 'WireGuard: 연결됨'
        else:
            text = 'WireGuard: 연결 안 됨'
        self.status_wireguard_label.setText(text)

    def _set_power_status(self, text: str, color: str):
        self.status_power_label.setText(text)
        self.status_power_label.setStyleSheet(f'color: {color}; font-weight: bold; font-size: 11pt;')

    def _check_power_status(self, pairing, on_result=None):
        """네트워크 종류를 따지지 않고 컴패니언에 곧장 핑을 시도한다(같은 LAN이든
        WireGuard 터널이든 도달 가능하면 응답이 온다)."""
        # 연결 정보/네트워크가 없어 확인 자체를 못 한 경우는 다시 확인이
        # 가능해졌을 때의 첫 시도에 "확인 중..."이 뜨도록 확인 이력을 되돌린다.
        if not pairing:
            self._has_checked_power_once = False
            self._set_power_status(STATUS_POWER_PAIRING_MISSING, HINT)
            if on_result:
                on_result(False)
            return
        if not network.has_any_network():
            self._has_checked_power_once = False
            self._set_power_status(STATUS_POWER_NO_NETWORK, HINT)
            if on_result:
                on_result(False)
            return
        if not self._has_checked_power_once:
            self._set_power_status(STATUS_POWER_CHECKING, HINT)
        if on_result is None and self._power_check_in_flight:
            return
        self._power_check_in_flight = True
        config = CompanionConfig.from_pairing(pairing)

        def on_done(_mac, error):
            self._power_check_in_flight = False
            self._has_checked_power_once = True
            if error is None:
                self._set_power_status(STATUS_POWER_ON, ACCENT_GREEN)
                if on_result:
                    on_result(True)
                return
            # 집 밖에서 와이어가드 없이 응답이 없는 건 PC가 꺼졌다는 뜻이 아니다 —
            # "꺼짐"으로 단정하지 않고 확인 불가로 구분해 보여준다.
            if self.config.load_wireguard_conf() and not wireguard_tunnel.is_up():
                self._set_power_status(STATUS_POWER_UNREACHABLE_WG_OFF, HINT)
            else:
                self._set_power_status(STATUS_POWER_OFF, ACCENT_RED)
            if on_result:
                on_result(False)

        run_async(lambda: companion_client.ping(config), on_done)

    # ---- PC 켜기 폴링 ----
    # 처음 15초는 1초 간격(절전에서 깨는 경우처럼 금방 켜질 수 있어서), 그
    # 이후엔(완전 종료 상태에서 부팅하는 경우) 5초 간격. 켜진 걸 확인하면 멈춘다.

    def _start_power_on_polling(self):
        self._power_poll_timer.stop()
        self._power_poll_started_at = time.monotonic()
        self._power_poll_timer.start(FAST_POWER_ON_POLL_INTERVAL_MS)

    def _poll_power_status(self):
        def on_result(is_on):
            if is_on:
                self._schedule_auto_shutdown_if_pending()
                return
            if not self._resumed:
                return
            elapsed = time.monotonic() - self._power_poll_started_at
            interval = (FAST_POWER_ON_POLL_INTERVAL_MS if elapsed < FAST_POWER_ON_POLL_DURATION_SECONDS
                        else POWER_ON_POLL_INTERVAL_MS)
            self._power_poll_timer.start(interval)

        self._check_power_status(self.config.load_pairing(), on_result)

    # ---- 연결 확보(WireGuard 자동 켜기) ----

    def _ensure_connectivity(self, on_ready):
        """WireGuard가 없거나 이미 켜져 있으면 곧바로 on_ready를 실행한다. 그 외엔
        이 PC가 대상 PC와 같은 대역에 있는지(집 안 — PC가 꺼져 있어도 판단된다), 아니면
        WireGuard 없이 컴패니언 포트로 짧게(1.5초) 직접 연결을 찔러봐서 이미 도달 가능한
        상태인지 확인하고, 둘 다 아니면(외부) 와이어가드를 자동으로 켠 뒤 진행한다 —
        매번 손으로 와이어가드부터 켤 필요가 없다."""
        conf = self.config.load_wireguard_conf()
        pairing = self.config.load_pairing()
        if not conf or wireguard_tunnel.is_up() or not pairing:
            on_ready()
            return

        def check():
            routes = network.tunnel_routes(pairing['host'], wireguard_tunnel.TUNNEL_NAME)
            if not routes or network.is_reachable_directly(pairing['host'], pairing['port']):
                return None
            return routes

        def on_check_done(routes, _error):
            if not routes:
                on_ready()
                return
            self._toast_short('PC에 직접 연결할 수 없어 WireGuard를 자동으로 켭니다…')

            def bring_up_and_wait():
                wireguard_tunnel.bring_up(conf, routes)
                self._wait_for_tunnel_ready(pairing)

            def on_up_done(_result, error):
                if error is not None:
                    self._toast_short('WireGuard 자동 연결에 실패했습니다 — 계속 진행합니다')
                self._refresh_wireguard_status()
                on_ready()

            run_async(bring_up_and_wait, on_up_done)

        run_async(check, on_check_done)

    @staticmethod
    def _wait_for_tunnel_ready(pairing):
        """터널을 올린 직후엔 라우팅 전환과 핸드셰이크가 아직 끝나지 않아 곧바로
        보낸 공유기 원격 WOL 요청이 실패하는 경우가 있다 — 컴패니언에 3초짜리
        연결 시도를 한 번 거쳐 터널이 자리 잡을 시간을 번다(결과는 보지 않는다).
        백그라운드 스레드에서만 호출할 것."""
        network.is_reachable_directly(pairing['host'], pairing['port'], TUNNEL_SETTLE_PROBE_TIMEOUT_SECONDS)

    # ---- PC 켜기 ----

    def _send_wake_on_lan(self, mac: str) -> bool:
        pairing = self.config.load_pairing()
        try:
            network.send_magic_packet(mac, pairing['host'] if pairing else None)
            return True
        except ValueError as e:
            self._toast_long(str(e))
            return False
        except OSError:
            return True

    def _on_power_on_clicked(self):
        mac = self.config.load_mac()
        if not mac:
            self._toast_long('MAC 주소가 없습니다 — 설정에서 PC를 등록하거나 직접 입력해주세요')
            return
        # 다시 켜려는 참이니 이전 종료의 "확인 후 와이어가드 끄기" 감시가 켜기
        # 흐름 중에 터널을 끊지 않도록 해제한다.
        self._clear_wireguard_off_watch()
        if self.config.load_auto_shutdown()[0]:
            self.config.save_auto_shutdown_wait_from(time.time())
        else:
            self.config.clear_auto_shutdown_wait_from()
        # _ensure_connectivity보다 먼저 한 번 보낸다 — PC가 꺼져 있으면 집 안에
        # 있어도 컴패니언 포트 탐지가 실패해 와이어가드가 자동으로 켜지는데, 그
        # 뒤에는 브로드캐스트가 터널로 빠져 집 LAN에 닿지 않는다.
        if not self._send_wake_on_lan(mac):
            return

        def proceed():
            self._send_wake_on_lan(mac)
            self._toast_short('PC로 깨우기 신호를 보냈습니다')
            self._trigger_remote_wake_if_configured(mac)
            if self.config.load_pairing():
                self._start_power_on_polling()

        self._ensure_connectivity(proceed)

    # ---- PC 켜기 후 자동 종료 ----
    # android MainActivity의 resumeAutoShutdownWait/scheduleAutoShutdownIfPending과
    # 같다 — PC 켜기를 누른 시각을 설정에 남겨두고, 전원 폴링이 켜짐을 확인하는
    # 순간 설정된 시간 뒤로 종료를 예약한다. 폴링은 최소화하면 멈추므로 대기
    # 시간 안에 창이 돌아오면 폴링을 다시 시작해 이어서 확인한다.

    def _resume_auto_shutdown_wait(self):
        wait_from = self.config.load_auto_shutdown_wait_from()
        if not wait_from:
            return
        if (not self.config.load_pairing() or not self.config.load_auto_shutdown()[0]
                or time.time() - wait_from > AUTO_SHUTDOWN_WAIT_TIMEOUT_SECONDS):
            self.config.clear_auto_shutdown_wait_from()
            return
        if not self._power_poll_timer.isActive():
            self._start_power_on_polling()

    def _schedule_auto_shutdown_if_pending(self):
        """사용자가 이미 걸어둔 예약이 남아있으면 덮어쓰지 않는다. 방금 ping이
        성공한 직후라 연결 확보나 깨우기 재시도는 필요 없다."""
        wait_from = self.config.load_auto_shutdown_wait_from()
        if not wait_from:
            return
        self.config.clear_auto_shutdown_wait_from()
        enabled, delay_minutes = self.config.load_auto_shutdown()
        pairing = self.config.load_pairing()
        if not enabled or not pairing or time.time() - wait_from > AUTO_SHUTDOWN_WAIT_TIMEOUT_SECONDS:
            return
        if self.config.load_pending_shutdown_at() > time.time():
            return
        delay_seconds = delay_minutes * 60
        config = CompanionConfig.from_pairing(pairing)

        def on_done(_result, error):
            if error is not None:
                self._toast_long(f'자동 종료 예약 실패: {error}')
                return
            target = self._record_scheduled_shutdown(delay_seconds)
            self._toast_long(f'PC가 켜져 {_time_label(target)}에 자동 종료되도록 예약했습니다')

        run_async(lambda: companion_client.shutdown(config, delay_seconds), on_done)

    def _trigger_remote_wake_if_configured(self, mac: str):
        router = self.config.load_router_wol()
        if not router:
            return

        def on_done(_result, error):
            if error is None:
                self._toast_short('공유기를 통해 깨우기 신호를 보냈습니다')
            else:
                self._toast_long(f'원격 WOL 실패: {error}')

        run_async(lambda: trigger_remote_wake(self.config, router, mac), on_done)

    # ---- PC 끄기 / 예약 끄기 ----

    def _on_power_off_clicked(self):
        pairing = self.config.load_pairing()
        if not pairing:
            self._toast_long('먼저 설정에서 PC를 등록해주세요')
            return
        box = QMessageBox(self)
        box.setWindowTitle('PC 끄기')
        box.setText('PC를 종료하시겠습니까? 전송 후 10초 뒤 실제로 꺼지며, 그 사이 취소할 수 있습니다.')
        ok = box.addButton('종료', QMessageBox.AcceptRole)
        box.addButton('취소', QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is ok:
            self._request_shutdown(pairing, None)

    def _on_schedule_shutdown_clicked(self):
        """시각을 직접 고르는 대신 타이머처럼 "몇 시간 몇 분 후"를 고른다."""
        pairing = self.config.load_pairing()
        if not pairing:
            self._toast_long('먼저 설정에서 PC를 등록해주세요')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('예약 끄기')
        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()
        hour_spin = QSpinBox()
        hour_spin.setRange(0, 23)
        minute_spin = QSpinBox()
        minute_spin.setRange(0, 59)
        minute_spin.setValue(30)
        row.addWidget(hour_spin)
        row.addWidget(QLabel('시간'))
        row.addSpacing(8)
        row.addWidget(minute_spin)
        row.addWidget(QLabel('분 후'))
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton('예약', QDialogButtonBox.AcceptRole)
        buttons.addButton('취소', QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        delay_seconds = hour_spin.value() * 3600 + minute_spin.value() * 60
        if delay_seconds <= 0:
            self._toast_short('1분 이상으로 설정해주세요')
            return
        self._request_shutdown(pairing, delay_seconds)

    def _request_shutdown(self, pairing, delay_seconds):
        def proceed():
            self._toast_short('종료 명령을 보내는 중…')
            self._attempt_shutdown(pairing, delay_seconds, allow_wake_retry=True)

        self._ensure_connectivity(proceed)

    def _attempt_shutdown(self, pairing, delay_seconds, allow_wake_retry):
        """컴패니언이 응답하지 않으면(PC가 절전 상태라 서버 자체가 멈춰있을 수
        있음) 매직 패킷으로 깨운 뒤 딱 한 번만 더 재시도한다."""
        config = CompanionConfig.from_pairing(pairing)

        def on_done(_result, error):
            if error is None:
                self._on_shutdown_scheduled(delay_seconds or QUICK_SHUTDOWN_SECONDS)
                return
            mac = self.config.load_mac()
            if allow_wake_retry and mac:
                self._wake_then_retry_shutdown(pairing, mac, delay_seconds)
            else:
                self._toast_long(f'종료 요청 실패: {error}')

        run_async(lambda: companion_client.shutdown(config, delay_seconds), on_done)

    def _wake_then_retry_shutdown(self, pairing, mac, delay_seconds):
        """절전 상태에선 랜카드가 매직 패킷을 받아 깨우는 것만 가능하고 컴패니언은
        OS가 완전히 깨어난 뒤에야 응답한다 — 깨운 뒤 응답이 올 때까지 짧은 간격으로
        ping하다가, 응답이 오는 즉시 종료 요청을 다시 보낸다."""
        self._send_wake_on_lan(mac)
        self._trigger_remote_wake_if_configured(mac)
        config = CompanionConfig.from_pairing(pairing)
        started_at = time.monotonic()
        timer = QTimer(self)
        timer.setSingleShot(True)

        def poll():
            def on_done(_mac, error):
                if error is None:
                    timer.deleteLater()
                    self._attempt_shutdown(pairing, delay_seconds, allow_wake_retry=False)
                elif time.monotonic() - started_at < WAKE_RETRY_TIMEOUT_SECONDS:
                    timer.start(WAKE_RETRY_POLL_INTERVAL_MS)
                else:
                    timer.deleteLater()
                    self._toast_long('PC가 응답하지 않아 종료할 수 없습니다(절전 상태에서 깨우기에 실패했을 수 있습니다)')

            run_async(lambda: companion_client.ping(config), on_done)

        timer.timeout.connect(poll)
        timer.start(WAKE_RETRY_POLL_INTERVAL_MS)

    def _record_scheduled_shutdown(self, delay_seconds: int) -> float:
        """예약 시각을 남겨 상태 카드에 표시하고 종료 확인 후 와이어가드 끄기
        감시를 건다 — PC 끄기/예약 끄기/자동 종료 예약이 공통으로 거친다."""
        target = time.time() + delay_seconds
        self.config.save_pending_shutdown_at(target)
        self._update_pending_shutdown_status()
        self._arm_wireguard_off_watch(target)
        return target

    def _on_shutdown_scheduled(self, delay_seconds: int):
        target = self._record_scheduled_shutdown(delay_seconds)
        if delay_seconds <= QUICK_SHUTDOWN_SECONDS:
            self._toast.hide()  # 같은 자리에 겹쳐 뜨지 않게
            self._snackbar.show_message('10초 후 PC가 종료됩니다', '취소',
                                        self._cancel_pending_shutdown, SHUTDOWN_SNACKBAR_MS)
        else:
            self._toast_long(f'{_time_label(target)}에 PC가 종료됩니다')

    # ---- 종료 확인 후 와이어가드 끄기 감시 ----
    # 예약 시각 전에는 PC가 켜져 있는 게 정상이고(그 사이 취소 요청이 터널로
    # 나가야 한다), 시각이 됐다고 곧바로 끄면 PC 쪽에서 종료가 취소/실패한
    # 경우에도 터널이 끊긴다 — 예약 시각부터 ping을 반복하다가 무응답(꺼짐)이
    # 확인될 때만 끈다.

    def _arm_wireguard_off_watch(self, target: float):
        self._clear_wireguard_off_watch()
        if not self.config.load_wireguard_conf() or not wireguard_tunnel.is_up():
            return
        self.config.save_wireguard_off_watch_from(target)
        self._resume_wireguard_off_watch()

    def _resume_wireguard_off_watch(self):
        self._wg_off_watch_timer.stop()
        self._wg_off_watch_generation += 1
        watch_from = self.config.load_wireguard_off_watch_from()
        if not watch_from:
            return
        self._wg_off_watch_timer.start(max(0, int((watch_from - time.time()) * 1000)))

    def _wireguard_off_watch_tick(self):
        watch_from = self.config.load_wireguard_off_watch_from()
        pairing = self.config.load_pairing()
        # 사용자가 그새 와이어가드를 직접 껐거나, 종료가 끝내 확인되지 않으면
        # (PC 쪽에서 취소 등) 감시를 그만둔다.
        if (not watch_from or not pairing or not wireguard_tunnel.is_up()
                or time.time() - watch_from > WIREGUARD_OFF_WATCH_TIMEOUT_SECONDS):
            self._clear_wireguard_off_watch()
            return
        # 이 PC 자체가 네트워크를 잃은 건 원격 PC 꺼짐의 근거가 아니다.
        if not network.has_any_network() or self._wg_off_watch_in_flight:
            self._wg_off_watch_timer.start(WIREGUARD_OFF_WATCH_INTERVAL_MS)
            return
        self._wg_off_watch_in_flight = True
        generation = self._wg_off_watch_generation
        config = CompanionConfig.from_pairing(pairing)

        def on_done(_mac, error):
            self._wg_off_watch_in_flight = False
            if generation != self._wg_off_watch_generation:
                return  # 그사이 감시가 해제/재설정됨
            if error is None:
                self._set_power_status(STATUS_POWER_ON, ACCENT_GREEN)
                self._wg_off_watch_timer.start(WIREGUARD_OFF_WATCH_INTERVAL_MS)
                return
            self._set_power_status(STATUS_POWER_OFF, ACCENT_RED)
            self._clear_wireguard_off_watch()
            run_async(wireguard_tunnel.bring_down, lambda *_: self._refresh_wireguard_status())

        run_async(lambda: companion_client.ping(config), on_done)

    def _clear_wireguard_off_watch(self, persist: bool = True):
        self._wg_off_watch_timer.stop()
        self._wg_off_watch_generation += 1
        if persist:
            self.config.clear_wireguard_off_watch_from()

    # ---- 예약된 종료 표시 ----

    def _update_pending_shutdown_status(self):
        """예약 지연이 길면(수 분~수 시간) 10초짜리 알림이 사라진 뒤에도 취소할
        방법이 있어야 하므로 상태 카드에 시각과 함께 보여준다."""
        self._shutdown_expiry_timer.stop()
        target = self.config.load_pending_shutdown_at()
        remaining = target - time.time()
        if remaining <= 0:
            self.status_shutdown_label.hide()
            if target:
                self.config.clear_pending_shutdown_at()
            self.adjustSize()
            return
        self.status_shutdown_label.setText(f'예약된 종료: {_time_label(target)} (클릭하여 취소)')
        self.status_shutdown_label.show()
        self.adjustSize()
        if self._resumed:
            self._shutdown_expiry_timer.start(int(remaining * 1000) + 500)

    def _cancel_pending_shutdown(self):
        pairing = self.config.load_pairing()
        if not pairing:
            self._toast_long('먼저 설정에서 PC를 등록해주세요')
            return
        config = CompanionConfig.from_pairing(pairing)

        def on_done(_result, error):
            if error is not None:
                self._toast_long(f'취소 요청 실패: {error}')
                return
            self.config.clear_pending_shutdown_at()
            self._clear_wireguard_off_watch()
            self._toast_short('종료 예약을 취소했습니다')
            self._update_pending_shutdown_status()

        run_async(lambda: companion_client.cancel_shutdown(config), on_done)

    # ---- 와이어가드 ----
    # 버튼을 직접 누르면 사용자의 선택이 우선이다 — 남아있던 "종료 확인 후 끄기"
    # 감시가 나중에 터널을 끊지 않도록 해제한다.

    def _on_wireguard_on_clicked(self):
        self._clear_wireguard_off_watch()
        conf = self.config.load_wireguard_conf()
        if not conf:
            self._toast_short('WireGuard 설정이 없습니다 — 먼저 설정에서 등록해주세요')
            return
        if wireguard_tunnel.is_up():
            self._update_status()
            return
        # 터널은 PC 대역만 맡으므로 PC가 등록돼 있어야 한다.
        pairing = self.config.load_pairing()
        if not pairing:
            self._toast_long('먼저 설정에서 PC를 등록해주세요')
            return
        self._toast_short('VPN 연결 중…')

        def bring_up() -> bool:
            routes = network.tunnel_routes(pairing['host'], wireguard_tunnel.TUNNEL_NAME)
            if not routes:
                return False
            wireguard_tunnel.bring_up(conf, routes)
            return True

        def on_done(started, error):
            if error is not None:
                self._toast_long(f'VPN 연결에 실패했습니다: {error}')
            elif not started:
                self._toast_long('PC와 같은 네트워크에 있어 와이어가드가 필요 없습니다')
            self._update_status()

        run_async(bring_up, on_done)

    def _network_watch_tick(self):
        """터널이 켜진 채로 망이 바뀌어 맡을 주소가 달라지면 다시 맞춘다 — 집에 들어와 PC와
        같은 대역이 되면 끈다(켜 둔 채면 PC 접속이 공유기를 한 바퀴 돌아야 한다). 앱을 다시
        켰을 때 남아 있던 터널(맡은 주소를 모름)도 새로 맞춘다."""
        conf = self.config.load_wireguard_conf()
        pairing = self.config.load_pairing()
        if (self._closing or self._network_watch_in_flight or not conf or not pairing
                or not wireguard_tunnel.is_up()):
            return
        self._network_watch_in_flight = True

        def work() -> bool:
            routes = network.tunnel_routes(pairing['host'], wireguard_tunnel.TUNNEL_NAME)
            if routes == wireguard_tunnel.active_routes:
                return False
            wireguard_tunnel.bring_down()
            if routes:
                wireguard_tunnel.bring_up(conf, routes)
            return True

        def on_done(changed, error):
            self._network_watch_in_flight = False
            if changed or error is not None:
                self._refresh_wireguard_status()

        run_async(work, on_done)

    def _on_wireguard_off_clicked(self):
        self._clear_wireguard_off_watch()
        if not wireguard_tunnel.is_up():
            self._update_status()
            return

        def on_done(_result, error):
            if error is not None:
                self._toast_long(str(error))
            self._update_status()

        run_async(wireguard_tunnel.bring_down, on_done)

    # ---- 설정 ----

    def _on_settings_clicked(self):
        SettingsDialog(self.config, self).exec_()
        self._has_checked_power_once = False
        self._update_status()
