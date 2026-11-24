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

from backend.app.core.db import get_db_context, AUDIO_CACHE_DIR, resolve_audio_path
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


BAD_TONE_NOISE_TOKENS = {
    "sine wave", "test tone", "oscillator", "purple noise", "blue noise", "pink noise",
    "white noise", "brown noise", "grey noise", "frequency sweep", "dtmf", "dial tone",
    "calibration", "tinnitus", "ear ringing", "1000hz", "440hz", "1khz", "hz tone",
    "sample pack", "sound pack", "sfx pack", "loop pack", "compilation", "demo reel",
}
BAD_SPEECH_TOKENS = {
    "speech", "talking", "voiceover", "voice over", "narration", "narrator",
    "podcast", "interview", "dialogue", "conversation", "spoken word", "human voice",
    "crowd chatter", "singing", "lyrics", "acapella", "announcer", "laughing",
    "reading aloud", "monologue", "commentary", "whispering", "chatter",
}
BAD_MUSIC_TOKENS = {
    "opening theme", "background music", "bgm", "soundtrack", "full song",
    "chord progression", "piano theme", "piano solo", "orchestral theme", "synthwave",
    "ambient music", "chillout", "relaxation track",
}
VOICE_OK_SLOT_IDS = {"sfx_player_hurt", "sfx_mob_death", "sfx_mob_male_swing"}
TONAL_OK_SLOT_IDS = {"sfx_elevator_bell", "sfx_ui_tab_open", "sfx_ui_tab_close", "sfx_elevator_button", "sfx_shield_bubble_activate"}
GENERIC_SIMILAR_STOP_WORDS = {
    "sound", "audio", "sfx", "effect", "effects", "tags", "tag", "cc0", "freesound",
    "loop", "file", "noise", "close", "open", "down", "up", "slowed", "fast",
    "high", "low", "heavy", "light", "big", "small", "good", "bad", "free", "game",
    "alt", "ver", "version", "mix", "part", "one", "two", "new", "old", "short", "long",
    "recording", "field-recording", "field", "ergonomic", "psychoactive", "relax",
}


class SoundScout:
    def __init__(self, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() and device == "cuda" else "cpu"
        self.model_id = "laion/clap-htsat-unfused"
        print(f"[SoundScout] Loading CLAP model ({self.model_id}) on {self.device.upper()}...")
        self.processor = ClapProcessor.from_pretrained(self.model_id)
        self.model = ClapModel.from_pretrained(self.model_id, use_safetensors=True).to(self.device).eval()
        self._audio_emb_cache: Dict[str, torch.Tensor] = {}
        self._slot_emb_cache: Dict[str, Dict[str, Any]] = {}
        self._similar_history: Dict[str, Dict[str, Any]] = {}
        self._neg_speech_emb = self.compute_text_embedding([
            "human speaking voice talking narration conversation podcast",
            "people talking crowd speech chatter vocals",
        ])
        self._neg_tone_emb = self.compute_text_embedding([
            "annoying continuous high pitched sine wave beep test tone oscillator",
            "harsh static white noise hiss buzz calibration tone",
        ])
        self._neg_music_emb = self.compute_text_embedding([
            "background music song melody chords piano soundtrack",
        ])
        print("[SoundScout] CLAP Model ready.")

    def _get_effective_max_duration(
        self,
        slot: Dict[str, Any],
        explicit_max_dur: Optional[float] = None,
        ref_dur: Optional[float] = None,
    ) -> float:
        """Computes a sane maximum duration ceiling so 5-minute tracks never leak into SFX queues."""
        if explicit_max_dur and explicit_max_dur > 0:
            return float(explicit_max_dur)
        slot_max = float(slot.get("max_dur") or 3.0)
        cat = str(slot.get("category") or "").lower()
        if cat == "environment":
            eff = min(25.0, max(12.0, slot_max * 4.0))
        else:
            eff = min(20.0, max(10.0, slot_max * 3.5))
        if ref_dur and ref_dur > 0:
            eff = min(eff, max(8.0, float(ref_dur) * 3.5))
        return eff

    def _is_metadata_blocked(self, item: Dict[str, Any], allow_voice: bool = False) -> Optional[str]:
        """Rejects speech, continuous test tones/colored noise, sample packs & BGM by metadata."""
        name = str(item.get("name") or "").lower()
        tags = item.get("tags") or []
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = [tags]
        tags_str = " ".join(str(t).lower() for t in tags)
        desc = str(item.get("description") or "").lower()
        hay = f"{name} {tags_str} {desc}"

        for kw in BAD_TONE_NOISE_TOKENS:
            if kw in hay:
                return f"metadata_tone:{kw}"
        if not allow_voice:
            for kw in BAD_SPEECH_TOKENS:
                if kw in hay:
                    return f"metadata_speech:{kw}"
        for kw in BAD_MUSIC_TOKENS:
            if kw in hay:
                return f"metadata_music:{kw}"
        return None

    def _analyze_audio_artifact(self, file_path: str, allow_tonal: bool = False) -> Optional[str]:
        """Detects silent, heavily clipped, or stationary continuous pure-sine beep test tones."""
        try:
            import numpy as np
            y, sr = librosa.load(file_path, sr=22050, mono=True, duration=15.0)
            if len(y) < int(0.03 * sr):
                return "too_short"
            abs_y = np.abs(y)
            peak = float(np.max(abs_y))
            if peak < 1e-4:
                return "silence"
            clip_ratio = float(np.mean(abs_y >= 0.995))
            if clip_ratio > 0.30:
                return "heavy_clipping"

            dur = len(y) / float(sr)
            if dur >= 1.5 and len(y) >= 4096:
                rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=512)[0]
                rms_mean = float(np.mean(rms))
                rms_cv = float(np.std(rms) / (rms_mean + 1e-8))
                S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
                crest = float(np.mean(np.max(S, axis=0) / (np.mean(S, axis=0) + 1e-8)))
                flat = float(np.median(librosa.feature.spectral_flatness(S=S + 1e-10)))
                # Continuous flat single-frequency sine beep / calibration tone (no natural decay)
                if not allow_tonal and crest > 45.0 and rms_cv < 0.16 and flat < 0.005:
                    return "stationary_sine_beep"
                # Extreme non-decaying continuous static/white/colored noise hiss over long span
                if dur >= 8.0 and flat > 0.15 and rms_cv < 0.12:
                    return "stationary_noise_hiss"
        except Exception:
            pass
        return None

    def _get_slot_embeddings(self, slot: Dict[str, Any]) -> Dict[str, Any]:
        sid = str(slot.get("id") or "")
        cached = self._slot_emb_cache.get(sid)
        if cached is not None:
            return cached
        prompts = slot.get("clap_prompts") or []
        if isinstance(prompts, str):
            prompts = json.loads(prompts or "[]")
        negs = slot.get("negative_prompts") or []
        if isinstance(negs, str):
            negs = json.loads(negs or "[]")
        pos_emb = self.compute_text_embedding(list(prompts) + [str(slot.get("name") or "")], fallback_text="game sound effect")
        neg_emb = self.compute_text_embedding(list(negs)) if negs else None
        bundle = {"pos_emb": pos_emb, "neg_emb": neg_emb}
        if sid:
            self._slot_emb_cache[sid] = bundle
        return bundle

    def _evaluate_candidate_for_slot(
        self,
        snd: Dict[str, Any],
        slot: Dict[str, Any],
        target_emb: torch.Tensor,
        pos_floor: float = 0.16,
        ref_dur: Optional[float] = None,
        explicit_max_dur: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluates duration, metadata, DSP sanity, and contrastive CLAP quality against negatives."""
        slot_id = str(slot.get("id") or "")
        allow_voice = slot_id in VOICE_OK_SLOT_IDS
        allow_tonal = slot_id in TONAL_OK_SLOT_IDS

        dur = float(snd.get("duration") or 0.0)
        eff_max_dur = self._get_effective_max_duration(slot, explicit_max_dur=explicit_max_dur, ref_dur=ref_dur)
        if dur < 0.04 or dur > eff_max_dur:
            return {"ok": False, "reason": "duration"}

        meta_reason = self._is_metadata_blocked(snd, allow_voice=allow_voice)
        if meta_reason:
            return {"ok": False, "reason": meta_reason}

        fp_resolved = resolve_audio_path(snd.get("id") or snd.get("sound_id"), snd.get("file_path"))
        if not fp_resolved.exists():
            return {"ok": False, "reason": "missing_file"}
        fp = str(fp_resolved)

        dsp_reason = self._analyze_audio_artifact(fp, allow_tonal=allow_tonal)
        if dsp_reason:
            return {"ok": False, "reason": dsp_reason}

        a_emb = self.compute_audio_embedding(fp)
        if a_emb is None:
            return {"ok": False, "reason": "embedding_failed"}

        slot_embs = self._get_slot_embeddings(slot)
        slot_pos_emb = slot_embs["pos_emb"]
        slot_neg_emb = slot_embs["neg_emb"]

        target_sim = float((a_emb @ target_emb.T).item())
        slot_pos_sim = float((a_emb @ slot_pos_emb.T).item())
        sp_sim = float((a_emb @ self._neg_speech_emb.T).item())
        tn_sim = float((a_emb @ self._neg_tone_emb.T).item())
        mu_sim = float((a_emb @ self._neg_music_emb.T).item())
        sneg_sim = float((a_emb @ slot_neg_emb.T).item()) if slot_neg_emb is not None else 0.0

        anchor_sim = max(target_sim, slot_pos_sim)
        if target_sim < pos_floor or slot_pos_sim < (pos_floor - 0.04):
            return {"ok": False, "reason": "low_similarity"}
        if not allow_voice and sp_sim > 0.23 and sp_sim > anchor_sim - 0.05:
            return {"ok": False, "reason": "speech_detected"}
        if tn_sim > 0.22 and tn_sim > anchor_sim - 0.08:
            return {"ok": False, "reason": "tone_or_noise_detected"}
        if mu_sim > 0.20 and mu_sim > anchor_sim - 0.04:
            return {"ok": False, "reason": "music_detected"}
        if slot_neg_emb is not None and sneg_sim > 0.26 and sneg_sim > slot_pos_sim + 0.04:
            return {"ok": False, "reason": "slot_negative_match"}

        slot_min = float(slot.get("min_dur") or 0.1)
        slot_max = float(slot.get("max_dur") or 3.0)
        if slot_min * 0.8 <= dur <= slot_max * 1.15:
            dur_bonus = 0.06
        elif dur <= slot_max * 2.0:
            dur_bonus = 0.02
        elif dur > slot_max * 2.5:
            dur_bonus = -0.04
        else:
            dur_bonus = 0.0

        final_score = (
            target_sim
            - (0.0 if allow_voice else 0.35 * max(0.0, sp_sim - 0.14))
            - 0.45 * max(0.0, tn_sim - 0.14)
            - 0.30 * max(0.0, mu_sim - 0.15)
            - 0.20 * max(0.0, sneg_sim - slot_pos_sim * 0.88)
            + dur_bonus
        )
        return {"ok": True, "score": float(final_score), "raw_sim": target_sim, "slot_sim": slot_pos_sim}

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
        """Loads audio (multi-window up to 25s) and returns normalized CLAP audio embedding."""
        key = str(file_path)
        if key in self._audio_emb_cache:
            return self._audio_emb_cache[key]
        try:
            audio, _ = librosa.load(file_path, sr=48000, mono=True, duration=25.0)
            if len(audio) == 0:
                return None
            # Split into start + mid windows when longer than 10.5s so mid-track speech/noise is heard
            win_samples = int(10.0 * 48000)
            if len(audio) > int(10.5 * 48000):
                mid_start = max(0, (len(audio) - win_samples) // 2)
                chunks = [audio[:win_samples], audio[mid_start : mid_start + win_samples]]
            else:
                chunks = [audio]

            embs = []
            for ch in chunks:
                inputs = self.processor(audio=ch, sampling_rate=48000, return_tensors="pt").to(self.device)
                with torch.no_grad():
                    e = self.model.get_audio_features(**inputs)
                    e = e / e.norm(dim=-1, keepdim=True)
                embs.append(e.cpu())
                del inputs, e

            res = embs[0] if len(embs) == 1 else torch.mean(torch.cat(embs, dim=0), dim=0, keepdim=True)
            res = res / res.norm(dim=-1, keepdim=True)
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
        eff_max_dur = self._get_effective_max_duration(slot, explicit_max_dur=max_duration)
        allow_voice = slot_id in VOICE_OK_SLOT_IDS

        # 1. Search across tags (LotsOfSounds + optional Freesound CC0, pre-filtering long/bad metadata!)
        raw_los = []
        raw_fs = []
        seen_ids = set(skipped_ids)
        for tag in search_tags:
            items = self.search_lotsofsounds(tag, limit=max_candidates_per_query)
            for item in items:
                if item["id"] in seen_ids:
                    continue
                item_dur = float(item.get("duration") or 0.0)
                if item_dur < 0.04 or item_dur > eff_max_dur:
                    continue
                if self._is_metadata_blocked(item, allow_voice=allow_voice):
                    continue
                seen_ids.add(item["id"])
                raw_los.append(item)
            if include_freesound:
                for fs_item in self.search_freesound(tag, pages=1, cc0_only=True):
                    if fs_item["id"] in seen_ids:
                        continue
                    fs_dur = float(fs_item.get("duration") or 0.0)
                    if fs_dur < 0.04 or fs_dur > eff_max_dur:
                        continue
                    if self._is_metadata_blocked(fs_item, allow_voice=allow_voice):
                        continue
                    seen_ids.add(fs_item["id"])
                    raw_fs.append(fs_item)
            time.sleep(0.02)

        if not raw_los and not raw_fs:
            return 0

        # 2. Download concurrently and apply full quality + contrastive CLAP gate
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

        target_emb = (
            self.compute_text_embedding(clap_prompts, fallback_text=slot.get("name") or "sound effect")
            if custom_clap_prompts
            else self._get_slot_embeddings(slot)["pos_emb"]
        )
        scored_candidates = []
        for snd in downloaded_sounds:
            if snd["id"] in skipped_ids:
                continue
            ev = self._evaluate_candidate_for_slot(
                snd, slot, target_emb, pos_floor=0.17, explicit_max_dur=max_duration
            )
            if not ev["ok"]:
                continue
            scored_candidates.append({
                "sound": snd,
                "score": ev["score"],
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
            slot = dict(slot_row)
            c.execute("SELECT sound_id FROM slot_candidates WHERE slot_id = ? AND status = 'skipped'", (slot_id,))
            skipped_ids = {r["sound_id"] for r in c.fetchall()}
            c.execute("SELECT * FROM sounds")
            local_pool = [dict(r) for r in c.fetchall()]

        allow_voice = slot_id in VOICE_OK_SLOT_IDS
        allow_tonal = slot_id in TONAL_OK_SLOT_IDS
        dur_cap = float(max_duration) if (max_duration and max_duration > 0) else 25.0
        query_tokens = [t.lower() for t in terms if len(t) >= 2] or [search_str.lower()]
        seen_ids = set(skipped_ids)
        ready_sounds: List[Dict[str, Any]] = []

        # 1. Local cache name/tag matches first (instant, quality-checked)
        for p in local_pool:
            pid = p["id"]
            if pid in seen_ids:
                continue
            pdur = float(p.get("duration") or 0.0)
            if pdur < 0.04 or pdur > dur_cap:
                continue
            if self._is_metadata_blocked(p, allow_voice=allow_voice):
                continue
            fp_resolved = resolve_audio_path(pid, p.get("file_path"))
            if not fp_resolved.exists():
                continue
            hay = f"{p.get('name', '')} {p.get('tags', '')}".lower()
            if any(tok in hay for tok in query_tokens):
                seen_ids.add(pid)
                ready_sounds.append({**p, "file_path": str(fp_resolved), "from_local_pool": True})

        # 2. Online LotsOfSounds + Freesound CC0 (smart paging + metadata & duration hygiene)
        raw_los: List[Dict[str, Any]] = []
        raw_fs: List[Dict[str, Any]] = []
        queries_tried = [search_str]
        for item in self.search_lotsofsounds(search_str, limit=12):
            sid = item.get("id")
            if not sid or sid in seen_ids or float(item.get("duration") or 0.0) > dur_cap:
                continue
            if self._is_metadata_blocked(item, allow_voice=allow_voice):
                continue
            seen_ids.add(sid)
            raw_los.append(item)
        for fs_item in self.search_freesound(search_str, pages=1, cc0_only=True):
            sid = fs_item.get("id")
            if not sid or sid in seen_ids or float(fs_item.get("duration") or 0.0) > dur_cap:
                continue
            if self._is_metadata_blocked(fs_item, allow_voice=allow_voice):
                continue
            seen_ids.add(sid)
            raw_fs.append(fs_item)

        if len(raw_los) + len(raw_fs) < 12:
            for q in [t for t in terms[:2] if t != search_str]:
                queries_tried.append(q)
                for item in self.search_lotsofsounds(q, limit=12):
                    sid = item.get("id")
                    if not sid or sid in seen_ids or float(item.get("duration") or 0.0) > dur_cap:
                        continue
                    if self._is_metadata_blocked(item, allow_voice=allow_voice):
                        continue
                    seen_ids.add(sid)
                    raw_los.append(item)
                for fs_item in self.search_freesound(q, pages=1, cc0_only=True):
                    sid = fs_item.get("id")
                    if not sid or sid in seen_ids or float(fs_item.get("duration") or 0.0) > dur_cap:
                        continue
                    if self._is_metadata_blocked(fs_item, allow_voice=allow_voice):
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

        # Score against user query text with contrastive speech/tone/music rejection
        text_emb = self.compute_text_embedding([search_str] + terms, fallback_text=search_str)
        scored: List[Dict[str, Any]] = []
        filtered_out = 0
        for snd in ready_sounds:
            if snd["id"] in skipped_ids:
                continue
            sdur = float(snd.get("duration") or 0.0)
            if sdur < 0.04 or sdur > dur_cap:
                filtered_out += 1
                continue
            if self._analyze_audio_artifact(snd["file_path"], allow_tonal=allow_tonal):
                filtered_out += 1
                continue
            a_emb = self.compute_audio_embedding(snd["file_path"])
            if a_emb is None:
                filtered_out += 1
                continue
            q_sim = float((a_emb @ text_emb.T).item())
            sp_sim = float((a_emb @ self._neg_speech_emb.T).item())
            tn_sim = float((a_emb @ self._neg_tone_emb.T).item())
            mu_sim = float((a_emb @ self._neg_music_emb.T).item())
            if q_sim < 0.14:
                filtered_out += 1
                continue
            if not allow_voice and sp_sim > 0.24 and sp_sim > q_sim - 0.04:
                filtered_out += 1
                continue
            if tn_sim > 0.23 and tn_sim > q_sim - 0.07:
                filtered_out += 1
                continue
            if mu_sim > 0.22 and mu_sim > q_sim - 0.03:
                filtered_out += 1
                continue
            final_q = q_sim - (0.0 if allow_voice else 0.30 * max(0.0, sp_sim - 0.15)) - 0.40 * max(0.0, tn_sim - 0.15) - 0.25 * max(0.0, mu_sim - 0.16)
            scored.append({"sound": snd, "score": float(final_q)})
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
            "filtered_out": filtered_out,
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
                return {"found_new": 0, "total_ranked": 0, "top_similar_index": 0, "queries_used": [], "round_no": round_no, "filtered_out": 0}
            ref_snd = dict(ref_row)

            c.execute("SELECT * FROM slots WHERE id = ?", (slot_id,))
            slot_row = c.fetchone()
            if not slot_row:
                return {"found_new": 0, "total_ranked": 0, "top_similar_index": 0, "queries_used": [], "round_no": round_no, "filtered_out": 0}
            slot = dict(slot_row)
            slot_tags = json.loads(slot.get("search_tags") or "[]")

            c.execute("""
            SELECT sc.id as cand_row_id, sc.sound_id, sc.status, snd.file_path, snd.name, snd.duration, snd.tags
            FROM slot_candidates sc
            JOIN sounds snd ON sc.sound_id = snd.id
            WHERE sc.slot_id = ?
            """, (slot_id,))
            all_slot_rows = [dict(r) for r in c.fetchall()]

        skipped_ids = {r["sound_id"] for r in all_slot_rows if r["status"] == "skipped"}
        existing_rows = [r for r in all_slot_rows if r["status"] != "skipped"]
        ref_dur = float(ref_snd.get("duration") or 0.0)
        eff_max_dur = self._get_effective_max_duration(slot, explicit_max_dur=max_duration, ref_dur=ref_dur)
        allow_voice = slot_id in VOICE_OK_SLOT_IDS

        # 1. Build Slot-Anchored Target Embedding (Prevents Acoustic Drift to Off-Topic Noise)
        slot_pos_emb = self._get_slot_embeddings(slot)["pos_emb"]
        ref_emb = None
        ref_fp = resolve_audio_path(reference_sound_id, ref_snd.get("file_path"))
        if ref_fp.exists():
            ref_emb = self.compute_audio_embedding(str(ref_fp))
        ref_tags = json.loads(ref_snd.get("tags") or "[]")
        if ref_emb is None:
            ref_emb = self.compute_text_embedding([ref_snd.get("name", "")] + ref_tags, fallback_text=slot.get("name") or "game sound effect")

        prompt_terms = _extract_english_prompt_terms(custom_prompt or "")
        if prompt_terms:
            prompt_phrase = " ".join(prompt_terms)
            prompt_emb = self.compute_text_embedding([prompt_phrase, f"{prompt_phrase} sound effect"], fallback_text=prompt_phrase)
            target_emb = 0.45 * ref_emb + 0.40 * prompt_emb + 0.15 * slot_pos_emb
            target_emb = target_emb / target_emb.norm(dim=-1, keepdim=True)
        else:
            target_emb = 0.72 * ref_emb + 0.28 * slot_pos_emb
            target_emb = target_emb / target_emb.norm(dim=-1, keepdim=True)

        # 2. Build CLEAN domain-anchored query pool (never query bare generic adjectives/verbs!)
        candidate_pool: List[str] = []
        core_slot_nouns = [str(st).strip().lower() for st in slot_tags if str(st).strip()]
        primary_noun = core_slot_nouns[0] if core_slot_nouns else "sfx"

        # Priority A: User prompt paired with slot noun + full prompt phrase
        if prompt_terms:
            if len(prompt_terms) >= 2:
                candidate_pool.append(" ".join(prompt_terms[:3]))
            for pt in prompt_terms[:3]:
                if pt in GENERIC_SIMILAR_STOP_WORDS and primary_noun:
                    paired = f"{pt} {primary_noun}"
                    if paired not in candidate_pool:
                        candidate_pool.append(paired)
                elif pt not in candidate_pool:
                    candidate_pool.append(pt)

        # Priority B: Slot's own curated tags FIRST so searches stay firmly on-topic
        for clean_st in core_slot_nouns:
            if clean_st not in candidate_pool:
                candidate_pool.append(clean_st)

        # Priority C: Clean reference sound title phrase & non-generic tags
        if ref_snd.get("name"):
            name_tokens = [
                w.strip().lower()
                for w in str(ref_snd["name"]).replace("-", " ").replace("_", " ").split()
                if len(w.strip()) >= 3 and w.strip().lower() not in GENERIC_SIMILAR_STOP_WORDS and not w.strip().isdigit()
            ]
            if name_tokens:
                filtered_name = " ".join(name_tokens[:2])
                if filtered_name not in candidate_pool:
                    candidate_pool.append(filtered_name)
        for t in ref_tags:
            clean = str(t).strip().lower().replace("-", " ")
            if (
                len(clean) >= 4
                and not clean.isdigit()
                and clean not in GENERIC_SIMILAR_STOP_WORDS
                and all(part not in GENERIC_SIMILAR_STOP_WORDS for part in clean.split())
                and clean not in candidate_pool
            ):
                candidate_pool.append(clean)

        # Priority D: Synonym expansions anchored cleanly to sound nouns
        for base_word in list(candidate_pool):
            for tok in base_word.split():
                if tok in GENERIC_SIMILAR_STOP_WORDS:
                    continue
                for syn in SFX_SYNONYM_MAP.get(tok, []):
                    if syn not in candidate_pool:
                        candidate_pool.append(syn)

        untried = [q for q in candidate_pool if q not in tried_queries]
        if not untried:
            tried_queries.clear()
            untried = candidate_pool

        queries = untried[:3]
        for q in queries:
            tried_queries.add(q)

        # 3. Search LotsOfSounds + Freesound CC0 with upfront duration & metadata hygiene gate
        existing_ids = {r["sound_id"] for r in all_slot_rows}
        seen_ids = set(existing_ids) | skipped_ids | {reference_sound_id}
        new_raw_items = []
        fs_raw_items = []
        filtered_out = 0
        for q in queries:
            for item in self.search_lotsofsounds(q, limit=12):
                sid = item.get("id")
                if not sid or sid in seen_ids:
                    continue
                idur = float(item.get("duration") or 0.0)
                if idur < 0.04 or idur > eff_max_dur or self._is_metadata_blocked(item, allow_voice=allow_voice):
                    filtered_out += 1
                    continue
                seen_ids.add(sid)
                new_raw_items.append(item)
            time.sleep(0.02)

        if len(new_raw_items) < 8 and queries:
            for fs_item in self.search_freesound(queries[0], pages=1, cc0_only=True):
                sid = fs_item.get("id")
                if not sid or sid in seen_ids:
                    continue
                fdur = float(fs_item.get("duration") or 0.0)
                if fdur < 0.04 or fdur > eff_max_dur or self._is_metadata_blocked(fs_item, allow_voice=allow_voice):
                    filtered_out += 1
                    continue
                seen_ids.add(sid)
                fs_raw_items.append(fs_item)

        downloaded_new = []
        targets = new_raw_items[:8]
        fs_targets = fs_raw_items[: max(0, 10 - len(targets))]
        if targets or fs_targets:
            with ThreadPoolExecutor(max_workers=10) as pool:
                if targets:
                    for dl in pool.map(self.download_sound, targets):
                        if dl:
                            downloaded_new.append(dl)
                if fs_targets:
                    for dl in pool.map(self.download_direct_audio, fs_targets):
                        if dl:
                            downloaded_new.append(dl)

        # 4. Evaluate newly downloaded sounds with FULL quality + contrastive CLAP gate
        scored_new = []
        for snd in downloaded_new:
            ev = self._evaluate_candidate_for_slot(
                snd, slot, target_emb, pos_floor=0.18, ref_dur=ref_dur, explicit_max_dur=max_duration
            )
            if not ev["ok"]:
                filtered_out += 1
                continue
            scored_new.append({"sound": snd, "score": ev["score"]})

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
                ev = self._evaluate_candidate_for_slot(
                    p_row, slot, target_emb, pos_floor=0.34, ref_dur=ref_dur, explicit_max_dur=max_duration
                )
                if not ev["ok"]:
                    continue
                local_matches.append({"sound": p_row, "score": ev["score"], "from_local_pool": True})
            local_matches.sort(key=lambda x: x["score"], reverse=True)
            scored_new.extend(local_matches[: max(1, 5 - len(scored_new))])

        # Re-score existing active candidates in this slot contrastively
        existing_scores: List[Dict[str, Any]] = []
        for ex in existing_rows:
            if ex["sound_id"] == reference_sound_id:
                continue
            ev = self._evaluate_candidate_for_slot(
                ex, slot, target_emb, pos_floor=-1.0, ref_dur=ref_dur, explicit_max_dur=max_duration
            )
            score_val = ev["score"] if ev["ok"] else -0.5
            existing_scores.append({"sound_id": ex["sound_id"], "score": score_val})

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
            "filtered_out": filtered_out,
            "total_ranked": total_ranked,
            "top_similar_index": 1 if total_ranked > 1 else 0,
            "queries_used": queries,
            "prompt_used": (custom_prompt or "").strip(),
        }

    def cleanup_existing_bad_candidates(self) -> Dict[str, Any]:
        """Moves existing pending (never liked/confirmed) bad, long, speech, or tone/noise rows to trash."""
        moved_count = 0
        reasons_count: Dict[str, int] = {}
        with get_db_context() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM slots")
            slots_map = {r["id"]: dict(r) for r in c.fetchall()}
            c.execute("""
            SELECT sc.id as cand_id, sc.slot_id, sc.sound_id, snd.*
            FROM slot_candidates sc
            JOIN sounds snd ON sc.sound_id = snd.id
            WHERE sc.status = 'pending'
            """)
            pending_rows = [dict(r) for r in c.fetchall()]

            to_skip_ids: List[int] = []
            touched_slots = set()
            for row in pending_rows:
                slot = slots_map.get(row["slot_id"])
                if not slot:
                    continue
                pos_emb = self._get_slot_embeddings(slot)["pos_emb"]
                ev = self._evaluate_candidate_for_slot(row, slot, pos_emb, pos_floor=0.14)
                if not ev["ok"]:
                    to_skip_ids.append(row["cand_id"])
                    touched_slots.add(row["slot_id"])
                    rsn = ev["reason"].split(":")[0]
                    reasons_count[rsn] = reasons_count.get(rsn, 0) + 1

            for cid in to_skip_ids:
                c.execute("UPDATE slot_candidates SET status = 'skipped' WHERE id = ?", (cid,))
            moved_count = len(to_skip_ids)

            for sid in touched_slots:
                c.execute("SELECT COUNT(*) as cnt FROM slot_candidates WHERE slot_id = ? AND status != 'skipped'", (sid,))
                if c.fetchone()["cnt"] == 0:
                    c.execute("UPDATE slots SET status = 'pending', review_passed = 0 WHERE id = ? AND status != 'confirmed'", (sid,))
            conn.commit()
        return {"moved_to_trash": moved_count, "reasons": reasons_count}

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
