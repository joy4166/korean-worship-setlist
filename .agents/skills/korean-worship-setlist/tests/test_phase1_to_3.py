"""Comprehensive Phase 1-3 unit and integration tests for korean-worship-setlist."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_audio import correct_bpm_and_classify
from collect_youtube import parse_worship_description
from common import (
    SEED_SONGS_PATH,
    clamp_to_vocal_range,
    connect_dbs,
    is_blocked_source,
    normalize_scripture_ref,
)
from enrich_metadata import enrich_and_index
from init_db import initialize_databases, upsert_song_record
from manage_history import bootstrap_repertoire, record_setlist, set_repertoire_entry
from plan_transitions import compute_bridge_chords, evaluate_transition, plan_setlist_transitions
from search_candidates import search_setlist_candidates


@pytest.fixture()
def seeded_dbs(tmp_path: Path) -> tuple[Path, Path]:
    worship_db = tmp_path / "worship.db"
    local_db = tmp_path / "church_local.db"
    stats = initialize_databases(worship_db, local_db, seed_path=SEED_SONGS_PATH)
    assert stats["songs"] >= 36
    assert stats["arrangements"] >= stats["songs"]
    return worship_db, local_db


def test_db_separation_and_copyright_constraints(seeded_dbs: tuple[Path, Path]) -> None:
    worship_db, local_db = seeded_dbs
    assert worship_db.exists()
    assert local_db.exists()

    # Check worship.db does NOT contain local tables
    w_only = connect_dbs(worship_db, local_db, attach_local=False, ensure_schemas=False)
    tables = {r[0] for r in w_only.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "songs" in tables
    assert "arrangements" in tables
    assert "repertoire" not in tables
    assert "setlist_history" not in tables

    # Check incipit length <= 15 chars across all seeded songs
    long_incipits = w_only.execute(
        "SELECT title_ko, first_line_incipit FROM songs WHERE length(COALESCE(first_line_incipit, '')) > 15"
    ).fetchall()
    assert long_incipits == []
    w_only.close()

    # Check ATTACH join works cleanly
    conn = connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)
    joined = conn.execute(
        "SELECT s.song_slug, r.familiarity FROM songs s LEFT JOIN local_db.repertoire r ON r.song_slug = s.song_slug LIMIT 5"
    ).fetchall()
    assert len(joined) == 5
    conn.close()


def test_trigram_fts_and_short_token_search(seeded_dbs: tuple[Path, Path]) -> None:
    worship_db, local_db = seeded_dbs
    enrich_and_index(worship_db, local_db, download_model=False)
    conn = connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)

    # Substring without spacing via trigram FTS5
    hits = conn.execute(
        "SELECT song_id FROM songs_fts WHERE songs_fts MATCH ?", ('"일하시네"',)
    ).fetchall()
    assert len(hits) >= 1

    # Single-word 3+ chars
    hits_worship = conn.execute(
        "SELECT song_id FROM songs_fts WHERE songs_fts MATCH ?", ('"예배합"',)
    ).fetchall()
    assert len(hits_worship) >= 1
    conn.close()

    # Short 1-2 char keyword ('은혜', '빛') via search_candidates LIKE fallback
    res = search_setlist_candidates(
        service_type="youth",
        theme="은혜와 빛",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert res["search_mode"] == "fts5_fallback"
    assert len(res["pre_sermon"]) >= 3


def test_heresy_gate_blocks_insert_and_search(seeded_dbs: tuple[Path, Path]) -> None:
    worship_db, local_db = seeded_dbs
    blocked, term = is_blocked_source("신천지 새노래 찬양", "작곡가")
    assert blocked and term == "신천지"

    conn = connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)
    with conn:
        upsert_song_record(
            conn,
            {
                "song_slug": "bad-heresy-test-song",
                "title_ko": "테스트 금지곡",
                "category": "modern_worship",
                "era": "2020s",
                "original_artist": "기쁜소식선교회",
                "theme_summary": "은혜 테스트 곡",
                "theme_tags": ["은혜"],
                "liturgical_stages": ["opener"],
                "arrangements": [
                    {
                        "worship_team": "테스트",
                        "key_signature": "G",
                        "bpm": 120,
                        "tempo_tier": "fast",
                    }
                ],
            },
        )
    status = conn.execute("SELECT source_status FROM songs WHERE song_slug = 'bad-heresy-test-song'").fetchone()[0]
    assert status == "blocked_heresy"
    conn.close()

    res = search_setlist_candidates(
        service_type="youth",
        theme="테스트 금지곡 은혜",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    picked_slugs = [s["primary"]["song_slug"] for s in res["pre_sermon"]]
    assert "bad-heresy-test-song" not in picked_slugs


def test_youtube_description_parser() -> None:
    desc = """
    00:00 감사함으로 (G Key)
    04:35 주가 일하시네 - 사장조
    10:15 광야를 지나며 [Bb]
    """
    parsed = parse_worship_description(
        description=desc,
        video_url="https://www.youtube.com/watch?v=abc123",
        upload_year=2024,
        channel_team="마커스워십",
    )
    assert len(parsed) == 3
    assert parsed[0]["title_ko"] == "감사함으로"
    assert parsed[0]["key_signature"] == "G"
    assert parsed[0]["youtube_start_sec"] == 0
    assert parsed[0]["youtube_end_sec"] == 275
    assert parsed[1]["key_signature"] == "G"  # 사장조 -> G
    assert "t=275" in parsed[1]["youtube_url"]
    assert parsed[2]["key_signature"] == "Bb"


def test_bpm_half_and_triple_time_correction() -> None:
    # 6/8 174 BPM -> /3 = 58 BPM ballad
    res_68 = correct_bpm_and_classify(174.0, time_signature="6/8", groove_hint="6_8_worship")
    assert res_68["bpm"] == 58
    assert res_68["tempo_tier"] == "ballad"
    assert res_68["was_corrected"] is True

    # 4/4 124 BPM ballad context -> /2 = 62 BPM ballad
    res_44_ballad = correct_bpm_and_classify(124.0, time_signature="4/4", slot_hint="confession", groove_hint="16th_ballad")
    assert res_44_ballad["bpm"] == 62
    assert res_44_ballad["tempo_tier"] == "ballad"

    # 4/4 124 BPM fast opener -> stays 124 fast
    res_44_fast = correct_bpm_and_classify(124.0, time_signature="4/4", slot_hint="opener", groove_hint="rock_upbeat")
    assert res_44_fast["bpm"] == 124
    assert res_44_fast["tempo_tier"] == "fast"
    assert res_44_fast["was_corrected"] is False


def test_vocal_range_auto_transposition() -> None:
    # Male leader ceiling F#4: song in A with treble-clef F#5 -> male effective F#4 (fits),
    # whereas song in A with treble-clef G#5 (effective G#4) shifts -2 to G!
    res_male = clamp_to_vocal_range(
        arrangement_key="A",
        highest_note="G#5",
        lowest_note="C#4",
        vocal_floor="A2",
        vocal_ceiling="F#4",
        leader_gender="male",
    )
    assert res_male["key_shift"] == -2
    assert res_male["target_key"] == "G"
    assert res_male["in_range"] is True

    # Female leader ceiling D5: song in A with E5 shifts down -2 to G
    res_female = clamp_to_vocal_range(
        arrangement_key="A",
        highest_note="E5",
        lowest_note="C#4",
        vocal_floor="A3",
        vocal_ceiling="D5",
        leader_gender="female",
    )
    assert res_female["key_shift"] == -2
    assert res_female["target_key"] == "G"

    # preferred_key overrides calculation
    res_pref = clamp_to_vocal_range("A", "E5", "C#4", "A2", "F#4", "male", preferred_key="F")
    assert res_pref["target_key"] == "F"
    assert res_pref["reason"] == "preferred_key"


def test_history_bootstrap_parts_cooldown_and_learning_promotion(seeded_dbs: tuple[Path, Path], tmp_path: Path) -> None:
    worship_db, local_db = seeded_dbs

    sheets = tmp_path / "sheets"
    sheets.mkdir()
    (sheets / "01_주가 일하시네_G.pdf").write_bytes(b"%PDF-1.4")
    (sheets / "마커스 - 주 은혜임을 (A키).png").write_bytes(b"PNG")
    hist_txt = tmp_path / "past.txt"
    hist_txt.write_text("1. 광야를 지나며 (G)\n2. 야곱의 축복 (G)\n", encoding="utf-8")

    boot = bootstrap_repertoire(worship_db, local_db, sheets_dir=sheets, history_text_file=hist_txt)
    assert boot["unique_songs_seeded"] >= 4

    # Set 야곱의 축복 as pinned_ritual, 광야를 지나며 as learning
    set_repertoire_entry("yegop-ui-chukbok-kim-in-sik", familiarity="pinned_ritual", worship_db_path=worship_db, local_db_path=local_db)
    set_repertoire_entry("gwangya-reul-jinamyeo-hiswill", familiarity="learning", worship_db_path=worship_db, local_db_path=local_db)

    # Record 1부 and 2부 on the same day -> times_sung should increment ONCE, not twice
    payload_p1 = {
        "service_date": "2026-11-01",
        "service_type": "sunday_main",
        "cooldown_scope": "sunday_main",
        "service_part": "1부",
        "theme_input": "인도하심",
        "songs": [
            {"song_slug": "yegop-ui-chukbok-kim-in-sik", "familiarity": "pinned_ritual"},
            {"song_slug": "gwangya-reul-jinamyeo-hiswill", "familiarity": "learning"},
            {"song_slug": "juga-ilhasine-kim-brian", "familiarity": "familiar"},
        ],
    }
    payload_p2 = {**payload_p1, "service_part": "2부"}

    r1 = record_setlist(payload_p1, worship_db_path=worship_db, local_db_path=local_db)
    r2 = record_setlist(payload_p2, worship_db_path=worship_db, local_db_path=local_db)
    assert len(r1["counted_songs"]) == 3
    assert len(r2["skipped_duplicate_part"]) == 3

    # Record learning song in weeks 2 and 3 -> promotes to 'familiar' on 3rd appearance!
    record_setlist({**payload_p1, "service_date": "2026-11-08", "songs": [{"song_slug": "gwangya-reul-jinamyeo-hiswill", "familiarity": "learning"}]}, worship_db_path=worship_db, local_db_path=local_db)
    r3 = record_setlist({**payload_p1, "service_date": "2026-11-15", "songs": [{"song_slug": "gwangya-reul-jinamyeo-hiswill", "familiarity": "learning"}]}, worship_db_path=worship_db, local_db_path=local_db)
    assert "gwangya-reul-jinamyeo-hiswill" in r3["promoted_to_familiar"]

    # Check pinned_ritual exempt from cooldown while normal 'juga-ilhasine-kim-brian' is excluded within 6 weeks
    res_slot = search_setlist_candidates(
        service_type="sunday_main",
        theme="축복과 파송",
        single_slot="sending_benediction",
        reference_date="2026-11-22",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    top_slug = res_slot["pre_sermon"][0]["primary"]["song_slug"]
    assert top_slug == "yegop-ui-chukbok-kim-in-sik"

    res_normal = search_setlist_candidates(
        service_type="sunday_main",
        theme="광야 오병이어",
        reference_date="2026-11-22",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    picked = [s["primary"]["song_slug"] for s in res_normal["pre_sermon"] if not s["primary"]["cooldown_warning"]]
    assert "juga-ilhasine-kim-brian" not in picked


def test_season_filtering_small_group_and_transitions(seeded_dbs: tuple[Path, Path]) -> None:
    worship_db, local_db = seeded_dbs

    # Lent season must exclude resurrection_victory & festive fast songs
    lent_res = search_setlist_candidates(
        service_type="youth",
        season="lent",
        theme="십자가 보혈 회개",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    for slot in lent_res["pre_sermon"]:
        p = slot["primary"]
        assert p["theological_tone"] != "resurrection_victory"
        assert p["tempo_tier"] != "fast"

    # Small group with piano_only excludes high band_dependency
    sg_res = search_setlist_candidates(
        service_type="small_group",
        session_mode="piano_only",
        theme="은혜와 평안",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    for slot in sg_res["pre_sermon"]:
        assert slot["primary"]["band_dependency"] != "high"

    # Bridge harmony & transition planning checks
    bridge_ga = compute_bridge_chords("G", "G", "A", "A", "medium")
    assert "D/E (V7sus4) -> E7" in bridge_ga["bridge_chords"]
    assert "서브도미넌트 마이너" not in bridge_ga["harmony_tip"]  # IV/V is V7sus4, not IVm6!

    bridge_iv = compute_bridge_chords("G", "G", "A", "Dmaj7", "medium")
    assert "A7" in bridge_iv["bridge_chords"]

    # Meter change 4/4 -> 6/8 forces pad_break
    tr_meter = evaluate_transition(
        {"title_ko": "A곡", "time_signature": "4/4", "bpm": 88, "outro_last_chord": "G"},
        {"title_ko": "B곡", "time_signature": "6/8", "bpm": 58, "intro_first_chord": "G", "tempo_tier": "ballad"},
        "G",
        "G",
        skill_tier="intermediate",
    )
    assert tr_meter["transition_mode"] == "pad_break"
    assert "6/8" in tr_meter["session_cue"]

    # Beginner friendly: Ab (Capo 1) -> Bb (Capo 3) flags capo warning & pad_break
    tr_capo = evaluate_transition(
        {"title_ko": "A곡", "time_signature": "4/4", "bpm": 74, "outro_last_chord": "Ab"},
        {"title_ko": "B곡", "time_signature": "4/4", "bpm": 76, "intro_first_chord": "Bb", "tempo_tier": "ballad"},
        "Ab",
        "Bb",
        skill_tier="beginner_friendly",
    )
    assert tr_capo["transition_mode"] == "pad_break"
    assert tr_capo["capo_warning"] != ""

    # Plan transitions preserves fixed narrative slot order
    youth_res = search_setlist_candidates(
        service_type="youth",
        theme="기쁨과 은혜의 예배",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    planned = plan_setlist_transitions(youth_res, skill_tier="beginner_friendly")
    slot_indices = [s["slot_index"] for s in planned["songs"]]
    assert slot_indices == sorted(slot_indices)
    assert len(planned["transitions"]) == len(planned["songs"]) - 1
    assert normalize_scripture_ref("에스겔 36:26") == "EZK.36:26"
    assert normalize_scripture_ref("EZE.36:26") == "EZK.36:26"


def test_custom_era_tempo_intergenerational_and_duration(seeded_dbs: tuple[Path, Path]) -> None:
    worship_db, local_db = seeded_dbs
    res = search_setlist_candidates(
        era="2000s,2010s",
        custom_tempos="fast,fast,fast,ballad",
        intergenerational=True,
        duration_min=15,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    slots = res["pre_sermon"]
    assert len(slots) == 4
    assert [s["primary"]["tempo_tier"] for s in slots] == ["fast", "fast", "fast", "ballad"]
    assert all(s["primary"]["era"] in ("2000s", "2010s") for s in slots)
    assert len({s["primary"]["era"] for s in slots}) == 2
    assert "Medley" in slots[0]["primary"]["song_form"]
    assert "Medley" in slots[1]["primary"]["song_form"]
    assert res["estimated_total_minutes"] == 15.0
