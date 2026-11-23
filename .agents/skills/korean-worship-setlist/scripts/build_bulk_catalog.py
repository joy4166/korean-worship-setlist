"""Build a 3,000+ song Korean worship catalog in worship.db from 4 public metadata layers without storing full lyrics."""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from analyze_audio import correct_bpm_and_classify
from common import (
    DATA_DIR,
    LOCAL_DB_PATH,
    SEED_SONGS_PATH,
    WORSHIP_DB_PATH,
    configure_utf8_stdio,
    connect_dbs,
    is_blocked_source,
    json_dumps_ko,
    load_blocked_sources,
    normalize_nfc,
    normalize_scripture_ref,
    normalize_title,
    parse_key_signature,
    slugify_song,
    sync_songs_fts,
)
from init_db import merge_arrangement, upsert_song_record

CACHE_DIR = DATA_DIR / "_cache"
HYMNAL_TAR_URL = "https://codeload.github.com/joshuakeum9-cell/hymnal/tar.gz/refs/heads/main"
KCCM_SONGS_URL = "https://kccm.app/songs"
KCCM_LLMS_URL = "https://kccm.app/llms-full.txt"

NOISE_TITLE_PATTERNS = re.compile(
    r"(?:\binst\.?\b|instrumental|\bmr\b|\bbgm\b|반주|연주곡|설교|메시지|기도회\s*\(연주\)|"
    r"\bcommentary\b|\bskit\b|\bnarration\b|\baccompaniment\b|\binterlude\b|\bprelude\b|\boutro\b|\bintro\b|\bmedley\s*intro\b|"
    r"나레이션|축도|개회|폐회|찬양인도|말씀\s*나눔|기도\s*제목|트랙\s*\d+)",
    re.IGNORECASE,
)

# Verified Korean worship teams & classic gospel artists (curated whitelist)
ARTIST_CATALOG_TARGETS: list[tuple[str, str, list[str]]] = [
    # (canonical_team_name, default_category, search_queries)
    ("마커스워십", "modern_worship", ["마커스워십", "Markers Worship", "마커스"]),
    ("어노인팅", "modern_worship", ["어노인팅", "Anointing"]),
    ("제이어스", "modern_worship", ["제이어스", "J-US"]),
    ("위러브", "modern_worship", ["위러브", "WELOVE"]),
    ("아이자야씩스티원", "modern_worship", ["아이자야씩스티원", "Isaiah 6tyOne"]),
    ("예수전도단", "modern_worship", ["예수전도단", "예수전도단 화요모임", "예수전도단 캠퍼스워십"]),
    ("예람워십", "modern_worship", ["예람워십", "Yeram Worship"]),
    ("팀룩워십", "modern_worship", ["팀룩워십", "Team Luke Worship"]),
    ("피아워십", "modern_worship", ["피아워십", "F.I.A WORSHIP"]),
    ("천관웅", "modern_worship", ["천관웅", "뉴제네레이션워십"]),
    ("디사이플스", "modern_worship", ["디사이플스", "Disciples Worship"]),
    ("레위지파", "modern_worship", ["레위지파", "Tribe of Levi"]),
    ("브라운워십", "modern_worship", ["브라운워십", "Brown Worship"]),
    ("히즈윌", "modern_worship", ["히즈윌", "HisWill"]),
    ("손경민", "modern_worship", ["손경민"]),
    ("김브라이언", "modern_worship", ["김브라이언"]),
    ("지선", "modern_worship", ["지선 CCM", "찬양사역자 지선"]),
    ("강찬", "modern_worship", ["강찬"]),
    ("아이빅밴드", "modern_worship", ["아이빅밴드"]),
    ("헤리티지", "modern_worship", ["헤리티지", "헤리티지 매스콰이어"]),
    ("온누리워십", "modern_worship", ["온누리워십", "온누리교회"]),
    ("제이워십", "modern_worship", ["제이워십"]),
    ("달빛마을", "modern_worship", ["달빛마을"]),
    ("러빔", "modern_worship", ["러빔"]),
    ("김복유", "modern_worship", ["김복유"]),
    ("소진영", "modern_worship", ["소진영"]),
    ("전은주", "modern_worship", ["전은주"]),
    ("심종호", "modern_worship", ["심종호"]),
    ("강명식", "modern_worship", ["강명식"]),
    ("김윤진", "modern_worship", ["김윤진 간사"]),
    ("남빈", "modern_worship", ["남빈"]),
    ("부흥한국", "modern_worship", ["부흥한국"]),
    ("한국컨티넨탈싱어즈", "classic_gospel", ["컨티넨탈싱어즈", "한국컨티넨탈싱어즈"]),
    ("워십빌더스", "modern_worship", ["워십빌더스"]),
    ("찬미워십", "modern_worship", ["찬미워십"]),
    ("조찬미", "modern_worship", ["조찬미"]),
    ("유은성", "modern_worship", ["유은성"]),
    ("PK", "modern_worship", ["피케이 PK", "Promise Keepers"]),
    ("시선", "modern_worship", ["시선 CCM", "김명선"]),
    ("주리", "modern_worship", ["주리 CCM"]),
    ("이윤화", "modern_worship", ["이윤화"]),
    ("함부영", "modern_worship", ["함부영"]),
    ("다윗의장막", "modern_worship", ["다윗의장막", "스캇 브래너"]),
    ("리조이스", "modern_worship", ["리조이스"]),
    ("어웨이크닝", "modern_worship", ["어웨이크닝"]),
    ("남궁송옥", "modern_worship", ["남궁송옥"]),
    ("박지현", "modern_worship", ["박지현 CCM"]),
    ("정신호", "modern_worship", ["정신호"]),
    ("원유경", "modern_worship", ["원유경"]),
    ("히즈가든", "modern_worship", ["히즈가든"]),
    ("지명현", "modern_worship", ["지명현"]),
    ("민호기", "modern_worship", ["민호기", "소망의바다"]),
    ("구현화", "modern_worship", ["구현화"]),
    ("동방현주", "modern_worship", ["동방현주"]),
    ("송솔나무", "modern_worship", ["송솔나무"]),
    ("김명식", "classic_gospel", ["김명식"]),
    ("올네이션스 경배와찬양", "classic_gospel", ["경배와찬양", "올네이션스"]),
    ("다윗과요나단", "classic_gospel", ["다윗과요나단"]),
    ("소리엘", "classic_gospel", ["소리엘"]),
    ("옹기장이", "classic_gospel", ["옹기장이"]),
    ("주찬양선교단", "classic_gospel", ["주찬양선교단", "최덕신"]),
    ("시와그림", "classic_gospel", ["시와그림"]),
    ("송정미", "classic_gospel", ["송정미"]),
    ("박종호", "classic_gospel", ["박종호"]),
    ("고형원", "classic_gospel", ["고형원"]),
    ("조준모", "classic_gospel", ["조준모"]),
    ("좋은씨앗", "classic_gospel", ["좋은씨앗"]),
    ("꿈이있는자유", "classic_gospel", ["꿈이있는자유", "한웅재", "정종원"]),
    ("김석균", "classic_gospel", ["김석균"]),
    ("최용덕", "classic_gospel", ["최용덕"]),
    ("에이멘", "classic_gospel", ["에이멘 CCM"]),
    ("노문환", "classic_gospel", ["노문환"]),
    ("전용대", "classic_gospel", ["전용대"]),
    ("김도현", "classic_gospel", ["김도현"]),
    ("이천", "classic_gospel", ["이천"]),
    ("하스데야", "classic_gospel", ["하스데야"]),
    ("김수지", "classic_gospel", ["김수지"]),
    ("나무엔", "classic_gospel", ["나무엔"]),
    ("조수아", "classic_gospel", ["조수아"]),
    ("축복의사람", "classic_gospel", ["축복의사람"]),
    ("아침", "classic_gospel", ["아침 CCM"]),
    ("사랑이야기", "classic_gospel", ["사랑이야기 CCM"]),
    ("유효림", "modern_worship", ["유효림"]),
    ("장윤영", "modern_worship", ["장윤영"]),
    ("한성워십", "modern_worship", ["한성워십"]),
    ("광야아트미니스트리", "modern_worship", ["광야"]),
    ("브리지임팩트", "modern_worship", ["브리지임팩트"]),
    ("라이즈업", "modern_worship", ["라이즈업"]),
    ("팀조슈아", "modern_worship", ["팀조슈아"]),
    ("램프", "modern_worship", ["램프 Lamp"]),
    ("헤세드", "modern_worship", ["헤세드"]),
    ("조이풀", "modern_worship", ["조이풀"]),
    ("강중현", "modern_worship", ["강중현"]),
    ("류수영", "classic_gospel", ["류수영"]),
    ("김영범", "classic_gospel", ["김영범"]),
    ("배다해", "modern_worship", ["배다해"]),
    ("주영광", "modern_worship", ["주영광"]),
    ("카르디아", "modern_worship", ["카르디아"]),
    ("찬송가", "hymn", ["한국찬송가"]),
]

# Semantic domain clusters for title/theme inference
SEMANTIC_CLUSTERS: list[dict[str, Any]] = [
    {
        "keywords": ["십자가", "보혈", "피", "고난", "갈보리", "겟세마네", "수난", "어린양", "죽임", "cross", "blood", "calvary"],
        "seasons": ["lent", "holy_week", "communion", "ordinary"],
        "stages": ["meditation", "confession", "dedication"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "cross_sacrifice",
        "tags": ["십자가", "보혈", "대속", "고난주간", "사순절"],
        "tempo": ("ballad", 68),
    },
    {
        "keywords": ["부활", "무덤", "살아", "이기", "승리", "생명", "권세", "사망", "resurrection", "alive", "victory"],
        "seasons": ["easter", "ordinary"],
        "stages": ["opener", "declaration"],
        "slots": ["worship_set", "pre_service"],
        "tone": "resurrection_victory",
        "tags": ["부활", "승리", "생명", "복음"],
        "tempo": ("fast", 122),
    },
    {
        "keywords": ["성탄", "아기", "구유", "베들레헴", "메리", "캐롤", "고요한 밤", "기쁘다 구주", "christmas", "noel"],
        "seasons": ["christmas", "advent"],
        "stages": ["opener", "declaration", "meditation"],
        "slots": ["worship_set", "introit_doxology"],
        "tone": "adoration",
        "tags": ["성탄절", "성육신", "구주", "기쁨"],
        "tempo": ("medium", 92),
    },
    {
        "keywords": ["오소서", "임마누엘", "다시 오실", "재림", "마라나타", "기다리", "대림", "immanuel", "maranatha"],
        "seasons": ["advent", "ordinary", "watchnight"],
        "stages": ["meditation", "declaration"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "comfort_trust",
        "tags": ["대림절", "임마누엘", "재림", "소망"],
        "tempo": ("ballad", 72),
    },
    {
        "keywords": ["성찬", "떡", "잔", "살과 피", "만찬", "주님의 상", "communion"],
        "seasons": ["communion", "lent", "holy_week"],
        "stages": ["meditation", "confession"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "cross_sacrifice",
        "tags": ["성찬", "기념", "연합", "보혈"],
        "tempo": ("ballad", 66),
    },
    {
        "keywords": ["세례", "거듭", "새 생명", "물과 성령", "baptism", "born again"],
        "seasons": ["baptism", "easter", "ordinary"],
        "stages": ["confession", "declaration"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "resurrection_victory",
        "tags": ["세례", "거듭남", "새생명", "고백"],
        "tempo": ("medium", 88),
    },
    {
        "keywords": ["추수", "감사", "곡식", "열매", "감사의", "은택", "thanks", "harvest"],
        "seasons": ["thanksgiving", "ordinary"],
        "stages": ["opener", "declaration", "dedication"],
        "slots": ["worship_set", "offertory"],
        "tone": "adoration",
        "tags": ["감사", "추수감사절", "은혜", "열매"],
        "tempo": ("medium", 96),
    },
    {
        "keywords": ["성령", "부흥", "불", "단비", "기름", "생수", "성령강림", "spirit", "revival", "fire"],
        "seasons": ["pentecost", "ordinary", "watchnight"],
        "stages": ["opener", "declaration", "dedication"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "adoration",
        "tags": ["성령", "부흥", "성령강림절", "임재"],
        "tempo": ("medium", 88),
    },
    {
        "keywords": ["광야", "눈물", "아픔", "상처", "위로", "평안", "안식", "피난처", "폭풍", "어둠", "쉼", "지친", "고단"],
        "seasons": ["ordinary", "lent", "watchnight"],
        "stages": ["meditation", "confession"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "comfort_trust",
        "tags": ["위로", "광야", "평안", "회복", "동행"],
        "tempo": ("ballad", 70),
    },
    {
        "keywords": ["회개", "죄", "용서", "정결", "돌아가", "상한", "겸손", "부족", "연약", "자복"],
        "seasons": ["lent", "holy_week", "ordinary"],
        "stages": ["confession", "meditation"],
        "slots": ["worship_set", "sermon_response"],
        "tone": "penitence",
        "tags": ["회개", "정결", "겸손", "용서", "회복"],
        "tempo": ("ballad", 68),
    },
    {
        "keywords": ["축복", "파송", "사랑받기", "담장", "평강", "걸음", "이웃", "세상", "열방", "선교", "소명", "부르심"],
        "seasons": ["ordinary", "epiphany", "watchnight", "thanksgiving"],
        "stages": ["dedication", "sending"],
        "slots": ["sending_benediction", "sermon_response", "offertory"],
        "tone": "mission_sending",
        "tags": ["파송", "선교", "축복", "소명", "헌신"],
        "tempo": ("medium", 82),
    },
    {
        "keywords": ["헌신", "순종", "봉헌", "드리", "바치", "결단", "제자", "따르", "기꺼이", "내 삶", "온 맘"],
        "seasons": ["ordinary", "watchnight", "lent"],
        "stages": ["dedication", "confession"],
        "slots": ["sermon_response", "offertory", "worship_set"],
        "tone": "mission_sending",
        "tags": ["헌신", "순종", "결단", "봉헌", "제자"],
        "tempo": ("ballad", 72),
    },
    {
        "keywords": ["송영", "입례", "거룩", "삼위", "만복", "보좌", "경배", "영광", "찬송", "할렐루야", "호산나", "왕", "높이", "glory", "holy", "king", "hosanna"],
        "seasons": ["ordinary", "easter", "epiphany", "thanksgiving"],
        "stages": ["opener", "declaration"],
        "slots": ["introit_doxology", "worship_set", "pre_service"],
        "tone": "adoration",
        "tags": ["경배", "영광", "거룩", "왕", "찬양"],
        "tempo": ("fast", 118),
    },
    {
        "keywords": ["기쁨", "춤", "뛰며", "외쳐", "즐거", "소리 높여", "손뼉", "선포", "찬양하세", "joy", "rejoice", "shout"],
        "seasons": ["ordinary", "easter", "thanksgiving"],
        "stages": ["opener", "declaration"],
        "slots": ["pre_service", "worship_set"],
        "tone": "adoration",
        "tags": ["기쁨", "선포", "찬양", "입례"],
        "tempo": ("fast", 124),
    },
    {
        "keywords": ["은혜", "사랑", "신실", "목자", "인도", "동행", "믿음", "소망", "주님", "예수", "하나님", "grace", "love"],
        "seasons": ["ordinary", "watchnight", "thanksgiving"],
        "stages": ["meditation", "declaration", "confession"],
        "slots": ["worship_set", "sermon_response", "offertory"],
        "tone": "comfort_trust",
        "tags": ["은혜", "사랑", "인도하심", "신뢰"],
        "tempo": ("ballad", 74),
    },
]

DEFAULT_CHORD_BY_KEY = {"G": "G", "C": "C", "D": "D", "A": "A", "E": "E", "F": "F", "Bb": "Bb", "Eb": "Eb", "Ab": "Ab"}


def _http_get_bytes(url: str, timeout: int = 20, retries: int = 3) -> bytes:
    headers = {"User-Agent": "WorshipSetlistCatalogBuilder/0.1 (+local-research)"}
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Failed GET {url}: {last_err}")


def _http_get_json(url: str, timeout: int = 15) -> Any:
    return json.loads(_http_get_bytes(url, timeout=timeout).decode("utf-8", errors="ignore"))


def _has_hangul(text: str) -> bool:
    return bool(re.search(r"[가-힣]", normalize_nfc(text)))


def _era_from_year(year: int | None, category: str) -> str:
    if category == "hymn":
        return "traditional"
    if not year:
        return "2010s"
    if year < 1990:
        return "1980s"
    if year < 2000:
        return "1990s"
    if year < 2010:
        return "2000s"
    if year < 2020:
        return "2010s"
    return "2020s"


def infer_worship_semantics(
    title_ko: str,
    theme_hint: str = "",
    category: str = "modern_worship",
    time_signature: str = "4/4",
    duration_sec: int = 0,
    explicit_bpm: int | None = None,
) -> dict[str, Any]:
    """Infer seasons, stages, slots, theological tone, theme summary/tags, and BPM estimate from Korean title/theme."""
    hay = normalize_nfc(f"{title_ko} {theme_hint}").lower()

    matched_clusters: list[dict[str, Any]] = []
    for cl in SEMANTIC_CLUSTERS:
        if any(kw in hay for kw in cl["keywords"]):
            matched_clusters.append(cl)

    if not matched_clusters:
        matched_clusters = [SEMANTIC_CLUSTERS[-1]]

    primary = matched_clusters[0]
    seasons: list[str] = []
    stages: list[str] = []
    slots: list[str] = []
    tags: list[str] = []
    for cl in matched_clusters[:3]:
        for s in cl["seasons"]:
            if s not in seasons:
                seasons.append(s)
        for st in cl["stages"]:
            if st not in stages:
                stages.append(st)
        for sl in cl["slots"]:
            if sl not in slots:
                slots.append(sl)
        for tg in cl["tags"]:
            if tg not in tags:
                tags.append(tg)

    if "ordinary" not in seasons and not any(s in seasons for s in ("christmas", "holy_week", "communion")):
        seasons.append("ordinary")

    tone = primary["tone"]
    if tone == "cross_sacrifice" or tone == "penitence":
        perspective = "god_centered_vertical"
    elif tone == "resurrection_victory":
        perspective = "gospel_proclamation"
    elif tone == "blessing_fellowship":
        perspective = "communal_exhortation"
    else:
        perspective = "god_centered_vertical"

    if explicit_bpm and explicit_bpm > 0:
        bpm_info = correct_bpm_and_classify(
            float(explicit_bpm),
            time_signature=time_signature,
            slot_hint=stages[0] if stages else None,
        )
        bpm_val = bpm_info["bpm"]
        tempo_tier = bpm_info["tempo_tier"]
    else:
        # Infer tempo tier and BPM thoughtfully from meter, duration, and primary cluster
        default_tier, default_bpm = primary["tempo"]
        if time_signature in ("6/8", "12/8"):
            tempo_tier = "ballad"
            bpm_val = 56
        elif time_signature == "3/4":
            tempo_tier = "ballad" if category == "hymn" else default_tier
            bpm_val = 72 if tempo_tier == "ballad" else 86
        elif duration_sec and duration_sec >= 330 and default_tier == "fast":
            # Very long worship tracks (>5m30s) with ballad cues lean slower/soaking unless explicit opener
            tempo_tier = "medium"
            bpm_val = 84
        elif duration_sec and 140 <= duration_sec <= 225 and category == "modern_worship" and default_tier != "ballad":
            tempo_tier = "fast"
            bpm_val = 120
        else:
            tempo_tier = default_tier
            bpm_val = default_bpm

    # Ensure meter-consistent tempo_tier classification
    bpm_info_final = correct_bpm_and_classify(float(bpm_val), time_signature=time_signature, slot_hint="fast" if tempo_tier == "fast" else None)
    bpm_val = bpm_info_final["bpm"]
    tempo_tier = bpm_info_final["tempo_tier"]

    summary_prefix = f"[{normalize_nfc(theme_hint).strip()}] " if theme_hint and theme_hint.strip() else ""
    theme_summary = f"{summary_prefix}{title_ko} — {', '.join(tags[:3])} 주제의 찬양"
    if category == "hymn":
        sections = ["I", "V1", "V2", "V3", "V4", "O"]
        form = "I(4m) - V1 - V2 - V3 - O"
    elif tempo_tier == "fast":
        sections = ["I", "V1", "PC", "C", "Inter", "B", "O"]
        form = "I(4m) - V1 - PC - C(x2) - B(x2) - C(x2) - O"
    else:
        sections = ["I", "V1", "V2", "C", "Inter", "B", "O"]
        form = "I(4m) - V1 - C - Inter(4m) - V2 - C(x2) - O"

    return {
        "church_seasons": seasons,
        "service_slots": slots,
        "liturgical_stages": stages,
        "theological_tone": tone,
        "lyrical_perspective": perspective,
        "theological_core": f"{', '.join(tags[:4])}의 성경적 메시지를 담은 {('전통 찬송' if category == 'hymn' else '회중 찬양')}",
        "theme_summary": theme_summary,
        "theme_tags": tags[:6],
        "available_sections": sections,
        "default_song_form": form,
        "bpm": bpm_val,
        "tempo_tier": tempo_tier,
    }


def _split_korean_english_title(raw_title: str) -> tuple[str, str | None]:
    """Clean Live/Feat tags and split mixed '한글 제목 English Title' tracks."""
    t = normalize_nfc(raw_title).strip()
    # Strip trailing live/version/featuring annotations
    t = re.sub(
        r"\s*[\(\[]\s*(?:live|studio|ver\.?|version|feat\.?[^\)\]]*|solo[^\)\]]*|reprise|radio\s*edit|remaster(?:ed)?|20\d{2}[^\)\]]*|with\s+[^\)\]]*)\s*[\)\]]",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(r"\s*[-–—]\s*(?:live|studio\s*ver\.?)\s*$", "", t, flags=re.IGNORECASE).strip()

    # Split mixed Hangul + trailing English subtitle (e.g. '지금 이 자리에서 Here at This Place')
    m = re.match(r"^([가-힣0-9\s,'\"·?!~]+?)\s+([A-Za-z][A-Za-z0-9\s,'\"?!.\-&]+)$", t)
    if m and _has_hangul(m.group(1)):
        ko = m.group(1).strip(" -–—")
        en = m.group(2).strip(" -–—")
        return ko, en
    return t, None


def _normalize_meter(raw_meter: str | None) -> str:
    m = (raw_meter or "4/4").strip()
    if "6/8" in m:
        return "6/8"
    if "12/8" in m:
        return "12/8"
    if "3/4" in m:
        return "3/4"
    if "9/8" in m:
        return "6/8"
    return "4/4"


def _normalize_hymn_key(raw_key: str | None) -> str:
    k = (raw_key or "C").strip()
    # Handle cases like "Ab / Fm" or "C, D"
    first = re.split(r"[/,;]+", k)[0].strip()
    return parse_key_signature(first)[0]


def fetch_hymnal_metadata(cache_dir: Path = CACHE_DIR) -> list[dict[str, Any]]:
    """Download joshuakeum9-cell/hymnal tarball once and extract ONLY meta.json (zero source.json lyrics)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tar_path = cache_dir / "hymnal-main.tar.gz"
    if not tar_path.exists():
        raw = _http_get_bytes(HYMNAL_TAR_URL, timeout=30)
        tar_path.write_bytes(raw)

    items: list[dict[str, Any]] = []
    with tarfile.open(tar_path, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            # Strictly read ONLY meta.json, never source.json lyrics
            if re.search(r"/data/hymns/\d{3}/meta\.json$", member.name.replace("\\", "/")):
                f = tf.extractfile(member)
                if f is None:
                    continue
                meta = json.loads(f.read().decode("utf-8"))
                items.append(meta)
    items.sort(key=lambda x: int(x.get("number", 0)))
    return items


def fetch_kccm_public_songs(cache_dir: Path = CACHE_DIR) -> list[dict[str, Any]]:
    """Fetch public KCCM /songs initialSongs + /llms-full.txt (explicitly allowed by robots.txt)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "kccm_public.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    songs_list: list[dict[str, Any]] = []
    try:
        html = _http_get_bytes(KCCM_SONGS_URL, timeout=15).decode("utf-8", errors="ignore")
        pushes = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)', html)
        joined = "".join(json.loads(p) for p in pushes)
        idx = joined.find('"initialSongs":[')
        if idx != -1:
            decoder = json.JSONDecoder()
            arr, _ = decoder.raw_decode(joined[idx + len('"initialSongs":') :])
            for it in arr:
                songs_list.append({"source": "kccm_songs", **it})
    except Exception:
        pass

    try:
        llms_txt = _http_get_bytes(KCCM_LLMS_URL, timeout=15).decode("utf-8", errors="ignore")
        # Extract chart / song entries formatted like '- **1위**: 제목 — 아티스트 (...)'
        for line in llms_txt.splitlines():
            m = re.match(r"^\s*-\s+\*\*[^*]+\*\*:\s+(.+?)\s+—\s+([^|(]+)", line)
            if m:
                songs_list.append(
                    {
                        "source": "kccm_llms",
                        "title": m.group(1).strip(),
                        "artist": m.group(2).strip(),
                    }
                )
    except Exception:
        pass

    cache_file.write_text(json_dumps_ko(songs_list), encoding="utf-8")
    return songs_list


def _verify_korean_deezer_artist(artist_obj: dict[str, Any], query: str, throttle: float) -> bool:
    """Verify artist is genuinely a Korean worship/CCM artist (prevents homonym pollution)."""
    name_nfc = normalize_nfc(artist_obj.get("name", ""))
    if _has_hangul(name_nfc):
        return True
    # For English artist names (e.g. 'Markers Worship', 'WELOVE', 'J-US'), inspect top tracks for Hangul titles
    aid = artist_obj["id"]
    time.sleep(throttle)
    try:
        top = _http_get_json(f"https://api.deezer.com/artist/{aid}/top?limit=15")
        hangul_tracks = sum(1 for tr in top.get("data", []) if _has_hangul(tr.get("title", "")))
        if hangul_tracks >= 2:
            return True
        # Also check query alignment for small Korean teams with English track titles
        if normalize_title(query) in normalize_title(name_nfc) and hangul_tracks >= 1:
            return True
    except Exception:
        return False
    return False


CHRISTIAN_VERIFY_RE = re.compile(
    r"(예수|하나님|주님|찬양|은혜|십자가|복음|성령|워십|ccm|기도|구원|주의|여호와|예배|할렐루야|임재|부흥|영광|축복|찬송|주께서|주여|주만|주를|그리스도|아버지|보혈|갈보리|거룩)",
    re.IGNORECASE,
)

WORSHIP_ALBUM_SEARCH_QUERIES: list[tuple[str, str, str]] = [
    # (search_query, canonical_team, default_category)
    ("마커스", "마커스워십", "modern_worship"),
    ("Markers Worship", "마커스워십", "modern_worship"),
    ("위러브", "위러브", "modern_worship"),
    ("WELOVE", "위러브", "modern_worship"),
    ("제이어스", "제이어스", "modern_worship"),
    ("J-US", "제이어스", "modern_worship"),
    ("아이자야씩스티원", "아이자야씩스티원", "modern_worship"),
    ("Isaiah 6tyOne", "아이자야씩스티원", "modern_worship"),
    ("예수전도단", "예수전도단", "modern_worship"),
    ("화요모임", "예수전도단", "modern_worship"),
    ("캠퍼스워십", "예수전도단", "modern_worship"),
    ("시와 그림", "시와그림", "classic_gospel"),
    ("히즈윌", "히즈윌", "modern_worship"),
    ("Hiswill", "히즈윌", "modern_worship"),
    ("레위지파", "레위지파", "modern_worship"),
    ("디사이플스", "디사이플스", "modern_worship"),
    ("브라운워십", "브라운워십", "modern_worship"),
    ("팀룩워십", "팀룩워십", "modern_worship"),
    ("아가파오 워십", "아가파오워십", "modern_worship"),
    ("뉴제네레이션워십", "천관웅", "modern_worship"),
    ("부흥", "부흥한국", "classic_gospel"),
    ("소리엘", "소리엘", "classic_gospel"),
    ("박종호", "박종호", "classic_gospel"),
    ("최인혁", "최인혁", "classic_gospel"),
    ("강찬", "강찬", "modern_worship"),
    ("유은성", "유은성", "modern_worship"),
    ("한웅재", "꿈이있는자유", "classic_gospel"),
    ("좋은씨앗", "좋은씨앗", "classic_gospel"),
    ("소망의바다", "소망의바다", "classic_gospel"),
    ("다윗의장막", "다윗의장막", "modern_worship"),
    ("헤리티지", "헤리티지", "modern_worship"),
    ("아이빅밴드", "아이빅밴드", "modern_worship"),
    ("복음성가", "복음성가 모음", "classic_gospel"),
    ("경배와 찬양", "올네이션스 경배와찬양", "classic_gospel"),
    ("은혜 찬양", "워십 모음", "modern_worship"),
    ("찬송가", "한국찬송가", "classic_gospel"),
    ("CCM 베스트", "한국 CCM", "modern_worship"),
    ("워십", "한국 워십", "modern_worship"),
    ("찬양집", "한국 워십", "classic_gospel"),
]


def _is_verified_ccm_album(album_title: str, artist_name: str, track_titles: list[str]) -> bool:
    """Verify album tracks/title belong to Korean Christian worship (blocks homonyms like Chopin/pop)."""
    hangul_tracks = [t for t in track_titles if _has_hangul(t)]
    if len(hangul_tracks) < 2:
        return False
    combined = " ".join([album_title, artist_name] + hangul_tracks)
    hits = len(CHRISTIAN_VERIFY_RE.findall(combined))
    # Require either >= 3 Christian keywords across album+tracks or >= 25% of tracks carrying Christian terms
    track_hits = sum(1 for t in hangul_tracks if CHRISTIAN_VERIFY_RE.search(t))
    return hits >= 3 or (track_hits / max(1, len(hangul_tracks))) >= 0.25


def fetch_deezer_worship_catalog(
    cache_dir: Path = CACHE_DIR,
    throttle: float = 0.06,
    max_albums_per_artist: int = 40,
) -> list[dict[str, Any]]:
    """Collect verified Korean worship/gospel discographies & worship albums from public Deezer API."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file_v2 = cache_dir / "deezer_catalog_v2.json"
    if cache_file_v2.exists():
        return json.loads(cache_file_v2.read_text(encoding="utf-8"))

    collected_tracks: list[dict[str, Any]] = []
    seen_track_keys: set[tuple[str, str]] = set()
    legacy_cache = cache_dir / "deezer_catalog.json"
    if legacy_cache.exists():
        for item in json.loads(legacy_cache.read_text(encoding="utf-8")):
            k = (normalize_title(item["raw_title"]), normalize_nfc(item["worship_team"]))
            if k not in seen_track_keys:
                seen_track_keys.add(k)
                collected_tracks.append(item)

    seen_artist_ids: set[int] = set()
    seen_album_ids: set[int] = set()
    candidate_albums: list[tuple[int, str, str]] = []

    # 1. Artist search across ALL queries (do not break after first match so e.g. 마커스 & 마커스워십 both run)
    for canonical_team, default_cat, queries in ARTIST_CATALOG_TARGETS:
        for q in queries:
            time.sleep(throttle)
            try:
                res = _http_get_json(
                    f"https://api.deezer.com/search/artist?limit=6&q={urllib.parse.quote(q)}"
                )
            except Exception:
                continue
            for art in res.get("data", []):
                aid = int(art["id"])
                if aid in seen_artist_ids:
                    continue
                art_name = normalize_nfc(art.get("name", ""))
                q_norm = normalize_title(q)
                a_norm = normalize_title(art_name)
                if q_norm not in a_norm and a_norm not in q_norm:
                    if normalize_title(canonical_team) not in a_norm:
                        continue
                if _verify_korean_deezer_artist(art, q, throttle):
                    seen_artist_ids.add(aid)
                    time.sleep(throttle)
                    try:
                        alb_res = _http_get_json(
                            f"https://api.deezer.com/artist/{aid}/albums?limit={max_albums_per_artist}"
                        )
                        for alb in alb_res.get("data", []):
                            candidate_albums.append((int(alb["id"]), canonical_team, default_cat))
                    except Exception:
                        pass

    # 2. Album search for major teams, homonyms, and Korean worship/gospel compilations
    for q, canonical_team, default_cat in WORSHIP_ALBUM_SEARCH_QUERIES:
        time.sleep(throttle)
        try:
            alb_search = _http_get_json(
                f"https://api.deezer.com/search/album?limit=35&q={urllib.parse.quote(q)}"
            )
        except Exception:
            continue
        for alb in alb_search.get("data", []):
            candidate_albums.append((int(alb["id"]), canonical_team, default_cat))

    for alb_id, fallback_team, default_cat in candidate_albums:
        if alb_id in seen_album_ids:
            continue
        seen_album_ids.add(alb_id)
        time.sleep(throttle)
        try:
            alb_detail = _http_get_json(f"https://api.deezer.com/album/{alb_id}")
        except Exception:
            continue

        album_title = normalize_nfc(alb_detail.get("title") or "")
        artist_name = normalize_nfc((alb_detail.get("artist") or {}).get("name") or "")
        tracks_data = alb_detail.get("tracks", {}).get("data", [])
        track_titles = [normalize_nfc(t.get("title") or "") for t in tracks_data]
        if not _is_verified_ccm_album(album_title, artist_name, track_titles):
            continue

        release_date = alb_detail.get("release_date") or ""
        year = int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None
        team_label = artist_name if (_has_hangul(artist_name) and "various" not in artist_name.lower()) else fallback_team

        for tr in tracks_data:
            raw_title = normalize_nfc(tr.get("title") or "")
            duration = int(tr.get("duration") or 0)
            if not raw_title or duration < 55:
                continue
            if NOISE_TITLE_PATTERNS.search(raw_title):
                continue
            k = (normalize_title(raw_title), team_label)
            if k in seen_track_keys:
                continue
            seen_track_keys.add(k)
            collected_tracks.append(
                {
                    "raw_title": raw_title,
                    "worship_team": team_label,
                    "default_category": default_cat,
                    "album_title": album_title,
                    "release_year": year,
                    "duration": duration,
                    "bpm": int(tr.get("bpm") or 0),
                }
            )

    cache_file_v2.write_text(json_dumps_ko(collected_tracks), encoding="utf-8")
    return collected_tracks


def build_catalog(
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
    min_target_songs: int = 3000,
    skip_network: bool = False,
) -> dict[str, Any]:
    """Run the 4-layer bulk catalog build pipeline and return summary statistics."""
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=True, ensure_schemas=True)
    gate = load_blocked_sources()

    # Clear songs/arrangements for clean rebuild while preserving local church_local.db!
    with conn:
        conn.execute("DELETE FROM arrangements")
        conn.execute("DELETE FROM songs")

        # ------------------------------------------------------------------
        # Layer 1: Golden Seeds (confidence = 0.98)
        # ------------------------------------------------------------------
        seeds = json.loads(SEED_SONGS_PATH.read_text(encoding="utf-8"))
        for item in seeds:
            item_copy = json.loads(json.dumps(item, ensure_ascii=False))
            for arr in item_copy.get("arrangements", []):
                arr["confidence"] = 0.98
            upsert_song_record(conn, item_copy, sync_fts=False)

        # Build lookup maps for deduplication & arrangement merging
        norm_to_song_id: dict[str, int] = {}
        hymn_to_song_id: dict[int, int] = {}
        for r in conn.execute("SELECT id, normalized_title, alternate_titles, hymn_number FROM songs").fetchall():
            norm_to_song_id[r["normalized_title"]] = r["id"]
            for alt in json.loads(r["alternate_titles"] or "[]"):
                na = normalize_title(alt)
                if na:
                    norm_to_song_id.setdefault(na, r["id"])
            if r["hymn_number"]:
                hymn_to_song_id[int(r["hymn_number"])] = r["id"]

        hymnal_count = 0
        kccm_count = 0
        deezer_count = 0

        if not skip_network:
            # ------------------------------------------------------------------
            # Layer 2: New Hymnal 645 (joshuakeum9-cell/hymnal meta.json only)
            # ------------------------------------------------------------------
            hymns = fetch_hymnal_metadata()
            for hm in hymns:
                h_num = int(hm["number"])
                old_num = hm.get("old_number")
                title_ko = normalize_nfc(hm.get("title_ko") or f"찬송가 {h_num}장").strip()
                title_en = normalize_nfc(hm.get("title_en")).strip() or None
                theme_str = normalize_nfc(hm.get("theme") or "경배").strip()
                meter = _normalize_meter(hm.get("time_table"))
                key_sig = _normalize_hymn_key(hm.get("key_table"))
                sem = infer_worship_semantics(title_ko, theme_hint=theme_str, category="hymn", time_signature=meter)

                lyricist = normalize_nfc(hm.get("lyricist")).strip() or None
                composer = normalize_nfc(hm.get("composer")).strip() or None
                blocked, _ = is_blocked_source(title_ko, lyricist, composer, blocked=gate)
                if blocked:
                    continue

                rights_ko = ((hm.get("rights") or {}).get("text_ko") or {}).get("status", "")
                copy_scope = "khs_hymn" if rights_ko == "claimed" else "public_domain"
                norm_t = normalize_title(title_ko)

                arr_payload = {
                    "worship_team": "표준/원곡",
                    "key_signature": key_sig,
                    "time_signature": meter,
                    "bpm": sem["bpm"],
                    "tempo_tier": sem["tempo_tier"],
                    "groove_style": "6_8_worship" if meter == "6/8" else "acoustic",
                    "intro_first_chord": key_sig.split()[0],
                    "outro_last_chord": key_sig.split()[0],
                    "ending_type": "ritardando",
                    "congregation_singability": "hymn_traditional",
                    "band_dependency": "low",
                    "session_fit": ["piano_only", "acoustic", "full_band"],
                    "arrangement_song_form": sem["default_song_form"],
                    "recorded_year": hm.get("year") if isinstance(hm.get("year"), int) else None,
                    "key_source": "hymnal_meta",
                    "bpm_source": "manual_seed",
                    "confidence": 0.95,
                }

                existing_id = hymn_to_song_id.get(h_num)
                if existing_id is not None:
                    # Keep golden seed metadata, update unified hymn numbers if missing, merge arrangement
                    conn.execute(
                        "UPDATE songs SET hymn_number = COALESCE(hymn_number, ?), hymn_number_unified = COALESCE(hymn_number_unified, ?) WHERE id = ?",
                        (h_num, old_num, existing_id),
                    )
                    merge_arrangement(conn, existing_id, arr_payload)
                else:
                    slug = f"hymn-{h_num:03d}-{slugify_song(title_ko, 'hymn')[-6:]}"
                    sid = upsert_song_record(
                        conn,
                        {
                            "song_slug": slug,
                            "title_ko": title_ko,
                            "title_en": title_en,
                            "alternate_titles": [f"찬송가 {h_num}장"] + ([f"통일 {old_num}장"] if old_num else []),
                            "first_line_incipit": title_ko[:15],
                            "category": "hymn",
                            "hymn_number": h_num,
                            "hymn_number_unified": old_num,
                            "era": "traditional",
                            "original_artist": "새찬송가",
                            "release_year": hm.get("year") if isinstance(hm.get("year"), int) else None,
                            "adoption_tier": "core_standard",
                            "lyric_accessibility": "standard_church",
                            "church_seasons": sem["church_seasons"],
                            "service_slots": sem["service_slots"],
                            "liturgical_stages": sem["liturgical_stages"],
                            "theological_tone": sem["theological_tone"],
                            "lyrical_perspective": sem["lyrical_perspective"],
                            "theological_core": sem["theological_core"],
                            "theme_summary": sem["theme_summary"],
                            "theme_tags": sem["theme_tags"],
                            "available_sections": sem["available_sections"],
                            "default_song_form": sem["default_song_form"],
                            "lyricist": lyricist,
                            "composer": composer,
                            "copyright_holder": "Public Domain / 한국찬송가공회",
                            "copyright_scope": copy_scope,
                            "source_status": "verified_official",
                            "arrangements": [arr_payload],
                        },
                        sync_fts=False,
                    )
                    norm_to_song_id.setdefault(norm_t, sid)
                    hymn_to_song_id[h_num] = sid
                hymnal_count += 1

            # ------------------------------------------------------------------
            # Layer 3: KCCM Public Songs (confidence = 0.92)
            # ------------------------------------------------------------------
            kccm_items = fetch_kccm_public_songs()
            for km in kccm_items:
                raw_t = normalize_nfc(km.get("title") or "")
                artist = normalize_nfc(km.get("artist") or "찬양사역자").strip()
                if not raw_t:
                    continue
                blocked, _ = is_blocked_source(raw_t, artist, blocked=gate)
                if blocked:
                    continue

                title_ko, title_en = _split_korean_english_title(raw_t)
                norm_t = normalize_title(title_ko)
                if not norm_t:
                    continue

                orig_key = parse_key_signature(km.get("originalKey") or "G")[0]
                raw_bpm = int(km.get("bpm") or 0) or None
                themes_list = km.get("themes") if isinstance(km.get("themes"), list) else []
                moods_list = km.get("moods") if isinstance(km.get("moods"), list) else []
                theme_hint = " ".join(str(x) for x in (themes_list + moods_list))
                sem = infer_worship_semantics(title_ko, theme_hint=theme_hint, explicit_bpm=raw_bpm)

                low_note, high_note = None, None
                vr = km.get("vocalRange") or ""
                vr_m = re.match(r"([A-G][#b]?\d)\s*[~\-]\s*([A-G][#b]?\d)", vr, re.IGNORECASE)
                if vr_m:
                    low_note, high_note = vr_m.group(1), vr_m.group(2)

                yt_id = km.get("youtubeId")
                yt_url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else None
                refs = [normalize_scripture_ref(km["scripture"])] if km.get("scripture") else []

                arr_payload = {
                    "worship_team": artist,
                    "key_signature": orig_key,
                    "time_signature": "4/4",
                    "bpm": sem["bpm"],
                    "tempo_tier": sem["tempo_tier"],
                    "intro_first_chord": orig_key.split()[0],
                    "outro_last_chord": orig_key.split()[0],
                    "lowest_note": low_note,
                    "highest_note": high_note,
                    "band_dependency": "low" if sem["tempo_tier"] == "ballad" else "medium",
                    "session_fit": ["full_band", "acoustic", "piano_only"] if sem["tempo_tier"] == "ballad" else ["full_band", "acoustic"],
                    "arrangement_song_form": sem["default_song_form"],
                    "youtube_url": yt_url,
                    "key_source": "kccm_verified" if km.get("originalKey") else "catalog_default",
                    "bpm_source": "kccm_verified" if raw_bpm else "catalog_estimated",
                    "confidence": 0.92 if km.get("originalKey") else 0.65,
                }

                existing_id = norm_to_song_id.get(norm_t)
                if existing_id is not None:
                    merge_arrangement(conn, existing_id, arr_payload)
                else:
                    sid = upsert_song_record(
                        conn,
                        {
                            "title_ko": title_ko,
                            "title_en": title_en,
                            "first_line_incipit": title_ko[:15],
                            "category": "modern_worship",
                            "era": "2010s",
                            "original_artist": artist,
                            "adoption_tier": "widely_sung",
                            "church_seasons": sem["church_seasons"],
                            "service_slots": sem["service_slots"],
                            "liturgical_stages": sem["liturgical_stages"],
                            "theological_tone": sem["theological_tone"],
                            "lyrical_perspective": sem["lyrical_perspective"],
                            "theological_core": sem["theological_core"],
                            "theme_summary": sem["theme_summary"],
                            "theme_tags": sem["theme_tags"],
                            "scripture_refs": refs,
                            "available_sections": sem["available_sections"],
                            "default_song_form": sem["default_song_form"],
                            "source_status": "community_reviewed",
                            "arrangements": [arr_payload],
                        },
                        sync_fts=False,
                    )
                    norm_to_song_id[norm_t] = sid
                kccm_count += 1

            # ------------------------------------------------------------------
            # Layer 4: Verified Korean Worship & Classic Gospel Discographies (Deezer)
            # ------------------------------------------------------------------
            dz_tracks = fetch_deezer_worship_catalog()
            for tr in dz_tracks:
                raw_t = tr["raw_title"]
                team = normalize_nfc(tr["worship_team"]).strip()
                blocked, _ = is_blocked_source(raw_t, team, tr.get("album_title"), blocked=gate)
                if blocked:
                    continue

                title_ko, title_en = _split_korean_english_title(raw_t)
                if not _has_hangul(title_ko):
                    continue

                norm_t = normalize_title(title_ko)
                if not norm_t or len(norm_t) < 2:
                    continue

                cat = tr["default_category"]
                year = tr.get("release_year")
                era = _era_from_year(year, cat)
                if year and year < 2003 and cat == "modern_worship":
                    cat = "classic_gospel"

                raw_bpm = int(tr.get("bpm") or 0) or None
                sem = infer_worship_semantics(
                    title_ko,
                    theme_hint=tr.get("album_title", ""),
                    category=cat,
                    duration_sec=int(tr.get("duration") or 0),
                    explicit_bpm=raw_bpm,
                )

                arr_payload = {
                    "worship_team": team,
                    "key_signature": "G",
                    "time_signature": "4/4",
                    "bpm": sem["bpm"],
                    "tempo_tier": sem["tempo_tier"],
                    "intro_first_chord": "G",
                    "outro_last_chord": "G",
                    "band_dependency": "low" if sem["tempo_tier"] == "ballad" else "medium",
                    "session_fit": ["full_band", "acoustic", "piano_only"] if sem["tempo_tier"] == "ballad" else ["full_band", "acoustic"],
                    "arrangement_song_form": sem["default_song_form"],
                    "recorded_year": year,
                    "key_source": "catalog_default",
                    "bpm_source": "deezer_verified" if raw_bpm else "catalog_estimated",
                    "confidence": 0.45 if not raw_bpm else 0.70,
                }

                existing_id = norm_to_song_id.get(norm_t)
                if existing_id is not None:
                    merge_arrangement(conn, existing_id, arr_payload)
                else:
                    sid = upsert_song_record(
                        conn,
                        {
                            "title_ko": title_ko,
                            "title_en": title_en,
                            "first_line_incipit": title_ko[:15],
                            "category": cat,
                            "era": era,
                            "original_artist": team,
                            "release_year": year,
                            "adoption_tier": "niche",
                            "church_seasons": sem["church_seasons"],
                            "service_slots": sem["service_slots"],
                            "liturgical_stages": sem["liturgical_stages"],
                            "theological_tone": sem["theological_tone"],
                            "lyrical_perspective": sem["lyrical_perspective"],
                            "theological_core": sem["theological_core"],
                            "theme_summary": sem["theme_summary"],
                            "theme_tags": sem["theme_tags"],
                            "available_sections": sem["available_sections"],
                            "default_song_form": sem["default_song_form"],
                            "copyright_holder": team,
                            "copyright_scope": "komca_separate",
                            "source_status": "community_reviewed",
                            "arrangements": [arr_payload],
                        },
                        sync_fts=False,
                    )
                    norm_to_song_id[norm_t] = sid
                deezer_count += 1

        # Auto-promote adoption_tier for songs covered by multiple worship teams
        conn.execute(
            """
            UPDATE songs
            SET adoption_tier = 'core_standard'
            WHERE id IN (
                SELECT song_id FROM arrangements GROUP BY song_id HAVING COUNT(DISTINCT worship_team) >= 3
            )
            """
        )
        conn.execute(
            """
            UPDATE songs
            SET adoption_tier = 'widely_sung'
            WHERE adoption_tier IN ('niche', 'recent_rising')
              AND id IN (
                SELECT song_id FROM arrangements GROUP BY song_id HAVING COUNT(DISTINCT worship_team) = 2
            )
            """
        )

        # Preserve curated pinpoint rhythm/groove & BPM fixes across full rebuilds
        from curate_rhythm_metadata import apply_curated_rhythm_updates
        apply_curated_rhythm_updates(conn)

        # Single fast bulk rebuild of trigram FTS5 index
        sync_songs_fts(conn, song_id=None)

    total_songs = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    total_arrs = conn.execute("SELECT COUNT(*) FROM arrangements").fetchone()[0]
    cat_counts = {
        r["category"]: r["cnt"]
        for r in conn.execute("SELECT category, COUNT(*) AS cnt FROM songs GROUP BY category").fetchall()
    }
    era_counts = {
        r["era"]: r["cnt"]
        for r in conn.execute("SELECT era, COUNT(*) AS cnt FROM songs GROUP BY era").fetchall()
    }
    top_teams = [
        {"team": r["worship_team"], "count": r["cnt"]}
        for r in conn.execute(
            "SELECT worship_team, COUNT(*) AS cnt FROM arrangements GROUP BY worship_team ORDER BY cnt DESC LIMIT 15"
        ).fetchall()
    ]
    conn.close()

    if not skip_network and total_songs < min_target_songs:
        raise RuntimeError(f"Built catalog has only {total_songs} songs, below target {min_target_songs}")

    return {
        "total_songs": total_songs,
        "total_arrangements": total_arrs,
        "categories": cat_counts,
        "eras": era_counts,
        "top_teams": top_teams,
        "layer_processed": {"seeds": len(seeds), "hymnal": hymnal_count, "kccm": kccm_count, "deezer": deezer_count},
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Build 3,000+ song Korean worship catalog in worship.db")
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    parser.add_argument("--local-db", type=Path, default=None)
    parser.add_argument("--min-songs", type=int, default=3000)
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()

    stats = build_catalog(
        worship_db_path=args.worship_db,
        local_db_path=args.local_db,
        min_target_songs=args.min_songs,
        skip_network=args.skip_network,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
