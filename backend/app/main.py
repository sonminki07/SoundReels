import os
import sys
import json
import re
import time
import shutil
import threading
import subprocess
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
from dotenv import load_dotenv

# Ensure repo root is on sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from backend.app.core.db import (
    get_db_context,
    init_db,
    seed_project_and_slots,
    DB_PATH,
    AUDIO_CACHE_DIR,
    SCENE_CACHE_DIR,
    resolve_audio_path,
    resolve_scene_path,
)
from backend.app.services.loudness import compute_norm_gain

_scout_instance = None
_scout_lock = threading.Lock()
_scene_analyzer_instance = None
_scene_analyzer_lock = threading.Lock()

def get_scout():
    global _scout_instance
    with _scout_lock:
        if _scout_instance is None:
            from backend.app.services.sound_scout import SoundScout
            _scout_instance = SoundScout(device="cuda")
    return _scout_instance

def get_scene_analyzer():
    global _scene_analyzer_instance
    with _scene_analyzer_lock:
        if _scene_analyzer_instance is None:
            from backend.app.services.scene_analyzer import SceneAnalyzer
            _scene_analyzer_instance = SceneAnalyzer()
    return _scene_analyzer_instance

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
    """Resolves original Freesound author URL, LotsOfSounds catalog URL, and source_kind."""
    src = str(sound_dict.get("source") or "").strip().lower()
    sid = str(sound_dict.get("source_id") or sound_dict.get("id") or "")
    full_id = str(sound_dict.get("sound_id") or sound_dict.get("id") or "")
    raw_url = str(sound_dict.get("source_url") or "")

    m = FS_ID_REGEX.match(sid)
    if raw_url.startswith("local://") or (src == "procedural" and not m):
        return {
            "source_kind": "ai_generated",
            "original_source_url": None,
            "catalog_url": None,
        }

    is_mutated = src == "procedural" or full_id.endswith("_mutated")
    source_kind = "ai_mutated" if is_mutated else "external"
    if m:
        fs_num = m.group(1)
        return {
            "source_kind": source_kind,
            "original_source_url": f"https://freesound.org/s/{fs_num}/",
            "catalog_url": f"https://www.lotsofsounds.com/sounds/fs-{fs_num}",
        }
    if raw_url.startswith("https://"):
        return {
            "source_kind": source_kind,
            "original_source_url": raw_url,
            "catalog_url": raw_url,
        }
    return {
        "source_kind": "ai_generated",
        "original_source_url": None,
        "catalog_url": None,
    }

# Pydantic Request Models
class ActionRequest(BaseModel):
    slot_id: str
    sound_id: str

class ConfirmRequest(BaseModel):
    slot_id: str
    sound_id: str
    unity_project_path: Optional[str] = None

class UnconfirmRequest(BaseModel):
    slot_id: str
    sound_id: Optional[str] = None

class BatchExportRequest(BaseModel):
    unity_project_path: str
    passed_only: bool = False

class ReviewPassRequest(BaseModel):
    passed: bool = True

class SimilarRequest(BaseModel):
    slot_id: str
    reference_sound_id: str
    max_dur: Optional[float] = Field(default=None, ge=0.0)
    custom_prompt: Optional[str] = None

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
    playback_rate: float = Field(default=1.0, ge=0.25, le=4.0)
    preserve_pitch: int = Field(default=1, ge=0, le=1)
    reverse: int = Field(default=0, ge=0, le=1)

class WorkNoteRequest(BaseModel):
    work_memo: str = ""
    assignee: str = ""
    handoff_status: str = ""

class OpenExternalRequest(BaseModel):
    url: str

class ManualSearchRequest(BaseModel):
    query: str
    max_dur: Optional[float] = Field(default=None, ge=0.0)
    limit: int = Field(default=30, ge=1, le=60)

class ImportUrlRequest(BaseModel):
    url: str

class ActiveSlotRequest(BaseModel):
    slot_id: str

@app.on_event("startup")
def on_startup():
    init_db()
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM slots")
        if c.fetchone()[0] == 0:
            seed_project_and_slots()
    # Warm up CLAP model in background so first search/import responds immediately
    import threading
    threading.Thread(target=get_scout, daemon=True).start()

# --- Helper: Audio Processing on Export (Trim + Silence Trim + -1dBFS Peak Normalization) ---
def export_processed_audio_to_unity(
    src_path: Path,
    dest_wav_path: Path,
    audio_in: float = 0.0,
    audio_out: float = 0.0,
    playback_rate: float = 1.0,
    preserve_pitch: bool = True,
    reverse: bool = False,
) -> bool:
    try:
        y, sr = librosa.load(str(src_path), sr=48000, mono=True)
        if len(y) == 0:
            return False

        if reverse:
            y = y[::-1].copy()

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

        # 2. Apply playback speed modification (pitch-preserving time-stretch OR pitch-linked resample)
        rate = max(0.25, min(4.0, float(playback_rate or 1.0)))
        if abs(rate - 1.0) > 1e-3 and len(processed) >= 128:
            if preserve_pitch:
                n_fft = min(2048, max(128, 2 ** int(np.floor(np.log2(len(processed))))))
                processed = librosa.effects.time_stretch(processed, rate=rate, n_fft=n_fft)
            else:
                orig_sr = max(8000, int(round(sr * rate)))
                processed = librosa.resample(processed, orig_sr=orig_sr, target_sr=sr)

        # 3. Peak normalization to -1.0 dBFS (prevent digital clipping)
        peak = np.max(np.abs(processed))
        if peak > 0:
            target_peak = 10.0 ** (-1.0 / 20.0)  # ~0.891
            processed = processed * (target_peak / peak)

        # 4. Write uncompressed 24-bit PCM WAV for Unity
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
    media_path = resolve_scene_path(d.get("scene_media_path"))
    if media_path and media_path.exists():
        mtime = int(media_path.stat().st_mtime)
        d["scene_media_url"] = f"/api/slots/{d['id']}/scene/media?v={mtime}"
    else:
        d["scene_media_url"] = None
    d["scene_in"] = float(d.get("scene_in") or 0.0)
    d["scene_out"] = float(d.get("scene_out") or 0.0)
    d["sound_offset"] = float(d.get("sound_offset") or 0.0)
    d["review_passed"] = bool(d.get("review_passed") or 0)
    d["work_memo"] = str(d.get("work_memo") or "")
    d["assignee"] = str(d.get("assignee") or "")
    d["handoff_status"] = str(d.get("handoff_status") or "")
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
def get_slots(max_dur: Optional[float] = Query(default=None, ge=0.0)):
    with get_db_context() as conn:
        c = conn.cursor()
        if max_dur and max_dur > 0:
            c.execute("""
            SELECT s.*, 
                   COUNT(CASE WHEN sc.status != 'skipped' AND snd.duration <= ? THEN sc.id END) as candidate_count,
                   SUM(CASE WHEN sc.status = 'liked' AND snd.duration <= ? THEN 1 ELSE 0 END) as liked_count,
                   SUM(CASE WHEN sc.status = 'confirmed' THEN 1 ELSE 0 END) as confirmed_count,
                   SUM(CASE WHEN sc.status = 'skipped' THEN 1 ELSE 0 END) as trash_count
            FROM slots s
            LEFT JOIN slot_candidates sc ON s.id = sc.slot_id
            LEFT JOIN sounds snd ON sc.sound_id = snd.id
            GROUP BY s.id
            ORDER BY s.created_at ASC
            """, (max_dur, max_dur))
        else:
            c.execute("""
            SELECT s.*, 
                   COUNT(CASE WHEN sc.status != 'skipped' THEN sc.id END) as candidate_count,
                   SUM(CASE WHEN sc.status = 'liked' THEN 1 ELSE 0 END) as liked_count,
                   SUM(CASE WHEN sc.status = 'confirmed' THEN 1 ELSE 0 END) as confirmed_count,
                   SUM(CASE WHEN sc.status = 'skipped' THEN 1 ELSE 0 END) as trash_count
            FROM slots s
            LEFT JOIN slot_candidates sc ON s.id = sc.slot_id
            GROUP BY s.id
            ORDER BY s.created_at ASC
            """)
        slots = [_enrich_slot_dict(dict(r)) for r in c.fetchall()]

        # Attach active confirmed sound summary per slot for Review Checklist & Receipt Preview
        c.execute("""
        SELECT sc.slot_id, sc.sound_id,
               COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
               COALESCE(sc.playback_rate, 1.0) as playback_rate, COALESCE(sc.preserve_pitch, 1) as preserve_pitch,
               COALESCE(sc.reverse, 0) as reverse,
               snd.name, snd.duration, snd.license_type, snd.author, snd.source, snd.source_url, snd.source_id,
               COALESCE(snd.norm_gain, 1.0) as norm_gain
        FROM slot_candidates sc
        JOIN sounds snd ON sc.sound_id = snd.id
        WHERE sc.status = 'confirmed'
        ORDER BY sc.rank ASC
        """)
        confirmed_map: Dict[str, Dict[str, Any]] = {}
        for cr in c.fetchall():
            item = dict(cr)
            sid_slot = item["slot_id"]
            if sid_slot not in confirmed_map:
                urls = resolve_source_urls(item)
                item["source_kind"] = urls["source_kind"]
                item["original_source_url"] = urls["original_source_url"]
                item["catalog_url"] = urls["catalog_url"]
                confirmed_map[sid_slot] = item

        for sl in slots:
            sl["confirmed_sound"] = confirmed_map.get(sl["id"])
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
def get_slot_candidates(slot_id: str, max_dur: Optional[float] = Query(default=None, ge=0.0)):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        if max_dur and max_dur > 0:
            c.execute("""
            SELECT sc.id as candidate_id, sc.slot_id, sc.sound_id, sc.score, sc.rank, sc.status as candidate_status,
                   COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
                   COALESCE(sc.playback_rate, 1.0) as playback_rate, COALESCE(sc.preserve_pitch, 1) as preserve_pitch,
                   COALESCE(sc.reverse, 0) as reverse,
                   snd.name, snd.file_path, snd.duration, snd.license_type, snd.author, snd.source, snd.source_url, snd.source_id, snd.tags, snd.sha256,
                   COALESCE(snd.norm_gain, 1.0) as norm_gain
            FROM slot_candidates sc
            JOIN sounds snd ON sc.sound_id = snd.id
            WHERE sc.slot_id = ? AND sc.status != 'skipped' AND snd.duration <= ?
            ORDER BY sc.rank ASC
            """, (slot_id, max_dur))
        else:
            c.execute("""
            SELECT sc.id as candidate_id, sc.slot_id, sc.sound_id, sc.score, sc.rank, sc.status as candidate_status,
                   COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
                   COALESCE(sc.playback_rate, 1.0) as playback_rate, COALESCE(sc.preserve_pitch, 1) as preserve_pitch,
                   COALESCE(sc.reverse, 0) as reverse,
                   snd.name, snd.file_path, snd.duration, snd.license_type, snd.author, snd.source, snd.source_url, snd.source_id, snd.tags, snd.sha256,
                   COALESCE(snd.norm_gain, 1.0) as norm_gain
            FROM slot_candidates sc
            JOIN sounds snd ON sc.sound_id = snd.id
            WHERE sc.slot_id = ? AND sc.status != 'skipped'
            ORDER BY sc.rank ASC
            """, (slot_id,))
        candidates = []
        for r in c.fetchall():
            item = dict(r)
            item["tags"] = json.loads(item.get("tags") or "[]")
            item["audio_url"] = f"/api/sounds/{item['sound_id']}/audio"
            urls = resolve_source_urls(item)
            item["source_kind"] = urls["source_kind"]
            item["original_source_url"] = urls["original_source_url"]
            item["catalog_url"] = urls["catalog_url"]
            candidates.append(item)
    return {"slot_id": slot_id, "candidates": candidates}

class RescoutRequest(BaseModel):
    max_dur: Optional[float] = Field(default=None, ge=0.0)
    include_freesound: bool = False
    target_count: int = Field(default=24, ge=8, le=60)

class SceneScoutRequest(BaseModel):
    scene_in: Optional[float] = Field(default=None, ge=0.0)
    scene_out: Optional[float] = Field(default=None, ge=0.0)
    max_dur: Optional[float] = Field(default=None, ge=0.0)
    use_scene_duration: bool = True

@app.post("/api/slots/{slot_id}/rescout")
def rescout_slot_with_duration(slot_id: str, req: RescoutRequest):
    """Re-searches LotsOfSounds (+ optional Freesound CC0 bulk) and scores with CLAP."""
    validate_id(slot_id, "slot_id")
    scout = get_scout()
    max_d = req.max_dur if (req.max_dur and req.max_dur > 0) else None
    found_count = scout.scout_slot(
        slot_id,
        max_candidates_per_query=12,
        max_duration=max_d,
        allow_on_hold=True,
        prioritize_by_score=True,
        include_freesound=bool(req.include_freesound),
        max_total_insert=req.target_count,
    )
    return {"status": "ok", "slot_id": slot_id, "found_count": found_count}

@app.post("/api/slots/{slot_id}/scene_scout")
def scout_slot_from_scene(slot_id: str, req: SceneScoutRequest):
    """Analyzes the uploaded scene video/GIF (keyframes, motion, duration) and scouts tailored SFX."""
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Slot not found")
        slot = dict(row)

        c.execute("SELECT value FROM app_settings WHERE key = 'gemini_api_key'")
        key_row = c.fetchone()
        stored_gemini_key = key_row["value"].strip() if key_row and key_row["value"] else None

    if not slot.get("scene_media_path"):
        raise HTTPException(status_code=400, detail="No scene video or GIF attached to this slot")

    media_path = resolve_scene_path(slot.get("scene_media_path"))
    if not media_path or not media_path.resolve().is_relative_to(SCENE_CACHE_DIR.resolve()) or not media_path.exists():
        raise HTTPException(status_code=404, detail="Attached scene media file not found")
    media_path = media_path.resolve()

    s_in = req.scene_in if req.scene_in is not None else float(slot.get("scene_in") or 0.0)
    s_out = req.scene_out if req.scene_out is not None else float(slot.get("scene_out") or 0.0)

    analyzer = get_scene_analyzer()
    analysis = analyzer.analyze_and_merge(
        media_path=media_path,
        media_type=slot.get("scene_media_type") or "video",
        slot=slot,
        scene_in=s_in,
        scene_out=s_out,
        gemini_api_key=stored_gemini_key,
    )

    if req.max_dur and req.max_dur > 0:
        effective_max_dur: Optional[float] = float(req.max_dur)
    elif req.use_scene_duration:
        effective_max_dur = float(analysis["recommended_max_dur"])
    else:
        effective_max_dur = None

    scout = get_scout()
    found_count = scout.scout_slot(
        slot_id=slot_id,
        max_candidates_per_query=12,
        max_duration=effective_max_dur,
        allow_on_hold=True,
        custom_search_tags=analysis["merged_tags"],
        custom_clap_prompts=analysis["merged_prompts"],
        prioritize_by_score=True,
    )

    return {
        "status": "ok",
        "slot_id": slot_id,
        "found_count": found_count,
        "engine": analysis["engine"],
        "summary_ko": analysis["summary_ko"],
        "visual_tags": analysis["visual_tags"],
        "merged_tags": analysis["merged_tags"],
        "effective_duration": analysis["effective_duration"],
        "motion_profile": analysis["motion_profile"],
        "recommended_max_dur": analysis["recommended_max_dur"],
        "applied_max_dur": effective_max_dur,
    }

@app.get("/api/sounds/{sound_id}/audio")
def get_sound_audio(sound_id: str, reverse: int = Query(default=0, ge=0, le=1)):
    validate_id(sound_id, "sound_id")
    local_path = (AUDIO_CACHE_DIR / f"{sound_id}.mp3").resolve()

    if not local_path.is_relative_to(AUDIO_CACHE_DIR.resolve()):
        raise HTTPException(status_code=403, detail="Forbidden path")

    if not local_path.exists():
        row_dict = None
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, source, source_id, source_url, file_path FROM sounds WHERE id = ?", (sound_id,))
            row = c.fetchone()
            if row:
                row_dict = dict(row)
                cand_path = resolve_audio_path(sound_id, row_dict.get("file_path")).resolve()
                if cand_path.exists() and cand_path.is_relative_to(AUDIO_CACHE_DIR.resolve()):
                    local_path = cand_path

        # Auto-redownload external audio if restored from trash on a fresh PC
        if not local_path.exists() and row_dict and not sound_id.endswith("_mutated"):
            try:
                scout = get_scout()
                src = str(row_dict.get("source") or "")
                src_url = str(row_dict.get("source_url") or "")
                if src == "lotsofsounds" or "lotsofsounds.com" in src_url:
                    scout.download_sound({
                        "id": sound_id,
                        "name": row_dict.get("name") or sound_id,
                        "stream_endpoint": f"/api/v1/sounds/sample/{sound_id}/stream",
                        "duration": 0.0,
                        "tags": [],
                    })
                elif src_url.startswith("https://"):
                    items = scout.fetch_sounds_from_url(src_url, max_items=1)
                    if items and "_already_downloaded" not in items[0]:
                        scout.download_direct_audio(items[0])
            except Exception as dl_exc:
                print(f"[Audio Auto-Heal] Failed for {sound_id}: {dl_exc}")

        if not local_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found in cache")

    if reverse == 1:
        rev_path = (AUDIO_CACHE_DIR / f"{sound_id}_rev.wav").resolve()
        if not rev_path.is_relative_to(AUDIO_CACHE_DIR.resolve()):
            raise HTTPException(status_code=403, detail="Forbidden path")
        if not rev_path.exists() or rev_path.stat().st_mtime < local_path.stat().st_mtime:
            tmp_rev = rev_path.with_suffix(".tmp.wav")
            try:
                y, sr = librosa.load(str(local_path), sr=48000, mono=True)
                sf.write(str(tmp_rev), y[::-1].copy(), sr, subtype="PCM_16")
                tmp_rev.replace(rev_path)
            except Exception as rev_exc:
                tmp_rev.unlink(missing_ok=True)
                raise HTTPException(status_code=500, detail=f"Reverse audio generation failed: {rev_exc}")
        return FileResponse(str(rev_path), media_type="audio/wav", headers={"Accept-Ranges": "bytes"})

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
        media_path = resolve_scene_path(row["scene_media_path"])

    if not media_path or not media_path.resolve().is_relative_to(SCENE_CACHE_DIR.resolve()) or not media_path.exists():
        raise HTTPException(status_code=404, detail="Scene media file not found")
    media_path = media_path.resolve()

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
            media_path = resolve_scene_path(row["scene_media_path"])
            if media_path and media_path.resolve().is_relative_to(SCENE_CACHE_DIR.resolve()) and media_path.exists():
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

@app.post("/api/slots/{slot_id}/work_note")
def update_slot_work_note(slot_id: str, req: WorkNoteRequest):
    validate_id(slot_id, "slot_id")
    allowed_statuses = {"", "todo", "in_progress", "needs_review", "ready"}
    clean_status = (req.handoff_status or "").strip()
    if clean_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Invalid handoff_status")
    clean_memo = (req.work_memo or "").strip()
    clean_assignee = (req.assignee or "").strip()
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        UPDATE slots
        SET work_memo = ?, assignee = ?, handoff_status = ?
        WHERE id = ?
        """, (clean_memo, clean_assignee, clean_status, slot_id))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Slot not found")
        conn.commit()
    return {
        "status": "ok",
        "slot_id": slot_id,
        "work_memo": clean_memo,
        "assignee": clean_assignee,
        "handoff_status": clean_status,
    }

@app.post("/api/actions/trim_audio")
def trim_candidate_audio(req: AudioTrimRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")
    if req.audio_out > 0.0 and req.audio_out <= req.audio_in:
        raise HTTPException(status_code=400, detail="audio_out must be greater than audio_in")

    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        UPDATE slot_candidates
        SET audio_in = ?, audio_out = ?, playback_rate = ?, preserve_pitch = ?, reverse = ?
        WHERE slot_id = ? AND sound_id = ?
        """, (req.audio_in, req.audio_out, req.playback_rate, req.preserve_pitch, req.reverse, req.slot_id, req.sound_id))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Candidate not found")
        conn.commit()
    return {
        "status": "ok",
        "audio_in": req.audio_in,
        "audio_out": req.audio_out,
        "playback_rate": req.playback_rate,
        "preserve_pitch": req.preserve_pitch,
        "reverse": req.reverse,
    }

ALLOWED_SETTING_KEYS = {
    "master_volume",
    "left_width",
    "right_width",
    "footer_height",
    "unity_path",
    "norm_enabled",
    "max_search_dur",
    "gemini_api_key",
    "keymap_settings",
}

class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]

@app.get("/api/settings")
def get_settings():
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT key, value FROM app_settings")
        raw = {r["key"]: r["value"] for r in c.fetchall()}
    has_db_key = bool((raw.pop("gemini_api_key", "") or "").strip())
    env_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    has_env_key = bool(env_key and env_key != "your_gemini_api_key_here")
    raw["has_gemini_key"] = "true" if (has_db_key or has_env_key) else "false"
    return {"settings": raw}

@app.post("/api/settings")
def update_settings(req: SettingsUpdateRequest):
    with get_db_context() as conn:
        c = conn.cursor()
        for k, v in req.settings.items():
            if k in ALLOWED_SETTING_KEYS:
                if k == "gemini_api_key" and not str(v).strip():
                    c.execute("DELETE FROM app_settings WHERE key = 'gemini_api_key'")
                else:
                    c.execute("""
                    INSERT INTO app_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value;
                    """, (k, str(v).strip() if k == "gemini_api_key" else str(v)))
        conn.commit()
    return {"status": "ok"}

@app.get("/api/system/check_update")
def check_system_update():
    """Checks GitHub origin/main for newer commits without modifying local files."""
    try:
        fetch_res = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if fetch_res.returncode != 0:
            return {"status": "error", "has_update": False, "message": fetch_res.stderr.strip() or "git fetch failed"}

        local_rev = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
        remote_rev = subprocess.check_output(["git", "rev-parse", "--short", "origin/main"], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
        behind_str = subprocess.check_output(["git", "rev-list", "--count", "HEAD..origin/main"], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
        behind_count = int(behind_str or "0")

        changes = []
        if behind_count > 0:
            log_out = subprocess.check_output(["git", "log", "-n", "5", "--oneline", "HEAD..origin/main"], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
            changes = [line for line in log_out.splitlines() if line.strip()]

        return {
            "status": "ok",
            "has_update": behind_count > 0,
            "behind_count": behind_count,
            "local_rev": local_rev,
            "remote_rev": remote_rev,
            "changes": changes,
        }
    except Exception as e:
        return {"status": "error", "has_update": False, "message": str(e)}

@app.post("/api/system/apply_update")
def apply_system_update():
    """Pulls the latest fast-forward commits from origin/main."""
    try:
        before_rev = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if pull_res.returncode != 0:
            raise HTTPException(status_code=409, detail=f"업데이트 충돌 또는 실패: {pull_res.stderr.strip()}")

        after_rev = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
        changed_files = []
        if before_rev != after_rev:
            diff_out = subprocess.check_output(["git", "diff", "--name-only", before_rev, after_rev], cwd=str(ROOT_DIR), text=True, timeout=5).strip()
            changed_files = [f for f in diff_out.splitlines() if f.strip()]

        needs_restart = any(f.endswith(".py") for f in changed_files)
        return {
            "status": "ok",
            "updated": before_rev != after_rev,
            "local_rev": after_rev[:7],
            "changed_files": changed_files,
            "needs_restart": needs_restart,
            "output": pull_res.stdout.strip(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"업데이트 실행 오류: {e}")

@app.post("/api/open_external_url")
def open_external_url(req: OpenExternalRequest):
    """Safely opens whitelisted external source URLs in the user's default browser."""
    parsed = urllib.parse.urlparse(req.url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_EXTERNAL_HOSTS:
        raise HTTPException(status_code=400, detail="URL host is not in the allowed whitelist")
    try:
        if os.name == "nt":
            subprocess.Popen(
                ["cmd", "/c", "start", "", req.url],
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            webbrowser.open(req.url, new=2)
    except Exception:
        try:
            if os.name == "nt":
                os.startfile(req.url)
            else:
                webbrowser.open(req.url, new=2)
        except Exception:
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
        c.execute(
            "UPDATE slot_candidates SET status = 'skipped' WHERE slot_id = ? AND sound_id = ?",
            (req.slot_id, req.sound_id),
        )
        c.execute(
            "DELETE FROM confirmed_exports WHERE slot_id = ? AND sound_id = ?",
            (req.slot_id, req.sound_id),
        )
        c.execute(
            "SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status = 'confirmed'",
            (req.slot_id,),
        )
        if c.fetchone()["cnt"] == 0:
            c.execute(
                "SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status != 'skipped'",
                (req.slot_id,),
            )
            has_cands = c.fetchone()["cnt"] > 0
            c.execute(
                "UPDATE slots SET status = ?, review_passed = 0 WHERE id = ? AND status = 'confirmed'",
                ("has_candidates" if has_cands else "pending", req.slot_id),
            )
        conn.commit()
    return {"status": "ok", "action": "skipped", "sound_id": req.sound_id}

@app.post("/api/slots/{slot_id}/candidates/prune_unliked")
def prune_unliked_candidates(slot_id: str):
    """Deletes (skips) all pending candidates in a slot while preserving liked & confirmed sounds."""
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE slot_candidates SET status = 'skipped' WHERE slot_id = ? AND status = 'pending'",
            (slot_id,),
        )
        removed_count = c.rowcount
        c.execute(
            "SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status != 'skipped'",
            (slot_id,),
        )
        remaining_count = c.fetchone()["cnt"]
        if remaining_count == 0:
            c.execute("UPDATE slots SET status = 'pending', review_passed = 0 WHERE id = ?", (slot_id,))
        conn.commit()
    return {"status": "ok", "slot_id": slot_id, "removed_count": removed_count, "remaining_count": remaining_count}

@app.post("/api/actions/confirm")
def confirm_candidate(req: ConfirmRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")

    with get_db_context() as conn:
        c = conn.cursor()

        c.execute("""
        SELECT COALESCE(audio_in, 0.0) as audio_in, COALESCE(audio_out, 0.0) as audio_out,
               COALESCE(playback_rate, 1.0) as playback_rate, COALESCE(preserve_pitch, 1) as preserve_pitch,
               COALESCE(reverse, 0) as reverse
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
                    playback_rate=float(cand_row["playback_rate"]),
                    preserve_pitch=bool(cand_row["preserve_pitch"]),
                    reverse=bool(cand_row["reverse"]),
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

@app.post("/api/actions/unconfirm")
def unconfirm_candidate(req: UnconfirmRequest):
    """Cancels confirmation for a sound/slot and reverts slot status when no confirmed sounds remain."""
    validate_id(req.slot_id, "slot_id")
    if req.sound_id is not None:
        validate_id(req.sound_id, "sound_id")

    with get_db_context() as conn:
        c = conn.cursor()
        if req.sound_id:
            c.execute(
                "UPDATE slot_candidates SET status = 'pending' WHERE slot_id = ? AND sound_id = ? AND status = 'confirmed'",
                (req.slot_id, req.sound_id),
            )
            c.execute(
                "DELETE FROM confirmed_exports WHERE slot_id = ? AND sound_id = ?",
                (req.slot_id, req.sound_id),
            )
        else:
            c.execute(
                "UPDATE slot_candidates SET status = 'pending' WHERE slot_id = ? AND status = 'confirmed'",
                (req.slot_id,),
            )
            c.execute("DELETE FROM confirmed_exports WHERE slot_id = ?", (req.slot_id,))

        c.execute(
            "SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status = 'confirmed'",
            (req.slot_id,),
        )
        remaining_confirmed = c.fetchone()["cnt"]

        if remaining_confirmed == 0:
            c.execute(
                "SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status != 'skipped'",
                (req.slot_id,),
            )
            has_cands = c.fetchone()["cnt"] > 0
            new_slot_status = "has_candidates" if has_cands else "pending"
            c.execute("UPDATE slots SET status = ?, review_passed = 0 WHERE id = ?", (new_slot_status, req.slot_id))
        else:
            new_slot_status = "confirmed"

        conn.commit()
    return {"status": "ok", "slot_id": req.slot_id, "new_slot_status": new_slot_status}

@app.post("/api/slots/{slot_id}/review_pass")
def set_slot_review_pass(slot_id: str, req: ReviewPassRequest):
    """Marks a confirmed slot as passed (ready for export) or sends it back to pending review tasks."""
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT id, status FROM slots WHERE id = ?", (slot_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Slot not found")
        if req.passed:
            c.execute(
                "SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status = 'confirmed'",
                (slot_id,),
            )
            if c.fetchone()["cnt"] == 0:
                raise HTTPException(status_code=400, detail="먼저 이 슬롯의 사운드를 '최종 채택(확정)'해 주세요.")
        c.execute("UPDATE slots SET review_passed = ? WHERE id = ?", (1 if req.passed else 0, slot_id))
        conn.commit()
    return {"status": "ok", "slot_id": slot_id, "review_passed": bool(req.passed)}

@app.post("/api/actions/reorder_similar")
def reorder_similar(req: SimilarRequest):
    """Finds new similar sounds (rotated tags + optional user prompt) & ranks via CLAP similarity."""
    validate_id(req.slot_id, "slot_id")
    validate_id(req.reference_sound_id, "reference_sound_id")
    max_d = req.max_dur if (req.max_dur and req.max_dur > 0) else None
    scout = get_scout()
    result = scout.find_similar_to_sound(
        slot_id=req.slot_id,
        reference_sound_id=req.reference_sound_id,
        max_duration=max_d,
        custom_prompt=req.custom_prompt,
    )
    return {
        "status": "ok",
        "reference_sound_id": req.reference_sound_id,
        "round_no": result.get("round_no", 1),
        "found_new": result["found_new"],
        "filtered_out": result.get("filtered_out", 0),
        "total_ranked": result["total_ranked"],
        "top_similar_index": result["top_similar_index"],
        "queries_used": result["queries_used"],
        "prompt_used": result.get("prompt_used", ""),
        "message": f"유사 탐색 #{result.get('round_no', 1)}회차 완료 (+{result['found_new']}개 추가, 총 {result['total_ranked']}개)",
    }

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
        mutated_norm_gain = compute_norm_gain(dest_file)

        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM sounds WHERE id = ?", (req.sound_id,))
            orig_row = c.fetchone()
            if not orig_row:
                raise HTTPException(status_code=404, detail="Original sound metadata not found")
            orig = dict(orig_row)

            c.execute("""
            INSERT INTO sounds (id, source, source_id, name, file_path, duration, license_type, author, source_url, tags, sha256, norm_gain)
            VALUES (?, 'procedural', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET file_path=excluded.file_path, norm_gain=excluded.norm_gain;
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
                "mutated_hash",
                mutated_norm_gain
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

def _format_receipt_entries(rows: List[Dict[str, Any]], passed_only: bool = False) -> Dict[str, Any]:
    """Builds structured receipt entries and formatted Korean receipt text."""
    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entries = []
    lines = [
        "================================================================================",
        "                     🎧 SoundReels 최종 확정 · 내보내기 영수증                    ",
        "================================================================================",
        f"발급 일시 : {now_str}",
        f"내보내기 범위 : {'총점검 패스 완료 항목만' if passed_only else '전체 확정 완료 항목'} ({len(rows)}건)",
        "--------------------------------------------------------------------------------",
    ]

    for idx, item in enumerate(rows, start=1):
        urls = resolve_source_urls(item)
        kind = urls["source_kind"]
        src_url = urls["original_source_url"] or item.get("source_url") or ""
        if kind == "ai_generated":
            source_desc = "🤖 AI / 로컬 자체 생성 (외부 출처 및 크레딧 표기 불필요)"
        elif kind == "ai_mutated":
            source_desc = f"🎲 AI 로컬 변형 (원곡 출처: {src_url})"
        else:
            source_desc = f"🌐 {src_url} ({item.get('license_type', 'CC0')} · {item.get('author', 'Unknown')})"

        a_in = float(item.get("audio_in") or 0.0)
        a_out = float(item.get("audio_out") or 0.0)
        raw_dur = float(item.get("duration") or 0.0)
        rate = float(item.get("playback_rate") or 1.0)
        p_pitch = bool(item.get("preserve_pitch", 1))
        is_rev = bool(item.get("reverse", 0))
        has_trim = (a_in > 0.0) or (a_out > 0.0)
        trim_desc = f"{a_in:.2f}s ~ {(a_out if a_out > a_in else raw_dur):.2f}s" if has_trim else f"전체 자동 무음제거 (원본 {raw_dur:.2f}s)"
        clip_len = (max(0.05, (a_out if a_out > a_in else raw_dur) - a_in) if has_trim else raw_dur)
        eff_len = clip_len / max(0.25, rate)
        speed_desc = f"{rate:.2f}x ({'피치유지' if p_pitch else '피치연동'})" if abs(rate - 1.0) >= 0.01 else "1.00x (원속도)"
        if is_rev:
            speed_desc = f"🔁 역재생 · {speed_desc}"
        rel_wav = f"Assets/Audio/SFX/{item['slot_id']}/{item['slot_id']}_01.wav"

        entry = {
            "no": idx,
            "slot_id": item["slot_id"],
            "slot_name": item.get("slot_name") or item["slot_id"],
            "slot_memo": item.get("slot_memo") or "",
            "work_memo": item.get("work_memo") or "",
            "assignee": item.get("assignee") or "",
            "handoff_status": item.get("handoff_status") or "",
            "output_wav": rel_wav,
            "sound_id": item["sound_id"],
            "sound_name": item["name"],
            "source_kind": kind,
            "source_url": src_url,
            "license_type": item.get("license_type", "CC0"),
            "author": item.get("author", "Unknown"),
            "trim": trim_desc,
            "playback_rate": round(rate, 2),
            "preserve_pitch": p_pitch,
            "reverse": is_rev,
            "effective_duration": round(eff_len, 2),
        }
        entries.append(entry)
        handoff_note = f" | 인수인계({entry['assignee'] or '공용'}): {entry['work_memo']}" if (entry["work_memo"] or entry["assignee"]) else ""
        lines.extend([
            f"[{idx:02d}] 용도/슬롯 : {entry['slot_name']} ({entry['slot_id']})",
            f"     메모      : {entry['slot_memo']}{handoff_note}",
            f"     출력 파일 : {rel_wav}",
            f"     원본 음원 : {entry['sound_name']}",
            f"     출처 정보 : {source_desc}",
            f"     편집/배속 : 자르기 [{trim_desc}] · 배속 [{speed_desc}] · 예상 길이 [{eff_len:.2f}s] · -1.0dBFS WAV",
            "--------------------------------------------------------------------------------",
        ])

    if not entries:
        lines.append("(내보낼 확정 또는 패스 완료 항목이 없습니다.)")
        lines.append("--------------------------------------------------------------------------------")
    lines.append(f"총 {len(entries)}개 사운드 검증 및 패키징 완료 · Generated by SoundReels")
    lines.append("================================================================================")
    return {"receipt_text": "\n".join(lines), "entries": entries, "generated_at": now_str}


@app.get("/api/export/receipt_preview")
def preview_export_receipt(passed_only: bool = Query(default=False)):
    """Returns live receipt preview of confirmed (or review-passed) sounds before export."""
    with get_db_context() as conn:
        c = conn.cursor()
        extra_where = "AND COALESCE(s.review_passed, 0) = 1" if passed_only else ""
        c.execute(f"""
        SELECT sc.slot_id, sc.sound_id,
               COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
               COALESCE(sc.playback_rate, 1.0) as playback_rate, COALESCE(sc.preserve_pitch, 1) as preserve_pitch,
               COALESCE(sc.reverse, 0) as reverse,
               s.name as slot_name, s.memo as slot_memo,
               COALESCE(s.work_memo, '') as work_memo, COALESCE(s.assignee, '') as assignee, COALESCE(s.handoff_status, '') as handoff_status,
               snd.name, snd.duration, snd.author, snd.source, snd.source_url, snd.source_id, snd.license_type
        FROM slot_candidates sc
        JOIN slots s ON sc.slot_id = s.id
        JOIN sounds snd ON sc.sound_id = snd.id
        WHERE sc.status = 'confirmed' {extra_where}
        ORDER BY s.created_at ASC
        """)
        rows = [dict(r) for r in c.fetchall()]
    return _format_receipt_entries(rows, passed_only=passed_only)


@app.post("/api/export/batch_unity")
def batch_export_to_unity(req: BatchExportRequest):
    target_root = Path(req.unity_project_path).resolve()
    if not target_root.exists():
        raise HTTPException(status_code=400, detail="Target Unity directory does not exist")

    dest_base = target_root / "Assets" / "Audio" / "SFX"
    dest_base.mkdir(parents=True, exist_ok=True)

    exported_rows: List[Dict[str, Any]] = []
    with get_db_context() as conn:
        c = conn.cursor()
        extra_where = "AND COALESCE(s.review_passed, 0) = 1" if req.passed_only else ""
        c.execute(f"""
        SELECT sc.slot_id, sc.sound_id,
               COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
               COALESCE(sc.playback_rate, 1.0) as playback_rate, COALESCE(sc.preserve_pitch, 1) as preserve_pitch,
               COALESCE(sc.reverse, 0) as reverse,
               s.name as slot_name, s.memo as slot_memo,
               COALESCE(s.work_memo, '') as work_memo, COALESCE(s.assignee, '') as assignee, COALESCE(s.handoff_status, '') as handoff_status,
               snd.name, snd.duration, snd.author, snd.source, snd.source_url, snd.source_id, snd.license_type
        FROM slot_candidates sc
        JOIN slots s ON sc.slot_id = s.id
        JOIN sounds snd ON sc.sound_id = snd.id
        WHERE sc.status = 'confirmed' {extra_where}
        ORDER BY s.created_at ASC
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
                        playback_rate=float(item["playback_rate"]),
                        preserve_pitch=bool(item["preserve_pitch"]),
                        reverse=bool(item.get("reverse", 0)),
                    )
                    urls = resolve_source_urls(item)
                    credit_url = urls["original_source_url"] or item["source_url"] or "local://ai-generated"
                    f.write(f'[{item["slot_name"]}] "{item["name"]}" ({credit_url}) by {item["author"]} - License: {item["license_type"]}\n')
                    exported_rows.append(item)

    receipt_bundle = _format_receipt_entries(exported_rows, passed_only=req.passed_only)
    receipt_txt_path = target_root / "SOUNDREELS_RECEIPT.txt"
    receipt_json_path = target_root / "SOUNDREELS_RECEIPT.json"
    receipt_txt_path.write_text(receipt_bundle["receipt_text"], encoding="utf-8")
    receipt_json_path.write_text(
        json.dumps(
            {
                "generated_at": receipt_bundle["generated_at"],
                "passed_only": req.passed_only,
                "exported_count": len(exported_rows),
                "items": receipt_bundle["entries"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "exported_count": len(exported_rows),
        "destination": str(dest_base),
        "receipt_path": str(receipt_txt_path),
        "receipt_json_path": str(receipt_json_path),
        "receipt_text": receipt_bundle["receipt_text"],
    }

# --- Trash / Restore, Manual Search, URL Import & Desktop Popup Bridge (v2.2) ---
_desktop_popup_state: Dict[str, Any] = {
    "active_slot_id": None,
    "import_seq": 0,
    "last_imported": None,
}

@app.get("/api/slots/{slot_id}/trash")
def get_slot_trash(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        SELECT sc.id as candidate_id, sc.slot_id, sc.sound_id, sc.score, sc.rank, sc.status as candidate_status,
               COALESCE(sc.audio_in, 0.0) as audio_in, COALESCE(sc.audio_out, 0.0) as audio_out,
               COALESCE(sc.playback_rate, 1.0) as playback_rate, COALESCE(sc.preserve_pitch, 1) as preserve_pitch,
               COALESCE(sc.reverse, 0) as reverse,
               snd.name, snd.file_path, snd.duration, snd.license_type, snd.author, snd.source, snd.source_url, snd.source_id, snd.tags, snd.sha256,
               COALESCE(snd.norm_gain, 1.0) as norm_gain
        FROM slot_candidates sc
        JOIN sounds snd ON sc.sound_id = snd.id
        WHERE sc.slot_id = ? AND sc.status = 'skipped'
        ORDER BY sc.id DESC
        """, (slot_id,))
        trash_items = []
        for r in c.fetchall():
            item = dict(r)
            item["tags"] = json.loads(item.get("tags") or "[]")
            item["audio_url"] = f"/api/sounds/{item['sound_id']}/audio"
            urls = resolve_source_urls(item)
            item["source_kind"] = urls["source_kind"]
            item["original_source_url"] = urls["original_source_url"]
            item["catalog_url"] = urls["catalog_url"]
            trash_items.append(item)
    return {"slot_id": slot_id, "trash": trash_items}

@app.post("/api/actions/restore")
def restore_candidate(req: ActionRequest):
    validate_id(req.slot_id, "slot_id")
    validate_id(req.sound_id, "sound_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("SELECT COALESCE(MAX(rank), 0) + 1 FROM slot_candidates WHERE slot_id = ? AND status != 'skipped'", (req.slot_id,))
        next_rank = c.fetchone()[0]
        c.execute("""
        UPDATE slot_candidates
        SET status = 'pending', rank = ?
        WHERE slot_id = ? AND sound_id = ? AND status = 'skipped'
        """, (next_rank, req.slot_id, req.sound_id))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Deleted candidate not found in trash")
        c.execute("UPDATE slots SET status = 'has_candidates' WHERE id = ? AND status = 'pending'", (req.slot_id,))
        conn.commit()
    return {"status": "ok", "action": "restored", "sound_id": req.sound_id}

@app.post("/api/slots/{slot_id}/trash/restore_all")
def restore_all_trash(slot_id: str):
    validate_id(slot_id, "slot_id")
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("UPDATE slot_candidates SET status = 'pending' WHERE slot_id = ? AND status = 'skipped'", (slot_id,))
        restored_count = c.rowcount
        if restored_count > 0:
            c.execute("UPDATE slots SET status = 'has_candidates' WHERE id = ? AND status = 'pending'", (slot_id,))
        conn.commit()
    return {"status": "ok", "slot_id": slot_id, "restored_count": restored_count}

@app.post("/api/slots/{slot_id}/manual_search")
def manual_search_sounds(slot_id: str, req: ManualSearchRequest):
    validate_id(slot_id, "slot_id")
    q = (req.query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="검색어를 입력하세요")
    scout = get_scout()
    max_d = req.max_dur if (req.max_dur and req.max_dur > 0) else None
    result = scout.manual_search_into_slot(slot_id, q, max_duration=max_d, limit=req.limit)
    return {"status": "ok", "slot_id": slot_id, "query": q, **result}

@app.post("/api/slots/{slot_id}/import_url")
def import_url_sounds(slot_id: str, req: ImportUrlRequest):
    validate_id(slot_id, "slot_id")
    scout = get_scout()
    try:
        result = scout.import_url_into_slot(slot_id, req.url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"URL 가져오기 실패: {exc}")
    return {"status": "ok", "slot_id": slot_id, "url": req.url, **result}

@app.post("/api/system/active_slot")
def set_active_slot(req: ActiveSlotRequest):
    validate_id(req.slot_id, "slot_id")
    _desktop_popup_state["active_slot_id"] = req.slot_id
    return {"status": "ok", "active_slot_id": req.slot_id}

@app.get("/api/desktop/popup_state")
def get_popup_state():
    return dict(_desktop_popup_state)

@app.post("/api/desktop/popup_import")
def popup_import_url(req: ImportUrlRequest):
    target_slot_id = _desktop_popup_state.get("active_slot_id")
    if not target_slot_id:
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM slots ORDER BY created_at ASC LIMIT 1")
            row = c.fetchone()
            target_slot_id = row["id"] if row else None
    if not target_slot_id:
        raise HTTPException(status_code=400, detail="활성 슬롯이 없습니다.")
    scout = get_scout()
    try:
        result = scout.import_url_into_slot(target_slot_id, req.url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"팝업 URL 가져오기 실패: {exc}")

    _desktop_popup_state["import_seq"] += 1
    _desktop_popup_state["last_imported"] = {
        "slot_id": target_slot_id,
        "url": req.url,
        **result,
    }
    return {"status": "ok", "seq": _desktop_popup_state["import_seq"], "slot_id": target_slot_id, **result}

@app.post("/api/system/cleanup_quality")
def cleanup_quality_candidates():
    """Moves existing unliked/unconfirmed bad, oversized, speech, or tone/noise rows to trash."""
    scout = get_scout()
    summary = scout.cleanup_existing_bad_candidates()
    return {"status": "ok", **summary}

# Mount static files for Frontend
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
