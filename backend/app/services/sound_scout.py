"""
SoundReels Sound Scout & Ranking Engine
- Autonomously queries LotsOfSounds CC0 API without API keys
- Caches audio locally with atomic download, metadata, SHA256, and LUFS norm_gain
- Runs LAION-CLAP embedding on GPU to calculate semantic match scores
- Supports dynamic max_duration filtering & re-scouting
"""

import os
import re
import sys
import html as html_lib
import time
import json
import shutil
import hashlib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
    "엘리베이터": "elevator", "승강기": "elevator", "닫힘": "close", "닫히는": "closing",
    "열림": "open", "열리는": "opening", "도착": "arrival", "움직": "moving", "모터": "motor",
    "쿠키": "cookie crunch", "환풍기": "fan", "바람소리": "wind", "플랫폼": "platform",
    "함정": "trap", "부시": "bush", "버섯": "mushroom", "점프": "jump", "총": "gun",
    "총기": "gunshot", "발사": "shoot", "방패": "shield", "실드": "shield",
    "스프레이": "spray", "공격": "attack", "피격": "hit", "죽음": "death", "유아이": "ui",
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

    def _parse_freesound_players(self, html_text: str, default_license: str = "CC0") -> List[Dict[str, Any]]:
        """Extracts sound metadata & preview URLs from Freesound HTML `<div class='bw-player'>` tags."""
        blocks = re.findall(r'<div[^>]*class="[^"]*\bbw-player\b[^"]*"[^>]*>', html_text or "", re.S)
        results: List[Dict[str, Any]] = []
        seen_ids = set()
        for blk in blocks:
            m_id = re.search(r'data-sound-id="(\d+)"', blk)
            m_mp3 = re.search(r'data-mp3="([^"]+)"', blk)
            if not m_id or not m_mp3:
                continue
            fs_num = m_id.group(1)
            sid = f"fs-{fs_num}"
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            m_title = re.search(r'data-title="([^"]+)"', blk)
            m_dur = re.search(r'data-duration="([0-9.]+)"', blk)
            m_user = re.search(r'data-username="([^"]+)"', blk)
            raw_title = html_lib.unescape(m_title.group(1)).strip() if m_title else f"Freesound {fs_num}"
            clean_title = re.sub(r'\.(wav|mp3|ogg|flac|aiff|aif)$', '', raw_title, flags=re.I)
            author = html_lib.unescape(m_user.group(1)).strip() if m_user else "Freesound"
            mp3_lq = html_lib.unescape(m_mp3.group(1)).strip()
            results.append({
                "id": sid,
                "name": clean_title or raw_title,
                "duration": float(m_dur.group(1)) if m_dur else 0.0,
                "mp3_url": mp3_lq,
                "license_type": default_license,
                "author": author,
                "source": "freesound",
                "source_id": fs_num,
                "source_url": f"https://freesound.org/s/{fs_num}/",
                "tags": [],
            })
        return results

    def search_freesound(self, query: str, pages: int = 1, cc0_only: bool = True) -> List[Dict[str, Any]]:
        """Queries Freesound web search without requiring an API key (CC0 filtered by default)."""
        all_items: List[Dict[str, Any]] = []
        seen = set()
        encoded_q = urllib.parse.quote((query or "").strip())
        if not encoded_q:
            return []
        license_param = "&f=license%3A%22Creative+Commons+0%22" if cc0_only else ""
        default_lic = "CC0" if cc0_only else "Freesound"
        for page in range(1, max(1, pages) + 1):
            try:
                url = f"https://freesound.org/search/?q={encoded_q}&page={page}{license_param}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    html_text = resp.read().decode("utf-8", errors="ignore")
                parsed = self._parse_freesound_players(html_text, default_license=default_lic)
                if not parsed:
                    break
                for item in parsed:
                    if item["id"] not in seen:
                        seen.add(item["id"])
                        all_items.append(item)
            except Exception as e:
                print(f"[Freesound] Search error for '{query}' p{page}: {e}")
                break
        return all_items

    def download_direct_audio(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Downloads direct preview MP3 atomically (prefers -hq.mp3 for <=20s clips or explicit import)."""
        sound_id = item.get("id")
        raw_url = (item.get("mp3_url") or "").strip()
        if not sound_id or not raw_url.startswith("https://"):
            return None

        local_file = AUDIO_CACHE_DIR / f"{sound_id}.mp3"
        if not local_file.exists() or local_file.stat().st_size < 1000:
            candidate_urls = []
            est_dur = float(item.get("duration") or 0.0)
            if raw_url.endswith("-lq.mp3") and est_dur <= 20.0:
                candidate_urls.append(raw_url[:-7] + "-hq.mp3")
            candidate_urls.append(raw_url)

            downloaded_ok = False
            for dl_url in candidate_urls:
                temp_file = local_file.with_suffix(".tmp")
                try:
                    req = urllib.request.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as resp, open(temp_file, "wb") as f:
                        shutil.copyfileobj(resp, f)
                    if temp_file.stat().st_size >= 500:
                        temp_file.replace(local_file)
                        downloaded_ok = True
                        break
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    temp_file.unlink(missing_ok=True)
            if not downloaded_ok:
                return None

        hasher = hashlib.sha256()
        with open(local_file, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        sha256_hash = hasher.hexdigest()

        norm_gain = compute_norm_gain(local_file)
        actual_duration = float(item.get("duration") or 0.0)
        try:
            actual_duration = float(sf.info(str(local_file)).duration)
        except Exception:
            pass

        return {
            "id": sound_id,
            "source": item.get("source") or "freesound",
            "source_id": str(item.get("source_id") or sound_id),
            "name": item.get("name") or sound_id,
            "file_path": str(local_file),
            "duration": round(actual_duration, 3),
            "license_type": item.get("license_type") or "CC0",
            "author": item.get("author") or "Freesound",
            "source_url": item.get("source_url") or "",
            "tags": item.get("tags") or [],
            "sha256": sha256_hash,
            "norm_gain": norm_gain,
        }

    def fetch_sounds_from_url(self, url: str, max_items: int = 18) -> List[Dict[str, Any]]:
        """Parses sound item(s) from a Freesound/LotsOfSounds page URL without API keys."""
        parsed = urllib.parse.urlparse((url or "").strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in {"freesound.org", "www.freesound.org", "lotsofsounds.com", "www.lotsofsounds.com"}:
            raise ValueError("지원되는 주소가 아닙니다. (freesound.org 또는 lotsofsounds.com HTTPS 주소만 지원)")

        # LotsOfSounds sound URL: /sounds/fs-12345 or /sounds/<id>
        if "lotsofsounds.com" in host:
            m_los = re.search(r'/sounds/([A-Za-z0-9_-]+)', parsed.path)
            if not m_los:
                raise ValueError("LotsOfSounds 사운드 URL 형식이 올바르지 않습니다.")
            los_id = m_los.group(1)
            # First try LotsOfSounds direct stream endpoint; fallback to Freesound page if fs-<num>
            dl = self.download_sound({"id": los_id, "name": los_id, "stream_endpoint": f"/api/v1/sounds/sample/{los_id}/stream", "duration": 0.0, "tags": []})
            if dl:
                return [{"_already_downloaded": dl}]
            m_fs = re.match(r'^fs-(\d+)$', los_id)
            if m_fs:
                url = f"https://freesound.org/s/{m_fs.group(1)}/"
                parsed = urllib.parse.urlparse(url)
                host = "freesound.org"
            else:
                raise ValueError("해당 LotsOfSounds 무료 샘플 스트림을 가져오지 못했습니다.")

        # Freesound single sound URL OR search/browse page
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")
            final_url = resp.geturl()

        m_single = re.search(r'/(?:s|sounds)/(\d+)', urllib.parse.urlparse(final_url).path)
        if m_single:
            fs_num = m_single.group(1)
            sid = f"fs-{fs_num}"
            m_hq = re.search(r'https://cdn\.freesound\.org/previews/[^"\s<>]+-hq\.mp3', html_text)
            m_lq = re.search(r'https://cdn\.freesound\.org/previews/[^"\s<>]+-lq\.mp3', html_text)
            m_og = re.search(r'<meta[^>]+property="og:audio"[^>]+content="([^"]+)"', html_text)
            mp3_url = ""
            if m_hq:
                mp3_url = m_hq.group(0)
            elif m_lq:
                mp3_url = m_lq.group(0)
            elif m_og:
                mp3_url = m_og.group(1).replace("https://freesound.orghttps://", "https://")
            if not mp3_url:
                raise ValueError("Freesound 페이지에서 오디오 미리듣기 주소를 찾지 못했습니다.")

            m_title = re.search(r'<meta[^>]+property="og:audio:title"[^>]+content="([^"]+)"', html_text)
            m_artist = re.search(r'<meta[^>]+property="og:audio:artist"[^>]+content="([^"]+)"', html_text)
            raw_title = html_lib.unescape(m_title.group(1)).strip() if m_title else f"Freesound {fs_num}"
            clean_title = re.sub(r'\.(wav|mp3|ogg|flac|aiff|aif)$', '', raw_title, flags=re.I)
            author = html_lib.unescape(m_artist.group(1)).strip() if m_artist else "Freesound"

            lic = "Freesound"
            if "creativecommons.org/publicdomain/zero" in html_text:
                lic = "CC0"
            elif "creativecommons.org/licenses/by-nc" in html_text:
                lic = "CC-BY-NC"
            elif "creativecommons.org/licenses/by/" in html_text:
                lic = "CC-BY"
            elif "sampling+" in html_text.lower():
                lic = "Sampling+"

            return [{
                "id": sid,
                "name": clean_title or raw_title,
                "duration": 0.0,
                "mp3_url": mp3_url,
                "license_type": lic,
                "author": author,
                "source": "freesound",
                "source_id": fs_num,
                "source_url": f"https://freesound.org/s/{fs_num}/",
                "tags": [],
            }]

        # Otherwise parse as Freesound search / browse list page
        list_items = self._parse_freesound_players(html_text, default_license="Freesound")
        if not list_items:
            raise ValueError("해당 페이지에서 가져올 수 있는 사운드를 찾지 못했습니다.")
        return list_items[:max_items]

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
        max_candidates_per_query: int = 12,
        max_duration: Optional[float] = None,
        allow_on_hold: bool = False,
        custom_search_tags: Optional[List[str]] = None,
        custom_clap_prompts: Optional[List[str]] = None,
        prioritize_by_score: bool = False,
        include_freesound: bool = False,
        max_total_insert: int = 24,
    ) -> int:
        """Finds, downloads, embeds, ranks and records candidates for a single slot."""
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
            row = c.fetchone()
            if not row:
                return 0
            slot = dict(row)
            c.execute("SELECT sound_id FROM slot_candidates WHERE slot_id = ? AND status = 'skipped'", (slot_id,))
            skipped_ids = {r["sound_id"] for r in c.fetchall()}

        if slot["status"] == "on_hold" and not allow_on_hold:
            print(f"[SoundScout] Skipping slot '{slot['name']}' (Status: on_hold)")
            return 0

        search_tags = custom_search_tags if custom_search_tags else json.loads(slot.get("search_tags") or "[]")
        clap_prompts = custom_clap_prompts if custom_clap_prompts else json.loads(slot.get("clap_prompts") or "[]")

        # 1. Search across tags (LotsOfSounds + optional Freesound CC0 multi-page, skipping deleted IDs)
        raw_los = []
        raw_fs = []
        seen_ids = set(skipped_ids)
        for tag in search_tags:
            items = self.search_lotsofsounds(tag, limit=max_candidates_per_query)
            for item in items:
                if item["id"] in seen_ids:
                    continue
                if max_duration and max_duration > 0 and item["duration"] > max_duration:
                    continue
                seen_ids.add(item["id"])
                raw_los.append(item)
            if include_freesound:
                fs_dur_cap = max_duration if (max_duration and max_duration > 0) else 45.0
                for fs_item in self.search_freesound(tag, pages=1, cc0_only=True):
                    if fs_item["id"] in seen_ids:
                        continue
                    if (fs_item.get("duration") or 0.0) > fs_dur_cap:
                        continue
                    seen_ids.add(fs_item["id"])
                    raw_fs.append(fs_item)
            time.sleep(0.02)

        if not raw_los and not raw_fs:
            return 0

        # 2. Download concurrently and compute loudness + CLAP embeddings
        downloaded_sounds = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            if raw_los:
                for dl in pool.map(self.download_sound, raw_los[:max_total_insert]):
                    if dl:
                        downloaded_sounds.append(dl)
            if raw_fs:
                for dl in pool.map(self.download_direct_audio, raw_fs[: min(24, max_total_insert)]):
                    if dl:
                        downloaded_sounds.append(dl)

        if not downloaded_sounds:
            return 0

        text_emb = self.compute_text_embedding(clap_prompts, fallback_text=slot.get("name") or "sound effect")
        scored_candidates = []
        for snd in downloaded_sounds:
            if snd["id"] in skipped_ids:
                continue
            if max_duration and max_duration > 0 and snd.get("duration", 0.0) > max_duration:
                continue
            audio_emb = self.compute_audio_embedding(snd["file_path"])
            if audio_emb is None:
                continue
            cos_sim = (audio_emb @ text_emb.T).item()
            scored_candidates.append({
                "sound": snd,
                "score": float(cos_sim),
            })

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)

        # 3. Persist sounds & candidates (excluding skipped) in one concise DB transaction
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

            c.execute("SELECT COALESCE(MAX(rank), 0) FROM slot_candidates WHERE slot_id = ?", (slot_id,))
            next_rank = c.fetchone()[0] + 1

            added = 0
            for entry in scored_candidates[:max_total_insert]:
                snd = entry["sound"]
                if snd["id"] in skipped_ids:
                    continue
                c.execute("""
                INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
                VALUES (?, ?, ?, ?, 'pending')
                ON CONFLICT(slot_id, sound_id) DO UPDATE SET
                    score=excluded.score;
                """, (slot_id, snd["id"], entry["score"], next_rank))
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
        return added

    def manual_search_into_slot(
        self,
        slot_id: str,
        query_text: str,
        max_duration: Optional[float] = None,
        limit: int = 30,
    ) -> Dict[str, Any]:
        """Directly searches local cache + LotsOfSounds + Freesound CC0 for manual browsing."""
        terms = _extract_english_prompt_terms(query_text)
        search_str = " ".join(terms[:4]) if terms else (query_text or "").strip()
        if not search_str:
            return {"added_count": 0, "matched_ids": [], "queries": []}

        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
            slot_row = c.fetchone()
            if not slot_row:
                return {"added_count": 0, "matched_ids": [], "queries": []}
            c.execute("SELECT sound_id FROM slot_candidates WHERE slot_id = ? AND status = 'skipped'", (slot_id,))
            skipped_ids = {r["sound_id"] for r in c.fetchall()}
            c.execute("SELECT * FROM sounds")
            local_pool = [dict(r) for r in c.fetchall()]

        query_tokens = [t.lower() for t in terms if len(t) >= 2] or [search_str.lower()]
        seen_ids = set(skipped_ids)
        ready_sounds: List[Dict[str, Any]] = []

        # 1. Local cache name/tag matches first (instant)
        for p in local_pool:
            pid = p["id"]
            if pid in seen_ids:
                continue
            if max_duration and max_duration > 0 and (p.get("duration") or 0.0) > max_duration:
                continue
            fp = p.get("file_path")
            if not fp or not Path(fp).exists():
                continue
            hay = f"{p.get('name', '')} {p.get('tags', '')}".lower()
            if any(tok in hay for tok in query_tokens):
                seen_ids.add(pid)
                ready_sounds.append({**p, "from_local_pool": True})

        # 2. Online LotsOfSounds + Freesound CC0 (smart paging: only query extra terms if primary is sparse)
        raw_los: List[Dict[str, Any]] = []
        raw_fs: List[Dict[str, Any]] = []
        dur_cap = max_duration if (max_duration and max_duration > 0) else 45.0
        queries_tried = [search_str]
        for item in self.search_lotsofsounds(search_str, limit=12):
            sid = item.get("id")
            if not sid or sid in seen_ids or item.get("duration", 0.0) > dur_cap:
                continue
            seen_ids.add(sid)
            raw_los.append(item)
        for fs_item in self.search_freesound(search_str, pages=1, cc0_only=True):
            sid = fs_item.get("id")
            if not sid or sid in seen_ids or fs_item.get("duration", 0.0) > dur_cap:
                continue
            seen_ids.add(sid)
            raw_fs.append(fs_item)

        if len(raw_los) + len(raw_fs) < 12:
            for q in [t for t in terms[:2] if t != search_str]:
                queries_tried.append(q)
                for item in self.search_lotsofsounds(q, limit=12):
                    sid = item.get("id")
                    if not sid or sid in seen_ids or item.get("duration", 0.0) > dur_cap:
                        continue
                    seen_ids.add(sid)
                    raw_los.append(item)
                for fs_item in self.search_freesound(q, pages=1, cc0_only=True):
                    sid = fs_item.get("id")
                    if not sid or sid in seen_ids or fs_item.get("duration", 0.0) > dur_cap:
                        continue
                    seen_ids.add(sid)
                    raw_fs.append(fs_item)
                if len(raw_los) + len(raw_fs) >= 18:
                    break

        fs_cap = min(limit, 18)
        los_cap = min(limit, 12)
        with ThreadPoolExecutor(max_workers=10) as pool:
            if raw_los:
                for dl in pool.map(self.download_sound, raw_los[:los_cap]):
                    if dl:
                        ready_sounds.append(dl)
            if raw_fs:
                for dl in pool.map(self.download_direct_audio, raw_fs[:fs_cap]):
                    if dl:
                        ready_sounds.append(dl)

        # Score against user query text with CLAP so closest results sit on top
        text_emb = self.compute_text_embedding([search_str] + terms, fallback_text=search_str)
        scored: List[Dict[str, Any]] = []
        for snd in ready_sounds:
            if snd["id"] in skipped_ids:
                continue
            if max_duration and max_duration > 0 and (snd.get("duration") or 0.0) > max_duration:
                continue
            a_emb = self.compute_audio_embedding(snd["file_path"])
            sim = float((a_emb @ text_emb.T).item()) if a_emb is not None else 0.5
            scored.append({"sound": snd, "score": sim})
        scored.sort(key=lambda x: x["score"], reverse=True)
        scored = scored[:limit]

        matched_ids: List[str] = []
        with get_db_context() as conn:
            c = conn.cursor()
            # Shift existing ranks so matched manual hits appear right at the top after confirmed
            c.execute("UPDATE slot_candidates SET rank = rank + ? WHERE slot_id = ?", (len(scored) + 10, slot_id))
            for offset, entry in enumerate(scored):
                snd = entry["sound"]
                if not snd.get("from_local_pool"):
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
                # Do NOT resurrect skipped candidates during search!
                c.execute("""
                INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
                VALUES (?, ?, ?, ?, 'pending')
                ON CONFLICT(slot_id, sound_id) DO UPDATE SET
                    score=excluded.score,
                    rank=CASE WHEN slot_candidates.status != 'skipped' THEN excluded.rank ELSE slot_candidates.rank END;
                """, (slot_id, snd["id"], entry["score"], offset + 1))
                matched_ids.append(snd["id"])

            # Re-number active ranks cleanly: confirmed first, then manual hits, then rest
            matched_set = set(matched_ids)
            c.execute("""
            SELECT id, sound_id, status, score, rank FROM slot_candidates
            WHERE slot_id = ? AND status != 'skipped'
            ORDER BY
                CASE status WHEN 'confirmed' THEN 0 ELSE 1 END ASC,
                rank ASC,
                score DESC
            """, (slot_id,))
            ordered_rows = c.fetchall()
            # Stable priority: confirmed -> matched_ids -> rest
            ordered_rows.sort(key=lambda r: (
                0 if r["status"] == "confirmed" else (1 if r["sound_id"] in matched_set else 2),
                r["rank"],
            ))
            for idx_rank, r in enumerate(ordered_rows, start=1):
                c.execute("UPDATE slot_candidates SET rank = ? WHERE id = ?", (idx_rank, r["id"]))

            if matched_ids:
                c.execute("UPDATE slots SET status = 'has_candidates' WHERE id = ? AND status = 'pending'", (slot_id,))
            conn.commit()

        return {
            "added_count": len(matched_ids),
            "matched_ids": matched_ids,
            "queries": queries_tried,
            "first_sound_id": matched_ids[0] if matched_ids else None,
        }

    def import_url_into_slot(self, slot_id: str, url: str) -> Dict[str, Any]:
        """Downloads sound(s) from a Freesound/LotsOfSounds URL and inserts at top of slot queue."""
        items = self.fetch_sounds_from_url(url, max_items=18)
        downloaded: List[Dict[str, Any]] = []
        to_dl: List[Dict[str, Any]] = []
        for it in items:
            if "_already_downloaded" in it:
                downloaded.append(it["_already_downloaded"])
            else:
                to_dl.append(it)
        if to_dl:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for dl in pool.map(self.download_direct_audio, to_dl):
                    if dl:
                        downloaded.append(dl)
        if not downloaded:
            raise ValueError("해당 URL에서 오디오 파일을 다운로드하지 못했습니다.")

        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT clap_prompts, name FROM slots WHERE id = ?", (slot_id,))
            slot_row = c.fetchone()
            if not slot_row:
                raise ValueError("Slot not found")
            clap_prompts = json.loads(slot_row["clap_prompts"] or "[]")
            text_emb = self.compute_text_embedding(clap_prompts, fallback_text=slot_row["name"] or "sound effect")

            imported_ids: List[str] = []
            # Shift existing ranks down so newly imported URL items sit cleanly at top
            c.execute("UPDATE slot_candidates SET rank = rank + ? WHERE slot_id = ?", (len(downloaded), slot_id))
            for idx, snd in enumerate(downloaded, start=1):
                a_emb = self.compute_audio_embedding(snd["file_path"])
                sim = float((a_emb @ text_emb.T).item()) if a_emb is not None else 0.75
                c.execute("""
                INSERT INTO sounds (id, source, source_id, name, file_path, duration, license_type, author, source_url, tags, sha256, norm_gain)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    file_path=excluded.file_path,
                    duration=excluded.duration,
                    license_type=excluded.license_type,
                    author=excluded.author,
                    source_url=excluded.source_url,
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
                    json.dumps(snd.get("tags") or [], ensure_ascii=False),
                    snd["sha256"],
                    snd["norm_gain"],
                ))
                # Explicit URL import revives if previously skipped
                c.execute("""
                INSERT INTO slot_candidates (slot_id, sound_id, score, rank, status)
                VALUES (?, ?, ?, ?, 'pending')
                ON CONFLICT(slot_id, sound_id) DO UPDATE SET
                    status=CASE WHEN slot_candidates.status = 'skipped' THEN 'pending' ELSE slot_candidates.status END,
                    score=excluded.score,
                    rank=excluded.rank;
                """, (slot_id, snd["id"], sim, idx))
                imported_ids.append(snd["id"])

            c.execute("UPDATE slots SET status = 'has_candidates' WHERE id = ? AND status = 'pending'", (slot_id,))
            conn.commit()

        return {
            "imported_count": len(imported_ids),
            "imported_ids": imported_ids,
            "first_sound_id": imported_ids[0] if imported_ids else None,
        }

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

        # 3. Search LotsOfSounds online (+ Freesound CC0 if needed so every round discovers fresh candidates)
        existing_ids = {r["sound_id"] for r in all_slot_rows}
        seen_ids = set(existing_ids) | skipped_ids | {reference_sound_id}
        new_raw_items = []
        fs_raw_items = []
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

        if len(new_raw_items) < 6 and queries:
            for fs_item in self.search_freesound(queries[0], pages=1, cc0_only=True):
                sid = fs_item.get("id")
                if not sid or sid in seen_ids:
                    continue
                if max_duration and max_duration > 0 and fs_item.get("duration", 0.0) > max_duration:
                    continue
                seen_ids.add(sid)
                fs_raw_items.append(fs_item)

        downloaded_new = []
        targets = new_raw_items[:6]
        fs_targets = fs_raw_items[: max(0, 8 - len(targets))]
        if targets or fs_targets:
            with ThreadPoolExecutor(max_workers=6) as pool:
                if targets:
                    for dl in pool.map(self.download_sound, targets):
                        if dl:
                            downloaded_new.append(dl)
                if fs_targets:
                    for dl in pool.map(self.download_direct_audio, fs_targets):
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
