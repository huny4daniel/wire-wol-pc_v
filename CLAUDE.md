# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 개요

`wire-wol_v`(같은 개발자의 프로젝트) 안드로이드 리모컨 앱의 **윈도우판**이다. 세부 기능과 동작 원리는 안드로이드 앱과 동일하게 유지하는 것이 원칙 — 안드로이드 쪽(`wire-wol_v/android/.../MainActivity.kt`, `SettingsActivity.kt`)의 동작이 바뀌면 이쪽도 맞춰야 한다. 명령을 받는 쪽(대상 PC의 컴패니언 `wirewol.pyw`)은 `wire-wol_v/windows/`에 있고 이 저장소에는 없다.

`wire-wol_v`에 한때 `client/`(PyQt5 데스크톱 클라이언트)가 있었다가 제거됐는데, 이 프로젝트는 그 코드를 출발점으로 안드로이드 최신 동작에 맞춰 다시 만든 것이다. 가장 큰 차이는 WireGuard: 예전 client는 설치된 공식 `wireguard.exe`를 호출했지만, 여기는 터널을 앱에 내장한다.

## 실행 / 빌드

```
pip install -r requirements.txt
pwsh -File scripts/build_tunnel.ps1   # vendor/amd64/ 생성(최초 1회)
python wirewolpc.pyw                  # 관리자 권한으로 스스로 재실행됨(UAC)
```

릴리즈는 `v*` 태그 푸시 → `.github/workflows/release.yml`이 터널 빌드 → PyInstaller(`--onefile --windowed --uac-admin`) → `WireWOLPC_vX.Y.Z.exe` 릴리즈. 같은 major.minor의 기존 릴리즈는 자동 삭제. CI가 `app/version.py`를 태그로 덮어쓴다.

## 아키텍처

- `wirewolpc.pyw`: 진입점. 관리자 권한 확인/재실행, 단일 인스턴스 뮤텍스, Qt 앱 기동.
- `app/main_window.py`: `MainActivity.kt` 대응. 안드로이드 수명 주기를 창 상태로 대응시킨다 — onResume ↔ 표시/최소화 복귀, onPause ↔ 최소화(자동 재확인·PC 켜기 폴링·예약 만료 타이머 정지). 안드로이드의 "앱이 백그라운드로 가면 와이어가드 끄기"(`WireWolApplication`)는 **창을 닫아 앱을 끝낼 때만 끄기**로 바꿨다(사용자 결정). "종료 확인 후 와이어가드 끄기" 감시는 최소화해도 멈추지 않고, 시작 시각은 설정에 저장돼 재시작 후에도 이어간다.
- `app/settings_dialog.py`: `SettingsActivity.kt` 대응. 카메라/갤러리/직접 입력 → 웹캠/이미지 파일/직접 입력. WireGuard는 `.conf` 파일 불러오기가 추가됨.
- `app/wireguard_tunnel.py`: `WireGuardController.kt` 대응. wireguard-windows의 **embeddable-dll-service** 방식 — `WireGuardTunnel$wirewolpc` 서비스(수동 시작, SID 타입 UNRESTRICTED 필수)를 켤 때 만들고 끌 때 지운다. 서비스 실행 파일은 PyInstaller exe 자신이 아니라 `tunnel_host/tunnel_host.c`로 만든 작은 호스트다 — onefile exe는 자식 프로세스를 하나 더 띄워 서비스 관리자와의 연결이 꼬일 수 있기 때문. 런타임 3파일(tunnel.dll, wireguard.dll, 호스트 exe)은 `%ProgramData%\WireWOL PC\`(SYSTEM/Administrators 전용 ACL)에 복사해 쓰며, tunnel.dll이 wireguard.dll을 "exe와 같은 폴더"에서 찾으므로 셋은 반드시 같은 폴더에 있어야 한다. `.conf`는 서비스 시작 직후 삭제한다(개인키를 디스크에 남기지 않음).
- `app/companion_client.py`: `CompanionClient.kt` 대응. `X-WireWOL-Token` 헤더, 평문 http, 타임아웃 8초.
- `app/network.py`: 매직 패킷(255.255.255.255 + /24 지정 브로드캐스트 + PC 유니캐스트), 직접 도달 탐지(`NetworkProbe.kt`), 네트워크 유무.
- `app/router_wol.py`: `RouterWol.kt` 대응(공유기 로그인 → wol/signal, TLS 1.2 고정, 인증서 TOFU).
- `app/config.py`: DPAPI 암호화 단일 파일(`%LOCALAPPDATA%\WireWOL PC\wirewolpc_secrets.dat`).
- `app/settings_backup.py`: 설정 내보내기/불러오기 — **안드로이드 `SettingsBackup.kt`와 같은 파일 형식**(PBKDF2-HMAC-SHA256 + AES-256-GCM, 비밀번호 UTF-8). 형식/필드를 바꾸면 양쪽을 함께 고칠 것. 불러오기는 현재 설정을 통째로 교체한다. AES-GCM에 `cryptography` 패키지를 쓴다.
- PC 켜기 후 자동 종료: `MainActivity`의 `scheduleAutoShutdownIfPending`/`resumeAutoShutdownWait`와 같은 흐름(`_schedule_auto_shutdown_if_pending`/`_resume_auto_shutdown_wait`). 설정은 `config.load_auto_shutdown()`.
- `app/qr_scan.py`: OpenCV 내장 QR 디코더(pyzbar 등 별도 DLL 없음). 한글 경로 때문에 `cv2.imread` 대신 `np.fromfile` + `imdecode`.
- `scripts/build_tunnel.ps1`: 지정한 wireguard-windows 태그를 받아 그 `build.bat`에 적힌 Go/wireguard-nt 주소·해시를 그대로 써서 tunnel.dll을 빌드한다. Git Bash의 GNU tar가 잡히지 않도록 `System32\tar.exe`를 직접 쓴다.

## 코드 관례

- 페어링 QR JSON(`{"host","port","token","mac"}`)은 `wire-wol_v/windows/app/tray.py`의 `_pairing_payload`와의 계약이다.
- 안드로이드에서 옮겨온 상수(폴링 간격, 타임아웃, 유예 시간)와 상태 문구는 안드로이드 쪽 값과 같게 유지한다.
- 네트워크/서비스 호출은 반드시 `ui_common.run_async`로 백그라운드에서 실행하고 콜백은 GUI 스레드에서 받는다.
