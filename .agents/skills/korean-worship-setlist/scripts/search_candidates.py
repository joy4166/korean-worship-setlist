"""Hybrid worship song candidate search with season gating, cooldown, learning, and auto-transposition."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from common import (
    GROOVE_SUBDIVISION_FAMILY,
    LOCAL_DB_PATH,
    WORSHIP_DB_PATH,
    check_skill_update,
    clamp_to_vocal_range,
    configure_utf8_stdio,
    connect_dbs,
    get_installed_skill_version,
    load_user_config,
    normalize_groove_style,
    normalize_scripture_ref,
    normalize_title,
    parse_key_signature,
    resolve_workspace_path,
    scripture_book_chapter,
)
from enrich_metadata import try_load_e5_model
from manage_history import SHEET_EXTENSIONS, extract_title_and_key_from_label

WORSHIP_PRESETS: dict[str, dict[str, Any]] = {
    "sunday_main": {
        "name": "주일 대예배",
        "max_monthly_new_songs": 2,
        "require_traditional_or_classic": True,
        "slots": [
            {"slot_index": 1, "stage": "opener", "tempo_tiers": ["medium", "fast"], "service_slot_pref": ["introit_doxology", "worship_set"]},
            {"slot_index": 2, "stage": "declaration", "tempo_tiers": ["medium", "fast"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 3, "stage": "confession", "tempo_tiers": ["ballad", "medium"], "service_slot_pref": ["worship_set", "sermon_response"]},
        ],
    },
    "youth": {
        "name": "청년부/다음세대 예배",
        "max_monthly_new_songs": 4,
        "require_traditional_or_classic": False,
        "slots": [
            {"slot_index": 1, "stage": "opener", "tempo_tiers": ["fast"], "service_slot_pref": ["worship_set", "pre_service"]},
            {"slot_index": 2, "stage": "declaration", "tempo_tiers": ["fast", "medium"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 3, "stage": "meditation", "tempo_tiers": ["medium", "ballad"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 4, "stage": "confession", "tempo_tiers": ["ballad"], "service_slot_pref": ["worship_set", "sermon_response"]},
        ],
    },
    "friday_revival": {
        "name": "금요성령집회/수련회",
        "max_monthly_new_songs": 4,
        "require_traditional_or_classic": False,
        "prefer_bridge": True,
        "slots": [
            {"slot_index": 1, "stage": "opener", "tempo_tiers": ["fast"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 2, "stage": "opener", "tempo_tiers": ["fast"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 3, "stage": "declaration", "tempo_tiers": ["medium"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 4, "stage": "meditation", "tempo_tiers": ["ballad"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 5, "stage": "confession", "tempo_tiers": ["ballad"], "service_slot_pref": ["worship_set", "sermon_response"]},
        ],
    },
    "small_group": {
        "name": "수요/새벽/소그룹",
        "max_monthly_new_songs": 2,
        "require_traditional_or_classic": False,
        "small_ensemble_only": True,
        "slots": [
            {"slot_index": 1, "stage": "declaration", "tempo_tiers": ["medium", "ballad"], "service_slot_pref": ["worship_set"]},
            {"slot_index": 2, "stage": "confession", "tempo_tiers": ["ballad"], "service_slot_pref": ["worship_set", "sermon_response"]},
        ],
    },
}

ADOPTION_SCORES = {
    "core_standard": 1.0,
    "widely_sung": 0.78,
    "recent_rising": 0.65,
    "niche": 0.45,
}

FAMILIARITY_SCORES = {
    "pinned_ritual": 1.0,
    "favorite": 1.0,
    "familiar": 0.85,
    "learning": 0.90,
    "unknown": 0.55,
    "new": 0.50,
}


def _extract_query_tokens(text: str) -> tuple[list[str], list[str]]:
    """Split text into >=3-char tokens (for trigram FTS5) and 1-2-char tokens (for LIKE fallback)."""
    raw_tokens = re.findall(r"[가-힣a-zA-Z0-9]+", (text or "").lower())
    stopwords = {"있는", "하는", "위한", "대한", "통해", "찬양", "예배", "콘티", "추천", "주제"}
    long_tokens: list[str] = []
    short_tokens: list[str] = []
    for tok in raw_tokens:
        if tok in stopwords:
            continue
        if len(tok) >= 3:
            long_tokens.append(tok)
        elif len(tok) >= 1:
            short_tokens.append(tok)
    return long_tokens, short_tokens


def _fetch_cooldown_map(
    conn: Any,
    cooldown_scope: str,
    reference_date: str,
    cooldown_weeks: int,
) -> tuple[dict[str, str], int]:
    """Return {song_slug: last_sung_date} within cooldown window and monthly new-song count."""
    ref_dt = datetime.strptime(reference_date, "%Y-%m-%d").date()
    cutoff = (ref_dt - timedelta(weeks=cooldown_weeks)).isoformat()
    month_prefix = reference_date[:7]

    rows = conn.execute(
        """
        SELECT service_date, setlist_json
        FROM local_db.setlist_history
        WHERE cooldown_scope = ? AND service_date >= ? AND service_date <= ?
        ORDER BY service_date DESC
        """,
        (cooldown_scope, cutoff, reference_date),
    ).fetchall()

    recent_sung: dict[str, str] = {}
    monthly_new_slugs: set[str] = set()
    for r in rows:
        sdate = r["service_date"]
        try:
            data = json.loads(r["setlist_json"])
        except Exception:
            continue
        items = data.get("songs", [])
        if not items:
            pre_items = [x.get("primary", x) for x in (data.get("pre_sermon") or []) if isinstance(x, dict)]
            post = data.get("post_sermon")
            if isinstance(post, list):
                post_items = [x.get("primary", x) for x in post if isinstance(x, dict)]
            elif isinstance(post, dict) and isinstance(post.get("reprise_candidate"), dict):
                post_items = [post["reprise_candidate"]]
            else:
                post_items = []
            items = pre_items + post_items
        for item in items:
            slug = item.get("song_slug")
            if not slug or item.get("is_reprise"):
                continue
            if slug not in recent_sung or sdate > recent_sung[slug]:
                recent_sung[slug] = sdate
            if sdate.startswith(month_prefix) and item.get("familiarity") in ("new", "learning"):
                monthly_new_slugs.add(slug)

    return recent_sung, len(monthly_new_slugs)


def _compute_fts_and_like_scores(conn: Any, query_text: str) -> dict[int, float]:
    long_tokens, short_tokens = _extract_query_tokens(query_text)
    scores: dict[int, float] = {}

    if long_tokens:
        fts_query = " OR ".join(f'"{t}"' for t in long_tokens[:8])
        try:
            fts_rows = conn.execute(
                "SELECT song_id, bm25(songs_fts) AS rank FROM songs_fts WHERE songs_fts MATCH ?",
                (fts_query,),
            ).fetchall()
            for r in fts_rows:
                # bm25 in SQLite is negative (closer to -inf is better)
                scores[r["song_id"]] = scores.get(r["song_id"], 0.0) + min(1.0, abs(float(r["rank"])) / 10.0 + 0.35)
        except Exception:
            pass

    if short_tokens:
        for st in short_tokens[:6]:
            pat = f"%{st}%"
            matched_ids = conn.execute(
                """
                SELECT id FROM songs
                WHERE lower(title_ko) LIKE ?
                   OR lower(theme_summary) LIKE ?
                   OR lower(theological_core) LIKE ?
                   OR lower(theme_tags) LIKE ?
                   OR lower(COALESCE(first_line_incipit, '')) LIKE ?
                """,
                (pat, pat, pat, pat, pat),
            ).fetchall()
            for r in matched_ids:
                scores[r["id"]] = min(1.0, scores.get(r["id"], 0.0) + 0.25)
    return scores


def _compute_e5_scores(conn: Any, query_text: str, scripture_code: str) -> dict[int, float]:
    model = try_load_e5_model(download=False)
    if model is None:
        return {}
    emb_rows = conn.execute("SELECT song_id, dimension, embedding FROM song_embeddings").fetchall()
    if not emb_rows:
        return {}
    q_vec = np.asarray(model.encode([f"query: {query_text} {scripture_code}".strip()], normalize_embeddings=True)[0], dtype=np.float32)
    scores: dict[int, float] = {}
    for r in emb_rows:
        vec = np.frombuffer(r["embedding"], dtype=np.float32)
        if vec.shape[0] == q_vec.shape[0]:
            sim = float(np.dot(q_vec, vec))
            scores[r["song_id"]] = max(0.0, (sim + 1.0) / 2.0)
    return scores


def _season_adjustment(
    season: str,
    song_seasons: list[str],
    theological_tone: str,
    tempo_tier: str,
) -> tuple[bool, float]:
    """Return (is_allowed, bonus_or_penalty) based on church calendar rules."""
    if season in ("lent", "holy_week"):
        if theological_tone == "resurrection_victory":
            return False, -1.0
        if season == "holy_week" and tempo_tier == "fast":
            return False, -1.0
        if tempo_tier == "fast" and season not in song_seasons:
            return False, -1.0
        if season in song_seasons or theological_tone in ("cross_sacrifice", "penitence"):
            return True, 0.25
        return True, 0.0

    if season == "advent":
        if "christmas" in song_seasons and "advent" not in song_seasons:
            return True, -0.35
        if "advent" in song_seasons:
            return True, 0.25

    if season == "communion":
        if tempo_tier == "fast" and "communion" not in song_seasons:
            return False, -1.0
        if "communion" in song_seasons:
            return True, 0.30

    if season != "ordinary" and season in song_seasons:
        return True, 0.25
    return True, 0.0


def search_setlist_candidates(
    service_type: str = "youth",
    theme: str = "",
    scripture: str = "",
    season: str = "ordinary",
    era: str | None = None,
    worship_team: str | None = None,
    category: str | None = None,
    session_mode: str | None = None,
    single_slot: str | None = None,
    custom_tempos: list[str] | str | None = None,
    intergenerational: bool = False,
    duration_min: int | None = None,
    cooldown_scope: str | None = None,
    reference_date: str | None = None,
    user_config: dict[str, Any] | None = None,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
) -> dict[str, Any]:
    """Search and score worship candidates for each slot, applying cooldown, season, and auto-transposition."""
    cfg = user_config if user_config is not None else load_user_config()
    ref_date = reference_date or date.today().isoformat()
    scope = cooldown_scope or service_type
    cooldown_weeks = int(cfg.get("cooldown_weeks", 6))
    session_target = session_mode or cfg.get("default_session", "full_band")
    leader_gender = cfg.get("leader_gender", "male")
    vocal_floor = cfg.get("vocal_floor")
    vocal_ceiling = cfg.get("vocal_ceiling")
    seeker_emphasis = bool(cfg.get("seeker_emphasis", False))
    age_group = cfg.get("congregation_age_group")
    if not intergenerational and age_group == "intergenerational":
        intergenerational = True
    lock_past_keys = bool(cfg.get("lock_past_keys", False))

    preset = WORSHIP_PRESETS.get(service_type, WORSHIP_PRESETS["youth"])
    era_list = [e.strip() for e in era.split(",") if e.strip()] if era else []

    if custom_tempos:
        tempo_seq = (
            [t.strip().lower() for t in custom_tempos.split(",") if t.strip()]
            if isinstance(custom_tempos, str)
            else [t.strip().lower() for t in custom_tempos if t.strip()]
        )
        slots = []
        seen_fast = False
        for i, tm in enumerate(tempo_seq, start=1):
            if tm == "fast" and not seen_fast:
                st = "opener"
                seen_fast = True
            elif tm in ("fast", "medium"):
                st = "declaration"
            elif i == len(tempo_seq):
                st = "confession"
            else:
                st = "meditation"
            slots.append(
                {
                    "slot_index": i,
                    "stage": st,
                    "tempo_tiers": [tm],
                    "strict_tempo": True,
                    "service_slot_pref": ["worship_set", "sermon_response"] if i == len(tempo_seq) else ["worship_set", "pre_service"],
                }
            )
    elif single_slot:
        slots = [{"slot_index": 1, "stage": "dedication", "tempo_tiers": ["ballad", "medium", "fast"], "service_slot_pref": [single_slot]}]
    else:
        slots = preset["slots"]

    norm_scripture = normalize_scripture_ref(scripture)
    scripture_chap = scripture_book_chapter(norm_scripture)

    conn = connect_dbs(worship_db_path, local_db_path, attach_local=True, ensure_schemas=True)
    recent_sung, monthly_new_count = _fetch_cooldown_map(conn, scope, ref_date, cooldown_weeks)

    e5_scores = _compute_e5_scores(conn, theme, norm_scripture)
    fts_scores = _compute_fts_and_like_scores(conn, f"{theme} {scripture}".strip())
    search_mode = "hybrid_e5" if e5_scores else "fts5_fallback"

    rows = conn.execute(
        """
        SELECT s.id, s.song_slug, s.title_ko, s.normalized_title, s.first_line_incipit,
               s.category, s.hymn_number, s.era, s.adoption_tier, s.lyric_accessibility,
               s.church_seasons, s.service_slots, s.liturgical_stages, s.theological_tone,
               s.theological_core, s.caution_nuance, s.scripture_refs,
               s.available_sections, s.default_song_form,
               a.worship_team, a.key_signature, a.time_signature,
               a.bpm, a.tempo_tier, a.groove_style, a.intro_first_chord, a.outro_last_chord,
               a.ending_type, a.lowest_note, a.highest_note,
               a.congregation_singability, a.band_dependency,
               a.session_fit, a.arrangement_song_form, a.youtube_url, a.youtube_start_sec,
               a.key_source, a.bpm_source, a.confidence, a.is_default,
               COALESCE(r.familiarity, 'unknown') AS familiarity,
               r.preferred_key,
               r.team_memo,
               COALESCE(r.consecutive_learning_count, 0) AS consecutive_learning_count
        FROM songs s
        JOIN arrangements a ON a.song_id = s.id
        LEFT JOIN local_db.repertoire r ON r.song_slug = s.song_slug
        WHERE s.source_status != 'blocked_heresy'
          AND COALESCE(r.familiarity, 'unknown') != 'excluded'
        """
    ).fetchall()

    sheet_cache_rows = conn.execute(
        """
        SELECT song_slug, detected_time_signature, detected_bpm, detected_groove
        FROM local_db.sheet_cache
        WHERE detected_time_signature IS NOT NULL
           OR detected_bpm IS NOT NULL
           OR detected_groove IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()
    conn.close()

    verified_sheet_meta: dict[str, dict[str, Any]] = {}
    for sc_row in sheet_cache_rows:
        verified_sheet_meta[sc_row["song_slug"]] = {
            "time_signature": sc_row["detected_time_signature"],
            "bpm": sc_row["detected_bpm"],
            "groove_style": normalize_groove_style(sc_row["detected_groove"]),
        }

    max_monthly_new = int(preset.get("max_monthly_new_songs", 4))
    new_song_cap_reached = (service_type == "sunday_main") and (monthly_new_count >= max_monthly_new)

    # Select winning sqlite3.Row per song_id BEFORE dict(r) conversion
    team_q = worship_team.lower() if worship_team else None
    best_by_song: dict[int, tuple[tuple[int, int, float], Any]] = {}
    for r in rows:
        sid = r["id"]
        vmeta = verified_sheet_meta.get(r["song_slug"])
        eff_conf = max(float(r["confidence"] or 0.5), 0.92) if vmeta else float(r["confidence"] or 0.0)
        team_hit = 1 if (team_q and team_q in (r["worship_team"] or "").lower()) else 0
        prio = (team_hit, int(r["is_default"] or 0), eff_conf)
        cur_best = best_by_song.get(sid)
        if cur_best is None or prio > cur_best[0]:
            best_by_song[sid] = (prio, r)

    # Pre-index local sheet keys so small/medium/beginner teams prefer existing un-transposed charts
    local_sheet_keys: dict[str, set[str]] = {}
    if cfg.get("church_scale") in ("small", "medium") or cfg.get("session_skill_tier") in ("beginner_friendly", "intermediate"):
        for d_str in cfg.get("local_sheet_dirs") or []:
            d_path = resolve_workspace_path(d_str)
            if not d_path.exists():
                continue
            for p in d_path.rglob("*"):
                if p.is_file() and p.suffix.lower() in SHEET_EXTENSIONS:
                    t_g, k_g = extract_title_and_key_from_label(p.stem)
                    nt = normalize_title(t_g)
                    if nt and k_g:
                        local_sheet_keys.setdefault(nt, set()).add(parse_key_signature(k_g)[1])

    def _fast_json_list(raw_val: str | None) -> list[Any]:
        if not raw_val or raw_val == "[]":
            return []
        return json.loads(raw_val)

    is_small = session_target in ("piano_only", "acoustic") or bool(preset.get("small_ensemble_only"))
    era_set = set(era_list) if era_list else None
    json_loads = json.loads

    # Precompute song-invariant filters & bonus terms once per song (not 4x per slot)
    active_cands: list[dict[str, Any]] = []
    for _, best_row in best_by_song.values():
        if era_set and best_row["era"] not in era_set:
            continue
        if category and best_row["category"] != category:
            continue
        if team_q and team_q not in (best_row["worship_team"] or "").lower():
            continue
        if is_small and best_row["band_dependency"] == "high":
            continue

        pick = dict(best_row)
        vmeta = verified_sheet_meta.get(pick["song_slug"])
        if vmeta:
            if vmeta.get("groove_style"):
                pick["groove_style"] = vmeta["groove_style"]
            if vmeta.get("bpm") is not None:
                v_bpm = int(vmeta["bpm"])
                pick["bpm"] = v_bpm
                pick["tempo_tier"] = "fast" if v_bpm >= 100 else ("medium" if v_bpm >= 80 else "ballad")
            if vmeta.get("time_signature"):
                pick["time_signature"] = vmeta["time_signature"]
            pick["bpm_source"] = "local_sheet_verified"
            pick["confidence"] = max(float(pick.get("confidence") or 0.5), 0.92)
        pick["groove_style"] = normalize_groove_style(pick.get("groove_style"))

        song_seasons = _fast_json_list(pick["church_seasons"])
        allowed_season, season_bonus = _season_adjustment(
            season, song_seasons, pick["theological_tone"], pick["tempo_tier"]
        )
        if not allowed_season:
            continue

        song_slots = _fast_json_list(pick["service_slots"])
        slots_set = set(song_slots)
        if single_slot and single_slot not in slots_set:
            continue

        fam = pick["familiarity"]
        if new_song_cap_reached and fam in ("new", "learning"):
            continue

        slug = pick["song_slug"]
        if fam == "pinned_ritual":
            in_cooldown = False
        elif fam == "learning" and int(pick["consecutive_learning_count"]) < 3:
            in_cooldown = False
        else:
            in_cooldown = slug in recent_sung

        song_refs = _fast_json_list(pick["scripture_refs"])
        stages = _fast_json_list(pick["liturgical_stages"])
        sections = _fast_json_list(pick["available_sections"])
        session_fit_list = _fast_json_list(pick.get("session_fit"))

        pick["_seasons"] = song_seasons
        pick["_service_slots"] = song_slots
        pick["_slots_set"] = slots_set
        pick["_scripture_refs"] = song_refs
        pick["_stages"] = stages
        pick["_stages_set"] = set(stages)
        pick["_sections"] = sections
        pick["_session_fit"] = session_fit_list
        pick["_in_cooldown"] = in_cooldown

        memo_str = str(pick.get("team_memo") or "")
        effective_pref_key = (
            pick["preferred_key"]
            if (pick["preferred_key"] and (lock_past_keys or not memo_str.startswith("bootstrapped")))
            else None
        )
        trans = clamp_to_vocal_range(
            arrangement_key=pick["key_signature"],
            highest_note=pick["highest_note"],
            lowest_note=pick["lowest_note"],
            vocal_floor=vocal_floor,
            vocal_ceiling=vocal_ceiling,
            leader_gender=leader_gender,
            preferred_key=effective_pref_key,
        )
        pick["_trans"] = trans

        if local_sheet_keys:
            norm_t = pick.get("normalized_title") or normalize_title(pick["title_ko"])
            local_match_bonus = (
                0.08 if parse_key_signature(trans["target_key"])[1] in local_sheet_keys.get(norm_t, set()) else 0.0
            )
        else:
            local_match_bonus = 0.0

        sem_score = e5_scores.get(pick["id"], 0.0)
        fts_score = fts_scores.get(pick["id"], 0.0)
        ref_match = 1.0 if scripture_chap and any(scripture_book_chapter(sr) == scripture_chap for sr in song_refs) else 0.0
        pick["_sem_score"] = sem_score
        pick["_fts_score"] = fts_score
        pick["_ref_match"] = ref_match

        fam_base = FAMILIARITY_SCORES.get(fam, 0.55)
        adopt_base = ADOPTION_SCORES.get(pick["adoption_tier"], 0.6)
        familiarity_score = 0.6 * fam_base + 0.4 * adopt_base
        if fam == "learning" and int(pick["consecutive_learning_count"]) < 3:
            familiarity_score += 0.18

        seeker_bonus = 0.15 if (seeker_emphasis and pick["lyric_accessibility"] == "easy_seeker") else 0.0
        bridge_bonus = 0.12 if (preset.get("prefer_bridge") and "B" in sections) else 0.0
        small_bonus = 0.12 if (is_small and pick["band_dependency"] == "low") else 0.0
        session_bonus = 0.08 if session_target in session_fit_list else 0.0
        intergen_bonus = 0.0
        if intergenerational:
            sing = pick.get("congregation_singability", "modern_simple")
            if sing in ("modern_simple", "hymn_traditional"):
                intergen_bonus += 0.25
            elif sing == "syncopated_band":
                intergen_bonus -= 0.35
            if pick["adoption_tier"] == "core_standard":
                intergen_bonus += 0.18
        if age_group == "senior_adult" and not era_list:
            if pick["category"] in ("hymn", "classic_gospel") or pick["era"] in ("traditional", "1980s", "1990s", "2000s"):
                intergen_bonus += 0.22
            if pick.get("congregation_singability") == "syncopated_band":
                intergen_bonus -= 0.30
        if age_group == "youth_young_adult" and not era_list:
            if pick["category"] == "modern_worship" and pick["era"] in ("2010s", "2020s"):
                intergen_bonus += 0.15

        conf = float(pick.get("confidence") or 0.5)
        pick["_conf"] = conf
        pick["_song_bonus"] = (
            0.15 * familiarity_score
            + season_bonus
            + seeker_bonus
            + bridge_bonus
            + small_bonus
            + session_bonus
            + local_match_bonus
            + intergen_bonus
            + 0.08 * conf
        )
        active_cands.append(pick)

    has_e5 = bool(e5_scores)

    def score_for_slot(
        cand: dict[str, Any],
        slot: dict[str, Any],
        slot_pref: tuple[str, ...],
        slot_tempo_tiers: set[str],
        strict_tempo: bool,
        slot_stage: str,
        ignore_cooldown: bool = False,
    ) -> tuple[bool, float, bool]:
        if strict_tempo and cand["tempo_tier"] not in slot_tempo_tiers:
            return False, -1.0, False

        in_cooldown = cand["_in_cooldown"]
        if in_cooldown and not ignore_cooldown:
            return False, -1.0, False
        cooldown_warning = in_cooldown

        stage_match = 1.0 if slot_stage in cand["_stages_set"] else 0.3
        tempo_match = 1.0 if cand["tempo_tier"] in slot_tempo_tiers else 0.15
        slots_set = cand["_slots_set"]
        slot_pref_match = 0.25 if any(sp in slots_set for sp in slot_pref) else 0.0

        if has_e5:
            relevance = 0.60 * cand["_sem_score"] + 0.25 * min(1.0, 0.45 * cand["_fts_score"] + 0.35 * cand["_ref_match"] + 0.20 * stage_match)
        else:
            relevance = 0.85 * min(1.0, 0.50 * cand["_fts_score"] + 0.25 * cand["_ref_match"] + 0.25 * stage_match)

        total = (
            relevance
            + cand["_song_bonus"]
            + 0.30 * tempo_match
            + slot_pref_match
            - (0.5 if cooldown_warning else 0.0)
        )
        return True, total, cooldown_warning

    def _materialize_enriched(cand: dict[str, Any], slot: dict[str, Any], score_val: float, cooldown_warning: bool) -> dict[str, Any]:
        trans = cand["_trans"]
        song_form = cand["arrangement_song_form"] or cand["default_song_form"] or ""
        ensemble_note = ""
        if is_small and "I(8m)" in song_form:
            song_form = song_form.replace("I(8m)", "I(4m)")
            ensemble_note = "소편성 인트로 축소(I(8m)->I(4m))"
        conf = cand["_conf"]
        return {
            "song_id": cand["id"],
            "song_slug": cand["song_slug"],
            "title_ko": cand["title_ko"],
            "first_line_incipit": cand["first_line_incipit"],
            "category": cand["category"],
            "hymn_number": cand["hymn_number"],
            "era": cand["era"],
            "worship_team": cand["worship_team"],
            "original_key": cand["key_signature"],
            "target_key": trans["target_key"],
            "key_shift": trans["key_shift"],
            "highest_note": cand["highest_note"],
            "lowest_note": cand["lowest_note"],
            "transposition_reason": trans["reason"],
            "key_source": cand.get("key_source", "manual_seed"),
            "key_verified": conf >= 0.80,
            "confidence": round(conf, 3),
            "time_signature": cand["time_signature"],
            "bpm": cand["bpm"],
            "tempo_tier": cand["tempo_tier"],
            "groove_style": cand.get("groove_style"),
            "bpm_source": cand.get("bpm_source", "manual_seed"),
            "intro_first_chord": cand["intro_first_chord"],
            "outro_last_chord": cand["outro_last_chord"],
            "ending_type": cand["ending_type"],
            "available_sections": list(cand["_sections"]),
            "song_form": song_form,
            "liturgical_stage": slot["stage"],
            "theological_tone": cand["theological_tone"],
            "theological_core": cand["theological_core"],
            "caution_nuance": cand["caution_nuance"],
            "scripture_refs": list(cand["_scripture_refs"]),
            "familiarity": cand["familiarity"],
            "adoption_tier": cand["adoption_tier"],
            "band_dependency": cand["band_dependency"],
            "youtube_url": cand["youtube_url"],
            "youtube_start_sec": cand["youtube_start_sec"],
            "cooldown_warning": cooldown_warning,
            "ensemble_note": ensemble_note,
            "score": round(score_val, 4),
        }

    used_slugs: set[str] = set()
    era_counts: dict[str, int] = {e: 0 for e in era_list}
    pre_sermon_results: list[dict[str, Any]] = []
    slot_pools: list[tuple[dict[str, Any], list[list[Any]]]] = []
    has_trad_or_classic = False

    for slot in slots:
        slot_pref = tuple(slot.get("service_slot_pref", []))
        slot_tempo_tiers = set(slot.get("tempo_tiers", []))
        strict_tempo = bool(slot.get("strict_tempo"))
        slot_stage = slot["stage"]

        raw_pool: list[list[Any]] = []
        for cand in active_cands:
            if cand["song_slug"] in used_slugs:
                continue
            ok, sc, cw = score_for_slot(cand, slot, slot_pref, slot_tempo_tiers, strict_tempo, slot_stage, ignore_cooldown=False)
            if ok:
                raw_pool.append([round(sc, 4), cw, cand])

        if not raw_pool:
            for cand in active_cands:
                if cand["song_slug"] in used_slugs:
                    continue
                ok, sc, cw = score_for_slot(cand, slot, slot_pref, slot_tempo_tiers, strict_tempo, slot_stage, ignore_cooldown=True)
                if ok:
                    raw_pool.append([round(sc, 4), cw, cand])

        # For sunday_main last slot, nudge hymn/classic_gospel if none picked yet
        if preset.get("require_traditional_or_classic") and slot["slot_index"] == len(slots) and not has_trad_or_classic:
            for entry in raw_pool:
                if entry[2]["category"] in ("hymn", "classic_gospel"):
                    entry[0] = round(entry[0] + 0.35, 4)

        # Balance era diversity when multiple eras are requested (e.g. "2000s,2010s")
        if len(era_list) > 1 and raw_pool:
            min_era_cnt = min(era_counts.values()) if era_counts else 0
            for entry in raw_pool:
                if era_counts.get(entry[2]["era"], 0) == min_era_cnt:
                    entry[0] = round(entry[0] + 0.25, 4)

        # Prefer rhythmically compatible groove when chaining fast songs
        if pre_sermon_results and "fast" in slot_tempo_tiers:
            prev_primary = pre_sermon_results[-1]["primary"]
            prev_gr = normalize_groove_style(prev_primary.get("groove_style"))
            prev_fam = GROOVE_SUBDIVISION_FAMILY.get(prev_gr or "", "neutral")
            if prev_primary.get("tempo_tier") == "fast" and prev_fam != "neutral":
                for entry in raw_pool:
                    cur_gr = entry[2].get("groove_style")
                    cur_fam = GROOVE_SUBDIVISION_FAMILY.get(cur_gr or "", "neutral")
                    if cur_fam == "neutral":
                        continue
                    if cur_fam == prev_fam:
                        entry[0] = round(entry[0] + 0.08, 4)
                    else:
                        entry[0] = round(entry[0] - 0.08, 4)

        raw_pool.sort(key=lambda x: -x[0])
        if not raw_pool:
            continue

        top_sc, top_cw, top_cand = raw_pool[0]
        primary = _materialize_enriched(top_cand, slot, top_sc, top_cw)
        used_slugs.add(primary["song_slug"])
        if primary["era"] in era_counts:
            era_counts[primary["era"]] += 1
        if primary["category"] in ("hymn", "classic_gospel"):
            has_trad_or_classic = True
        plan_b = [_materialize_enriched(c, slot, sc, cw) for sc, cw, c in raw_pool[1:3]]
        slot_pools.append((slot, raw_pool))

        pre_sermon_results.append(
            {
                "slot_index": slot["slot_index"],
                "target_stage": slot["stage"],
                "target_tempo_tiers": slot["tempo_tiers"],
                "primary": primary,
                "plan_b": plan_b,
            }
        )

    # Balance familiar_ratio (~75% familiar : 25% fresh) proportionally for 4+ song youth/revival sets
    familiar_ratio = float(cfg.get("familiar_ratio", 0.75))
    if (
        service_type in ("youth", "friday_revival")
        and len(pre_sermon_results) >= 4
        and not new_song_cap_reached
        and familiar_ratio < 0.95
    ):
        def _is_fresh_item(item: dict[str, Any]) -> bool:
            return item["familiarity"] in ("learning", "new") or (
                item["familiarity"] == "unknown" and item["adoption_tier"] == "recent_rising"
            )

        target_fresh = max(1 if familiar_ratio <= 0.85 else 0, round(len(pre_sermon_results) * (1.0 - familiar_ratio)))
        current_fresh = sum(1 for s in pre_sermon_results if _is_fresh_item(s["primary"]))
        while current_fresh < target_fresh:
            best_promo: tuple[int, dict[str, Any], float, bool, dict[str, Any]] | None = None
            for idx, sl in enumerate(pre_sermon_results):
                if sl["target_stage"] not in ("meditation", "declaration") or _is_fresh_item(sl["primary"]):
                    continue
                top_score = sl["primary"]["score"]
                slot_obj, raw_entries = slot_pools[idx]
                for sc, cw, cand in raw_entries[1:]:
                    if cand["song_slug"] in used_slugs:
                        continue
                    gap = top_score - sc
                    if _is_fresh_item(cand) and gap <= 0.25:
                        if best_promo is None or sc > best_promo[2]:
                            best_promo = (idx, slot_obj, sc, cw, cand)
            if best_promo is None:
                break
            idx, slot_obj, sc, cw, fresh_raw = best_promo
            fresh_cand = _materialize_enriched(fresh_raw, slot_obj, sc, cw)
            old_primary = pre_sermon_results[idx]["primary"]
            used_slugs.discard(old_primary["song_slug"])
            used_slugs.add(fresh_cand["song_slug"])
            new_plan_b = [old_primary] + [
                b for b in pre_sermon_results[idx]["plan_b"] if b["song_slug"] != fresh_cand["song_slug"]
            ]
            pre_sermon_results[idx]["primary"] = fresh_cand
            pre_sermon_results[idx]["plan_b"] = new_plan_b[:2]
            current_fresh += 1

    # Compact medley song-form & estimated duration budgeting when --duration-min is specified
    est_total = 0.0
    if duration_min is not None and pre_sermon_results:
        avg_min = duration_min / len(pre_sermon_results)
        compact = avg_min <= 4.2
        running_sum = 0.0
        n_songs = len(pre_sermon_results)
        for i, sl in enumerate(pre_sermon_results):
            p = sl["primary"]
            tier = p["tempo_tier"]
            next_tier = pre_sermon_results[i + 1]["primary"]["tempo_tier"] if i + 1 < n_songs else None
            prev_tier = pre_sermon_results[i - 1]["primary"]["tempo_tier"] if i > 0 else None
            secs = p.get("available_sections", [])
            if compact:
                if tier == "fast" and next_tier == "fast":
                    p["song_form"] = "I(4m) - V1 - C(x2) -->(Medley)" if i == 0 else "V1 - C(x2) -->(Medley)"
                    est = 3.0
                elif tier == "fast":
                    p["song_form"] = "V1 - C(x2) - O" if prev_tier == "fast" else "I(4m) - V1 - C(x2) - O"
                    est = 3.5
                elif tier == "medium":
                    p["song_form"] = "I(4m) - V1 - C(x2) - O"
                    est = 3.5
                else:
                    if "PC" in secs:
                        p["song_form"] = "I(4m) - V1 - PC - C - V2 - C(x2) - O"
                    else:
                        p["song_form"] = "I(4m) - V1 - C - V2 - C(x2) - O"
                    if i == n_songs - 1:
                        est = max(4.0, round(duration_min - running_sum - 0.5, 1))
                    else:
                        est = 4.5
            else:
                est = 5.0 if tier == "ballad" else 4.0
            p["estimated_minutes"] = est
            running_sum += est
        est_total = round(running_sum + (0.5 if compact else 0.0), 1)

    # Block B: Post-sermon dedication / Reprise suggestion
    reprise_base = None
    if pre_sermon_results:
        ballad_picks = [s["primary"] for s in pre_sermon_results if s["primary"]["tempo_tier"] == "ballad"]
        base = ballad_picks[-1] if ballad_picks else pre_sermon_results[-1]["primary"]
        reprise_base = {
            **base,
            "is_reprise": True,
            "song_form": "I(2m) - [C only](x2) - Vamp(결단기도)",
            "liturgical_stage": "dedication",
        }

    result: dict[str, Any] = {
        "service_type": service_type,
        "church_season": season,
        "cooldown_scope": scope,
        "reference_date": ref_date,
        "search_mode": search_mode,
        "theme_input": theme,
        "scripture_input": norm_scripture,
        "duration_min": duration_min,
        "estimated_total_minutes": est_total if duration_min is not None else None,
        "skill_version": get_installed_skill_version(),
        "pre_sermon": pre_sermon_results,
        "post_sermon": {
            "reprise_candidate": reprise_base,
            "prayer_chord_loops": ["IV - V - iii - vi", "ii7 - V7sus4 - Imaj7"],
        },
    }
    upd = check_skill_update()
    if upd is not None:
        result["update_available"] = upd
    return result


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Search and score Korean worship setlist candidates")
    parser.add_argument("--service-type", default="youth", choices=list(WORSHIP_PRESETS.keys()))
    parser.add_argument("--theme", default="")
    parser.add_argument("--scripture", default="")
    parser.add_argument("--season", default="ordinary")
    parser.add_argument("--era", default=None, help="Single or comma-separated eras e.g. '2000s,2010s'")
    parser.add_argument("--team", default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--session", default=None, choices=["full_band", "acoustic", "piano_only"])
    parser.add_argument("--slot", default=None, help="Single service slot (e.g. offertory, sending_benediction)")
    parser.add_argument("--custom-tempos", default=None, help="Comma-separated tempo tiers e.g. 'fast,fast,fast,ballad'")
    parser.add_argument("--intergenerational", action="store_true", help="Prefer congregation-friendly songs across adults and youth")
    parser.add_argument("--duration-min", type=int, default=None, help="Target duration in minutes (compresses medley/song forms when tight)")
    parser.add_argument("--scope", default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    parser.add_argument("--local-db", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    res = search_setlist_candidates(
        service_type=args.service_type,
        theme=args.theme,
        scripture=args.scripture,
        season=args.season,
        era=args.era,
        worship_team=args.team,
        category=args.category,
        session_mode=args.session,
        single_slot=args.slot,
        custom_tempos=args.custom_tempos,
        intergenerational=args.intergenerational,
        duration_min=args.duration_min,
        cooldown_scope=args.scope,
        reference_date=args.date,
        worship_db_path=args.worship_db,
        local_db_path=args.local_db,
    )
    out_text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out_text, encoding="utf-8")
    else:
        print(out_text)


if __name__ == "__main__":
    main()
