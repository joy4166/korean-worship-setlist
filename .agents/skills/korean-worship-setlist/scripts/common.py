"""Shared utilities, database connection, key/pitch math, and security gates."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable

KEY_SOURCE_VALUES = (
    "manual_seed",
    "youtube_parsed",
    "kccm_verified",
    "hymnal_meta",
    "audio_estimated",
    "catalog_default",
)

DEFAULT_VOCAL_RANGES: dict[str, tuple[str, str]] = {
    "male": ("A2", "F#4"),
    "female": ("G3", "D5"),
}

_WORKSPACE_MARKER_DIRS = {".agents", ".agent", "_agents", "_agent", ".claude", ".cursor", ".codex"}
SKILL_DIR = Path(__file__).resolve().parent.parent


def is_global_skill_install(skill_dir: Path | None = None, *, home_dir: Path | None = None) -> bool:
    """Return True when the skill lives in a global plugin/skill/npm directory rather than inside a workspace."""
    s_dir = (skill_dir or SKILL_DIR).resolve()
    h_dir = (home_dir or Path.home()).resolve()
    if any(part.lower() in ("node_modules", "site-packages") for part in s_dir.parts):
        return True
    for anc in (s_dir, *s_dir.parents):
        if anc.name.lower() in _WORKSPACE_MARKER_DIRS and anc.parent.resolve() != h_dir:
            return False
    return True


def detect_workspace_root(
    skill_dir: Path | None = None,
    *,
    cwd: Path | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Resolve the active church workspace root for both workspace-local and global plugin/skill installs."""
    override = os.environ.get("WORSHIP_WORKSPACE_ROOT")
    if override:
        return Path(override).resolve()
    s_dir = (skill_dir or SKILL_DIR).resolve()
    h_dir = (home_dir or Path.home()).resolve()
    if not is_global_skill_install(s_dir, home_dir=h_dir):
        ws_candidate: Path | None = None
        for anc in (s_dir, *s_dir.parents):
            if anc.name.lower() in _WORKSPACE_MARKER_DIRS and anc.parent.resolve() != h_dir:
                ws_candidate = anc.parent.resolve()
        if ws_candidate is not None:
            return ws_candidate
    return (cwd or Path.cwd()).resolve()


def get_local_state_dir(
    skill_dir: Path | None = None,
    *,
    cwd: Path | None = None,
    home_dir: Path | None = None,
) -> Path:
    """Store per-church mutable state in SKILL_DIR/data when workspace-local, or WORKSPACE/.worship-setlist when global."""
    override = os.environ.get("WORSHIP_STATE_DIR")
    if override:
        return Path(override).resolve()
    s_dir = (skill_dir or SKILL_DIR).resolve()
    if not is_global_skill_install(s_dir, home_dir=home_dir):
        return s_dir / "data"
    return detect_workspace_root(s_dir, cwd=cwd, home_dir=home_dir) / ".worship-setlist"


def get_user_config_path(
    skill_dir: Path | None = None,
    *,
    cwd: Path | None = None,
    home_dir: Path | None = None,
) -> Path:
    return get_local_state_dir(skill_dir, cwd=cwd, home_dir=home_dir) / "user_config.json"


def get_local_db_path(
    skill_dir: Path | None = None,
    *,
    cwd: Path | None = None,
    home_dir: Path | None = None,
) -> Path:
    return get_local_state_dir(skill_dir, cwd=cwd, home_dir=home_dir) / "church_local.db"


WORKSPACE_ROOT = detect_workspace_root()
DATA_DIR = SKILL_DIR / "data"
RESOURCES_DIR = SKILL_DIR / "resources"
FONT_PATH = RESOURCES_DIR / "fonts" / "NotoSansKR-VF.ttf"

WORSHIP_DB_PATH = DATA_DIR / "worship.db"
LOCAL_DB_PATH = get_local_db_path()
BLOCKED_SOURCES_PATH = DATA_DIR / "blocked_sources.json"
USER_CONFIG_PATH = get_user_config_path()
USER_CONFIG_EXAMPLE_PATH = DATA_DIR / "user_config.example.json"
SEED_SONGS_PATH = DATA_DIR / "seed_songs.json"

WORSHIP_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_slug TEXT NOT NULL UNIQUE,
    title_ko TEXT NOT NULL,
    title_en TEXT,
    normalized_title TEXT NOT NULL,
    alternate_titles TEXT NOT NULL DEFAULT '[]',
    first_line_incipit TEXT CHECK (first_line_incipit IS NULL OR length(first_line_incipit) <= 15),
    category TEXT NOT NULL CHECK (category IN ('modern_worship', 'classic_gospel', 'hymn')),
    hymn_number INTEGER,
    hymn_number_unified INTEGER,
    era TEXT NOT NULL CHECK (era IN ('1980s', '1990s', '2000s', '2010s', '2020s', 'traditional')),
    original_artist TEXT,
    release_year INTEGER,
    adoption_tier TEXT NOT NULL DEFAULT 'widely_sung'
        CHECK (adoption_tier IN ('core_standard', 'widely_sung', 'recent_rising', 'niche')),
    lyric_accessibility TEXT NOT NULL DEFAULT 'standard_church'
        CHECK (lyric_accessibility IN ('easy_seeker', 'standard_church', 'deep_theological')),
    church_seasons TEXT NOT NULL DEFAULT '["ordinary"]',
    service_slots TEXT NOT NULL DEFAULT '["worship_set"]',
    liturgical_stages TEXT NOT NULL DEFAULT '[]',
    theological_tone TEXT NOT NULL DEFAULT 'adoration',
    lyrical_perspective TEXT NOT NULL DEFAULT 'god_centered_vertical',
    theological_core TEXT NOT NULL DEFAULT '',
    caution_nuance TEXT DEFAULT '',
    theme_summary TEXT NOT NULL,
    theme_tags TEXT NOT NULL DEFAULT '[]',
    scripture_refs TEXT NOT NULL DEFAULT '[]',
    available_sections TEXT NOT NULL DEFAULT '[]',
    default_song_form TEXT,
    lyricist TEXT,
    composer TEXT,
    copyright_holder TEXT,
    ccli_number TEXT,
    copyright_scope TEXT DEFAULT 'komca_separate'
        CHECK (copyright_scope IN ('ccli_covered', 'komca_separate', 'khs_hymn', 'public_domain')),
    source_status TEXT NOT NULL DEFAULT 'community_reviewed'
        CHECK (source_status IN ('verified_official', 'community_reviewed', 'caution_disputed', 'blocked_heresy')),
    theological_notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS arrangements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    worship_team TEXT NOT NULL,
    key_signature TEXT NOT NULL,
    time_signature TEXT NOT NULL DEFAULT '4/4',
    bpm INTEGER NOT NULL,
    tempo_tier TEXT NOT NULL CHECK (tempo_tier IN ('ballad', 'medium', 'fast')),
    groove_style TEXT,
    intro_first_chord TEXT,
    outro_last_chord TEXT,
    ending_type TEXT DEFAULT 'pad_hold'
        CHECK (ending_type IN ('pad_hold', 'clean_cut', 'ritardando', 'vamp')),
    drum_grooves TEXT NOT NULL DEFAULT '[]',
    section_dynamics TEXT NOT NULL DEFAULT '{}',
    lowest_note TEXT,
    highest_note TEXT,
    vocal_range_fit TEXT DEFAULT 'congregation_comfortable',
    congregation_singability TEXT DEFAULT 'modern_simple'
        CHECK (congregation_singability IN ('hymn_traditional', 'modern_simple', 'syncopated_band')),
    band_dependency TEXT NOT NULL DEFAULT 'medium'
        CHECK (band_dependency IN ('low', 'medium', 'high')),
    session_fit TEXT NOT NULL DEFAULT '["full_band"]',
    arrangement_song_form TEXT,
    youtube_url TEXT,
    youtube_start_sec INTEGER DEFAULT 0,
    recorded_year INTEGER,
    key_source TEXT DEFAULT 'manual_seed',
    bpm_source TEXT DEFAULT 'manual_seed',
    confidence REAL DEFAULT 0.9,
    is_default INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS song_embeddings (
    song_id INTEGER PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL DEFAULT 'intfloat/multilingual-e5-small',
    dimension INTEGER NOT NULL DEFAULT 384,
    embedding BLOB NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS songs_fts USING fts5(
    song_id UNINDEXED,
    title_ko,
    title_en,
    normalized_title,
    alternate_titles,
    first_line_incipit,
    theme_summary,
    theological_core,
    theme_tags,
    scripture_refs,
    worship_teams,
    tokenize='trigram'
);

CREATE INDEX IF NOT EXISTS idx_songs_category ON songs(category);
CREATE INDEX IF NOT EXISTS idx_songs_era ON songs(era);
CREATE INDEX IF NOT EXISTS idx_songs_status ON songs(source_status);
CREATE INDEX IF NOT EXISTS idx_arrangements_song_id ON arrangements(song_id);
CREATE INDEX IF NOT EXISTS idx_arrangements_team ON arrangements(worship_team);
"""

LOCAL_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {prefix}repertoire (
    song_slug TEXT PRIMARY KEY,
    familiarity TEXT NOT NULL DEFAULT 'unknown'
        CHECK (familiarity IN ('pinned_ritual', 'favorite', 'familiar', 'learning', 'new', 'unknown', 'excluded')),
    preferred_key TEXT,
    times_sung INTEGER NOT NULL DEFAULT 0,
    consecutive_learning_count INTEGER NOT NULL DEFAULT 0,
    last_sung_date TEXT,
    team_memo TEXT
);

CREATE TABLE IF NOT EXISTS {prefix}setlist_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_date TEXT NOT NULL,
    service_type TEXT NOT NULL,
    cooldown_scope TEXT NOT NULL,
    service_part TEXT DEFAULT '1부',
    preacher TEXT,
    sermon_title TEXT,
    sermon_scripture TEXT,
    church_season TEXT DEFAULT 'ordinary',
    theme_input TEXT NOT NULL,
    setlist_json TEXT NOT NULL,
    output_dir TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS {prefix}sheet_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_slug TEXT NOT NULL,
    file_path TEXT NOT NULL,
    detected_key TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 1,
    verified_sections_json TEXT DEFAULT '[]',
    detected_time_signature TEXT,
    detected_bpm INTEGER,
    detected_groove TEXT,
    verified_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS {prefix}idx_history_scope_date
    ON setlist_history(cooldown_scope, service_date);
"""

GROOVE_SUBDIVISION_FAMILY: dict[str, str] = {
    "shuffle_bounce": "triplet_shuffle",
    "country_gogo": "gogo_2beat",
    "16th_upbeat": "straight_16th",
    "straight_8th": "straight_8th",
    "rock_upbeat": "straight_8th",
    "16th_ballad": "straight_ballad",
    "6_8_worship": "compound_triple",
    "acoustic": "neutral",
}

GROOVE_LABELS_KO: dict[str, str] = {
    "shuffle_bounce": "셔플/바운스",
    "country_gogo": "고고/컨트리",
    "16th_upbeat": "16비트 스트레이트",
    "straight_8th": "8비트 스트레이트",
    "rock_upbeat": "업비트 록",
    "16th_ballad": "16비트 발라드",
    "6_8_worship": "6/8 워십",
    "acoustic": "",
}

_GROOVE_ALIASES: dict[str, str] = {
    "shuffle": "shuffle_bounce",
    "shuffle_bounce": "shuffle_bounce",
    "bounce": "shuffle_bounce",
    "swing": "shuffle_bounce",
    "16th_shuffle": "shuffle_bounce",
    "triplet": "shuffle_bounce",
    "셔플": "shuffle_bounce",
    "바운스": "shuffle_bounce",
    "스윙": "shuffle_bounce",
    "셔플고고": "shuffle_bounce",
    "셔플/바운스": "shuffle_bounce",
    "country_gogo": "country_gogo",
    "gogo": "country_gogo",
    "go_go": "country_gogo",
    "go-go": "country_gogo",
    "country": "country_gogo",
    "polka": "country_gogo",
    "2beat": "country_gogo",
    "고고": "country_gogo",
    "컨트리": "country_gogo",
    "투비트": "country_gogo",
    "폴카": "country_gogo",
    "고고/컨트리": "country_gogo",
    "16th_upbeat": "16th_upbeat",
    "funk_16th": "16th_upbeat",
    "syncopated_16th": "16th_upbeat",
    "16beat": "16th_upbeat",
    "16비트": "16th_upbeat",
    "펑키": "16th_upbeat",
    "16비트스트레이트": "16th_upbeat",
    "straight_8th": "straight_8th",
    "straight": "straight_8th",
    "8beat": "straight_8th",
    "8비트": "straight_8th",
    "스트레이트": "straight_8th",
    "8비트스트레이트": "straight_8th",
    "rock_upbeat": "rock_upbeat",
    "modern_rock": "rock_upbeat",
    "rock": "rock_upbeat",
    "모던록": "rock_upbeat",
    "업비트록": "rock_upbeat",
    "16th_ballad": "16th_ballad",
    "ballad": "16th_ballad",
    "발라드": "16th_ballad",
    "16비트발라드": "16th_ballad",
    "6_8_worship": "6_8_worship",
    "6/8": "6_8_worship",
    "12/8": "6_8_worship",
    "compound": "6_8_worship",
    "6/8워십": "6_8_worship",
    "acoustic": "acoustic",
    "어쿠스틱": "acoustic",
}


def normalize_groove_style(raw: str | None) -> str | None:
    """Normalize groove/rhythm hint strings to canonical groove_style keys."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in GROOVE_SUBDIVISION_FAMILY:
        return text
    if text in _GROOVE_ALIASES:
        return _GROOVE_ALIASES[text]
    compact = text.replace(" ", "").replace("-", "_")
    if compact in _GROOVE_ALIASES:
        return _GROOVE_ALIASES[compact]
    underscored = re.sub(r"[\s\-]+", "_", text)
    if underscored in _GROOVE_ALIASES:
        return _GROOVE_ALIASES[underscored]
    return None

PITCH_CLASSES: dict[str, int] = {
    "C": 0, "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_NAMES = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]

KOREAN_KEY_MAP = {
    "다장조": "C", "올림다장조": "C#", "내림라장조": "Db",
    "라장조": "D", "내림마장조": "Eb", "마장조": "E",
    "바장조": "F", "올림바장조": "F#", "내림사장조": "Gb",
    "사장조": "G", "내림가장조": "Ab", "가장조": "A",
    "내림나장조": "Bb", "나장조": "B",
    "가단조": "Am (C Key)", "마단조": "Em (G Key)", "라단조": "Dm (F Key)",
    "나단조": "Bm (D Key)", "사단조": "Gm (Bb Key)", "다단조": "Cm (Eb Key)",
    "올림바단조": "F#m (A Key)", "올림다단조": "C#m (E Key)",
}

# Standard USFM-like 3-letter Bible book codes mapped from Korean & English names
BIBLE_BOOK_ALIASES: dict[str, str] = {
    "창세기": "GEN", "창": "GEN", "genesis": "GEN", "gen": "GEN",
    "출애굽기": "EXO", "출": "EXO", "exodus": "EXO", "exo": "EXO",
    "레위기": "LEV", "레": "LEV", "leviticus": "LEV", "lev": "LEV",
    "민수기": "NUM", "민": "NUM", "numbers": "NUM", "num": "NUM",
    "신명기": "DEU", "신": "DEU", "deuteronomy": "DEU", "deu": "DEU",
    "여호수아": "JOS", "수": "JOS", "joshua": "JOS", "jos": "JOS",
    "사사기": "JDG", "삿": "JDG", "judges": "JDG", "jdg": "JDG",
    "룻기": "RUT", "룻": "RUT", "ruth": "RUT", "rut": "RUT",
    "사무엘상": "1SA", "삼상": "1SA", "1samuel": "1SA", "1sa": "1SA",
    "사무엘하": "2SA", "삼하": "2SA", "2samuel": "2SA", "2sa": "2SA",
    "열왕기상": "1KI", "왕상": "1KI", "1kings": "1KI", "1ki": "1KI",
    "열왕기하": "2KI", "왕하": "2KI", "2kings": "2KI", "2ki": "2KI",
    "역대상": "1CH", "대상": "1CH", "1chronicles": "1CH", "1ch": "1CH",
    "역대하": "2CH", "대하": "2CH", "2chronicles": "2CH", "2ch": "2CH",
    "에스라": "EZR", "스": "EZR", "ezra": "EZR", "ezr": "EZR",
    "느헤미야": "NEH", "느": "NEH", "nehemiah": "NEH", "neh": "NEH",
    "에스더": "EST", "에": "EST", "esther": "EST", "est": "EST",
    "욥기": "JOB", "욥": "JOB", "job": "JOB",
    "시편": "PSA", "시": "PSA", "psalms": "PSA", "psalm": "PSA", "psa": "PSA", "ps": "PSA",
    "잠언": "PRO", "잠": "PRO", "proverbs": "PRO", "pro": "PRO",
    "전도서": "ECC", "전": "ECC", "ecclesiastes": "ECC", "ecc": "ECC",
    "아가": "SNG", "아": "SNG", "songofsolomon": "SNG", "sng": "SNG",
    "이사야": "ISA", "사": "ISA", "isaiah": "ISA", "isa": "ISA",
    "예레미야": "JER", "렘": "JER", "jeremiah": "JER", "jer": "JER",
    "예레미야애가": "LAM", "애": "LAM", "lamentations": "LAM", "lam": "LAM",
    "에스겔": "EZK", "겔": "EZK", "ezekiel": "EZK", "ezk": "EZK", "eze": "EZK", "ezek": "EZK",
    "다니엘": "DAN", "단": "DAN", "daniel": "DAN", "dan": "DAN",
    "호세아": "HOS", "호": "HOS", "hosea": "HOS", "hos": "HOS",
    "요엘": "JOL", "욜": "JOL", "joel": "JOL", "jol": "JOL",
    "아모스": "AMO", "암": "AMO", "amos": "AMO", "amo": "AMO",
    "오바댜": "OBA", "옵": "OBA", "obadiah": "OBA", "oba": "OBA",
    "요나": "JON", "욘": "JON", "jonah": "JON", "jon": "JON",
    "미가": "MIC", "미": "MIC", "micah": "MIC", "mic": "MIC",
    "나훔": "NAM", "나": "NAM", "nahum": "NAM", "nam": "NAM",
    "하박국": "HAB", "합": "HAB", "habakkuk": "HAB", "hab": "HAB",
    "스바냐": "ZEP", "습": "ZEP", "zephaniah": "ZEP", "zep": "ZEP",
    "학개": "HAG", "학": "HAG", "haggai": "HAG", "hag": "HAG",
    "스가랴": "ZEC", "슥": "ZEC", "zechariah": "ZEC", "zec": "ZEC",
    "말라기": "MAL", "말": "MAL", "malachi": "MAL", "mal": "MAL",
    "마태복음": "MAT", "마": "MAT", "matthew": "MAT", "matt": "MAT", "mat": "MAT",
    "마가복음": "MRK", "막": "MRK", "mark": "MRK", "mrk": "MRK",
    "누가복음": "LUK", "눅": "LUK", "luke": "LUK", "luk": "LUK",
    "요한복음": "JHN", "요": "JHN", "john": "JHN", "jhn": "JHN",
    "사도행전": "ACT", "행": "ACT", "acts": "ACT", "act": "ACT",
    "로마서": "ROM", "롬": "ROM", "romans": "ROM", "rom": "ROM",
    "고린도전서": "1CO", "고전": "1CO", "1corinthians": "1CO", "1co": "1CO",
    "고린도후서": "2CO", "고후": "2CO", "2corinthians": "2CO", "2co": "2CO",
    "갈라디아서": "GAL", "갈": "GAL", "galatians": "GAL", "gal": "GAL",
    "에베소서": "EPH", "엡": "EPH", "ephesians": "EPH", "eph": "EPH",
    "빌립보서": "PHP", "빌": "PHP", "philippians": "PHP", "php": "PHP",
    "골로새서": "COL", "골": "COL", "colossians": "COL", "col": "COL",
    "데살로니가전서": "1TH", "살전": "1TH", "1thessalonians": "1TH", "1th": "1TH",
    "데살로니가후서": "2TH", "살후": "2TH", "2thessalonians": "2TH", "2th": "2TH",
    "디모데전서": "1TI", "딤전": "1TI", "1timothy": "1TI", "1ti": "1TI",
    "디모데후서": "2TI", "딤후": "2TI", "2timothy": "2TI", "2ti": "2TI",
    "디도서": "TIT", "딛": "TIT", "titus": "TIT", "tit": "TIT",
    "빌레몬서": "PHM", "몬": "PHM", "philemon": "PHM", "phm": "PHM",
    "히브리서": "HEB", "히": "HEB", "hebrews": "HEB", "heb": "HEB",
    "야고보서": "JAS", "약": "JAS", "james": "JAS", "jas": "JAS",
    "베드로전서": "1PE", "벧전": "1PE", "1peter": "1PE", "1pe": "1PE",
    "베드로후서": "2PE", "벧후": "2PE", "2peter": "2PE", "2pe": "2PE",
    "요한일서": "1JN", "요일": "1JN", "1john": "1JN", "1jn": "1JN",
    "요한이서": "2JN", "요이": "2JN", "2john": "2JN", "2jn": "2JN",
    "요한삼서": "3JN", "요삼": "3JN", "3john": "3JN", "3jn": "3JN",
    "유다서": "JUD", "유": "JUD", "jude": "JUD", "jud": "JUD",
    "요한계시록": "REV", "계": "REV", "revelation": "REV", "rev": "REV",
}


def normalize_nfc(text: str | None) -> str:
    """Normalize decomposed Hangul (NFD) and other Unicode strings to canonical NFC."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text)


def normalize_title(text: str) -> str:
    """Strip whitespace and punctuation for normalized Korean/English matching."""
    if not text:
        return ""
    nfc = normalize_nfc(text)
    return re.sub(r"[\s\-_.,!?()\[\]'\"·ㆍ:;/~]+", "", nfc.strip().lower())


def slugify_song(title_ko: str, artist: str | None = None) -> str:
    """Create a deterministic, collision-resistant song_slug."""
    nfc_title = normalize_nfc(title_ko).strip().lower()
    nfc_artist = normalize_nfc(artist or "").strip().lower()
    base_title = re.sub(r"[^\w가-힣]+", "-", nfc_title).strip("-")
    base_artist = re.sub(r"[^\w가-힣]+", "-", nfc_artist).strip("-")
    digest = hashlib.sha1(f"{normalize_title(nfc_title)}|{normalize_title(nfc_artist)}".encode("utf-8")).hexdigest()[:6]
    if base_artist:
        return f"{base_title}-{base_artist}-{digest}"
    return f"{base_title}-{digest}"


def normalize_scripture_ref(text: str) -> str:
    """Normalize Korean/English Bible references into standard codes like 'ROM.8' or 'PSA.23:1-4'."""
    raw = text.strip()
    if not raw:
        return ""
    upper_match = re.match(r"^([1-3]?[A-Z]{2,4})\.(\d+(?::[\d\-]+)?)$", raw.upper())
    if upper_match:
        mapped = BIBLE_BOOK_ALIASES.get(upper_match.group(1).lower(), upper_match.group(1))
        return f"{mapped}.{upper_match.group(2)}"

    cleaned = re.sub(r"[장편절]", "", raw).strip()
    m = re.match(r"^([1-3]?\s*[가-힣a-zA-Z]+)\s*(\d+(?:\s*[:\-]\s*\d+(?:\-\d+)?)?)?$", cleaned)
    if not m:
        return raw
    book_raw = re.sub(r"\s+", "", m.group(1)).lower()
    code = BIBLE_BOOK_ALIASES.get(book_raw)
    if not code:
        return raw
    if m.group(2):
        ref_num = re.sub(r"\s+", "", m.group(2))
        return f"{code}.{ref_num}"
    return code


def scripture_book_chapter(norm_ref: str) -> str:
    """Extract 'BOOK.CHAPTER' from a normalized scripture ref."""
    if not norm_ref:
        return ""
    return norm_ref.split(":", 1)[0]


def load_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_user_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load user_config.json if present, falling back to user_config.example.json."""
    cfg = load_json_file(USER_CONFIG_EXAMPLE_PATH, default={}) or {}
    target = config_path or get_user_config_path()
    if target.exists():
        override = load_json_file(target, default={}) or {}
        cfg.update(override)
    return cfg


def load_blocked_sources(path: Path | None = None) -> dict[str, list[str]]:
    target = path or BLOCKED_SOURCES_PATH
    return load_json_file(
        target,
        default={"blocked_groups": [], "blocked_channels_or_domains": [], "blocked_song_keywords": []},
    )


def configure_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows terminals to prevent cp949 encode errors."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def resolve_workspace_path(raw_path: str | Path) -> Path:
    """Resolve a path relative to the active workspace root if not already absolute."""
    p = Path(raw_path)
    return p if p.is_absolute() else (detect_workspace_root() / p)


def assert_no_raw_lyrics_text(text: str | None, field_name: str = "text", max_len: int = 300) -> None:
    """Guard against dumping full song lyrics into memos, JSON payloads, or DB fields."""
    if not text:
        return
    non_empty_lines = [ln for ln in str(text).splitlines() if ln.strip()]
    if len(str(text)) > max_len or len(non_empty_lines) >= 3:
        raise ValueError(f"{field_name} exceeds memo bounds; raw lyrics storage is forbidden")


def is_blocked_source(*texts: str | None, blocked: dict[str, list[str]] | None = None) -> tuple[bool, str | None]:
    """Return (True, matched_term) if any provided text matches the heresy/controversial gate."""
    gate = blocked or load_blocked_sources()
    combined = " ".join(t for t in texts if t).lower()
    compact = re.sub(r"\s+", "", combined)
    for group_key in ("blocked_groups", "blocked_channels_or_domains", "blocked_song_keywords"):
        for term in gate.get(group_key, []):
            term_low = term.lower()
            term_clean = re.sub(r"\s+", "", term_low)
            if not term_clean:
                continue
            if term_clean.isascii() and term_clean.isalnum() and len(term_clean) <= 4:
                if re.search(rf"(?<![a-z0-9]){re.escape(term_clean)}(?![a-z0-9])", combined):
                    return True, term
            elif term_low in combined or term_clean in compact:
                return True, term
    return False, None


def _migrate_sheet_cache_columns(conn: sqlite3.Connection, prefix: str = "") -> None:
    """Backward-compatibly add rhythm/tempo verification columns to existing sheet_cache tables."""
    schema_prefix = f"{prefix.rstrip('.')}." if prefix else ""
    existing = {row[1] for row in conn.execute(f"PRAGMA {schema_prefix}table_info(sheet_cache)").fetchall()}
    if not existing:
        return
    migrations = {
        "detected_time_signature": "TEXT",
        "detected_bpm": "INTEGER",
        "detected_groove": "TEXT",
    }
    for col, col_type in migrations.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {prefix}sheet_cache ADD COLUMN {col} {col_type}")


def init_schema_on_connection(
    conn: sqlite3.Connection,
    include_worship: bool = True,
    include_local: bool = False,
    local_prefix: str = "local_db.",
) -> None:
    """Ensure tables exist on worship and/or attached local DB."""
    if include_worship:
        conn.executescript(WORSHIP_SCHEMA_SQL)
    if include_local:
        conn.executescript(LOCAL_SCHEMA_TEMPLATE.format(prefix=local_prefix))
        _migrate_sheet_cache_columns(conn, prefix=local_prefix)


def connect_dbs(
    worship_db_path: Path | str = WORSHIP_DB_PATH,
    local_db_path: Path | str | None = None,
    attach_local: bool = True,
    ensure_schemas: bool = True,
) -> sqlite3.Connection:
    """Connect to worship.db and optionally ATTACH church_local.db as local_db."""
    w_path = Path(worship_db_path)
    w_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(w_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    if ensure_schemas:
        init_schema_on_connection(conn, include_worship=True, include_local=False)

    if attach_local:
        l_path = Path(local_db_path) if local_db_path is not None else get_local_db_path()
        l_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure standalone local DB schema first so ATTACH + index statements always succeed cleanly.
        if ensure_schemas:
            l_conn = sqlite3.connect(str(l_path))
            l_conn.executescript(LOCAL_SCHEMA_TEMPLATE.format(prefix=""))
            _migrate_sheet_cache_columns(l_conn, prefix="")
            l_conn.commit()
            l_conn.close()
        escaped = str(l_path).replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{escaped}' AS local_db;")
    return conn


def sync_songs_fts(conn: sqlite3.Connection, song_id: int | None = None) -> None:
    """Synchronize songs_fts trigram virtual table with songs and aggregated arrangements."""
    if song_id is not None:
        conn.execute("DELETE FROM songs_fts WHERE song_id = ?", (song_id,))
        where_clause = "WHERE s.id = ?"
        params: tuple[Any, ...] = (song_id,)
    else:
        conn.execute("DELETE FROM songs_fts")
        where_clause = ""
        params = ()

    rows = conn.execute(
        f"""
        SELECT s.id, s.title_ko, COALESCE(s.title_en, '') AS title_en,
               s.normalized_title, s.alternate_titles, COALESCE(s.first_line_incipit, '') AS incipit,
               s.theme_summary, s.theological_core, s.theme_tags, s.scripture_refs,
               COALESCE(GROUP_CONCAT(DISTINCT a.worship_team), '') AS worship_teams
        FROM songs s
        LEFT JOIN arrangements a ON a.song_id = s.id
        {where_clause}
        GROUP BY s.id
        """,
        params,
    ).fetchall()

    for r in rows:
        alt_str = " ".join(json.loads(r["alternate_titles"] or "[]"))
        tags_str = " ".join(json.loads(r["theme_tags"] or "[]"))
        refs_list = json.loads(r["scripture_refs"] or "[]")
        refs_str = " ".join(refs_list + [scripture_book_chapter(x) for x in refs_list])
        teams_str = (r["worship_teams"] or "").replace(",", " ")
        conn.execute(
            """
            INSERT INTO songs_fts (
                song_id, title_ko, title_en, normalized_title, alternate_titles,
                first_line_incipit, theme_summary, theological_core, theme_tags,
                scripture_refs, worship_teams
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["id"],
                r["title_ko"],
                r["title_en"],
                r["normalized_title"],
                alt_str,
                r["incipit"],
                r["theme_summary"],
                r["theological_core"],
                tags_str,
                refs_str,
                teams_str,
            ),
        )


@functools.lru_cache(maxsize=512)
def parse_key_signature(key_str: str = "C") -> tuple[str, str, int, bool]:
    """
    Parse key strings like 'G', 'Em (G Key)', '사장조', 'Bb Key' into:
    (normalized_display_key, canonical_major_key, major_semitone_0_11, is_minor).
    """
    raw = (key_str or "C").strip()
    if raw in KOREAN_KEY_MAP:
        raw = KOREAN_KEY_MAP[raw]

    # Pattern like 'Em (G Key)' or 'F#m(A)'
    m_pair = re.match(r"^([A-G][#b]?m)\s*\(\s*([A-G][#b]?)(?:\s*Key)?\s*\)$", raw, re.IGNORECASE)
    if m_pair:
        minor_root = _canon_note(m_pair.group(1)[:-1]) + "m"
        major_root = _canon_note(m_pair.group(2))
        return f"{minor_root} ({major_root} Key)", major_root, PITCH_CLASSES[major_root], True

    raw_clean = re.sub(r"\s*(key|키)\s*$", "", raw, flags=re.IGNORECASE).strip()
    m = re.match(r"^([A-G])([#b]?)(m?)$", raw_clean, re.IGNORECASE)
    if not m:
        return raw, "C", 0, False

    root = m.group(1).upper() + m.group(2).lower().replace("♯", "#").replace("♭", "b")
    is_minor = m.group(3).lower() == "m"
    pc = PITCH_CLASSES.get(root, 0)
    if is_minor:
        # Relative major is +3 semitones
        rel_major_pc = (pc + 3) % 12
        rel_major = MAJOR_NAMES[rel_major_pc]
        minor_disp = f"{root}m"
        return f"{minor_disp} ({rel_major} Key)", rel_major, rel_major_pc, True
    canon_major = root if root in PITCH_CLASSES else MAJOR_NAMES[pc]
    return canon_major, canon_major, pc, False


def _canon_note(note: str) -> str:
    if not note:
        return "C"
    head = note[0].upper()
    acc = note[1:].replace("♯", "#").replace("♭", "b").lower()
    return head + acc


def transpose_key(key_str: str, semitones: int) -> str:
    """Transpose a major or minor ('Em (G Key)') signature by semitones."""
    disp, major_root, pc, is_minor = parse_key_signature(key_str)
    new_major_pc = (pc + semitones) % 12
    new_major = MAJOR_NAMES[new_major_pc]
    if is_minor:
        new_minor_pc = (new_major_pc - 3) % 12
        new_minor = MINOR_NAMES[new_minor_pc]
        return f"{new_minor} ({new_major} Key)"
    return new_major


def semitone_distance(key_a: str, key_b: str) -> int:
    """Signed minimal semitone shift from key_a to key_b in [-5, +6]."""
    _, _, pc_a, _ = parse_key_signature(key_a)
    _, _, pc_b, _ = parse_key_signature(key_b)
    diff = (pc_b - pc_a) % 12
    if diff > 6:
        diff -= 12
    return diff


@functools.lru_cache(maxsize=512)
def parse_pitch_midi(note_str: str | None) -> int | None:
    """Convert scientific pitch notation (e.g. 'F#4', 'A2', 'Bb4') to MIDI number."""
    if not note_str:
        return None
    m = re.match(r"^\s*([A-G])([#b♯♭]?)(-?\d)\s*$", note_str.strip(), re.IGNORECASE)
    if not m:
        return None
    note_name = m.group(1).upper() + m.group(2).replace("♯", "#").replace("♭", "b").lower()
    pc = PITCH_CLASSES.get(note_name)
    if pc is None:
        return None
    octave = int(m.group(3))
    return (octave + 1) * 12 + pc


def clamp_to_vocal_range(
    arrangement_key: str,
    highest_note: str | None,
    lowest_note: str | None,
    vocal_floor: str | None,
    vocal_ceiling: str | None,
    leader_gender: str = "male",
    preferred_key: str | None = None,
    min_shift: int = -3,
    max_shift: int = 2,
) -> dict[str, Any]:
    """
    Compute automatic transposition instead of dropping songs above vocal_ceiling.
    Applies treble-clef -12 octave adjustment for male leaders when ceiling is <= Bb4.
    """
    if preferred_key:
        shift = semitone_distance(arrangement_key, preferred_key)
        return {
            "target_key": parse_key_signature(preferred_key)[0],
            "key_shift": shift,
            "reason": "preferred_key",
            "in_range": True,
        }

    high_midi = parse_pitch_midi(highest_note)
    ceil_midi = parse_pitch_midi(vocal_ceiling)
    low_midi = parse_pitch_midi(lowest_note)
    floor_midi = parse_pitch_midi(vocal_floor)

    if high_midi is None or ceil_midi is None:
        return {
            "target_key": parse_key_signature(arrangement_key)[0],
            "key_shift": 0,
            "reason": "no_range_data",
            "in_range": True,
        }

    # Standard lead sheets annotate melody in treble clef (e.g. D5/E5); male voices sing -1 octave.
    eff_high = high_midi
    eff_low = low_midi
    if leader_gender == "male" and ceil_midi <= 70 and high_midi >= 70:
        eff_high = high_midi - 12
        if eff_low is not None:
            eff_low = eff_low - 12

    if eff_high <= ceil_midi and (floor_midi is None or eff_low is None or eff_low >= floor_midi):
        return {
            "target_key": parse_key_signature(arrangement_key)[0],
            "key_shift": 0,
            "reason": "original_in_range",
            "in_range": True,
        }

    best_shift = 0
    best_in_range = False
    if eff_high > ceil_midi:
        for shift in range(-1, min_shift - 1, -1):
            new_high = eff_high + shift
            new_low = (eff_low + shift) if eff_low is not None else None
            floor_ok = floor_midi is None or new_low is None or new_low >= floor_midi
            if new_high <= ceil_midi and floor_ok:
                best_shift = shift
                best_in_range = True
                break
        if not best_in_range:
            # Clamp to max downward shift even if slightly high, rather than eliminating the song.
            needed = ceil_midi - eff_high
            best_shift = max(min_shift, min(-1, needed))
            best_in_range = (eff_high + best_shift) <= ceil_midi
    elif floor_midi is not None and eff_low is not None and eff_low < floor_midi:
        for shift in range(1, max_shift + 1):
            new_low = eff_low + shift
            new_high = eff_high + shift
            if new_low >= floor_midi and new_high <= ceil_midi:
                best_shift = shift
                best_in_range = True
                break
        if not best_in_range:
            best_shift = max_shift

    return {
        "target_key": transpose_key(arrangement_key, best_shift),
        "key_shift": best_shift,
        "reason": "auto_transposed" if best_shift != 0 else "original_in_range",
        "in_range": best_in_range,
    }


def is_within_vocal_range(
    arrangement_key: str,
    target_key: str,
    highest_note: str | None,
    lowest_note: str | None,
    vocal_floor: str | None,
    vocal_ceiling: str | None,
    leader_gender: str = "male",
) -> bool:
    """Verify that transposing arrangement_key -> target_key stays within [vocal_floor, vocal_ceiling]."""
    high_midi = parse_pitch_midi(highest_note)
    ceil_midi = parse_pitch_midi(vocal_ceiling)
    if high_midi is None or ceil_midi is None:
        return True

    low_midi = parse_pitch_midi(lowest_note)
    floor_midi = parse_pitch_midi(vocal_floor)

    eff_high = high_midi
    eff_low = low_midi
    if leader_gender == "male" and ceil_midi <= 70 and high_midi >= 70:
        eff_high -= 12
        if eff_low is not None:
            eff_low -= 12

    shift = semitone_distance(arrangement_key, target_key)
    new_high = eff_high + shift
    if new_high > ceil_midi:
        return False
    if floor_midi is not None and eff_low is not None and (eff_low + shift) < floor_midi:
        return False
    return True


def json_dumps_ko(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


NPM_LATEST_URL = "https://registry.npmjs.org/korean-worship-setlist/latest"


def get_installed_skill_version(skill_dir: Path | None = None) -> str:
    """Read installed skill version from pyproject.toml."""
    toml_path = (skill_dir or SKILL_DIR) / "pyproject.toml"
    try:
        content = toml_path.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        return m.group(1).strip() if m else "0.0.0"
    except Exception:
        return "0.0.0"


def _parse_semver_tuple(ver: str | None) -> tuple[int, int, int] | None:
    """Parse strict stable semver (x.y.z), rejecting empty strings and pre-releases."""
    if not ver:
        return None
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", str(ver).strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _resolve_upgrade_command(skill_dir: Path | None = None, *, home_dir: Path | None = None) -> str:
    """Infer appropriate npx update command from the active skill installation location."""
    s_dir = (skill_dir or SKILL_DIR).resolve()
    parts = {p.lower() for p in s_dir.parts}
    if ".gemini" in parts and "plugins" in parts:
        return "npx korean-worship-setlist@latest update --plugin"
    if ".gemini" in parts and "skills" in parts:
        return "npx korean-worship-setlist@latest update --global"
    if ".claude" in parts:
        return (
            "npx korean-worship-setlist@latest update --claude --global"
            if is_global_skill_install(s_dir, home_dir=home_dir)
            else "npx korean-worship-setlist@latest update --claude"
        )
    if is_global_skill_install(s_dir, home_dir=home_dir) and ".agents" in parts:
        return "npx korean-worship-setlist@latest update --agents --global"
    return "npx korean-worship-setlist@latest update"


def check_skill_update(
    skill_dir: Path | None = None,
    *,
    cache_path: Path | None = None,
    ttl_seconds: int = 86400,
    timeout_sec: float = 1.2,
    fetcher: Callable[[str, float], dict[str, Any]] | None = None,
    force: bool = False,
    home_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Throttled, offline-safe npm version check returning UPDATE_AVAILABLE directive when newer stable release exists."""
    if os.environ.get("WORSHIP_NO_UPDATE_CHECK") == "1":
        return None
    if not force and fetcher is None and "PYTEST_CURRENT_TEST" in os.environ:
        return None

    s_dir = (skill_dir or SKILL_DIR).resolve()
    installed = get_installed_skill_version(s_dir)
    state_dir = get_local_state_dir(s_dir, home_dir=home_dir)
    c_path = cache_path or (state_dir / "_cache" / "update_check.json")
    now = time.time()

    latest: str | None = None
    used_cache = False
    if not force and c_path.exists():
        try:
            cached = json.loads(c_path.read_text(encoding="utf-8"))
            if now - float(cached.get("checked_at", 0)) < ttl_seconds:
                latest = cached.get("latest_version")
                used_cache = True
        except Exception:
            pass

    if not used_cache:
        try:
            if fetcher is not None:
                data = fetcher(NPM_LATEST_URL, timeout_sec)
            else:
                req = urllib.request.Request(
                    NPM_LATEST_URL,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": f"korean-worship-setlist/{installed}",
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            raw_ver = data.get("version") if isinstance(data, dict) else None
            latest = str(raw_ver).strip() if raw_ver else None
            if cache_path is not None or state_dir.exists():
                try:
                    c_path.parent.mkdir(parents=True, exist_ok=True)
                    c_path.write_text(
                        json.dumps({"checked_at": now, "latest_version": latest}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
        except Exception:
            if cache_path is not None or state_dir.exists():
                try:
                    c_path.parent.mkdir(parents=True, exist_ok=True)
                    c_path.write_text(
                        json.dumps({"checked_at": now - ttl_seconds + 21600, "latest_version": None}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            return None

    inst_t = _parse_semver_tuple(installed)
    lat_t = _parse_semver_tuple(latest)
    if inst_t and lat_t and lat_t > inst_t:
        latest_clean = str(latest).strip().lstrip("v")
        cmd = _resolve_upgrade_command(s_dir, home_dir=home_dir)
        return {
            "directive": "UPDATE_AVAILABLE",
            "installed_version": installed,
            "latest_version": latest_clean,
            "upgrade_command": cmd,
            "message": (
                f"새 버전(v{latest_clean}, 현재 v{installed})이 출시되었습니다. "
                f"`{cmd}`로 교회 데이터를 유지한 채 업데이트할 수 있습니다."
            ),
        }
    return None
