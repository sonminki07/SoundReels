import os
import sys
import json
import re
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional

import numpy as np
import soundfile as sf
import librosa
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure repo root is on sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.db import get_db_context, seed_project_and_slots, DB_PATH, AUDIO_CACHE_DIR

app = FastAPI(title="SoundReels API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAFE_ID_REGEX = re.compile(r'^[a-zA-Z0-9_\-]+$')

def validate_id(identifier: str, name: str = "id"):
    if not identifier or not SAFE_ID_REGEX.match(identifier):
        raise HTTPException(status_code=400, detail=f"Invalid {name} format. Allowed: alphanumeric, _, -")

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

@app.on_event("startup")
def on_startup():
    if not DB_PATH.exists():
        seed_project_and_slots()

# --- Helper: Audio Processing on Export (Silence Trim & -1dBFS Peak Normalization) ---
def export_processed_audio_to_unity(src_path: Path, dest_wav_path: Path) -> bool:
    try:
        y, sr = librosa.load(str(src_path), sr=48000, mono=True)
        if len(y) == 0:
            return False
            
        # 1. Trim leading and trailing silence (eliminates lag!)
        trimmed, _ = librosa.effects.trim(y, top_db=30)
        if len(trimmed) == 0:
            trimmed = y
            
        # 2. Peak normalization to -1.0 dBFS (prevent digital clipping)
        peak = np.max(np.abs(trimmed))
        if peak > 0:
            target_peak = 10.0 ** (-1.0 / 20.0) # ~0.891
            trimmed = trimmed * (target_peak / peak)
            
        # 3. Write uncompressed 24-bit PCM WAV for Unity
        sf.write(str(dest_wav_path), trimmed, sr, subtype='PCM_24')
        return True
    except Exception as e:
        print(f"[Export Error] Failed to process audio {src_path} -> {dest_wav_path}: {e}")
        # Fallback to copy raw
        shutil.copy2(src_path, dest_wav_path)
        return True

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
        slots = []
        for r in c.fetchall():
            d = dict(r)
            d["clap_prompts"] = json.loads(d.get("clap_prompts") or "[]")
            d["search_tags"] = json.loads(d.get("search_tags") or "[]")
            d["negative_prompts"] = json.loads(d.get("negative_prompts") or "[]")
            slots.append(d)
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
        d = dict(row)
        d["clap_prompts"] = json.loads(d.get("clap_prompts") or "[]")
        d["search_tags"] = json.loads(d.get("search_tags") or "[]")
        d["negative_prompts"] = json.loads(d.get("negative_prompts") or "[]")
    return d

@app.get("/api/slots/{slot_id}/candidates")
def get_slot_candidates(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        SELECT sc.id as candidate_id, sc.slot_id, sc.sound_id, sc.score, sc.rank, sc.status as candidate_status,
               snd.name, snd.file_path, snd.duration, snd.license_type, snd.author, snd.source_url, snd.tags, snd.sha256
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
            candidates.append(item)
    return {"slot_id": slot_id, "candidates": candidates}

@app.get("/api/sounds/{sound_id}/audio")
def get_sound_audio(sound_id: str):
    validate_id(sound_id, "sound_id")
    local_path = (AUDIO_CACHE_DIR / f"{sound_id}.mp3").resolve()
    
    # Path traversal protection
    if not local_path.is_relative_to(AUDIO_CACHE_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Forbidden path")
        
    if not local_path.exists():
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT file_path FROM sounds WHERE id = ?", (sound_id,))
            row = c.fetchone()
            if row and Path(row["file_path"]).exists():
                local_path = Path(row["file_path"]).resolve()
            else:
                raise HTTPException(status_code=404, detail="Audio file not found in cache")
    return FileResponse(str(local_path), media_type="audio/mpeg", headers={"Accept-Ranges": "bytes"})

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
        
        # Verify candidate exists
        c.execute("SELECT * FROM slot_candidates WHERE slot_id = ? AND sound_id = ?", (req.slot_id, req.sound_id))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Candidate record not found")
            
        c.execute("SELECT * FROM sounds WHERE id = ?", (req.sound_id,))
        snd_row = c.fetchone()
        if not snd_row:
            raise HTTPException(status_code=404, detail="Sound record not found")
        snd = dict(snd_row)
        
        # 1. Update candidate status
        c.execute("""
        UPDATE slot_candidates SET status = 'confirmed' WHERE slot_id = ? AND sound_id = ?
        """, (req.slot_id, req.sound_id))
        
        # 2. Update slot status to 'confirmed'
        c.execute("UPDATE slots SET status = 'confirmed' WHERE id = ?", (req.slot_id,))
        
        # 3. Determine variant number
        c.execute("SELECT COUNT(*) as cnt FROM confirmed_exports WHERE slot_id = ?", (req.slot_id,))
        variant_no = c.fetchone()["cnt"] + 1
        
        # 4. Optional Unity copy with pre-processing (silence trim & peak norm)
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
                export_processed_audio_to_unity(src_file, dest_file)
                exported_path = str(dest_file)
                
                # Update CREDITS.txt
                credits_file = target_root / "CREDITS.txt"
                with open(credits_file, "a", encoding="utf-8") as f:
                    f.write(f'"{snd["name"]}" ({snd["source_url"]}) by {snd["author"]} - License: {snd["license_type"]}\n')
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
    """Re-ranks candidates based on audio similarity to reference_sound_id."""
    validate_id(req.slot_id, "slot_id")
    validate_id(req.reference_sound_id, "reference_sound_id")
    
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM slot_candidates WHERE slot_id = ? ORDER BY score DESC", (req.slot_id,))
        rows = [dict(r) for r in c.fetchall()]
        
        # Place reference first, followed by others
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
    """Creates a pitch/speed varied take of the candidate sound."""
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")
    
    src_file = AUDIO_CACHE_DIR / f"{req.sound_id}.mp3"
    if not src_file.exists():
        raise HTTPException(status_code=404, detail="Sound not found in cache")
        
    try:
        y, sr = librosa.load(str(src_file), sr=48000, mono=True)
        # Shift pitch by +2 semitones for mutation take
        mutated = librosa.effects.pitch_shift(y, sr=sr, n_steps=2.0)
        
        mutated_id = f"{req.sound_id}_mutated"
        dest_file = AUDIO_CACHE_DIR / f"{mutated_id}.mp3"
        sf.write(str(dest_file), mutated, sr)
        
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM sounds WHERE id = ?", (req.sound_id,))
            orig = dict(c.fetchone())
            
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mutation error: {e}")

@app.post("/api/export/batch_unity")
def batch_export_to_unity(req: BatchExportRequest):
    """Batch exports all confirmed sound effects to the Unity project directory."""
    target_root = Path(req.unity_project_path).resolve()
    if not target_root.exists():
        raise HTTPException(status_code=400, detail="Target Unity directory does not exist")
        
    dest_base = target_root / "Assets" / "Audio" / "SFX"
    dest_base.mkdir(parents=True, exist_ok=True)
    
    exported_count = 0
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        SELECT sc.slot_id, sc.sound_id, snd.name, snd.author, snd.source_url, snd.license_type
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
                    export_processed_audio_to_unity(src_file, dest_file)
                    f.write(f'"{item["name"]}" ({item["source_url"]}) by {item["author"]} - License: {item["license_type"]}\n')
                    exported_count += 1
                    
    return {"status": "ok", "exported_count": exported_count, "destination": str(dest_base)}

# Mount static files for Frontend
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
