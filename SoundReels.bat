@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" goto :run_app

echo [SoundReels] 최초 실행 환경(.venv)을 자동 설치합니다...
where py >nul 2>&1
if %errorlevel%==0 (
    py -3.11 -m venv "%~dp0.venv" 2>nul || py -3 -m venv "%~dp0.venv"
) else (
    python -m venv "%~dp0.venv"
)
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [오류] Python 3.10~3.12가 설치되어 있지 않습니다. https://www.python.org/ 에서 설치 후 다시 실행하세요.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
echo [SoundReels] PyTorch 설치 중 (NVIDIA GPU 우선 시도, 미지원 시 CPU 버전 자동 설치)...
"%~dp0.venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cu121 || "%~dp0.venv\Scripts\python.exe" -m pip install torch
echo [SoundReels] SoundReels 의존성 패키지 설치 중...
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0backend\requirements.txt"

:run_app
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run_desktop.py"

