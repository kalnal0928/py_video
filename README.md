# Py Video Player

간단한 파이썬 동영상 플레이어 (Windows용 권장)

요구사항

- Python 3.8+
- `PyQt5`, `python-vlc`

설치

Windows PowerShell에서:

```powershell
python -m pip install -r requirements.txt
```

실행

```powershell
python player.py "C:\path\to\video.mp4"
```

단축키 및 기능

- `Enter`: 전체화면 토글 (다시 누르면 축소)
- `Space`: 재생/일시정지 토글
- `Left` / `Right`: -5초 / +5초 탐색
- `Up` / `Down`: 볼륨 +10% / -10%
- 파일 열기: 다중 파일 선택하여 재생목록에 추가
- 폴더 열기: 폴더 내의 비디오 파일들을 재귀적으로 찾아 재생목록에 추가
- 재생목록 창: 우측에 재생목록 표시, 더블클릭으로 재생

지원 포맷 및 코덱

- `python-vlc`(libVLC)를 백엔드로 사용하므로 MKV, MP4, AVI, MOV, WMV, FLV, WEBM 등 다양한 포맷을 지원합니다. 시스템에 VLC가 설치되어 있으면 libVLC 바이너리를 활용하여 호환성이 향상됩니다.

참고

- Windows에서 작동하도록 `set_hwnd`를 사용합니다.

데스크탑 배포 (PyInstaller 예시)

Windows에서 단일 실행파일로 패키징하려면 `PyInstaller`를 사용합니다. 주의: `python-vlc`는 시스템의 `libvlc`(VLC 설치)와 연동하므로, libvlc 바이너리를 함께 포함하거나 사용자의 시스템에 VLC를 설치해야 합니다.

예시 명령 (PowerShell):

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --onefile --add-data "qml;qml" --add-data "player_qml.py;." --hidden-import PySide6 --hidden-import vlc g:\project\py_video\player_qml.py
```

권장 사항:
- `--add-data "qml;qml"` 옵션으로 QML 폴더를 포함하세요 (Windows는 `;` 구분자 사용). 패키지 내부에서 QML 경로를 적절히 찾도록 경로 처리 코드를 추가할 필요가 있습니다.
- libVLC (예: `libvlc.dll` 및 관련 플러그인)을 exe와 함께 배포하거나, 설치 안내서로 사용자가 VLC를 설치하도록 안내하세요.
- PySide6/QML 관련 런타임 파일(플러그인)이 누락되면 GUI가 실행되지 않습니다. PyInstaller 사용 시 생성된 `dist` 폴더를 테스트해 누락 파일을 확인하세요.

테스트

패키징 후 `dist` 폴더에 생성된 exe를 실행하여 정상 동작(동영상 렌더링, 재생목록, QML 리소스 로드 등)을 확인하세요. 문제가 발생하면 PyInstaller 로그를 확인하고 필요한 추가 데이터를 `--add-data`로 포함하세요.

패키징 스크립트 (Windows)

리포지토리에 `build_windows.ps1` 스크립트를 추가했습니다. 이 스크립트는 다음을 자동으로 수행합니다:
- `pyinstaller`를 사용해 `--onedir` 형식으로 패키징
- QML 폴더를 포함
- 사용자의 VLC 설치 폴더(`PY_VIDEO_LIBVLC` 환경변수 또는 일반 설치 경로)를 찾아 `libvlc` DLL 및 `plugins` 폴더를 `dist` 내부로 복사
- `run.bat` 실행 스크립트 생성 (배포 후 쉽게 실행하도록 PATH를 설정)

사용 방법 (PowerShell에서 프로젝트 루트에서 실행):

```powershell
# 자동으로 설치 경로를 찾아 사용합니다. 필요하면 명시적으로 경로 지정:

.\build_windows.ps1
```

빌드가 완료되면 `dist\py_video_player` 폴더에 실행 가능한 배포가 생성됩니다. `run.bat`을 사용해 실행하세요.

중요: PyInstaller로 패키징할 때 Python 아키텍처(32/64비트)와 VLC 아키텍처(32/64비트)가 일치해야 합니다. 또한 libVLC 바이너리와 플러그인들이 포함되어야만 다양한 코덱을 사용할 수 있습니다.
