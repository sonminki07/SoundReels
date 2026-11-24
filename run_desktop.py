"""
SoundReels Desktop Application Launcher
Runs SoundReels as a standalone native Windows desktop application window (via WebView2)
with persistent storage (volume, panel sizes, settings) and custom icon.
"""

import os
import sys
import time
import shutil
import atexit
import ctypes
import msvcrt
import threading
import urllib.request
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Under pythonw.exe (GUI mode), stdout/stderr are None, which crashes uvicorn's logging formatter.
if sys.stdout is None or sys.stderr is None:
    _log_dir = ROOT_DIR / "data"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_stream = open(_log_dir / "desktop_app.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_stream
    if sys.stderr is None:
        sys.stderr = _log_stream

import uvicorn
import webview

def is_server_ready(url: str = "http://127.0.0.1:8000/api/slots") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False

def start_backend():
    try:
        print("[SoundReels] Starting embedded FastAPI backend...", flush=True)
        config = uvicorn.Config(
            "backend.app.main:app",
            host="127.0.0.1",
            port=8000,
            log_level="warning",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        server.run()
    except Exception as exc:
        import traceback
        print(f"[SoundReels] Backend crashed: {exc}\n{traceback.format_exc()}", flush=True)

WINDOW_TITLE = "SoundReels — Game SFX Explorer"

def _focus_existing_window() -> bool:
    """Brings an already-open SoundReels window to the foreground if present."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd and user32.IsWindowVisible(hwnd):
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False

def _acquire_storage_dir() -> Path:
    """Returns primary storage_dir if unlocked, or a clean temp dir so WebView2 never hits 0x800700AA."""
    data_dir = ROOT_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    primary_dir = data_dir / "webview_storage"
    primary_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / "webview_storage.lock"

    # Best-effort cleanup of old temp storage dirs
    for stale in data_dir.glob("webview_storage_tmp_*"):
        try:
            shutil.rmtree(stale)
        except Exception:
            pass

    try:
        lock_fp = open(lock_path, "a+")
        msvcrt.locking(lock_fp.fileno(), msvcrt.LK_NBLCK, 1)
        atexit.register(lambda: lock_fp.close())
        return primary_dir
    except OSError:
        fallback_dir = data_dir / f"webview_storage_tmp_{os.getpid()}"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        atexit.register(lambda: shutil.rmtree(fallback_dir, ignore_errors=True))
        print(f"[SoundReels] Primary webview_storage in use; using clean folder {fallback_dir.name}", flush=True)
        return fallback_dir

def main():
    # 0. If window already exists, bring it to front instead of opening a colliding WebView2 instance
    if _focus_existing_window():
        print("[SoundReels] Existing window focused; exiting duplicate launcher.", flush=True)
        return

    # 1. Start backend thread if needed
    if not is_server_ready():
        server_thread = threading.Thread(target=start_backend, daemon=True)
        server_thread.start()

        for _ in range(150):
            if is_server_ready():
                print("[SoundReels] Backend ready on http://127.0.0.1:8000/", flush=True)
                break
            time.sleep(0.1)

    storage_dir = _acquire_storage_dir()
    icon_path = ROOT_DIR / "assets" / "soundreels.ico"

    # 2. Launch Standalone Native Window (Spotify Dark Style)
    webview.create_window(
        title=WINDOW_TITLE,
        url="http://127.0.0.1:8000/",
        width=1380,
        height=880,
        min_size=(1080, 720),
        background_color="#121212",
        text_select=False,
        confirm_close=False,
    )

    # 3. Start webview GUI loop with persistent storage (private_mode=False)
    start_kwargs = {
        "debug": False,
        "private_mode": False,
        "storage_path": str(storage_dir),
    }
    if icon_path.exists():
        start_kwargs["icon"] = str(icon_path)

    webview.start(**start_kwargs)

if __name__ == "__main__":
    main()
