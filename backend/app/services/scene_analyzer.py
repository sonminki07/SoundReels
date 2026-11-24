"""
SoundReels Video & GIF Scene Analyzer
- Extracts representative keyframes and motion dynamics from trimmed video/GIF scenes
- Hybrid Engine:
  1) Gemini Vision REST API (when GEMINI_API_KEY is configured via .env or UI settings)
  2) Local Zero-Shot CLIP ViT-B/32 + Motion Analyzer on CPU (zero VRAM conflict with CUDA CLAP)
- Produces SFX-tailored search tags, CLAP prompts, Korean summary, and recommended duration
"""

import io
import os
import json
import base64
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import cv2
import numpy as np
import torch
from PIL import Image, ImageSequence
from transformers import CLIPModel, CLIPProcessor


# Visual Scene -> Game SFX Concept Bank for Local CLIP Zero-Shot Matching
SCENE_SFX_CONCEPTS: List[Dict[str, str]] = [
    {"visual": "a bright fiery explosion, blast, fireball, or smoke burst", "tag": "explosion", "prompt": "punchy fire explosion blast debris boom", "ko": "폭발/화염"},
    {"visual": "a sword slash, blade swing, knife cut, or sharp melee strike", "tag": "slash", "prompt": "sharp metallic sword blade slash whoosh impact", "ko": "칼/베기"},
    {"visual": "a glowing laser beam, sci-fi gun shot, energy projectile, or muzzle flash", "tag": "laser", "prompt": "sci-fi energy laser projectile shot zap", "ko": "레이저/발사"},
    {"visual": "electric sparks, lightning bolts, thunder, or electrical shock", "tag": "electric", "prompt": "crackling electric spark lightning shock zap", "ko": "전기/스파크"},
    {"visual": "water splash, droplets, liquid spray, rain, or underwater bubbles", "tag": "splash", "prompt": "water splash liquid droplets spray", "ko": "물/액체"},
    {"visual": "pressurized mist spray, aerosol burst, steam puff, or gas hiss", "tag": "spray", "prompt": "pressurized aerosol mist spray hiss puff", "ko": "분사/스프레이"},
    {"visual": "fast wind rush, motion blur dash, air swoosh, or speed lines", "tag": "whoosh", "prompt": "fast airy wind dash swoosh whoosh", "ko": "바람/대시"},
    {"visual": "heavy impact, collision, punch, hit spark, or target strike", "tag": "impact", "prompt": "crisp solid collision impact punch hit thud", "ko": "타격/충돌"},
    {"visual": "an elevator door or heavy metallic door opening or sliding shut", "tag": "door", "prompt": "heavy metallic elevator door mechanical slide clunk", "ko": "문/개폐"},
    {"visual": "mechanical gears, machinery, robot servo, or industrial platform moving", "tag": "mechanical", "prompt": "heavy mechanical gear servo motor hum", "ko": "기계/장치"},
    {"visual": "a character walking, running, jumping, or footsteps on ground", "tag": "footstep", "prompt": "character footsteps walking running floor impact", "ko": "발걸음/이동"},
    {"visual": "a game UI button, menu tab, popup window, inventory grid, or cursor click", "tag": "click", "prompt": "clean crisp UI button click menu popup", "ko": "UI 클릭/메뉴"},
    {"visual": "a glowing notification bell, chime icon, level up banner, or achievement popup", "tag": "chime", "prompt": "bright clear notification bell chime ding", "ko": "알림/벨"},
    {"visual": "magical sparkles, healing aura, spell casting particles, or fairy dust", "tag": "magic", "prompt": "shimmering magic sparkle spell cast chime", "ko": "마법/이펙트"},
    {"visual": "gold coins, gems, star pickup, chest reward, or loot drop", "tag": "coin", "prompt": "bright metallic coin treasure pickup chime", "ko": "코인/보상"},
    {"visual": "wooden crate, wooden door, plank hit, or forest tree breaking", "tag": "wood", "prompt": "wooden crate impact knock crack thud", "ko": "목재/상자"},
    {"visual": "shattering glass, ice crystals, frozen shard, or brittle break", "tag": "glass", "prompt": "sharp glass ice crystal shatter break tinkle", "ko": "유리/얼음 파편"},
    {"visual": "metallic shield block, steel armor clang, anvil, or metal pipe", "tag": "metal", "prompt": "heavy steel metallic clang ring impact", "ko": "금속/방어"},
    {"visual": "heavy rocks, boulders crumbling, earth tremor, or stone slam", "tag": "rock", "prompt": "heavy stone boulder rumble impact crumble", "ko": "바위/진동"},
    {"visual": "fire flames burning, torch flicker, campfire, or lava glow", "tag": "fire", "prompt": "crackling fire flames burning whoosh", "ko": "불꽃/연소"},
    {"visual": "bushes, grass leaves, trees shaking, or forest foliage rustling", "tag": "rustle", "prompt": "leaves bush grass foliage rustling", "ko": "수풀/풀잎"},
    {"visual": "cookie, biscuit, snack, or food biting and chewing", "tag": "crunch", "prompt": "crisp dry crunchy bite snack chewing", "ko": "음식/바삭함"},
    {"visual": "a monster, zombie, beast, boss roar, or scary dark creature", "tag": "growl", "prompt": "deep creature monster growl roar snarl", "ko": "몬스터/크리처"},
    {"visual": "warning siren, flashing red alert icon, danger indicator, or timer alarm", "tag": "alarm", "prompt": "urgent electronic warning alarm siren beep", "ko": "경고/사이렌"},
    {"visual": "dark spooky shadow, ghost, horror hallway, or eerie atmosphere", "tag": "spooky", "prompt": "eerie dark horror ghost ambience", "ko": "공포/어두운 분위기"},
    {"visual": "paper note, book page, card flip, or parchment scroll", "tag": "paper", "prompt": "crisp paper page card flip rustle", "ko": "종이/카드"},
    {"visual": "computer terminal, keyboard typing, hacking screen, or digital data", "tag": "typing", "prompt": "digital terminal keyboard typing electronic beep", "ko": "터미널/타이핑"},
    {"visual": "arrow flying from bow, dart throw, or whistling projectile", "tag": "arrow", "prompt": "fast whistling arrow dart flyby thwack", "ko": "화살/투척"},
]


def _encode_pil_jpeg_b64(img: Image.Image, max_dim: int = 768) -> str:
    """Resizes PIL image safely and encodes as JPEG base64 string."""
    copy = img.convert("RGB")
    w, h = copy.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        copy = copy.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def extract_scene_visuals(
    media_path: Path,
    media_type: str,
    scene_in: float = 0.0,
    scene_out: float = 0.0,
    max_frames: int = 5,
) -> Dict[str, Any]:
    """Extracts trimmed keyframes, effective duration, and motion dynamics from GIF or Video."""
    frames: List[Image.Image] = []
    total_duration = 0.0

    if media_type == "gif":
        with Image.open(media_path) as im:
            all_frames: List[Tuple[float, Image.Image]] = []
            t_acc = 0.0
            for raw_frame in ImageSequence.Iterator(im):
                dur_ms = float(raw_frame.info.get("duration", 100) or 100)
                if dur_ms < 20:
                    dur_ms = 100.0
                all_frames.append((t_acc, raw_frame.convert("RGB")))
                t_acc += dur_ms / 1000.0
            total_duration = max(t_acc, 0.1)

            start_t = max(0.0, float(scene_in or 0.0))
            end_t = float(scene_out) if (scene_out and scene_out > start_t) else total_duration
            end_t = min(total_duration, end_t)

            in_range = [fr for ts, fr in all_frames if start_t <= ts <= end_t]
            if not in_range and all_frames:
                in_range = [fr for _, fr in all_frames]
            if in_range:
                indices = np.linspace(0, len(in_range) - 1, min(max_frames, len(in_range)), dtype=int)
                frames = [in_range[i] for i in indices]
            effective_dur = max(0.1, end_t - start_t)
    else:
        cap = cv2.VideoCapture(str(media_path))
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
            if fps <= 0.1 or fps > 240:
                fps = 30.0
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            total_duration = (frame_count / fps) if frame_count > 0 else 2.0

            start_t = max(0.0, float(scene_in or 0.0))
            end_t = float(scene_out) if (scene_out and scene_out > start_t) else total_duration
            end_t = min(total_duration, end_t)
            effective_dur = max(0.1, end_t - start_t)

            if frame_count > 0:
                sample_times = np.linspace(start_t, max(start_t, end_t - 0.02), max_frames)
                for ts in sample_times:
                    target_idx = min(frame_count - 1, max(0, int(ts * fps)))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
                    ok, bgr = cap.read()
                    if ok and bgr is not None:
                        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                        frames.append(Image.fromarray(rgb))
        finally:
            cap.release()

    if not frames:
        raise ValueError("Unable to extract frames from uploaded scene media")

    # Motion analysis across consecutive grayscale thumbnails
    diffs: List[float] = []
    prev_arr: Optional[np.ndarray] = None
    for fr in frames:
        thumb = np.asarray(fr.resize((96, 54)).convert("L"), dtype=np.float32) / 255.0
        if prev_arr is not None:
            diffs.append(float(np.mean(np.abs(thumb - prev_arr))))
        prev_arr = thumb

    mean_diff = float(np.mean(diffs)) if diffs else 0.0
    max_diff = float(np.max(diffs)) if diffs else 0.0
    peak_ratio = max_diff / (mean_diff + 1e-6) if diffs else 1.0

    if effective_dur <= 1.2 or (peak_ratio >= 1.65 and max_diff >= 0.03):
        motion_profile = "impulsive"
    elif mean_diff >= 0.025:
        motion_profile = "sustained"
    else:
        motion_profile = "subtle"

    return {
        "frames": frames,
        "total_duration": round(total_duration, 2),
        "effective_duration": round(effective_dur, 2),
        "motion_profile": motion_profile,
        "mean_motion": round(mean_diff, 4),
        "peak_motion": round(max_diff, 4),
    }


class SceneAnalyzer:
    """Hybrid video/GIF scene analyzer (Gemini Vision + Local CPU CLIP fallback)."""

    def __init__(self):
        self._clip_model: Optional[CLIPModel] = None
        self._clip_processor: Optional[CLIPProcessor] = None
        self._concept_text_embs: Optional[torch.Tensor] = None

    def _ensure_clip_loaded(self):
        """Lazy-loads safetensors CLIP ViT-B/32 on CPU to preserve GTX 1060 3GB VRAM for CLAP."""
        if self._clip_model is not None:
            return
        model_id = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"
        print(f"[SceneAnalyzer] Loading local CLIP vision model ({model_id}) on CPU...")
        self._clip_processor = CLIPProcessor.from_pretrained(model_id, use_fast=False)
        self._clip_model = CLIPModel.from_pretrained(model_id, use_safetensors=True).to("cpu").eval()

        texts = [c["visual"] for c in SCENE_SFX_CONCEPTS]
        inputs = self._clip_processor(text=texts, return_tensors="pt", padding=True)
        with torch.no_grad():
            embs = self._clip_model.get_text_features(**inputs)
            embs = embs / embs.norm(dim=-1, keepdim=True)
        self._concept_text_embs = embs
        print("[SceneAnalyzer] Local CLIP ready.")

    def _analyze_with_gemini(
        self,
        api_key: str,
        frames: List[Image.Image],
        slot: Dict[str, Any],
        effective_dur: float,
        motion_profile: str,
    ) -> Optional[Dict[str, Any]]:
        """Calls Gemini Vision REST API for rich video-to-SFX prompt generation."""
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        sample_frames = frames[:4]
        parts: List[Dict[str, Any]] = []
        for fr in sample_frames:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": _encode_pil_jpeg_b64(fr, max_dim=768),
                }
            })

        instruction = (
            "You are an expert game audio & Foley sound designer.\n"
            "Analyze these sequential video/GIF frames from a game scene and generate optimal English SFX search queries "
            "and CLAP audio-matching descriptions.\n\n"
            f"Slot Name (Korean): {slot.get('name', '')}\n"
            f"Slot Memo (Korean): {slot.get('memo', '')}\n"
            f"Trimmed Scene Duration: {effective_dur:.2f}s (Motion profile: {motion_profile})\n\n"
            "Return ONLY valid JSON with these exact keys:\n"
            '{\n'
            '  "summary_ko": "장면에서 보이는 핵심 동작/재질/분위기 한국어 1줄 요약 (25자 이내)",\n'
            '  "search_tags": ["short_keyword_1", "short_keyword_2", "short_keyword_3"],\n'
            '  "clap_prompts": ["detailed sonic texture prompt 1", "detailed sonic prompt 2", "detailed sonic prompt 3"],\n'
            '  "recommended_max_dur": 2.5\n'
            '}\n'
            "Rules:\n"
            "- search_tags: 3 to 4 single English keywords or concise 2-word sound terms suitable for CC0 SFX search.\n"
            "- clap_prompts: 3 vivid English sound-effect descriptions matching material, impact energy, and speed.\n"
            "- recommended_max_dur: float >= 0.8 reflecting scene duration with slight tail room."
        )
        parts.append({"text": instruction})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.25,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            tags = [str(t).strip() for t in (parsed.get("search_tags") or []) if str(t).strip()][:5]
            prompts = [str(p).strip() for p in (parsed.get("clap_prompts") or []) if str(p).strip()][:4]
            if not tags or not prompts:
                return None
            rec_dur = float(parsed.get("recommended_max_dur") or max(1.0, effective_dur * 1.35))
            return {
                "engine": "gemini",
                "summary_ko": str(parsed.get("summary_ko") or "Gemini 영상 장면 분석 완료").strip(),
                "tags": tags,
                "prompts": prompts,
                "recommended_max_dur": round(max(0.8, min(15.0, rec_dur)), 1),
            }
        except Exception as e:
            print(f"[SceneAnalyzer] Gemini Vision fallback triggered ({e})")
            return None

    def _analyze_locally(
        self,
        frames: List[Image.Image],
        slot: Dict[str, Any],
        effective_dur: float,
        motion_profile: str,
    ) -> Dict[str, Any]:
        """Runs local CPU CLIP concept scoring + motion dynamics analysis."""
        self._ensure_clip_loaded()
        inputs = self._clip_processor(images=frames, return_tensors="pt")
        with torch.no_grad():
            img_embs = self._clip_model.get_image_features(**inputs)
            img_embs = img_embs / img_embs.norm(dim=-1, keepdim=True)
            mean_img_emb = img_embs.mean(dim=0, keepdim=True)
            mean_img_emb = mean_img_emb / mean_img_emb.norm(dim=-1, keepdim=True)
            sims = (mean_img_emb @ self._concept_text_embs.T).squeeze(0)

        top_indices = torch.topk(sims, k=min(4, len(SCENE_SFX_CONCEPTS))).indices.tolist()
        matched = [SCENE_SFX_CONCEPTS[idx] for idx in top_indices]

        tags: List[str] = []
        prompts: List[str] = []
        ko_labels: List[str] = []

        motion_suffix = {
            "impulsive": "short punchy crisp attack",
            "sustained": "continuous loop ambience texture",
            "subtle": "clean subtle soft cue",
        }.get(motion_profile, "")

        for item in matched[:3]:
            if item["tag"] not in tags:
                tags.append(item["tag"])
            prompts.append(f"{item['prompt']} {motion_suffix}".strip())
            if item["ko"] not in ko_labels:
                ko_labels.append(item["ko"])

        motion_ko = {
            "impulsive": "짧은 임팩트",
            "sustained": "지속 움직임",
            "subtle": "잔잔한 변화",
        }.get(motion_profile, "일반")

        summary_ko = f"{' · '.join(ko_labels[:2])} ({motion_ko}, {effective_dur:.1f}s)"
        rec_max_dur = round(max(1.0, min(15.0, effective_dur * 1.4)), 1)

        return {
            "engine": "local_clip",
            "summary_ko": summary_ko,
            "tags": tags,
            "prompts": prompts,
            "recommended_max_dur": rec_max_dur,
        }

    def analyze_and_merge(
        self,
        media_path: Path,
        media_type: str,
        slot: Dict[str, Any],
        scene_in: float = 0.0,
        scene_out: float = 0.0,
        gemini_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyzes uploaded scene and merges findings with existing slot context."""
        visuals = extract_scene_visuals(media_path, media_type, scene_in=scene_in, scene_out=scene_out)
        frames = visuals["frames"]
        eff_dur = visuals["effective_duration"]
        motion_profile = visuals["motion_profile"]

        result: Optional[Dict[str, Any]] = None
        api_key = (gemini_api_key or os.getenv("GEMINI_API_KEY") or "").strip()
        if api_key and api_key != "your_gemini_api_key_here":
            result = self._analyze_with_gemini(api_key, frames, slot, eff_dur, motion_profile)

        if result is None:
            result = self._analyze_locally(frames, slot, eff_dur, motion_profile)

        base_tags = json.loads(slot.get("search_tags") or "[]")
        base_prompts = json.loads(slot.get("clap_prompts") or "[]")

        # Merge unique search tags: prioritize visual findings + keep top 2 original slot tags
        merged_tags: List[str] = []
        for t in list(result["tags"]) + list(base_tags[:2]):
            clean_t = str(t).strip().lower()
            if clean_t and clean_t not in merged_tags:
                merged_tags.append(clean_t)
        merged_tags = merged_tags[:5]

        # Merge CLAP prompts cleanly: visual scene prompts + original slot prompts
        merged_prompts: List[str] = []
        for p in list(result["prompts"]) + list(base_prompts[:2]):
            clean_p = str(p).strip()
            if clean_p and clean_p not in merged_prompts:
                merged_prompts.append(clean_p)
        merged_prompts = merged_prompts[:6]

        return {
            "engine": result["engine"],
            "summary_ko": result["summary_ko"],
            "effective_duration": eff_dur,
            "motion_profile": motion_profile,
            "recommended_max_dur": result["recommended_max_dur"],
            "visual_tags": result["tags"],
            "merged_tags": merged_tags,
            "merged_prompts": merged_prompts,
        }
