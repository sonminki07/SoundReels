"""
SoundReels Desktop Application Launcher
Runs SoundReels as a standalone native Windows desktop application window (via WebView2)
with persistent storage (volume, panel sizes, settings) and custom icon.
"""

import os
import sys
import time
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

def main():
    # 1. Start backend thread if needed
    if not is_server_ready():
        server_thread = threading.Thread(target=start_backend, daemon=True)
        server_thread.start()

        for _ in range(150):
            if is_server_ready():
                print("[SoundReels] Backend ready on http://127.0.0.1:8000/", flush=True)
                break
            time.sleep(0.1)

    storage_dir = ROOT_DIR / "data" / "webview_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    icon_path = ROOT_DIR / "assets" / "soundreels.ico"

    # 2. Launch Standalone Native Window (Spotify Dark Style)
    webview.create_window(
        title="SoundReels — Game SFX Explorer",
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
