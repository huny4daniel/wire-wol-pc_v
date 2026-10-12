"""자주 쓰지 않는 설정들을 모아둔 화면 — android SettingsActivity.kt와 동일한
구성(PC 등록, WireGuard 설정, 원격(공유기) WOL 설정, PC 켜기 후 자동 종료, 설정
내보내기/불러오기, 전체 초기화).

각 항목 아래에 현재 설정 여부를 보여주는 상태 문구를 둔다 — 어떤 게
필수(연결 정보)고 어떤 게 선택(WireGuard/원격 WOL)인지, 그리고 지금 실제로
저장된 값이 뭔지 한눈에 보이게 하기 위함.

입력 경로는 android의 "카메라로 스캔 / 갤러리에서 QR 이미지 불러오기 / 직접
입력"을 웹캠 / 이미지 파일 / 직접 입력으로 옮겼다. WireGuard는 PC에서 .conf
파일을 그대로 갖고 있는 경우가 많아 ".conf 파일 불러오기"를 하나 더 둔다.
"""
import json

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from app import settings_backup
from app.config import DEFAULT_ROUTER_PORT, ClientConfig
from app.qr_scan import QrScanDialog, decode_qr_from_file
from app.ui_common import HINT, TOAST_LONG_MS, Toast, run_async

_IMAGE_FILTER = '이미지 파일 (*.png *.jpg *.jpeg *.bmp *.webp)'
_BACKUP_FILTER = 'WireWOL 설정 파일 (*.json);;모든 파일 (*)'


def _format_duration(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f'{minutes}분'
    if minutes == 0:
        return f'{hours}시간'
    return f'{hours}시간 {minutes}분'


class SettingsDialog(QDialog):
    def __init__(self, config: ClientConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle('설정')
        self.setFixedWidth(400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(4)

        back_button = QPushButton('‹ 뒤로')
        back_button.setObjectName('flat')
        back_button.setCursor(Qt.PointingHandCursor)
        back_button.clicked.connect(self.accept)
        layout.addWidget(back_button, 0, Qt.AlignLeft)
        title = QLabel('설정')
        title.setStyleSheet('font-size: 15pt; font-weight: bold;')
        layout.addWidget(title)
        layout.addSpacing(12)

        self.pairing_button, self.pairing_status = self._item(layout, 'PC 등록', self._on_pairing_clicked)
        self.wireguard_button, self.wireguard_status = self._item(
            layout, 'WireGuard 설정 등록', self._on_wireguard_clicked)
        _, self.router_status = self._item(layout, '원격(외부망) WOL 설정', self._show_router_wol_dialog)
        _, self.auto_shutdown_status = self._item(
            layout, 'PC 켜기 후 자동 종료', self._show_auto_shutdown_dialog)

        layout.addSpacing(8)
        backup_row = QHBoxLayout()
        backup_row.setSpacing(8)
        for text, slot in (('설정 내보내기', self._on_export_clicked), ('설정 불러오기', self._on_import_clicked)):
            button = QPushButton(text)
            button.setFixedHeight(48)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            backup_row.addWidget(button, 1)
        layout.addLayout(backup_row)
        backup_hint = QLabel('모든 설정을 비밀번호로 암호화한 파일 하나로 저장하거나 불러옵니다. '
                             'WireWOL 안드로이드 앱과 파일을 서로 주고받을 수 있습니다.')
        backup_hint.setWordWrap(True)
        backup_hint.setStyleSheet(f'color: {HINT}; padding-left: 4px;')
        layout.addWidget(backup_hint)
        clear_button, _ = self._item(layout, '저장된 정보 초기화', self._on_clear_all, with_status=False)
        clear_button.setStyleSheet('color: #e07070;')

        self._toast = Toast(self)
        self._refresh_statuses()

    @staticmethod
    def _item(layout, text, slot, with_status=True):
        button = QPushButton(text)
        button.setFixedHeight(48)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet('text-align: left; padding-left: 16px;')
        button.clicked.connect(slot)
        layout.addSpacing(8)
        layout.addWidget(button)
        status = None
        if with_status:
            status = QLabel()
            status.setWordWrap(True)
            status.setStyleSheet(f'color: {HINT}; padding-left: 4px;')
            layout.addWidget(status)
        return button, status

    def _refresh_statuses(self):
        pairing = self.config.load_pairing()
        mac = self.config.load_mac()
        if pairing:
            text = f'✓ 설정됨 — {pairing["host"]}:{pairing["port"]}'
            text += f'\nMAC: {mac}' if mac else '\nMAC 없음 (PC 켜기에 필요 — 직접 입력에서 채울 수 있습니다)'
        else:
            text = '설정 안 됨 (필수 — PC 켜기/끄기에 필요)'
        self.pairing_status.setText(text)

        self.wireguard_status.setText(
            '✓ 설정됨' if self.config.load_wireguard_conf()
            else '설정 안 됨 (선택 — 집 밖에서 접속할 때만 필요)')

        router = self.config.load_router_wol()
        self.router_status.setText(f'✓ 설정됨 — {router["host"]}' if router else '설정 안 됨 (선택 사항)')

        enabled, delay_minutes = self.config.load_auto_shutdown()
        self.auto_shutdown_status.setText(
            f'✓ 사용 — 켜진 뒤 {_format_duration(delay_minutes)} 후 종료' if enabled else '사용 안 함')
        self.adjustSize()

    def _popup_menu(self, button, actions):
        menu = QMenu(self)
        for text, slot in actions:
            menu.addAction(text, slot)
        menu.exec_(button.mapToGlobal(button.rect().bottomLeft()))

    # ---- QR 입력 공통 ----

    def _scan_with_camera(self, handler):
        dialog = QrScanDialog(self)
        if dialog.exec_() == QDialog.Accepted and dialog.result_text:
            handler(dialog.result_text)

    def _scan_from_image(self, handler):
        path, _ = QFileDialog.getOpenFileName(self, 'QR 이미지 선택', '', _IMAGE_FILTER)
        if not path:
            return
        decoded = decode_qr_from_file(path)
        if not decoded:
            self._toast.show_message('선택한 이미지에서 QR 코드를 찾을 수 없습니다')
            return
        handler(decoded)

    # ---- PC 등록 ----

    def _on_pairing_clicked(self):
        self._popup_menu(self.pairing_button, [
            ('웹캠으로 스캔', lambda: self._scan_with_camera(self._handle_pairing_scan)),
            ('이미지 파일에서 QR 불러오기', lambda: self._scan_from_image(self._handle_pairing_scan)),
            ('직접 입력', self._show_manual_pairing_dialog),
        ])

    def _handle_pairing_scan(self, scanned: str):
        """PC 트레이 "연결 정보 보기" QR은 {"host","port","token","mac"} JSON이다
        (wire-wol_v windows/app/tray.py의 _pairing_payload와 짝)."""
        try:
            data = json.loads(scanned)
            host = str(data['host'])
            port = str(int(data['port']))
            token = str(data['token'])
            mac = str(data.get('mac', '') or '')
        except (ValueError, KeyError, TypeError):
            self._toast.show_message('인식된 QR이 WireWOL 연결 정보가 아닙니다')
            return
        self.config.save_pairing(host, port, token, mac)
        self._toast.show_message('연결 정보를 저장했습니다')
        self._refresh_statuses()

    def _show_manual_pairing_dialog(self):
        """QR 없이 host/port/token/mac을 직접 입력한다 — QR로 받은 MAC이 잘못된
        어댑터를 가리킬 때 MAC만 고치는 용도로도 쓴다(기존 값이 채워져 있음)."""
        pairing = self.config.load_pairing() or {}
        dialog = QDialog(self)
        dialog.setWindowTitle('PC 등록 (직접 입력)')
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        form = QFormLayout(dialog)
        host_edit = QLineEdit(pairing.get('host', ''))
        host_edit.setPlaceholderText('예: 192.168.0.10')
        port_edit = QLineEdit(str(pairing.get('port', '')))
        token_edit = QLineEdit(pairing.get('token', ''))
        mac_edit = QLineEdit(self.config.load_mac())
        mac_edit.setPlaceholderText('예: AA:BB:CC:DD:EE:FF')
        form.addRow('PC 주소 (IP)', host_edit)
        form.addRow('포트', port_edit)
        form.addRow('토큰', token_edit)
        form.addRow('MAC 주소', mac_edit)
        buttons = QDialogButtonBox()
        buttons.addButton('저장', QDialogButtonBox.AcceptRole)
        buttons.addButton('취소', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def on_save():
            host = host_edit.text().strip()
            port = port_edit.text().strip()
            token = token_edit.text().strip()
            if not host or not port or not token:
                QMessageBox.warning(dialog, 'PC 등록', 'PC 주소/포트/토큰을 입력해주세요')
                return
            if not port.isdigit():
                QMessageBox.warning(dialog, 'PC 등록', '포트는 숫자여야 합니다')
                return
            self.config.save_pairing(host, port, token, mac_edit.text().strip())
            dialog.accept()

        buttons.accepted.connect(on_save)
        if dialog.exec_() == QDialog.Accepted:
            self._toast.show_message('연결 정보를 저장했습니다')
            self._refresh_statuses()

    # ---- WireGuard ----

    def _on_wireguard_clicked(self):
        actions = [
            ('웹캠으로 스캔', lambda: self._scan_with_camera(self._handle_wireguard_conf)),
            ('이미지 파일에서 QR 불러오기', lambda: self._scan_from_image(self._handle_wireguard_conf)),
            ('.conf 파일 불러오기', self._load_wireguard_conf_file),
        ]
        if self.config.load_wireguard_conf():
            actions.append(('설정 삭제', self._clear_wireguard_conf))
        self._popup_menu(self.wireguard_button, actions)

    def _load_wireguard_conf_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'WireGuard 설정 파일 선택', '', 'WireGuard 설정 (*.conf);;모든 파일 (*)')
        if not path:
            return
        try:
            with open(path, encoding='utf-8-sig') as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            self._toast.show_message('파일을 읽을 수 없습니다')
            return
        self._handle_wireguard_conf(text)

    def _handle_wireguard_conf(self, text: str):
        """WireGuard 설정 QR(공유기/공식 앱이 만든 것)은 표준 .conf 텍스트가 그대로
        들어있어 [Interface]로 시작하는지로 구분한다."""
        if not text.lstrip().startswith('[Interface]'):
            self._toast.show_message('WireGuard 설정이 아닙니다')
            return
        self.config.save_wireguard_conf(text.strip())
        self._toast.show_message('WireGuard 설정을 저장했습니다')
        self._refresh_statuses()

    def _clear_wireguard_conf(self):
        self.config.clear_wireguard_conf()
        self._toast.show_message('WireGuard 설정을 삭제했습니다')
        self._refresh_statuses()

    # ---- 원격 WOL ----

    def _show_router_wol_dialog(self):
        existing = self.config.load_router_wol() or {}
        dialog = QDialog(self)
        dialog.setWindowTitle('원격 WOL 설정')
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setFixedWidth(400)
        layout = QVBoxLayout(dialog)
        desc = QLabel('집 밖에서도 PC를 켤 수 있도록, 공유기에 내장된 원격 깨우기 기능을 사용합니다. '
                      '공유기 관리자 비밀번호가 이 PC에 암호화되어 저장되고, 공유기의 자체 서명 인증서는 '
                      '최초 연결 시 지문을 기억해두는 방식으로만 신뢰합니다. '
                      '신뢰할 수 있는 본인 소유 공유기에서만 사용하세요.')
        desc.setWordWrap(True)
        desc.setStyleSheet(f'color: {HINT};')
        layout.addWidget(desc)
        form = QFormLayout()
        host_edit = QLineEdit(existing.get('host', ''))
        host_edit.setPlaceholderText('IP 또는 DDNS')
        port_edit = QLineEdit(existing.get('port', DEFAULT_ROUTER_PORT))
        id_edit = QLineEdit(existing.get('id', ''))
        pw_edit = QLineEdit(existing.get('password', ''))
        pw_edit.setEchoMode(QLineEdit.Password)
        form.addRow('공유기 주소', host_edit)
        form.addRow('포트', port_edit)
        form.addRow('관리자 ID', id_edit)
        form.addRow('비밀번호', pw_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        buttons.addButton('저장', QDialogButtonBox.AcceptRole)
        clear_button = buttons.addButton('설정 삭제', QDialogButtonBox.DestructiveRole)
        buttons.addButton('취소', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def on_save():
            values = (host_edit.text().strip(), port_edit.text().strip(), id_edit.text().strip(), pw_edit.text())
            if not all(values):
                QMessageBox.warning(dialog, '원격 WOL 설정', '모든 항목을 입력해주세요')
                return
            self.config.save_router_wol(*values)
            self._toast.show_message('원격 WOL 설정을 저장했습니다')
            dialog.accept()

        def on_clear():
            self.config.clear_router_wol()
            self._toast.show_message('원격 WOL 설정을 삭제했습니다')
            dialog.accept()

        buttons.accepted.connect(on_save)
        clear_button.clicked.connect(on_clear)
        dialog.exec_()
        self._refresh_statuses()

    # ---- PC 켜기 후 자동 종료 ----

    def _show_auto_shutdown_dialog(self):
        enabled, delay_minutes = self.config.load_auto_shutdown()
        dialog = QDialog(self)
        dialog.setWindowTitle('PC 켜기 후 자동 종료')
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setFixedWidth(400)
        layout = QVBoxLayout(dialog)
        desc = QLabel('PC 켜기로 켠 PC가 켜진 것이 확인되면, 아래 시간 뒤에 자동으로 종료되도록 예약합니다. '
                      '원격으로 켜두고 깜빡 잊고 못 끄는 것을 막아줍니다.')
        desc.setWordWrap(True)
        desc.setStyleSheet(f'color: {HINT};')
        layout.addWidget(desc)
        enable_check = QCheckBox('사용')
        enable_check.setChecked(enabled)
        layout.addWidget(enable_check)
        row = QHBoxLayout()
        hour_spin = QSpinBox()
        hour_spin.setRange(0, 23)
        hour_spin.setValue(delay_minutes // 60)
        minute_spin = QSpinBox()
        minute_spin.setRange(0, 59)
        minute_spin.setValue(delay_minutes % 60)
        row.addWidget(hour_spin)
        row.addWidget(QLabel('시간'))
        row.addSpacing(8)
        row.addWidget(minute_spin)
        row.addWidget(QLabel('분 후'))
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton('저장', QDialogButtonBox.AcceptRole)
        buttons.addButton('취소', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def on_save():
            minutes = hour_spin.value() * 60 + minute_spin.value()
            if enable_check.isChecked() and minutes <= 0:
                QMessageBox.warning(dialog, 'PC 켜기 후 자동 종료', '1분 이상으로 설정해주세요')
                return
            self.config.save_auto_shutdown(enable_check.isChecked(), minutes if minutes > 0 else delay_minutes)
            dialog.accept()

        buttons.accepted.connect(on_save)
        if dialog.exec_() == QDialog.Accepted:
            self._toast.show_message('자동 종료 설정을 저장했습니다')
            self._refresh_statuses()

    # ---- 설정 내보내기/불러오기 ----
    # 형식은 app/settings_backup.py 참고(안드로이드 SettingsBackup.kt와 같은 파일).

    def _ask_password(self, title: str, desc: str, confirm: bool) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setFixedWidth(400)
        layout = QVBoxLayout(dialog)
        label = QLabel(desc)
        label.setWordWrap(True)
        label.setStyleSheet(f'color: {HINT};')
        layout.addWidget(label)
        form = QFormLayout()
        password_edit = QLineEdit()
        password_edit.setEchoMode(QLineEdit.Password)
        form.addRow('비밀번호', password_edit)
        confirm_edit = None
        if confirm:
            confirm_edit = QLineEdit()
            confirm_edit.setEchoMode(QLineEdit.Password)
            form.addRow('비밀번호 확인', confirm_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        buttons.addButton('다음', QDialogButtonBox.AcceptRole)
        buttons.addButton('취소', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def on_next():
            if not password_edit.text():
                QMessageBox.warning(dialog, title, '비밀번호를 입력해주세요')
                return
            if confirm_edit is not None and confirm_edit.text() != password_edit.text():
                QMessageBox.warning(dialog, title, '비밀번호가 서로 다릅니다')
                return
            dialog.accept()

        buttons.accepted.connect(on_next)
        return password_edit.text() if dialog.exec_() == QDialog.Accepted else None

    def _on_export_clicked(self):
        password = self._ask_password(
            '설정 내보내기',
            '파일에는 PC 토큰, WireGuard 개인키, 공유기 비밀번호가 담깁니다. 불러올 때 필요한 비밀번호를 정해주세요.',
            confirm=True)
        if password is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, '설정 내보내기', settings_backup.FILE_NAME, _BACKUP_FILTER)
        if not path:
            return
        contents = settings_backup.collect(self.config)

        def work():
            text = settings_backup.export_text(contents, password)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)

        def on_done(_result, error):
            if error is None:
                self._toast.show_message('설정을 파일로 내보냈습니다')
            else:
                self._toast.show_message(f'내보내기 실패: {error}', TOAST_LONG_MS)

        self._toast.show_message('처리 중…')
        run_async(work, on_done)

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, '설정 불러오기', '', _BACKUP_FILTER)
        if not path:
            return
        try:
            with open(path, encoding='utf-8-sig') as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            self._toast.show_message('WireWOL 설정 파일이 아니거나 손상된 파일입니다')
            return
        self._ask_import_password(text)

    def _ask_import_password(self, text: str):
        password = self._ask_password('설정 불러오기', '내보낼 때 정한 비밀번호를 입력해주세요.', confirm=False)
        if password is None:
            return

        def on_done(contents, error):
            if isinstance(error, settings_backup.WrongPasswordError):
                self._toast.show_message('비밀번호가 틀렸습니다')
                self._ask_import_password(text)
                return
            if error is not None:
                self._toast.show_message('WireWOL 설정 파일이 아니거나 손상된 파일입니다')
                return
            box = QMessageBox(self)
            box.setWindowTitle('설정 덮어쓰기')
            box.setText('지금 저장된 설정을 모두 지우고 파일의 설정으로 바꿉니다. 계속할까요?')
            ok = box.addButton('불러오기', QMessageBox.AcceptRole)
            box.addButton('취소', QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is not ok:
                return
            settings_backup.apply(contents, self.config)
            self._toast.show_message('설정을 불러왔습니다')
            self._refresh_statuses()

        self._toast.show_message('처리 중…')
        run_async(lambda: settings_backup.import_text(text, password), on_done)

    # ---- 전체 초기화 ----

    def _on_clear_all(self):
        box = QMessageBox(self)
        box.setWindowTitle('저장된 정보 초기화')
        box.setText('연결 정보, WireGuard 설정, 원격 WOL 설정, 자동 종료 설정을 모두 지웁니다. 계속할까요?')
        ok = box.addButton('초기화', QMessageBox.AcceptRole)
        box.addButton('취소', QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is not ok:
            return
        self.config.clear_all()
        self._toast.show_message('모두 초기화했습니다')
        self._refresh_statuses()
