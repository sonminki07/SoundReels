"""
SoundReels Desktop Application Launcher
Runs SoundReels as a standalone native Windows desktop application window (via WebView2)
without requiring a browser tab or command prompt window.
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

import uvicorn
import webview

def is_server_ready(url: str = "http://127.0.0.1:8000/api/slots") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False

def start_backend():
    if not is_server_ready():
        print("[Desktop] Starting background FastAPI server on port 8000...")
        config = uvicorn.Config(
            "backend.app.main:app",
            host="127.0.0.1",
            port=8000,
            log_level="warning",
            loop="asyncio"
        )
        server = uvicorn.Server(config)
        server.run()

def main():
    # 1. Start backend thread if needed
    if not is_server_ready():
        server_thread = threading.Thread(target=start_backend, daemon=True)
        server_thread.start()
        
        # Wait up to 5s for server to start
        for _ in range(50):
            if is_server_ready():
                break
            time.sleep(0.1)

    # 2. Launch Standalone Native Window (Spotify Dark Style)
    window = webview.create_window(
        title="SoundReels — Game SFX Explorer",
        url="http://127.0.0.1:8000/",
        width=1380,
        height=880,
        min_size=(1080, 720),
        background_color="#121212",
        text_select=False,
        confirm_close=False
    )
    
    # 3. Start webview GUI loop
    webview.start(debug=False)

if __name__ == "__main__":
    main()
