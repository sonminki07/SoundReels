"""
SoundReels Sound Scout & Ranking Engine
- Autonomously queries LotsOfSounds CC0 API without API keys
- Caches audio locally with atomic download, metadata, SHA256, and LUFS norm_gain
- Runs LAION-CLAP embedding on GPU to calculate semantic match scores
- Supports dynamic max_duration filtering & re-scouting
"""

import os
import sys
import time
import json
import shutil
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

import torch
import librosa
from transformers import ClapModel, ClapProcessor

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.db import get_db_context, AUDIO_CACHE_DIR
from backend.app.services.loudness import compute_norm_gain

AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class SoundScout:
    def __init__(self, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() and device == "cuda" else "cpu"
        self.model_id = "laion/clap-htsat-unfused"
        print(f"[SoundScout] Loading CLAP model ({self.model_id}) on {self.device.upper()}...")
        self.processor = ClapProcessor.from_pretrained(self.model_id)
        self.model = ClapModel.from_pretrained(self.model_id, use_safetensors=True).to(self.device).eval()
        print("[SoundScout] CLAP Model ready.")

    def search_lotsofsounds(self, query: str, limit: int = 12) -> List[Dict[str, Any]]:
        """Queries the LotsOfSounds no-key CC0 API and returns sound items."""
        results = []
        try:
            encoded_q = urllib.parse.quote(query)
            url = f"https://api.lotsofsounds.com/api/v1/sounds/sample?q={encoded_q}&limit={min(12, max(1, limit))}"
            req = urllib.request.Request(url, headers={"User-Agent": "SoundReels/1.3"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("data", [])
                for item in items:
                    results.append({
                        "id": item.get("id"),
                        "name": item.get("name", "Unknown SFX"),
                        "description": item.get("description", ""),
                        "tags": item.get("tags", []),
                        "duration": float(item.get("duration", 0.0)),
                        "stream_endpoint": item.get("stream_url"),
                    })
        except Exception as e:
            print(f"[LotsOfSounds] Error searching query '{query}': {e}")
        return results

    def download_sound(self, sound_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Downloads actual audio bytes atomically and computes SHA256 + perceptual norm_gain."""
        sound_id = sound_item["id"]
        stream_path = sound_item.get("stream_endpoint")
        if not sound_id or not stream_path:
            return None

        if stream_path.startswith("/"):
            stream_url = f"https://api.lotsofsounds.com{stream_path}"
        else:
            stream_url = stream_path

        local_file = AUDIO_CACHE_DIR / f"{sound_id}.mp3"

        if not local_file.exists() or local_file.stat().st_size < 1000:
            temp_file = local_file.with_suffix(".tmp")
            try:
                s_req = urllib.request.Request(stream_url, headers={"User-Agent": "SoundReels/1.3"})
                with urllib.request.urlopen(s_req, timeout=10) as s_resp:
                    s_data = json.loads(s_resp.read().decode("utf-8"))
                    actual_url = s_data.get("data", {}).get("stream_url")

                if not actual_url or not actual_url.startswith("https://"):
                    return None

                down_req = urllib.request.Request(actual_url, headers={"User-Agent": "SoundReels/1.3"})
                with urllib.request.urlopen(down_req, timeout=20) as d_resp, open(temp_file, "wb") as f:
                    shutil.copyfileobj(d_resp, f)
                if temp_file.stat().st_size < 500:
                    temp_file.unlink(missing_ok=True)
                    return None
                temp_file.replace(local_file)
            except Exception as dl_err:
                temp_file.unlink(missing_ok=True)
                print(f"[SoundScout] Download error for {sound_id}: {dl_err}")
                return None

        hasher = hashlib.sha256()
        with open(local_file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        sha256_hash = hasher.hexdigest()

        norm_gain = compute_norm_gain(local_file)

        return {
            "id": sound_id,
            "source": "lotsofsounds",
            "source_id": sound_id,
            "name": sound_item["name"],
            "file_path": str(local_file),
            "duration": sound_item.get("duration", 0.0),
            "license_type": "CC0",
            "author": "LotsOfSounds / CC0 Community",
            "source_url": f"https://lotsofsounds.com/sounds/{sound_id}",
            "tags": sound_item.get("tags", []),
            "sha256": sha256_hash,
            "norm_gain": norm_gain,
        }

    def compute_audio_embedding(self, file_path: str) -> Optional[torch.Tensor]:
        """Loads audio (up to 10s) and returns normalized CLAP audio embedding."""
        try:
            audio, _ = librosa.load(file_path, sr=48000, mono=True, duration=10.0)
            if len(audio) == 0:
                return None
            inputs = self.processor(audio=audio, sampling_rate=48000, return_tensors="pt").to(self.device)
            with torch.no_grad():
                emb = self.model.get_audio_features(**inputs)
                emb = emb / emb.norm(dim=-1, keepdim=True)
            res = emb.cpu()
            del inputs, emb
            return res
        except Exception as e:
            print(f"[SoundScout] Embedding error for {file_path}: {e}")
            return None

    def compute_text_embedding(self, texts: List[str], fallback_text: str = "game sound effect") -> torch.Tensor:
        """Computes mean normalized text embedding for a list of prompt strings."""
        valid_texts = [t.strip() for t in texts if t and t.strip()]
        if not valid_texts:
            valid_texts = [fallback_text]
        inputs = self.processor(text=valid_texts, return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            emb = self.model.get_text_features(**inputs)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            mean_emb = emb.mean(dim=0, keepdim=True)
            mean_emb = mean_emb / mean_emb.norm(dim=-1, keepdim=True)
        res = mean_emb.cpu()
        del inputs, emb, mean_emb
        return res

    def scout_slot(
        self,
        slot_id: str,
        max_candidates_per_query: int = 10,
        max_duration: Optional[float] = None,
        allow_on_hold: bool = False,
        custom_search_tags: Optional[List[str]] = None,
        custom_clap_prompts: Optional[List[str]] = None,
        prioritize_by_score: bool = False,
    ) -> int:
        """Finds, downloads, embeds, ranks and records candidates for a single slot."""
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
            row = c.fetchone()
            if not row:
                return 0
            slot = dict(row)

        if slot["status"] == "on_hold" and not allow_on_hold:
            print(f"[SoundScout] Skipping slot '{slot['name']}' (Status: on_hold)")
            return 0

        search_tags = custom_search_tags if custom_search_tags else json.loads(slot.get("search_tags") or "[]")
        clap_prompts = custom_clap_prompts if custom_clap_prompts else json.loads(slot.get("clap_prompts") or "[]")

        # 1. Search across tags (Network I/O without holding DB connection)
        raw_sounds = []
        seen_ids = set()
        for tag in search_tags:
            items = self.search_lotsofsounds(tag, limit=max_candidates_per_query)
            for item in items:
                if item["id"] in seen_ids:
                    continue
                if max_duration and max_duration > 0 and item["duration"] > max_duration:
                    continue
                seen_ids.add(item["id"])
                raw_sounds.append(item)
            time.sleep(0.05)

        if not raw_sounds:
            return 0

        # 2. Download and compute loudness + CLAP embeddings
        downloaded_sounds = []
        for raw in raw_sounds:
            sound_data = self.download_sound(raw)
            if sound_data:
                downloaded_sounds.append(sound_data)

        if not downloaded_sounds:
            return 0

        text_emb = self.compute_text_embedding(clap_prompts, fallback_text=slot.get("name") or "sound effect")
        scored_candidates = []
        for snd in downloaded_sounds:
            audio_emb = self.compute_audio_embedding(snd["file_path"])
            if audio_emb is None:
                continue
            cos_sim = (audio_emb @ text_emb.T).item()
            scored_candidates.append({
                "sound": snd,
                "score": float(cos_sim),
            })

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # 3. Persist sounds & top candidates in one concise DB transaction
        with get_db_context() as conn:
            c = conn.cursor()
            for entry in scored_candidates:
                snd = entry["sound"]
                c.execute("""
                INSERT INTO sounds (id, source, source_id, name, file_path, duration, license_type, author, source_url, tags, sha256, norm_gain)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    file_path=excluded.file_path,
                    duration=excluded.duration,
                    sha256=excluded.sha256,
                    norm_gain=excluded.norm_gain;
                """, (
                    snd["id"],
                    snd["source"],
                    snd["source_id"],
                    snd["name"],
                    snd["file_path"],
                    snd["duration"],
                    snd["license_type"],
                    snd["author"],
                    snd["source_url"],
                    json.dumps(snd["tags"], ensure_ascii=False),
                    snd["sha256"],
                    snd["norm_gain"],
                ))

            # Preserve liked/confirmed candidates; rank new candidates up to 12 total
            c.execute("SELECT COALESCE(MAX(rank), 0) FROM slot_candidates WHERE slot_id = ?", (slot_id,))
            next_rank = c.fetchone()[0] + 1

            added = 0
            for entry in scored_candidates[:12]:
                snd = entry["sound"]
                c.execute("""
                INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
                VALUES (?, ?, ?, ?, 'pending')
                ON CONFLICT(slot_id, sound_id) DO UPDATE SET
                    score=excluded.score;
                """, (slot_id, snd["id"], entry["score"], next_rank))
                if c.rowcount > 0:
                    next_rank += 1
                    added += 1

            if prioritize_by_score:
                c.execute("""
                SELECT id FROM slot_candidates
                WHERE slot_id = ? AND status != 'skipped'
                ORDER BY
                    CASE status WHEN 'confirmed' THEN 0 WHEN 'liked' THEN 1 ELSE 2 END ASC,
                    score DESC
                """, (slot_id,))
                for idx_rank, r in enumerate(c.fetchall(), start=1):
                    c.execute("UPDATE slot_candidates SET rank = ? WHERE id = ?", (idx_rank, r["id"]))

            if scored_candidates and slot["status"] == "pending":
                c.execute("UPDATE slots SET status = 'has_candidates' WHERE id = ?", (slot_id,))

            conn.commit()
        return len(scored_candidates)

    def scout_all_slots(self):
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM slots ORDER BY created_at ASC")
            slot_ids = [r["id"] for r in c.fetchall()]

        total_matched = 0
        for sid in slot_ids:
            total_matched += self.scout_slot(sid)
        print(f"\n[SoundScout] Completed! Total candidates processed: {total_matched}")


if __name__ == "__main__":
    scout = SoundScout(device="cuda")
    scout.scout_all_slots()
