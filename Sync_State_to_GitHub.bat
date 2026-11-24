@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [SoundReels] 현재 DB 및 활성 후보 음원 상태를 GitHub에 백업(동기화)합니다...
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

"%PY_EXE%" "%~dp0scripts\sync_state.py"
if %errorlevel% neq 0 (
    echo [오류] 상태 스테이징에 실패했습니다.
    pause
    exit /b 1
)

git status -s
git commit -m "sync: backup SoundReels workspace state (DB + active audio + scenes)"
git push origin main
echo [SoundReels] GitHub 백업 완료!
pause
