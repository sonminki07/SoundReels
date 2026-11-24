"""Checkpoints SQLite WAL and stages current DB, active candidate MP3s, and scene media for GitHub sync."""
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "db" / "soundreels.db"
CACHE_DIR = ROOT / "data" / "audio_cache"
SCENE_DIR = ROOT / "data" / "scene_cache"


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    active_ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT sound_id FROM slot_candidates WHERE status != 'skipped'"
        ).fetchall()
    ]
    conn.close()

    to_add = ["data/db/soundreels.db"]
    for sid in active_ids:
        mp3 = CACHE_DIR / f"{sid}.mp3"
        if mp3.exists():
            to_add.append(f"data/audio_cache/{sid}.mp3")

    if SCENE_DIR.exists():
        for sc in SCENE_DIR.iterdir():
            if sc.is_file():
                to_add.append(f"data/scene_cache/{sc.name}")

    print(f"[Sync] Staging DB + {len(to_add) - 1} active audio/scene files...")
    subprocess.run(["git", "-C", str(ROOT), "add", "-f", "--", *to_add], check=True)
    subprocess.run(["git", "-C", str(ROOT), "add", "-u"], check=True)


if __name__ == "__main__":
    main()
