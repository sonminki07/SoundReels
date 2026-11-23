"""
SoundReels Sound Scout & Ranking Engine
- Autonomously queries LotsOfSounds CC0 API without API keys
- Caches audio locally with metadata & SHA256
- Runs LAION-CLAP embedding on GPU to calculate semantic match scores
- Ranks and queues candidates into `slot_candidates` table for the reels player
"""

import os
import sys
import time
import json
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

import torch
import librosa
from transformers import ClapModel, ClapProcessor

from backend.app.core.db import get_connection, DB_PATH

CACHE_DIR = Path("E:/SoundReels/data/audio_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

class SoundScout:
    def __init__(self, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() and device == "cuda" else "cpu"
        self.model_id = "laion/clap-htsat-unfused"
        print(f"[SoundScout] Loading CLAP model ({self.model_id}) on {self.device.upper()}...")
        self.processor = ClapProcessor.from_pretrained(self.model_id)
        self.model = ClapModel.from_pretrained(self.model_id, use_safetensors=True).to(self.device).eval()
        print("[SoundScout] CLAP Model ready.")

    def search_lotsofsounds(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Queries the LotsOfSounds no-key CC0 API and returns sound items."""
        results = []
        try:
            encoded_q = urllib.parse.quote(query)
            url = f"https://api.lotsofsounds.com/api/v1/sounds/sample?q={encoded_q}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "SoundReels/1.0"})
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
                        "stream_endpoint": item.get("stream_url")
                    })
        except Exception as e:
            print(f"[LotsOfSounds] Error searching query '{query}': {e}")
        return results

    def download_sound(self, sound_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Downloads actual audio bytes and returns local metadata."""
        sound_id = sound_item["id"]
        stream_path = sound_item.get("stream_endpoint")
        if not stream_path:
            return None

        if stream_path.startswith("/"):
            stream_url = f"https://api.lotsofsounds.com{stream_path}"
        else:
            stream_url = stream_path

        local_file = CACHE_DIR / f"{sound_id}.mp3"

        if not local_file.exists() or local_file.stat().st_size < 1000:
            try:
                # 1. Fetch JSON from /stream to obtain signed S3 URL
                s_req = urllib.request.Request(stream_url, headers={"User-Agent": "SoundReels/1.0"})
                with urllib.request.urlopen(s_req, timeout=10) as s_resp:
                    s_data = json.loads(s_resp.read().decode("utf-8"))
                    actual_url = s_data.get("data", {}).get("stream_url")

                if not actual_url:
                    return None

                # 2. Download raw MP3 bytes
                down_req = urllib.request.Request(actual_url, headers={"User-Agent": "SoundReels/1.0"})
                with urllib.request.urlopen(down_req, timeout=20) as d_resp, open(local_file, "wb") as f:
                    content = d_resp.read()
                    f.write(content)
            except Exception as dl_err:
                print(f"[SoundScout] Download error for {sound_id}: {dl_err}")
                return None

        # Compute sha256
        hasher = hashlib.sha256()
        with open(local_file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        sha256_hash = hasher.hexdigest()

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
            "sha256": sha256_hash
        }

    def compute_audio_embedding(self, file_path: str) -> Optional[torch.Tensor]:
        """Loads audio and returns normalized CLAP audio embedding."""
        try:
            audio, _ = librosa.load(file_path, sr=48000, mono=True)
            if len(audio) == 0:
                return None
            # Truncate long sounds to 10s for embedding
            if len(audio) > 48000 * 10:
                audio = audio[:48000 * 10]
            inputs = self.processor(audio=audio, sampling_rate=48000, return_tensors="pt").to(self.device)
            with torch.no_grad():
                emb = self.model.get_audio_features(**inputs)
                emb = emb / emb.norm(dim=-1, keepdim=True)
            return emb.cpu()
        except Exception as e:
            print(f"[SoundScout] Embedding error for {file_path}: {e}")
            return None

    def compute_text_embedding(self, texts: List[str]) -> torch.Tensor:
        """Computes mean normalized text embedding for a list of prompt strings."""
        inputs = self.processor(text=texts, return_tensors="pt", padding=True).to(self.device)
        with torch.no_grad():
            emb = self.model.get_text_features(**inputs)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            mean_emb = emb.mean(dim=0, keepdim=True)
            mean_emb = mean_emb / mean_emb.norm(dim=-1, keepdim=True)
        return mean_emb.cpu()

    def scout_slot(self, slot_id: str, max_candidates_per_query: int = 4) -> int:
        """Finds, downloads, embeds, ranks and records candidates for a single slot."""
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return 0

        slot = dict(row)
        if slot["status"] == "on_hold":
            print(f"[SoundScout] Skipping slot '{slot['name']}' (Status: on_hold)")
            conn.close()
            return 0

        search_tags = json.loads(slot.get("search_tags") or "[]")
        clap_prompts = json.loads(slot.get("clap_prompts") or "[]")

        print(f"\n==================================================")
        print(f"[SoundScout] Scouting Slot: {slot['name']} ({slot['id']})")
        print(f"Tags: {search_tags}")
        print(f"CLAP Prompts: {clap_prompts}")

        # 1. Search across tags
        raw_sounds = []
        seen_ids = set()
        for tag in search_tags:
            items = self.search_lotsofsounds(tag, limit=max_candidates_per_query)
            for item in items:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    raw_sounds.append(item)
            time.sleep(0.1)

        print(f"Found {len(raw_sounds)} unique candidates online.")

        # 2. Download and save to sounds DB
        downloaded_sounds = []
        for raw in raw_sounds:
            sound_data = self.download_sound(raw)
            if not sound_data:
                continue

            # Upsert into sounds table
            c.execute("""
            INSERT INTO sounds (id, source, source_id, name, file_path, duration, license_type, author, source_url, tags, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                file_path=excluded.file_path,
                duration=excluded.duration,
                sha256=excluded.sha256;
            """, (
                sound_data["id"],
                sound_data["source"],
                sound_data["source_id"],
                sound_data["name"],
                sound_data["file_path"],
                sound_data["duration"],
                sound_data["license_type"],
                sound_data["author"],
                sound_data["source_url"],
                json.dumps(sound_data["tags"], ensure_ascii=False),
                sound_data["sha256"]
            ))
            downloaded_sounds.append(sound_data)

        conn.commit()

        if not downloaded_sounds:
            print("No audio successfully downloaded.")
            conn.close()
            return 0

        # 3. Compute text query embedding
        text_emb = self.compute_text_embedding(clap_prompts) # [1, D]

        # 4. Compute audio embeddings & cosine similarity
        scored_candidates = []
        for snd in downloaded_sounds:
            audio_emb = self.compute_audio_embedding(snd["file_path"])
            if audio_emb is None:
                continue
            cos_sim = (audio_emb @ text_emb.T).item()
            scored_candidates.append({
                "sound_id": snd["id"],
                "name": snd["name"],
                "score": float(cos_sim),
                "duration": snd["duration"]
            })

        # 5. Sort by similarity score descending
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # 6. Insert into slot_candidates
        rank = 1
        for cand in scored_candidates[:8]: # Keep top 8 candidates per slot
            c.execute("""
            INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
            VALUES (?, ?, ?, ?, 'pending')
            ON CONFLICT(slot_id, sound_id) DO UPDATE SET
                score=excluded.score,
                rank=excluded.rank;
            """, (slot_id, cand["sound_id"], cand["score"], rank))
            print(f"  #{rank} [{cand['score']:.3f}] {cand['name']} ({cand['sound_id']})")
            rank += 1

        # 7. Update slot status to 'has_candidates'
        if scored_candidates:
            c.execute("UPDATE slots SET status = 'has_candidates' WHERE id = ?", (slot_id,))

        conn.commit()
        conn.close()
        return len(scored_candidates)

    def scout_all_slots(self):
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT id, name, status FROM slots ORDER BY created_at ASC")
        slots = [dict(r) for r in c.fetchall()]
        conn.close()

        print(f"\n=======================================================")
        print(f"[SoundScout] Starting Autonomous Scouting for {len(slots)} Slots")
        print(f"=======================================================")

        total_matched = 0
        for s in slots:
            count = self.scout_slot(s["id"])
            total_matched += count

        print(f"\n[SoundScout] Completed! Total candidates registered: {total_matched}")

if __name__ == "__main__":
    scout = SoundScout(device="cuda")
    scout.scout_all_slots()
