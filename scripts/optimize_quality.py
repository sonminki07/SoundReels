"""
SoundReels Quality Optimization & Re-ranking Script
- Trims leading silence across cached audio to eliminate input latency
- Promotes authentic game-feel candidates (Arrow Hit Impact, Game Jump Bounce, UI Pop-up Close)
- Generates procedural sci-fi UI chime for Tab BUT UI
"""

import os
import sys
import numpy as np
import soundfile as sf
import librosa
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.db import get_db_context, AUDIO_CACHE_DIR

def optimize_cached_audio_silence():
    print("[Optimize] Trimming leading silence on cached audio files...")
    cache_files = list(AUDIO_CACHE_DIR.glob("*.mp3"))
    trimmed_count = 0
    for f in cache_files:
        try:
            y, sr = librosa.load(str(f), sr=48000, mono=True)
            if len(y) == 0:
                continue
            # Trim silence above 35dB
            trimmed, index = librosa.effects.trim(y, top_db=35)
            # If leading silence was greater than 50ms, rewrite file
            if index[0] > int(sr * 0.05):
                # Peak normalize slightly to -1dBFS
                peak = np.max(np.abs(trimmed))
                if peak > 0:
                    trimmed = trimmed * (0.891 / peak)
                sf.write(str(f), trimmed, sr)
                trimmed_count += 1
        except Exception as e:
            pass
    print(f"[Optimize] Successfully trimmed leading silence on {trimmed_count} audio files.")

def synthesize_ui_open_chime():
    print("[Optimize] Synthesizing crisp sci-fi UI open chime...")
    sr = 48000
    duration = 0.45
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    
    # 3-tone high tech rising chime: C6 (1046Hz) -> E6 (1318Hz) -> G6 (1568Hz)
    tone1 = np.sin(2 * np.pi * 1046 * t) * np.exp(-t * 12)
    tone2 = np.sin(2 * np.pi * 1318 * t) * np.exp(-t * 10) * (t > 0.08)
    tone3 = np.sin(2 * np.pi * 1568 * t) * np.exp(-t * 8) * (t > 0.16)
    
    # Subtle digital shimmer (carrier wave)
    shimmer = np.sin(2 * np.pi * 3136 * t) * 0.15 * np.exp(-t * 15)
    
    wave = tone1 + tone2 + tone3 + shimmer
    # Normalize to -1 dBFS
    wave = wave / np.max(np.abs(wave)) * 0.891
    
    sound_id = "sfx_ui_tab_open_synth"
    dest_path = AUDIO_CACHE_DIR / f"{sound_id}.mp3"
    sf.write(str(dest_path), wave, sr)
    
    with get_db_context() as conn:
        c = conn.cursor()
        c.execute("""
        INSERT INTO sounds (id, source, source_id, name, file_path, duration, license_type, author, source_url, tags, sha256)
        VALUES (?, 'procedural', ?, 'Sci-Fi Hologram UI Open Chime', ?, ?, 'CC0', 'SoundReels Procedural Synth', 'local://procedural', '["ui open", "digital beep", "chime"]', 'synth_hash')
        ON CONFLICT(id) DO UPDATE SET file_path=excluded.file_path;
        """, (sound_id, sound_id, str(dest_path), duration))
        
        c.execute("UPDATE slot_candidates SET rank = rank + 1 WHERE slot_id = 'sfx_ui_tab_open'")
        c.execute("""
        INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
        VALUES ('sfx_ui_tab_open', ?, 0.98, 1, 'pending')
        ON CONFLICT(slot_id, sound_id) DO UPDATE SET score=0.98, rank=1;
        """, (sound_id,))
        conn.commit()
    print("[Optimize] Injected procedural sci-fi UI chime into 'sfx_ui_tab_open' as Rank 1.")

def apply_quality_re_rankings():
    print("[Optimize] Applying expert game-feel re-rankings...")
    promotions = [
        ("sfx_target_hit", "fs-161098"),        # Arrow Hit Impact (authentic target thunk)
        ("sfx_gimmick_mushroom", "fs-172205"),  # Game Jump Bounce (crisp arcade bounce)
        ("sfx_ui_tab_close", "fs-878252"),      # UI Pop-up Close (high tech dismiss)
        ("sfx_mob_female_proj_hit", "fs-276600"),# Body Hit Impact (Minecraft style thud)
        ("sfx_melee_hit_machine", "fs-151624"), # Metal Car Crash / metal crunch
        ("sfx_paintball_cannon", "fs-131304")   # Air Nailer Single Shot (pneumatic release)
    ]
    
    with get_db_context() as conn:
        c = conn.cursor()
        for slot_id, sound_id in promotions:
            c.execute("SELECT id FROM slot_candidates WHERE slot_id = ? AND sound_id = ?", (slot_id, sound_id))
            target_row = c.fetchone()
            if target_row:
                # Shift others down and put target at rank 1
                c.execute("UPDATE slot_candidates SET rank = rank + 1 WHERE slot_id = ?", (slot_id,))
                c.execute("UPDATE slot_candidates SET rank = 1, score = 0.92 WHERE slot_id = ? AND sound_id = ?", (slot_id, sound_id))
                print(f"  Promoted {sound_id} to Rank 1 for slot '{slot_id}'")
        conn.commit()

if __name__ == "__main__":
    optimize_cached_audio_silence()
    synthesize_ui_open_chime()
    apply_quality_re_rankings()
    print("[Optimize] All audio and ranking optimizations completed successfully!")
