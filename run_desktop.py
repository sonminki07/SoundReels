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
import subprocess
import threading
import urllib.parse
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

ALLOWED_SOURCE_HOSTS = {"freesound.org", "www.freesound.org", "lotsofsounds.com", "www.lotsofsounds.com"}


def _is_allowed_source_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url or "")
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_SOURCE_HOSTS


class DesktopApi:
    """Native PyWebView JS-to-Python bridge for guaranteed window & browser launching."""

    def __init__(self):
        self._popup_window = None

    def get_popup_url(self):
        win = self._popup_window
        if not win:
            return {"status": "empty", "url": ""}
        try:
            url = win.get_current_url() or ""
            return {"status": "ok", "url": url}
        except Exception:
            self._popup_window = None
            return {"status": "closed", "url": ""}

    def import_from_popup(self, url: str = ""):
        target_url = (url or "").strip()
        if not target_url and self._popup_window:
            try:
                target_url = self._popup_window.get_current_url() or ""
            except Exception:
                target_url = ""
        if not _is_allowed_source_url(target_url):
            return {"status": "error", "detail": "현재 페이지 URL은 가져오기 지원 대상이 아닙니다."}
        try:
            import json as _json
            body = _json.dumps({"url": target_url}).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/desktop/popup_import",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    def open_source_window(self, url: str, title: str = "원본 출처"):
        if not _is_allowed_source_url(url):
            return {"status": "error", "detail": "Blocked URL"}

        def _inject_bridge(win):
            js_code = """
            (function() {
              if (document.getElementById('sr-popup-bridge-bar')) return;
              var bar = document.createElement('div');
              bar.id = 'sr-popup-bridge-bar';
              bar.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483647;display:flex;gap:8px;align-items:center;background:#121212;border:2px solid #1DB954;border-radius:999px;padding:8px 14px;box-shadow:0 8px 28px rgba(0,0,0,0.6);font-family:sans-serif;';
              var btn = document.createElement('button');
              btn.textContent = '📥 현재 소리 SoundReels 후보에 추가';
              btn.style.cssText = 'background:#1DB954;color:#000;border:none;border-radius:999px;padding:6px 14px;font-weight:800;font-size:13px;cursor:pointer;';
              btn.onclick = async function() {
                btn.disabled = true;
                btn.textContent = '⏳ 가져오는 중...';
                try {
                  var res = await window.pywebview.api.import_from_popup(window.location.href);
                  if (res && res.status === 'ok') {
                    btn.textContent = '✅ ' + (res.imported_count || 1) + '개 추가됨!';
                    setTimeout(function() { btn.textContent = '📥 현재 소리 SoundReels 후보에 추가'; btn.disabled = false; }, 2200);
                  } else {
                    btn.textContent = '❌ ' + ((res && res.detail) || '가져오기 실패');
                    setTimeout(function() { btn.textContent = '📥 현재 소리 SoundReels 후보에 추가'; btn.disabled = false; }, 2600);
                  }
                } catch (e) {
                  btn.textContent = '❌ 오류';
                  setTimeout(function() { btn.textContent = '📥 현재 소리 SoundReels 후보에 추가'; btn.disabled = false; }, 2200);
                }
              };
              bar.appendChild(btn);
              document.body.appendChild(bar);
            })();
            """
            try:
                win.evaluate_js(js_code)
            except Exception:
                pass

        def _spawn():
            try:
                win = webview.create_window(
                    title=f"원본 출처: {title} — {url}",
                    url=url,
                    js_api=self,
                    width=1120,
                    height=780,
                    focus=True,
                    text_select=True,
                )
                self._popup_window = win
                win.events.loaded += lambda: _inject_bridge(win)
            except Exception as exc:
                print(f"[SoundReels] Failed to open source popup window: {exc}", flush=True)

        threading.Thread(target=_spawn, daemon=True).start()
        return {"status": "ok", "opened": url}

    def open_external_browser(self, url: str):
        if not _is_allowed_source_url(url):
            return {"status": "error", "detail": "Blocked URL"}
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", url],
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {"status": "ok", "opened": url}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}


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
        js_api=DesktopApi(),
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
    try:
        main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        try:
            with open(ROOT_DIR / "data" / "crash.log", "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
