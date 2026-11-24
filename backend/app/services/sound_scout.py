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
import soundfile as sf
from transformers import ClapModel, ClapProcessor

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.db import get_db_context, AUDIO_CACHE_DIR
from backend.app.services.loudness import compute_norm_gain

AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

SFX_KO_EN_MAP: Dict[str, str] = {
    "짧": "short", "짧은": "short", "짧게": "short", "간결": "snappy", "순간": "instant",
    "긴": "long", "길게": "long", "지속": "loop", "맑은": "clear", "맑고": "bright",
    "밝은": "bright", "경쾌": "bright", "어두운": "dark", "무거운": "heavy", "묵직": "heavy",
    "부드러운": "soft", "부드럽게": "soft", "약한": "gentle", "강한": "punchy", "세게": "hard",
    "날카로운": "sharp", "날카롭게": "sharp", "크리스피": "crisp", "바삭": "crunch",
    "금속": "metal", "쇠": "metallic", "철": "iron", "나무": "wood", "유리": "glass",
    "물": "water", "액체": "liquid", "불": "fire", "바람": "wind", "공기": "air",
    "전기": "electric", "스파크": "spark", "레이저": "laser", "마법": "magic",
    "종": "bell", "벨": "bell", "딩": "ding", "차임": "chime", "알림": "notification",
    "클릭": "click", "버튼": "button", "팝업": "popup", "문": "door", "발소리": "footstep",
    "타격": "impact", "충격": "hit", "폭발": "explosion", "베기": "slash", "칼": "sword",
    "대시": "whoosh", "휙": "swoosh", "기계": "mechanical", "엔진": "engine", "로봇": "robot",
    "높은": "high pitch", "낮은": "low bass", "저음": "bass", "고음": "high",
    "미래": "sci-fi", "공상": "scifi", "레트로": "retro", "8비트": "8bit", "몬스터": "monster",
}

SFX_SYNONYM_MAP: Dict[str, List[str]] = {
    "bell": ["chime", "ding", "ping", "metallic", "gong", "ringtone"],
    "doorbell": ["chime", "ding", "bell", "buzzer", "intercom"],
    "chime": ["bell", "sparkle", "ding", "glockenspiel", "tone"],
    "wind": ["whoosh", "air", "breeze", "gust", "swoosh"],
    "whoosh": ["swoosh", "swipe", "air", "wind", "flyby"],
    "click": ["tap", "switch", "button", "pop", "tick"],
    "button": ["click", "switch", "ui", "beep", "snap"],
    "crunch": ["crisp", "crackle", "bite", "chips", "biscuit"],
    "impact": ["hit", "thud", "punch", "slam", "crash"],
    "hit": ["impact", "strike", "smack", "knock", "thump"],
    "door": ["latch", "slam", "creak", "hinge", "shut"],
    "metal": ["clang", "clank", "steel", "metallic", "anvil"],
    "wood": ["knock", "crate", "plank", "tap", "creak"],
    "water": ["splash", "bubble", "drip", "liquid", "pour"],
    "spray": ["hiss", "mist", "aerosol", "steam", "puff"],
    "fire": ["flame", "burn", "crackle", "torch", "whoosh"],
    "electric": ["zap", "spark", "static", "shock", "buzz"],
    "laser": ["sci-fi", "zap", "blaster", "beam", "synth"],
    "explosion": ["boom", "blast", "detonation", "debris", "thunder"],
    "magic": ["sparkle", "spell", "cast", "shimmer", "chime"],
    "footstep": ["walk", "step", "shoe", "run", "boots"],
    "mechanical": ["gear", "servo", "motor", "ratchet", "machinery"],
}


def _extract_english_prompt_terms(prompt_text: str) -> List[str]:
    """Converts Korean/English user similarity prompt into clean English SFX search & CLAP tokens."""
    raw = (prompt_text or "").strip().lower()
    if not raw:
        return []
    terms: List[str] = []
    # Match Korean keywords cleanly by substring
    for ko, en in SFX_KO_EN_MAP.items():
        if ko in raw and en not in terms:
            terms.append(en)
    # Keep ASCII English words
    for token in raw.replace(",", " ").replace(".", " ").split():
        ascii_only = "".join(ch for ch in token if ch.isascii() and (ch.isalnum() or ch == "-"))
        if len(ascii_only) >= 2 and not ascii_only.isdigit() and ascii_only not in terms:
            terms.append(ascii_only)
    return terms


class SoundScout:
    def __init__(self, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() and device == "cuda" else "cpu"
        self.model_id = "laion/clap-htsat-unfused"
        print(f"[SoundScout] Loading CLAP model ({self.model_id}) on {self.device.upper()}...")
        self.processor = ClapProcessor.from_pretrained(self.model_id)
        self.model = ClapModel.from_pretrained(self.model_id, use_safetensors=True).to(self.device).eval()
        self._audio_emb_cache: Dict[str, torch.Tensor] = {}
        self._similar_history: Dict[str, Dict[str, Any]] = {}
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
        actual_duration = float(sound_item.get("duration", 0.0) or 0.0)
        try:
            actual_duration = float(sf.info(str(local_file)).duration)
        except Exception:
            pass

        return {
            "id": sound_id,
            "source": "lotsofsounds",
            "source_id": sound_id,
            "name": sound_item["name"],
            "file_path": str(local_file),
            "duration": round(actual_duration, 3),
            "license_type": "CC0",
            "author": "LotsOfSounds / CC0 Community",
            "source_url": f"https://lotsofsounds.com/sounds/{sound_id}",
            "tags": sound_item.get("tags", []),
            "sha256": sha256_hash,
            "norm_gain": norm_gain,
        }

    def compute_audio_embedding(self, file_path: str) -> Optional[torch.Tensor]:
        """Loads audio (up to 10s) and returns normalized CLAP audio embedding (cached in memory)."""
        key = str(file_path)
        if key in self._audio_emb_cache:
            return self._audio_emb_cache[key]
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
            self._audio_emb_cache[key] = res
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

    def find_similar_to_sound(
        self,
        slot_id: str,
        reference_sound_id: str,
        max_duration: Optional[float] = None,
        custom_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Finds & ranks acoustically similar sounds with tag/synonym rotation and optional user prompt."""
        hist_key = f"{slot_id}::{reference_sound_id}"
        hist = self._similar_history.setdefault(hist_key, {"round_no": 0, "tried_queries": set()})
        hist["round_no"] += 1
        round_no = hist["round_no"]
        tried_queries = hist["tried_queries"]

        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM sounds WHERE id = ?", (reference_sound_id,))
            ref_row = c.fetchone()
            if not ref_row:
                return {"found_new": 0, "total_ranked": 0, "top_similar_index": 0, "queries_used": [], "round_no": round_no}
            ref_snd = dict(ref_row)

            c.execute("SELECT search_tags FROM slots WHERE id = ?", (slot_id,))
            slot_row = c.fetchone()
            slot_tags = json.loads((slot_row["search_tags"] if slot_row else None) or "[]")

            c.execute("""
            SELECT sc.id as cand_row_id, sc.sound_id, sc.status, snd.file_path, snd.name, snd.duration
            FROM slot_candidates sc
            JOIN sounds snd ON sc.sound_id = snd.id
            WHERE sc.slot_id = ?
            """, (slot_id,))
            all_slot_rows = [dict(r) for r in c.fetchall()]

        skipped_ids = {r["sound_id"] for r in all_slot_rows if r["status"] == "skipped"}
        existing_rows = [r for r in all_slot_rows if r["status"] != "skipped"]

        # 1. Build Target Embedding (Audio-to-Audio, or Hybrid Audio + User Prompt Text)
        ref_emb = None
        if ref_snd.get("file_path") and Path(ref_snd["file_path"]).exists():
            ref_emb = self.compute_audio_embedding(ref_snd["file_path"])
        ref_tags = json.loads(ref_snd.get("tags") or "[]")
        if ref_emb is None:
            ref_emb = self.compute_text_embedding([ref_snd.get("name", "")] + ref_tags, fallback_text="game sound effect")

        prompt_terms = _extract_english_prompt_terms(custom_prompt or "")
        if prompt_terms:
            prompt_phrase = " ".join(prompt_terms)
            prompt_emb = self.compute_text_embedding([prompt_phrase, f"{prompt_phrase} sound effect"], fallback_text=prompt_phrase)
            target_emb = 0.55 * ref_emb + 0.45 * prompt_emb
            target_emb = target_emb / target_emb.norm(dim=-1, keepdim=True)
        else:
            target_emb = ref_emb

        # 2. Build diversified query pool & rotate untried queries across rounds
        stop_words = {"sound", "audio", "sfx", "effect", "effects", "tags", "tag", "cc0", "freesound", "loop", "file", "noise"}
        candidate_pool: List[str] = []

        # Priority A: User prompt keywords (combined & individual)
        if prompt_terms:
            if len(prompt_terms) >= 2:
                candidate_pool.append(" ".join(prompt_terms[:2]))
            for pt in prompt_terms[:4]:
                if pt not in candidate_pool:
                    candidate_pool.append(pt)

        # Priority B: Reference sound tags & title keywords
        for t in ref_tags:
            clean = str(t).strip().lower().replace("-", " ")
            if len(clean) >= 3 and not clean.isdigit() and clean not in stop_words and clean not in candidate_pool:
                candidate_pool.append(clean)
        if ref_snd.get("name"):
            name_tokens = [w.strip().lower() for w in str(ref_snd["name"]).replace("-", " ").split() if len(w.strip()) >= 3]
            filtered_name = " ".join([w for w in name_tokens if w not in stop_words and not w.isdigit()][:2])
            if filtered_name and filtered_name not in candidate_pool:
                candidate_pool.append(filtered_name)

        # Priority C: Synonym expansions so repeated clicks continue discovering fresh sounds
        for base_word in list(candidate_pool):
            for tok in base_word.split():
                for syn in SFX_SYNONYM_MAP.get(tok, []):
                    if syn not in candidate_pool:
                        candidate_pool.append(syn)

        # Priority D: Slot tags fallback
        for st in slot_tags:
            clean_st = str(st).strip().lower()
            if clean_st and clean_st not in candidate_pool:
                candidate_pool.append(clean_st)

        untried = [q for q in candidate_pool if q not in tried_queries]
        if not untried:
            # Reset tried rotation when all synonyms cycled once, keeping prompt terms first
            tried_queries.clear()
            untried = candidate_pool

        queries = untried[:3]
        for q in queries:
            tried_queries.add(q)

        # 3. Search LotsOfSounds online (full 12 sample page per untried query)
        existing_ids = {r["sound_id"] for r in all_slot_rows}
        seen_ids = set(existing_ids) | skipped_ids | {reference_sound_id}
        new_raw_items = []
        for q in queries:
            for item in self.search_lotsofsounds(q, limit=12):
                sid = item.get("id")
                if not sid or sid in seen_ids:
                    continue
                if max_duration and max_duration > 0 and item.get("duration", 0.0) > max_duration:
                    continue
                seen_ids.add(sid)
                new_raw_items.append(item)
            time.sleep(0.03)

        downloaded_new = []
        targets = new_raw_items[:6]
        if targets:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4) as pool:
                for dl in pool.map(self.download_sound, targets):
                    if dl:
                        downloaded_new.append(dl)

        # 4. Score newly downloaded sounds + check local library cross-matches if online yields < 3
        scored_new = []
        for snd in downloaded_new:
            a_emb = self.compute_audio_embedding(snd["file_path"])
            if a_emb is None:
                continue
            sim = float((a_emb @ target_emb.T).item())
            scored_new.append({"sound": snd, "score": sim})

        if len(scored_new) < 3:
            with get_db_context() as conn:
                c = conn.cursor()
                c.execute("SELECT * FROM sounds")
                pool_rows = [dict(r) for r in c.fetchall()]
            local_matches = []
            for p_row in pool_rows:
                pid = p_row["id"]
                if pid in seen_ids:
                    continue
                if max_duration and max_duration > 0 and (p_row.get("duration") or 0.0) > max_duration:
                    continue
                fp = p_row.get("file_path")
                if not fp or not Path(fp).exists():
                    continue
                a_emb = self.compute_audio_embedding(fp)
                if a_emb is None:
                    continue
                sim = float((a_emb @ target_emb.T).item())
                if sim >= 0.45:
                    local_matches.append({"sound": p_row, "score": sim, "from_local_pool": True})
            local_matches.sort(key=lambda x: x["score"], reverse=True)
            scored_new.extend(local_matches[: max(1, 4 - len(scored_new))])

        # Re-score existing active candidates in this slot against target_emb
        existing_scores: List[Dict[str, Any]] = []
        for ex in existing_rows:
            if ex["sound_id"] == reference_sound_id:
                continue
            sim = 0.5
            fp = ex.get("file_path")
            if fp and Path(fp).exists():
                a_emb = self.compute_audio_embedding(fp)
                if a_emb is not None:
                    sim = float((a_emb @ target_emb.T).item())
            existing_scores.append({"sound_id": ex["sound_id"], "score": sim})

        # 5. Persist newly found sounds & update acoustic/prompt similarity ordering cleanly
        with get_db_context() as conn:
            c = conn.cursor()
            for entry in scored_new:
                snd = entry["sound"]
                if not entry.get("from_local_pool"):
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
                        json.dumps(snd["tags"], ensure_ascii=False) if isinstance(snd.get("tags"), list) else (snd.get("tags") or "[]"),
                        snd["sha256"],
                        snd["norm_gain"],
                    ))
                c.execute("""
                INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
                VALUES (?, ?, ?, 999, 'pending')
                ON CONFLICT(slot_id, sound_id) DO UPDATE SET score=excluded.score;
                """, (slot_id, snd["id"], entry["score"]))

            for ex in existing_scores:
                c.execute(
                    "UPDATE slot_candidates SET score = ? WHERE slot_id = ? AND sound_id = ?",
                    (ex["score"], slot_id, ex["sound_id"]),
                )

            c.execute("UPDATE slot_candidates SET rank = 1 WHERE slot_id = ? AND sound_id = ?", (slot_id, reference_sound_id))
            c.execute("""
            SELECT id, sound_id FROM slot_candidates
            WHERE slot_id = ? AND sound_id != ? AND status != 'skipped'
            ORDER BY score DESC
            """, (slot_id, reference_sound_id))
            other_rows = c.fetchall()
            for rank_idx, r in enumerate(other_rows, start=2):
                c.execute("UPDATE slot_candidates SET rank = ? WHERE id = ?", (rank_idx, r["id"]))

            conn.commit()

        total_ranked = 1 + len(other_rows)
        return {
            "round_no": round_no,
            "found_new": len(scored_new),
            "total_ranked": total_ranked,
            "top_similar_index": 1 if total_ranked > 1 else 0,
            "queries_used": queries,
            "prompt_used": (custom_prompt or "").strip(),
        }

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
