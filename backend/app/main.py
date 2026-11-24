import os
import sys
import json
import re
import time
import shutil
import webbrowser
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import soundfile as sf
import librosa
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure repo root is on sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.db import (
    get_db_context,
    init_db,
    seed_project_and_slots,
    DB_PATH,
    AUDIO_CACHE_DIR,
    SCENE_CACHE_DIR,
)

app = FastAPI(title="SoundReels API", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAFE_ID_REGEX = re.compile(r'^[a-zA-Z0-9_\-]+$')
FS_ID_REGEX = re.compile(r'^fs-(\d+)')
ALLOWED_EXTERNAL_HOSTS = {"freesound.org", "www.freesound.org", "lotsofsounds.com", "www.lotsofsounds.com"}
ALLOWED_SCENE_EXTS = {
    ".gif": ("gif", "image/gif"),
    ".mp4": ("video", "video/mp4"),
    ".webm": ("video", "video/webm"),
    ".mov": ("video", "video/quicktime"),
}
MAX_SCENE_BYTES = 100 * 1024 * 1024  # 100 MB

def validate_id(identifier: str, name: str = "id"):
    if not identifier or not SAFE_ID_REGEX.match(identifier):
        raise HTTPException(status_code=400, detail=f"Invalid {name} format. Allowed: alphanumeric, _, -")

def resolve_source_urls(sound_dict: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Resolves original Freesound author URL and LotsOfSounds catalog URL."""
    sid = str(sound_dict.get("source_id") or sound_dict.get("id") or "")
    raw_url = sound_dict.get("source_url") or ""
    m = FS_ID_REGEX.match(sid)
    if m:
        fs_num = m.group(1)
        return {
            "original_source_url": f"https://freesound.org/s/{fs_num}/",
            "catalog_url": f"https://www.lotsofsounds.com/sounds/fs-{fs_num}",
        }
    if raw_url.startswith("https://"):
        return {"original_source_url": raw_url, "catalog_url": raw_url}
    return {"original_source_url": None, "catalog_url": None}

# Pydantic Request Models
class ActionRequest(BaseModel):
    slot_id: str
    sound_id: str

class ConfirmRequest(BaseModel):
    slot_id: str
    sound_id: str
    unity_project_path: Optional[str] = None

class BatchExportRequest(BaseModel):
    unity_project_path: str

class SimilarRequest(BaseModel):
    slot_id: str
    reference_sound_id: str

class MutateRequest(BaseModel):
    slot_id: str
    sound_id: str

class SceneTrimRequest(BaseModel):
    scene_in: float = Field(ge=0.0)
    scene_out: float = Field(ge=0.0)
    sound_offset: float = Field(default=0.0, ge=0.0)

class AudioTrimRequest(BaseModel):
    slot_id: str
    sound_id: str
    audio_in: float = Field(ge=0.0)
    audio_out: float = Field(ge=0.0)

class OpenExternalRequest(BaseModel):
    url: str

@app.on_event("startup")
def on_startup():
    init_db()
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM slots")
        if c.fetchone()[0] == 0:
            seed_project_and_slots()

# --- Helper: Audio Processing on Export (Trim + Silence Trim + -1dBFS Peak Normalization) ---
def export_processed_audio_to_unity(
    src_path: Path,
    dest_wav_path: Path,
    audio_in: float = 0.0,
    audio_out: float = 0.0,
) -> bool:
    try:
        y, sr = librosa.load(str(src_path), sr=48000, mono=True)
        if len(y) == 0:
            return False

        # 1. Apply user-defined non-destructive trim range if specified
        has_custom_trim = (audio_in > 0.0) or (audio_out > 0.0)
        if has_custom_trim:
            start_sample = int(max(0.0, audio_in) * sr)
            end_sample = int(audio_out * sr) if audio_out > audio_in else len(y)
            end_sample = min(max(start_sample + 1, end_sample), len(y))
            processed = y[start_sample:end_sample]
        else:
            # Default: trim leading and trailing silence automatically
            processed, _ = librosa.effects.trim(y, top_db=30)
            if len(processed) == 0:
                processed = y

        # 2. Peak normalization to -1.0 dBFS (prevent digital clipping)
        peak = np.max(np.abs(processed))
        if peak > 0:
            target_peak = 10.0 ** (-1.0 / 20.0)  # ~0.891
            processed = processed * (target_peak / peak)

        # 3. Write uncompressed 24-bit PCM WAV for Unity
        sf.write(str(dest_wav_path), processed, sr, subtype="PCM_24")
        return True
    except Exception as e:
        print(f"[Export Error] Failed to process audio {src_path} -> {dest_wav_path}: {e}")
        shutil.copy2(src_path, dest_wav_path)
        return True

def _enrich_slot_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    d["clap_prompts"] = json.loads(d.get("clap_prompts") or "[]")
    d["search_tags"] = json.loads(d.get("search_tags") or "[]")
    d["negative_prompts"] = json.loads(d.get("negative_prompts") or "[]")
    media_path = d.get("scene_media_path")
    if media_path and Path(media_path).exists():
        mtime = int(Path(media_path).stat().st_mtime)
        d["scene_media_url"] = f"/api/slots/{d['id']}/scene/media?v={mtime}"
    else:
        d["scene_media_url"] = None
    d["scene_in"] = float(d.get("scene_in") or 0.0)
    d["scene_out"] = float(d.get("scene_out") or 0.0)
    d["sound_offset"] = float(d.get("sound_offset") or 0.0)
    return d

# --- API Endpoints ---

@app.get("/api/projects")
def get_projects():
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM projects")
        projects = [dict(r) for r in c.fetchall()]
    return {"projects": projects}

@app.get("/api/slots")
def get_slots():
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        SELECT s.*, 
               COUNT(sc.id) as candidate_count,
               SUM(CASE WHEN sc.status = 'liked' THEN 1 ELSE 0 END) as liked_count,
               SUM(CASE WHEN sc.status = 'confirmed' THEN 1 ELSE 0 END) as confirmed_count
        FROM slots s
        LEFT JOIN slot_candidates sc ON s.id = sc.slot_id
        GROUP BY s.id
        ORDER BY s.created_at ASC
        """)
        slots = [_enrich_slot_dict(dict(r)) for r in c.fetchall()]
    return {"slots": slots}

@app.get("/api/slots/{slot_id}")
def get_slot(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Slot not found")
        d = _enrich_slot_dict(dict(row))
    return d

@app.get("/api/slots/{slot_id}/candidates")
def get_slot_candidates(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        SELECT sc.id as candidate_id, sc.slot_id, sc.sound_id, sc.score, sc.rank, sc.status as candidate_status,
               COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
               snd.name, snd.file_path, snd.duration, snd.license_type, snd.author, snd.source_url, snd.source_id, snd.tags, snd.sha256
        FROM slot_candidates sc
        JOIN sounds snd ON sc.sound_id = snd.id
        WHERE sc.slot_id = ?
        ORDER BY sc.rank ASC
        """, (slot_id,))
        candidates = []
        for r in c.fetchall():
            item = dict(r)
            item["tags"] = json.loads(item.get("tags") or "[]")
            item["audio_url"] = f"/api/sounds/{item['sound_id']}/audio"
            urls = resolve_source_urls(item)
            item["original_source_url"] = urls["original_source_url"]
            item["catalog_url"] = urls["catalog_url"]
            candidates.append(item)
    return {"slot_id": slot_id, "candidates": candidates}

@app.get("/api/sounds/{sound_id}/audio")
def get_sound_audio(sound_id: str):
    validate_id(sound_id, "sound_id")
    local_path = (AUDIO_CACHE_DIR / f"{sound_id}.mp3").resolve()

    if not local_path.is_relative_to(AUDIO_CACHE_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Forbidden path")

    if not local_path.exists():
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT file_path FROM sounds WHERE id = ?", (sound_id,))
            row = c.fetchone()
            if row and Path(row["file_path"]).exists():
                candidate_path = Path(row["file_path"]).resolve()
                if not candidate_path.is_relative_to(AUDIO_CACHE_DIR.resolve()):
                    raise HTTPException(status_code=403, detail="Forbidden path")
                local_path = candidate_path
            else:
                raise HTTPException(status_code=404, detail="Audio file not found in cache")
    return FileResponse(str(local_path), media_type="audio/mpeg", headers={"Accept-Ranges": "bytes"})

# --- Scene Media Upload / Stream / Trim Endpoints ---

@app.post("/api/slots/{slot_id}/scene")
async def upload_slot_scene(slot_id: str, file: UploadFile = File(...)):
    validate_id(slot_id, "slot_id")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_SCENE_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported format. Allowed: .gif, .mp4, .webm, .mov")

    SCENE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_SCENE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 100MB limit")

    # Remove any previous scene files for this slot (different extension)
    for old_ext in ALLOWED_SCENE_EXTS:
        old_path = SCENE_CACHE_DIR / f"{slot_id}{old_ext}"
        if old_path.exists():
            old_path.unlink()

    dest_path = (SCENE_CACHE_DIR / f"{slot_id}{ext}").resolve()
    if not dest_path.is_relative_to(SCENE_CACHE_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Forbidden path")

    with open(dest_path, "wb") as f:
        f.write(content)

    media_type, _ = ALLOWED_SCENE_EXTS[ext]
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM slots WHERE id = ?", (slot_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Slot not found")
        c.execute("""
        UPDATE slots
        SET scene_media_path = ?, scene_media_type = ?, scene_in = 0.0, scene_out = 0.0, sound_offset = 0.0
        WHERE id = ?
        """, (str(dest_path), media_type, slot_id))
        conn.commit()

    return {
        "status": "ok",
        "slot_id": slot_id,
        "scene_media_type": media_type,
        "scene_media_url": f"/api/slots/{slot_id}/scene/media?v={int(time.time())}",
    }

@app.get("/api/slots/{slot_id}/scene/media")
def get_slot_scene_media(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT scene_media_path FROM slots WHERE id = ?", (slot_id,))
        row = c.fetchone()
        if not row or not row["scene_media_path"]:
            raise HTTPException(status_code=404, detail="No scene media attached to this slot")
        media_path = Path(row["scene_media_path"]).resolve()

    if not media_path.is_relative_to(SCENE_CACHE_DIR.resolve()) or not media_path.exists():
        raise HTTPException(status_code=404, detail="Scene media file not found")

    ext = media_path.suffix.lower()
    _, mime_type = ALLOWED_SCENE_EXTS.get(ext, ("video", "application/octet-stream"))
    return FileResponse(str(media_path), media_type=mime_type, headers={"Accept-Ranges": "bytes"})

@app.delete("/api/slots/{slot_id}/scene")
def delete_slot_scene(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT scene_media_path FROM slots WHERE id = ?", (slot_id,))
        row = c.fetchone()
        if row and row["scene_media_path"]:
            media_path = Path(row["scene_media_path"]).resolve()
            if media_path.is_relative_to(SCENE_CACHE_DIR.resolve()) and media_path.exists():
                media_path.unlink()
        c.execute("""
        UPDATE slots
        SET scene_media_path = NULL, scene_media_type = NULL, scene_in = 0.0, scene_out = 0.0, sound_offset = 0.0
        WHERE id = ?
        """, (slot_id,))
        conn.commit()
    return {"status": "ok"}

@app.post("/api/slots/{slot_id}/scene/trim")
def trim_slot_scene(slot_id: str, req: SceneTrimRequest):
    validate_id(slot_id, "slot_id")
    if req.scene_out > 0.0 and req.scene_out <= req.scene_in:
        raise HTTPException(status_code=400, detail="scene_out must be greater than scene_in")

    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        UPDATE slots SET scene_in = ?, scene_out = ?, sound_offset = ? WHERE id = ?
        """, (req.scene_in, req.scene_out, req.sound_offset, slot_id))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Slot not found")
        conn.commit()
    return {"status": "ok", "scene_in": req.scene_in, "scene_out": req.scene_out, "sound_offset": req.sound_offset}

@app.post("/api/actions/trim_audio")
def trim_candidate_audio(req: AudioTrimRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")
    if req.audio_out > 0.0 and req.audio_out <= req.audio_in:
        raise HTTPException(status_code=400, detail="audio_out must be greater than audio_in")

    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        UPDATE slot_candidates SET audio_in = ?, audio_out = ? WHERE slot_id = ? AND sound_id = ?
        """, (req.audio_in, req.audio_out, req.slot_id, req.sound_id))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Candidate not found")
        conn.commit()
    return {"status": "ok", "audio_in": req.audio_in, "audio_out": req.audio_out}

@app.post("/api/open_external_url")
def open_external_url(req: OpenExternalRequest):
    """Safely opens whitelisted external source URLs in the user's default browser."""
    parsed = urllib.parse.urlparse(req.url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_EXTERNAL_HOSTS:
        raise HTTPException(status_code=400, detail="URL host is not in the allowed whitelist")
    webbrowser.open(req.url, new=2)
    return {"status": "ok", "opened": req.url}

# --- Existing Actions ---

@app.post("/api/actions/like")
def like_candidate(req: ActionRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        UPDATE slot_candidates 
        SET status = CASE WHEN status = 'liked' THEN 'pending' ELSE 'liked' END
        WHERE slot_id = ? AND sound_id = ?
        """, (req.slot_id, req.sound_id))
        conn.commit()

        c.execute("SELECT status FROM slot_candidates WHERE slot_id = ? AND sound_id = ?", (req.slot_id, req.sound_id))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate not found")
        new_status = row["status"]
    return {"status": "ok", "new_candidate_status": new_status}

@app.post("/api/actions/skip")
def skip_candidate(req: ActionRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        UPDATE slot_candidates SET status = 'skipped' WHERE slot_id = ? AND sound_id = ?
        """, (req.slot_id, req.sound_id))
        conn.commit()
    return {"status": "ok", "action": "skipped"}

@app.post("/api/actions/confirm")
def confirm_candidate(req: ConfirmRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")

    with get_db_context() as conn:
        c = conn.cursor()

        c.execute("""
        SELECT COALESCE(audio_in, 0.0) as audio_in, COALESCE(audio_out, 0.0) as audio_out
        FROM slot_candidates WHERE slot_id = ? AND sound_id = ?
        """, (req.slot_id, req.sound_id))
        cand_row = c.fetchone()
        if not cand_row:
            raise HTTPException(status_code=404, detail="Candidate record not found")

        c.execute("SELECT * FROM sounds WHERE id = ?", (req.sound_id,))
        snd_row = c.fetchone()
        if not snd_row:
            raise HTTPException(status_code=404, detail="Sound record not found")
        snd = dict(snd_row)
        urls = resolve_source_urls(snd)
        credit_url = urls["original_source_url"] or snd["source_url"]

        c.execute("""
        UPDATE slot_candidates SET status = 'confirmed' WHERE slot_id = ? AND sound_id = ?
        """, (req.slot_id, req.sound_id))
        c.execute("UPDATE slots SET status = 'confirmed' WHERE id = ?", (req.slot_id,))

        c.execute("SELECT COUNT(*) as cnt FROM confirmed_exports WHERE slot_id = ?", (req.slot_id,))
        variant_no = c.fetchone()["cnt"] + 1

        exported_path = ""
        if req.unity_project_path:
            target_root = Path(req.unity_project_path).resolve()
            if not target_root.exists():
                raise HTTPException(status_code=400, detail="Target Unity directory does not exist")

            dest_dir = (target_root / "Assets" / "Audio" / "SFX" / req.slot_id).resolve()
            if not dest_dir.is_relative_to(target_root):
                raise HTTPException(status_code=403, detail="Path traversal detected in Unity destination")

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / f"{req.slot_id}_{variant_no:02d}.wav"
            src_file = AUDIO_CACHE_DIR / f"{req.sound_id}.mp3"

            if src_file.exists():
                export_processed_audio_to_unity(
                    src_file,
                    dest_file,
                    audio_in=float(cand_row["audio_in"]),
                    audio_out=float(cand_row["audio_out"]),
                )
                exported_path = str(dest_file)

                credits_file = target_root / "CREDITS.txt"
                with open(credits_file, "a", encoding="utf-8") as f:
                    f.write(f'"{snd["name"]}" ({credit_url}) by {snd["author"]} - License: {snd["license_type"]}\n')
            else:
                raise HTTPException(status_code=404, detail="Source audio file missing from cache")

        c.execute("""
        INSERT INTO confirmed_exports (slot_id, sound_id, variant_no, unity_dest_path)
        VALUES (?, ?, ?, ?)
        """, (req.slot_id, req.sound_id, variant_no, exported_path))

        conn.commit()
    return {"status": "ok", "variant_no": variant_no, "exported_path": exported_path}

@app.post("/api/actions/reorder_similar")
def reorder_similar(req: SimilarRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.reference_sound_id, "reference_sound_id")

    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM slot_candidates WHERE slot_id = ? ORDER BY score DESC", (req.slot_id,))
        rows = [dict(r) for r in c.fetchall()]

        ref_item = next((r for r in rows if r["sound_id"] == req.reference_sound_id), None)
        other_items = [r for r in rows if r["sound_id"] != req.reference_sound_id]

        if ref_item:
            new_order = [ref_item] + other_items
            for rank_idx, item in enumerate(new_order, start=1):
                c.execute("UPDATE slot_candidates SET rank = ? WHERE id = ?", (rank_idx, item["id"]))
            conn.commit()

    return {"status": "ok", "message": "Candidates reordered by similarity anchor"}

@app.post("/api/actions/mutate")
def mutate_sound(req: MutateRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")

    src_file = AUDIO_CACHE_DIR / f"{req.sound_id}.mp3"
    if not src_file.exists():
        raise HTTPException(status_code=404, detail="Sound not found in cache")

    try:
        y, sr = librosa.load(str(src_file), sr=48000, mono=True)
        mutated = librosa.effects.pitch_shift(y, sr=sr, n_steps=2.0)

        mutated_id = f"{req.sound_id}_mutated"
        dest_file = AUDIO_CACHE_DIR / f"{mutated_id}.mp3"
        sf.write(str(dest_file), mutated, sr)

        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM sounds WHERE id = ?", (req.sound_id,))
            orig_row = c.fetchone()
            if not orig_row:
                raise HTTPException(status_code=404, detail="Original sound metadata not found")
            orig = dict(orig_row)

            c.execute("""
            INSERT INTO sounds (id, source, source_id, name, file_path, duration, license_type, author, source_url, tags, sha256)
            VALUES (?, 'procedural', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET file_path=excluded.file_path;
            """, (
                mutated_id,
                orig["source_id"],
                f"{orig['name']} (Mutated Take)",
                str(dest_file),
                orig["duration"],
                orig["license_type"],
                orig["author"],
                orig["source_url"],
                orig["tags"],
                "mutated_hash"
            ))

            c.execute("""
            INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
            VALUES (?, ?, ?, 1, 'liked')
            ON CONFLICT(slot_id, sound_id) DO UPDATE SET status='liked';
            """, (req.slot_id, mutated_id, 0.95))
            conn.commit()

        return {"status": "ok", "mutated_sound_id": mutated_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mutation error: {e}")

@app.post("/api/export/batch_unity")
def batch_export_to_unity(req: BatchExportRequest):
    target_root = Path(req.unity_project_path).resolve()
    if not target_root.exists():
        raise HTTPException(status_code=400, detail="Target Unity directory does not exist")

    dest_base = target_root / "Assets" / "Audio" / "SFX"
    dest_base.mkdir(parents=True, exist_ok=True)

    exported_count = 0
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        SELECT sc.slot_id, sc.sound_id,
               COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
               snd.name, snd.author, snd.source_url, snd.source_id, snd.license_type
        FROM slot_candidates sc
        JOIN sounds snd ON sc.sound_id = snd.id
        WHERE sc.status = 'confirmed'
        """)
        confirmed_items = [dict(r) for r in c.fetchall()]

        credits_file = target_root / "CREDITS.txt"
        with open(credits_file, "a", encoding="utf-8") as f:
            for item in confirmed_items:
                slot_dir = dest_base / item["slot_id"]
                slot_dir.mkdir(parents=True, exist_ok=True)
                dest_file = slot_dir / f"{item['slot_id']}_01.wav"
                src_file = AUDIO_CACHE_DIR / f"{item['sound_id']}.mp3"
                if src_file.exists():
                    export_processed_audio_to_unity(
                        src_file,
                        dest_file,
                        audio_in=float(item["audio_in"]),
                        audio_out=float(item["audio_out"]),
                    )
                    urls = resolve_source_urls(item)
                    credit_url = urls["original_source_url"] or item["source_url"]
                    f.write(f'"{item["name"]}" ({credit_url}) by {item["author"]} - License: {item["license_type"]}\n')
                    exported_count += 1

    return {"status": "ok", "exported_count": exported_count, "destination": str(dest_base)}

# Mount static files for Frontend
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
