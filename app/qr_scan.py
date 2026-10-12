"""QR 입력 — android SettingsActivity의 "카메라로 스캔"(웹캠)과 "갤러리에서 QR
이미지 불러오기"(이미지 파일)에 대응한다.

디코딩은 OpenCV 내장 QR 디코더만 쓴다 — pyzbar처럼 exe에 따로 동봉해야 하는
네이티브 DLL이 없다.
"""
import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout


def _detectors():
    # Aruco 기반 디코더(OpenCV 4.8+)가 작은/기울어진 QR에 더 강해 먼저 시도한다.
    if hasattr(cv2, 'QRCodeDetectorAruco'):
        yield cv2.QRCodeDetectorAruco()
    yield cv2.QRCodeDetector()


def decode_qr_from_frame(image) -> str | None:
    for detector in _detectors():
        try:
            text, _, _ = detector.detectAndDecode(image)
        except cv2.error:
            continue
        if text:
            return text
    return None


def decode_qr_from_file(path: str) -> str | None:
    # cv2.imread는 한글 등 비ASCII 경로를 열지 못한다 — 바이트로 읽어 디코딩한다.
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        return None
    text = decode_qr_from_frame(image)
    if text:
        return text
    # 고해상도 스크린샷 속 작은 QR은 흑백으로 바꿔 키우면 읽히는 경우가 있다.
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return decode_qr_from_frame(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))


class QrScanDialog(QDialog):
    """웹캠 화면을 보여주며 QR이 인식되는 즉시 닫힌다(result_text에 결과)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('QR 코드 스캔')
        self.result_text: str | None = None
        self._frame_count = 0

        self._label = QLabel('카메라를 여는 중…')
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setMinimumSize(480, 360)
        hint = QLabel('QR 코드를 카메라에 비춰주세요')
        hint.setObjectName('hint')
        hint.setAlignment(Qt.AlignCenter)
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addWidget(hint)
        layout.addWidget(cancel)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_frame)
        self._capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not self._capture.isOpened():
            self._label.setText('카메라를 열 수 없습니다')
            return
        self._timer.start(60)

    def _on_frame(self):
        ok, frame = self._capture.read()
        if not ok:
            return
        # 디코딩은 프레임 표시보다 무거워 몇 프레임에 한 번만 시도한다.
        self._frame_count += 1
        if self._frame_count % 3 == 0:
            text = decode_qr_from_frame(frame)
            if text:
                self.result_text = text
                self.accept()
                return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self._label.setPixmap(QPixmap.fromImage(image).scaled(
            self._label.width(), self._label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def done(self, result):
        self._timer.stop()
        if self._capture.isOpened():
            self._capture.release()
        super().done(result)
