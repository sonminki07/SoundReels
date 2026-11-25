import sqlite3
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import contextmanager

# Dynamic Base Directory (replaces hardcoded E:/SoundReels)
BASE_DIR = Path(__file__).resolve().parents[3]
DB_PATH = Path(os.getenv("SOUNDREELS_DB_PATH", BASE_DIR / "data" / "db" / "soundreels.db"))
AUDIO_CACHE_DIR = Path(os.getenv("SOUNDREELS_CACHE_DIR", BASE_DIR / "data" / "audio_cache"))
SCENE_CACHE_DIR = Path(os.getenv("SOUNDREELS_SCENE_DIR", BASE_DIR / "data" / "scene_cache"))


def resolve_audio_path(sound_id: Optional[str], stored_path: Optional[str] = None) -> Path:
    """Resolves cached audio path relative to current PC's AUDIO_CACHE_DIR (cross-PC portable)."""
    if sound_id:
        local_default = AUDIO_CACHE_DIR / f"{sound_id}.mp3"
        if local_default.exists():
            return local_default
    if stored_path:
        p = Path(stored_path)
        if p.exists():
            return p
        rel_by_name = AUDIO_CACHE_DIR / p.name
        if rel_by_name.exists():
            return rel_by_name
    if sound_id:
        return AUDIO_CACHE_DIR / f"{sound_id}.mp3"
    return Path(stored_path) if stored_path else AUDIO_CACHE_DIR


def resolve_scene_path(stored_path: Optional[str]) -> Optional[Path]:
    """Resolves attached scene media path relative to current PC's SCENE_CACHE_DIR."""
    if not stored_path:
        return None
    p = Path(stored_path)
    rel_local = SCENE_CACHE_DIR / p.name
    if rel_local.exists():
        return rel_local
    if p.exists():
        return p
    return rel_local

def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # High-concurrency WAL mode and busy timeout to avoid locks on Windows
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

@contextmanager
def get_db_context():
    """Safe context manager to prevent connection leaks on Windows."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

def _ensure_column(c: sqlite3.Cursor, table: str, column: str, definition: str):
    """Idempotently adds a column to an existing SQLite table."""
    c.execute(f"PRAGMA table_info({table});")
    existing = {row[1] for row in c.fetchall()}
    if column not in existing:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition};")

def init_db():
    SCENE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with get_db_context() as conn:
        c = conn.cursor()
        
        # 1. Projects
        c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            unity_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # 2. Slots
        c.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            memo TEXT,
            status TEXT DEFAULT 'pending', -- 'pending', 'in_progress', 'has_candidates', 'confirmed', 'on_hold'
            min_dur REAL DEFAULT 0.0,
            max_dur REAL DEFAULT 10.0,
            clap_prompts TEXT, -- JSON array of strings
            search_tags TEXT,  -- JSON array of strings
            negative_prompts TEXT, -- JSON array of strings
            scene_media_path TEXT,
            scene_media_type TEXT,
            scene_in REAL DEFAULT 0.0,
            scene_out REAL DEFAULT 0.0,
            sound_offset REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # 3. Sounds
        c.execute("""
        CREATE TABLE IF NOT EXISTS sounds (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL, -- 'lotsofsounds', 'local', 'procedural'
            source_id TEXT,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            duration REAL DEFAULT 0.0,
            license_type TEXT NOT NULL, -- 'CC0', 'CC-BY', 'Royalty-Free'
            author TEXT DEFAULT 'Unknown',
            source_url TEXT,
            tags TEXT, -- JSON array
            sha256 TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        # 4. Slot Candidates (Ranked pool per slot)
        c.execute("""
        CREATE TABLE IF NOT EXISTS slot_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id TEXT NOT NULL,
            sound_id TEXT NOT NULL,
            score REAL NOT NULL,
            rank INTEGER NOT NULL,
            status TEXT DEFAULT 'pending', -- 'pending', 'liked', 'confirmed', 'skipped'
            audio_in REAL DEFAULT 0.0,
            audio_out REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(slot_id, sound_id)
        );
        """)
        
        # 5. Confirmed Exports
        c.execute("""
        CREATE TABLE IF NOT EXISTS confirmed_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id TEXT NOT NULL,
            sound_id TEXT NOT NULL,
            variant_no INTEGER DEFAULT 1,
            unity_dest_path TEXT,
            exported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 6. App Settings (Persistent Volume, Panel Sizes, Unity Path)
        c.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # Schema Migrations for existing DBs
        _ensure_column(c, "slots", "scene_media_path", "TEXT")
        _ensure_column(c, "slots", "scene_media_type", "TEXT")
        _ensure_column(c, "slots", "scene_in", "REAL DEFAULT 0.0")
        _ensure_column(c, "slots", "scene_out", "REAL DEFAULT 0.0")
        _ensure_column(c, "slots", "sound_offset", "REAL DEFAULT 0.0")
        _ensure_column(c, "slots", "review_passed", "INTEGER DEFAULT 0")
        _ensure_column(c, "slots", "work_memo", "TEXT DEFAULT ''")
        _ensure_column(c, "slots", "assignee", "TEXT DEFAULT ''")
        _ensure_column(c, "slots", "handoff_status", "TEXT DEFAULT ''")
        _ensure_column(c, "slots", "is_exported", "INTEGER DEFAULT 0")
        _ensure_column(c, "slots", "exported_at", "TEXT DEFAULT ''")
        _ensure_column(c, "slot_candidates", "audio_in", "REAL DEFAULT 0.0")
        _ensure_column(c, "slot_candidates", "audio_out", "REAL DEFAULT 0.0")
        _ensure_column(c, "slot_candidates", "playback_rate", "REAL DEFAULT 1.0")
        _ensure_column(c, "slot_candidates", "preserve_pitch", "INTEGER DEFAULT 1")
        _ensure_column(c, "slot_candidates", "reverse", "INTEGER DEFAULT 0")
        _ensure_column(c, "slot_candidates", "pitch_semitones", "REAL DEFAULT 0.0")
        _ensure_column(c, "slot_candidates", "tail_sec", "REAL DEFAULT 0.0")
        _ensure_column(c, "sounds", "norm_gain", "REAL DEFAULT 1.0")
        
        # Performance Indices
        c.execute("CREATE INDEX IF NOT EXISTS idx_candidates_slot_rank ON slot_candidates(slot_id, rank);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_confirmed_slot ON confirmed_exports(slot_id);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sounds_source ON sounds(source);")

        # Cross-PC Portability: normalize stale absolute paths from another drive/folder
        c.execute("SELECT id, file_path FROM sounds")
        for r in c.fetchall():
            expected = AUDIO_CACHE_DIR / f"{r['id']}.mp3"
            if str(r["file_path"] or "") != str(expected):
                if expected.exists() or not Path(str(r["file_path"] or "")).exists():
                    c.execute("UPDATE sounds SET file_path = ? WHERE id = ?", (str(expected), r["id"]))

        c.execute("SELECT id, scene_media_path FROM slots WHERE scene_media_path IS NOT NULL AND scene_media_path != ''")
        for r in c.fetchall():
            fname = Path(str(r["scene_media_path"])).name
            expected_scene = SCENE_CACHE_DIR / fname
            if str(r["scene_media_path"]) != str(expected_scene) and expected_scene.exists():
                c.execute("UPDATE slots SET scene_media_path = ? WHERE id = ?", (str(expected_scene), r["id"]))

        conn.commit()

# 28 Game SFX Slot Definition Dictionary (Expanded into 30 granular slots)
DEFAULT_SLOTS_DATA = [
    {
        "id": "sfx_spray_fkill",
        "name": "스프레이 발사 소리",
        "category": "weapon",
        "memo": "에프킬라 2초 동안 발사하는 듯한 치이익 분사음",
        "status": "pending",
        "min_dur": 1.0,
        "max_dur": 4.0,
        "clap_prompts": ["aerosol spray can spraying continuously for two seconds", "insect spray can continuous hissing release", "compressed air spray mist continuous hiss"],
        "search_tags": ["spray", "aerosol", "hiss", "gas"],
        "negative_prompts": ["explosion", "fire crackle", "water splash"]
    },
    {
        "id": "sfx_paintball_cannon",
        "name": "페인트볼 캐논 발사",
        "category": "weapon",
        "memo": "묵직한데 깔끔한 발포음 (공기압 압축 런처)",
        "status": "pending",
        "min_dur": 0.3,
        "max_dur": 2.5,
        "clap_prompts": ["deep punchy paintball cannon launch firing", "clean heavy pneumatic cannon shot", "air nailer single shot compressed air release"],
        "search_tags": ["cannon", "launch", "pneumatic", "air gun"],
        "negative_prompts": ["machine gun", "laser beep", "metallic ring"]
    },
    {
        "id": "sfx_paintball_impact",
        "name": "페인트볼 폭발",
        "category": "weapon",
        "memo": "펑 터지는 소리 + 페인트들이 흩뿌려져 주변에 찰팍 하고 살짝 겹치는 소리",
        "status": "pending",
        "min_dur": 0.4,
        "max_dur": 2.5,
        "clap_prompts": ["paintball splat explosion with wet paint splatter debris", "slime burst pop and wet liquid splash", "mud splat squelch bursting on wall"],
        "search_tags": ["splat", "splash", "slime", "burst"],
        "negative_prompts": ["dry explosion", "fireworks", "glass shatter"]
    },
    {
        "id": "sfx_melee_hit_machine",
        "name": "평타 맞추는 소리 (기계/쇳소리)",
        "category": "combat",
        "memo": "기계를 때리면 쇳소리가 나고 그런 타격음",
        "status": "pending",
        "min_dur": 0.1,
        "max_dur": 1.5,
        "clap_prompts": ["metal pipe striking steel robot chassis with sharp clang", "heavy metallic hit on iron machine with clank residue", "sword striking robot metal armor"],
        "search_tags": ["metal hit", "clank", "clang", "metal impact"],
        "negative_prompts": ["flesh punch", "wooden thud", "water"]
    },
    {
        "id": "sfx_melee_swing_combo_1",
        "name": "평타 1타 휘두르기 (가벼움)",
        "category": "combat",
        "memo": "평타 콤보 1타: 가볍고 날렵하게 가르는 스윙",
        "status": "pending",
        "min_dur": 0.1,
        "max_dur": 1.0,
        "clap_prompts": ["fast light sword whoosh swing in air", "quick blade swish slice through air", "light stick whoosh"],
        "search_tags": ["whoosh", "sword swing", "light whoosh"],
        "negative_prompts": ["heavy explosion", "impact", "roar"]
    },
    {
        "id": "sfx_melee_swing_combo_2",
        "name": "평타 2타 휘두르기 (중간)",
        "category": "combat",
        "memo": "평타 콤보 2타: 적당한 힘이 실린 스윙",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.2,
        "clap_prompts": ["medium blade whoosh attack swing", "clean weapon swoosh with momentum", "sword swinging through air"],
        "search_tags": ["swoosh", "whoosh", "blade swing"],
        "negative_prompts": ["impact", "heavy thunder"]
    },
    {
        "id": "sfx_melee_swing_combo_3",
        "name": "평타 3타 휘두르기 (묵직함)",
        "category": "combat",
        "memo": "평타 콤보 3타: 가장 묵직하고 강렬하게 베어내는 피니시 스윙",
        "status": "pending",
        "min_dur": 0.3,
        "max_dur": 1.5,
        "clap_prompts": ["heavy mighty two handed sword swing whoosh", "deep resonant weapon swoosh with wind force", "powerful broadsword air slice"],
        "search_tags": ["heavy whoosh", "deep swoosh", "power swing"],
        "negative_prompts": ["light tap", "tinny"]
    },
    {
        "id": "sfx_player_hurt",
        "name": "플레이어 피격음 (보류)",
        "category": "player",
        "memo": "플레이어 데미지 피격음 (기획 보류 상태)",
        "status": "on_hold",
        "min_dur": 0.2,
        "max_dur": 1.5,
        "clap_prompts": ["game player taking hit grunt pain damage", "player character hurt impact sound"],
        "search_tags": ["player hurt", "grunt", "damage"],
        "negative_prompts": []
    },
    {
        "id": "sfx_dash_wind",
        "name": "대시 소리",
        "category": "player",
        "memo": "바람을 가르며 빠르게 이동하는 느낌",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.2,
        "clap_prompts": ["rapid dash whoosh rushing through wind", "ninja high speed teleport air dash burst", "quick aerodynamic dash swoosh"],
        "search_tags": ["dash", "air rush", "whoosh", "quick"],
        "negative_prompts": ["footstep walking", "heavy blast"]
    },
    {
        "id": "sfx_cookie_crunch",
        "name": "쿠키 사용 시 바삭거리는 소리",
        "category": "item",
        "memo": "쿠키 아이템 먹을 때 바삭 깨물어 먹는 소리",
        "status": "pending",
        "min_dur": 0.3,
        "max_dur": 2.0,
        "clap_prompts": ["crisp cookie biscuit crunch bite chewing", "loud crunchy cracker eating bite", "crispy sweet biscuit crunching"],
        "search_tags": ["crunch", "cookie", "bite", "chew", "cracker"],
        "negative_prompts": ["wet bite", "slurp", "metal"]
    },
    {
        "id": "sfx_shield_bubble_activate",
        "name": "버블 방어막(파랑) 활성화",
        "category": "skill",
        "memo": "파란색 보호막/버블 쉴드가 전개되는 미래지향적 에너지음",
        "status": "pending",
        "min_dur": 0.5,
        "max_dur": 3.0,
        "clap_prompts": ["sci fi energy shield activation hum with resonant harmonic tone", "futuristic bubble forcefield powering up", "protective plasma barrier activating"],
        "search_tags": ["shield", "forcefield", "barrier", "energy hum"],
        "negative_prompts": ["explosion", "gunshot"]
    },
    {
        "id": "sfx_shield_bubble_pop",
        "name": "버블 방어막 터지는 소리",
        "category": "skill",
        "memo": "비눗방울 터지는 소리거나, 깨지는 소리가 섞인 디스펠음",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.5,
        "clap_prompts": ["soap bubble pop bursting with light glass shatter residue", "magical energy shield breaking and popping", "glass forcefield shattered into pieces"],
        "search_tags": ["bubble pop", "glass shatter", "barrier break"],
        "negative_prompts": ["cannon blast", "heavy motor"]
    },
    {
        "id": "sfx_ui_tab_open",
        "name": "Tab BUT UI 활성화",
        "category": "ui",
        "memo": "Tab 눌러서 b-u-t ui 활성화할 때 나는 전자음 (삐비빅 위이잉)",
        "status": "pending",
        "min_dur": 0.3,
        "max_dur": 1.8,
        "clap_prompts": ["futuristic sci fi holographic menu opening with digital beeps and rising tone", "high tech computer interface activating beep whir", "sci-fi interface chime alert"],
        "search_tags": ["ui open", "menu pop", "digital beep", "hologram"],
        "negative_prompts": ["heavy explosion", "room tone", "ambient drone"]
    },
    {
        "id": "sfx_ui_tab_close",
        "name": "Tab BUT UI 꺼짐",
        "category": "ui",
        "memo": "Tab 눌러서 나오는 but ui 꺼질 때 나는 짧은 효과음 (띠릭, 패드 내리는 느낌)",
        "status": "pending",
        "min_dur": 0.1,
        "max_dur": 1.0,
        "clap_prompts": ["quick digital device close chirp sound", "futuristic menu closing soft tactile click tone", "clean tech interface dismiss beep"],
        "search_tags": ["ui close", "digital click", "menu dismiss"],
        "negative_prompts": ["loud horn", "crash"]
    },
    {
        "id": "sfx_elevator_move",
        "name": "엘리베이터 움직이는 소리",
        "category": "elevator",
        "memo": "엘리베이터가 상하로 움직일 때 나는 모터와 케이블 윙윙음",
        "status": "pending",
        "min_dur": 2.0,
        "max_dur": 6.0,
        "clap_prompts": ["interior elevator moving motor hum and gentle vibration loop", "lift shaft ascending humming cable motor", "smooth elevator cabin moving sound"],
        "search_tags": ["elevator", "motor hum", "lift", "cable"],
        "negative_prompts": ["car engine", "bell ring"]
    },
    {
        "id": "sfx_elevator_door_open",
        "name": "엘리베이터 문 열리는 소리",
        "category": "elevator",
        "memo": "엘리베이터 자동 슬라이딩 문이 슈욱 열리는 소리",
        "status": "pending",
        "min_dur": 1.0,
        "max_dur": 3.0,
        "clap_prompts": ["elevator sliding doors smoothly opening with mechanical whoosh", "automatic metal sliding doors opening", "pneumatic sliding door opening"],
        "search_tags": ["elevator door", "sliding door", "door open"],
        "negative_prompts": ["wood door slam", "window break"]
    },
    {
        "id": "sfx_elevator_door_close",
        "name": "엘리베이터 문 닫히는 소리",
        "category": "elevator",
        "memo": "엘리베이터 문이 닫히며 끝에 딱 체결되는 소리",
        "status": "pending",
        "min_dur": 1.0,
        "max_dur": 3.0,
        "clap_prompts": ["elevator sliding doors closing and latching shut", "automatic elevator door slide close thump", "pneumatic sliding door closing"],
        "search_tags": ["elevator door", "sliding door", "door close"],
        "negative_prompts": ["glass breaking", "water"]
    },
    {
        "id": "sfx_elevator_bell",
        "name": "엘리베이터 도착음 (띵~)",
        "category": "elevator",
        "memo": "엘리베이터 목적 층에 도착했을 때 맑게 울리는 띵 벨소리",
        "status": "pending",
        "min_dur": 0.5,
        "max_dur": 2.5,
        "clap_prompts": ["classic elevator arrival bell chime ding sound", "clear single ding chime bell inside lift", "hotel elevator bell chime"],
        "search_tags": ["elevator chime", "ding", "bell chime", "arrival"],
        "negative_prompts": ["church bell", "alarm siren"]
    },
    {
        "id": "sfx_elevator_button",
        "name": "엘리베이터 층 버튼 누름음",
        "category": "elevator",
        "memo": "스크린 터치해서 띠딕 띡 하는 느낌의 층수 입력음",
        "status": "pending",
        "min_dur": 0.1,
        "max_dur": 0.8,
        "clap_prompts": ["touchscreen elevator button press beep tone", "clean tactile digital keypad beep", "modern electronic button press chime"],
        "search_tags": ["button press", "keypad beep", "touch tone", "click"],
        "negative_prompts": ["mechanical switch", "loud crash"]
    },
    {
        "id": "sfx_mob_footstep",
        "name": "잡몹 걸어다니는 발소리 (하리)",
        "category": "enemy",
        "memo": "(남자&여자) 하리가 걸어다닐 때 나는 규칙적인 발소리",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 0.8,
        "clap_prompts": ["single shoe footstep on floor", "single sneaker walking step on pavement", "boots casual step tap"],
        "search_tags": ["single footstep", "walking step", "shoe step"],
        "negative_prompts": ["giant stomp", "horse gallop", "continuous walking loop"]
    },
    {
        "id": "sfx_mob_male_swing",
        "name": "잡몹(남) 휘두르는 공격음",
        "category": "enemy",
        "memo": "공격 시 바람 소리마냥 훙하고 한번 휘두르는 둔기/주먹 소리",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.2,
        "clap_prompts": ["heavy blunt fist attack swing whoosh", "enemy monster club swing in air", "deep punching swoosh"],
        "search_tags": ["blunt whoosh", "punch swing", "heavy whoosh"],
        "negative_prompts": ["laser", "metallic slash"]
    },
    {
        "id": "sfx_mob_female_projectile",
        "name": "잡몹(여) 투사체 비행음",
        "category": "enemy",
        "memo": "투사체를 날려서 투사체가 날아가는 동안 날 바람소리",
        "status": "pending",
        "min_dur": 0.5,
        "max_dur": 2.5,
        "clap_prompts": ["flying projectile rushing whistle whoosh in air", "magic missile flying through air with air trail", "thrown projectile wind trail"],
        "search_tags": ["projectile", "whistle whoosh", "wind whoosh", "magic fly"],
        "negative_prompts": ["impact explosion", "static hum"]
    },
    {
        "id": "sfx_mob_female_proj_hit",
        "name": "투사체 적중 퍽 소리",
        "category": "enemy",
        "memo": "날아온 투사체에 맞았을 때 날만한 퍽 소리 (마인크래프트 타격 느낌)",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.0,
        "clap_prompts": ["blunt meat punch impact thud hit", "minecraft style body punch damage hit sound", "solid body impact thud"],
        "search_tags": ["thud hit", "punch impact", "body hit", "blunt strike"],
        "negative_prompts": ["metallic ring", "gunshot"]
    },
    {
        "id": "sfx_mob_death",
        "name": "잡몹 사망 (좀비 쓰러짐)",
        "category": "enemy",
        "memo": "마크 좀비 죽는 소리 또는 털썩하고 쓰러지는 쓰러짐음",
        "status": "pending",
        "min_dur": 0.4,
        "max_dur": 2.0,
        "clap_prompts": ["zombie groaning and collapsing to ground thud", "enemy creature death sound falling limp to floor", "body collapse on floor"],
        "search_tags": ["zombie death", "body fall", "collapse", "death groan"],
        "negative_prompts": ["robot explode", "happy chime"]
    },
    {
        "id": "sfx_midboss_death_shutdown",
        "name": "중간보스 거대 로봇 사망 셧다운",
        "category": "boss",
        "memo": "거대한 로봇이 지잉 위잉 하면서 절전되듯 꺼지는 소리",
        "status": "pending",
        "min_dur": 2.0,
        "max_dur": 6.0,
        "clap_prompts": ["giant mecha robot powering down with deep decelerating pitch whir to silence", "heavy machine system shutdown hum fade out", "cybernetic boss deactivating sound"],
        "search_tags": ["robot shutdown", "power down", "decelerate hum", "system off"],
        "negative_prompts": ["living scream", "quick pop"]
    },
    {
        "id": "sfx_env_fan",
        "name": "환풍기 바람/팬 소리",
        "category": "environment",
        "memo": "환풍기 돌아가는 바람 소리 느낌",
        "status": "pending",
        "min_dur": 2.0,
        "max_dur": 6.0,
        "clap_prompts": ["industrial ventilation fan blowing air steadily loop", "large exhaust blower fan wind hum", "air duct wind fan rotation"],
        "search_tags": ["ventilation fan", "exhaust fan", "wind hum", "blower"],
        "negative_prompts": ["hammering", "engine combustion"]
    },
    {
        "id": "sfx_env_platform_move",
        "name": "움직이는 플랫폼 기계음",
        "category": "environment",
        "memo": "움직이는 발판/플랫폼이 이동할 때 윙윙거리는 기계음",
        "status": "pending",
        "min_dur": 1.5,
        "max_dur": 5.0,
        "clap_prompts": ["mechanical elevator platform moving gear motorized whir", "moving industrial platform rail hum", "machinery motorized track sliding"],
        "search_tags": ["platform moving", "motor whir", "mechanical lift", "gears"],
        "negative_prompts": ["water drip", "car horn"]
    },
    {
        "id": "sfx_gimmick_mushroom",
        "name": "버섯 점프대 (트램펄린)",
        "category": "gimmick",
        "memo": "버섯 밟으면 튀는 소리 트램펄린 같이 통/보잉",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.5,
        "clap_prompts": ["cartoon bouncy spring boing jump sound", "trampoline bounce spring launching sound", "arcade game jump bounce"],
        "search_tags": ["boing", "spring jump", "bounce", "trampoline"],
        "negative_prompts": ["heavy explosion", "metal clang"]
    },
    {
        "id": "sfx_gimmick_bush_trap",
        "name": "덤불 함정 (수풀 바스락)",
        "category": "gimmick",
        "memo": "덤불이나 수풀에 걸렸을 때 바스락거리는 소리",
        "status": "pending",
        "min_dur": 0.3,
        "max_dur": 2.0,
        "clap_prompts": ["brushing through dense forest bush foliage rustle", "dry leaves and branches shaking rustling sound", "stepping into shrub thicket"],
        "search_tags": ["bush rustle", "foliage", "leaves rustle", "shrub"],
        "negative_prompts": ["water splash", "metal clank"]
    },
    {
        "id": "sfx_target_hit",
        "name": "타겟 명중 (과녁 박힘)",
        "category": "gimmick",
        "memo": "화살이나 투사체가 과녁에 탁! 하고 맞았을 때 나는 소리",
        "status": "pending",
        "min_dur": 0.2,
        "max_dur": 1.5,
        "clap_prompts": ["arrow hitting wooden archery target bullseye with solid thunk", "arrow hit impact", "solid target impact thud"],
        "search_tags": ["arrow target", "arrow hit impact", "wood thunk", "bullseye"],
        "negative_prompts": ["miss whoosh", "knife stabbing", "flesh"]
    }
]

def seed_project_and_slots(project_id: str = "proj_default", project_name: str = "My Game SFX", unity_path: str = ""):
    init_db()
    with get_db_context() as conn:
        c = conn.cursor()
        
        # Insert or update Project
        c.execute("""
        INSERT INTO projects (id, name, unity_path)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name, unity_path=excluded.unity_path;
        """, (project_id, project_name, unity_path))
        
        # Insert Slots
        for s in DEFAULT_SLOTS_DATA:
            c.execute("""
            INSERT INTO slots (id, project_id, name, category, memo, status, min_dur, max_dur, clap_prompts, search_tags, negative_prompts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                category=excluded.category,
                memo=excluded.memo,
                min_dur=excluded.min_dur,
                max_dur=excluded.max_dur,
                clap_prompts=excluded.clap_prompts,
                search_tags=excluded.search_tags,
                negative_prompts=excluded.negative_prompts;
            """, (
                s["id"],
                project_id,
                s["name"],
                s["category"],
                s["memo"],
                s["status"],
                s["min_dur"],
                s["max_dur"],
                json.dumps(s["clap_prompts"], ensure_ascii=False),
                json.dumps(s["search_tags"], ensure_ascii=False),
                json.dumps(s["negative_prompts"], ensure_ascii=False)
            ))
            
        conn.commit()
    print(f"Seeded project '{project_name}' with {len(DEFAULT_SLOTS_DATA)} game sound slots.")

if __name__ == "__main__":
    seed_project_and_slots()
