"""Regression tests covering Superpowers skill/code/scenario audit fixes and 9-persona polish."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import common
from common import SEED_SONGS_PATH, is_blocked_source
from init_db import initialize_databases
from init_profile import apply_profile, inspect_profile
import init_profile


@pytest.fixture()
def seeded_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    worship_db = tmp_path / "worship.db"
    local_db = tmp_path / "church_local.db"
    initialize_databases(worship_db, local_db, seed_path=SEED_SONGS_PATH)
    sheets_dir = tmp_path / "sheet"
    sheets_dir.mkdir()
    return worship_db, local_db, sheets_dir


def test_is_blocked_source_avoids_short_ascii_cdn_false_positives() -> None:
    assert is_blocked_source("https://blog.kakaocdn.net/dn/bKscj9/sheet.png") == (False, None)
    assert is_blocked_source("https://scj.tv/live")[0] is True
    assert is_blocked_source("@jms_official")[0] is True
    assert is_blocked_source("신천지 찬양")[0] is True


def test_vocal_range_bound_checker_and_lyrics_guard() -> None:
    assert common.is_within_vocal_range(
        "G",
        "A",
        highest_note="F#5",
        lowest_note="D4",
        vocal_floor="A2",
        vocal_ceiling="F#4",
        leader_gender="male",
    ) is False
    assert common.is_within_vocal_range(
        "G",
        "G",
        highest_note="F#5",
        lowest_note="D4",
        vocal_floor="A2",
        vocal_ceiling="F#4",
        leader_gender="male",
    ) is True

    common.assert_no_raw_lyrics_text("1절 반복 없이 후렴 2회 진행", field_name="team_memo")
    with pytest.raises(ValueError):
        common.assert_no_raw_lyrics_text("첫째줄 가사\n둘째줄 가사\n셋째줄 가사", field_name="team_memo")
    with pytest.raises(ValueError):
        common.assert_no_raw_lyrics_text("가" * 305, field_name="team_memo")


def test_apply_profile_female_defaults_small_church_and_cli_reset(
    seeded_env: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worship_db, local_db, sheets_dir = seeded_env
    cfg_path = tmp_path / "user_config.json"

    res_female = apply_profile(
        leader_gender="female",
        church_scale="small",
        sheet_dirs=[str(sheets_dir)],
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert res_female["config"]["vocal_ceiling"] == "D5"
    assert res_female["config"]["vocal_floor"] == "G3"
    assert res_female["config"]["familiar_ratio"] == pytest.approx(0.85)
    assert res_female["config"]["vocal_ensemble"] == "solo_leader"

    # Turn lock_past_keys on, then reset via CLI --no-lock-past-keys
    apply_profile(
        lock_past_keys=True,
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["lock_past_keys"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "init_profile.py",
            "--apply",
            "--no-lock-past-keys",
            "--config-path",
            str(cfg_path),
            "--worship-db",
            str(worship_db),
            "--local-db",
            str(local_db),
        ],
    )
    init_profile.main()
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["lock_past_keys"] is False

    # inspect_profile counts sheets across all configured local_sheet_dirs (even extra custom dir)
    extra_dir = tmp_path / "extra_sheets"
    extra_dir.mkdir()
    (sheets_dir / "a_G.png").write_bytes(b"PNG")
    (extra_dir / "b_A.pdf").write_bytes(b"%PDF")
    apply_profile(
        sheet_dirs=[str(sheets_dir), str(extra_dir)],
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    insp = inspect_profile(
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert insp["local_sheet_count"] == 2


def test_fetch_sheets_ssrf_redirects_image_bomb_and_windows_reserved_names(tmp_path: Path) -> None:
    import io
    import urllib.request
    from PIL import Image
    import fetch_sheets

    for bad in (
        "http://127.0.0.1:8080/x.png",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.10/a.png",
        "ftp://example.com/a.png",
    ):
        with pytest.raises(ValueError):
            fetch_sheets.validate_public_http_url(bad)

    handler = fetch_sheets._SafeRedirectHandler()
    req = urllib.request.Request("https://example.org/sheet")
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/secret.png")
    with pytest.raises(ValueError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://scj.tv/sheet.png")

    # Pixel-bomb guard rejects image above max_pixels threshold
    buf = io.BytesIO()
    Image.new("RGB", (1200, 1200), (255, 255, 255)).save(buf, format="PNG")
    assert fetch_sheets._inspect_and_normalize_image(buf.getvalue(), max_pixels=1_000_000) is None

    # Windows reserved device names guarded & out_path stays inside target_dir
    safe_con = fetch_sheets.sanitize_filename_part("CON")
    assert safe_con.upper() != "CON"
    out_p = fetch_sheets._build_output_path(tmp_path, "CON", "G", "png")
    assert out_p.resolve().is_relative_to(tmp_path.resolve())


def test_youtube_ingestion_preserves_curated_metadata_and_arrangements(
    seeded_env: tuple[Path, Path, Path],
) -> None:
    import collect_youtube

    worship_db, local_db, _ = seeded_env
    conn_before = common.connect_dbs(worship_db, local_db, attach_local=False, ensure_schemas=False)
    song_before = dict(
        conn_before.execute("SELECT * FROM songs WHERE normalized_title = ?", (common.normalize_title("주가 일하시네"),)).fetchone()
    )
    arrs_before = conn_before.execute("SELECT * FROM arrangements WHERE song_id = ?", (song_before["id"],)).fetchall()
    conn_before.close()

    res = collect_youtube.ingest_parsed_entries(
        [
            {
                "title_ko": "주가 일하시네",
                "worship_team": "테스트워십팀",
                "key_signature": "G",
                "bpm": 74,
                "tempo_tier": "ballad",
                "recorded_year": 2024,
                "youtube_url": "https://www.youtube.com/watch?v=test123",
                "youtube_start_sec": 30,
            }
        ],
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert res["processed"] == 1

    conn_after = common.connect_dbs(worship_db, local_db, attach_local=False, ensure_schemas=False)
    song_after = dict(conn_after.execute("SELECT * FROM songs WHERE id = ?", (song_before["id"],)).fetchone())
    arrs_after = conn_after.execute("SELECT * FROM arrangements WHERE song_id = ?", (song_before["id"],)).fetchall()
    conn_after.close()

    assert song_after["theological_core"] == song_before["theological_core"]
    assert song_after["scripture_refs"] == song_before["scripture_refs"]
    assert len(arrs_after) >= len(arrs_before) + 1


def test_filename_key_parser_lyrics_memo_and_post_sermon_dict_history(
    seeded_env: tuple[Path, Path, Path],
) -> None:
    import manage_history

    worship_db, local_db, _ = seeded_env

    assert manage_history.extract_title_and_key_from_label("01_주가 일하시네_G_p1") == ("주가 일하시네", "G")
    assert manage_history.extract_title_and_key_from_label("Who You Say I Am") == ("Who You Say I Am", None)
    assert manage_history.extract_title_and_key_from_label("내 영혼이 은총 입어 (사장조)") == ("내 영혼이 은총 입어", "G")

    with pytest.raises(ValueError):
        manage_history.set_repertoire_entry(
            "juga-ilhasine-kim-brian",
            team_memo="가사 첫줄\n가사 둘째줄\n가사 셋째줄",
            worship_db_path=worship_db,
            local_db_path=local_db,
        )

    rec = manage_history.record_setlist(
        {
            "service_date": "2026-11-22",
            "service_type": "youth",
            "theme_input": "감사",
            "pre_sermon": [{"primary": {"song_slug": "juga-ilhasine-kim-brian"}}],
            "post_sermon": {"reprise_candidate": {"song_slug": "juga-ilhasine-kim-brian", "is_reprise": True}},
        },
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert rec["counted_songs"] == ["juga-ilhasine-kim-brian"]


def test_explicit_preferred_key_honored_while_bootstrapped_stays_unlocked(
    seeded_env: tuple[Path, Path, Path],
) -> None:
    import manage_history
    from search_candidates import search_setlist_candidates

    worship_db, local_db, sheets_dir = seeded_env
    # Bootstrapped file sets key in church_local? No, preferred_key=None; explicit set-repertoire pins 'F'
    manage_history.set_repertoire_entry(
        "juga-ilhasine-kim-brian",
        familiarity="favorite",
        preferred_key="F",
        team_memo="멀티트랙 클릭 고정",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    res = search_setlist_candidates(
        service_type="small_group",
        theme="빈 손에",
        user_config={"lock_past_keys": False, "leader_gender": "male", "vocal_ceiling": "F#4"},
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    found = [sl["primary"] for sl in res["pre_sermon"] if sl["primary"]["song_slug"] == "juga-ilhasine-kim-brian"]
    assert found, "Expected 주가 일하시네 in candidates"
    assert found[0]["target_key"] == "F"
    assert found[0]["transposition_reason"] == "preferred_key"


def test_transition_optimizer_respects_vocal_ceiling_bridge_regex_and_guitarless_cues() -> None:
    from plan_transitions import compute_bridge_chords, evaluate_transition, plan_setlist_transitions

    # +2 semitone shift from G -> A pushes F#5 (male F#4) to G#4 > F#4 -> MUST NOT happen
    cands = {
        "service_type": "youth",
        "pre_sermon": [
            {
                "slot_index": 1,
                "target_stage": "opener",
                "primary": {
                    "song_slug": "s1",
                    "title_ko": "곡1",
                    "worship_team": "팀",
                    "original_key": "A",
                    "target_key": "A",
                    "key_shift": 0,
                    "bpm": 120,
                    "tempo_tier": "fast",
                    "time_signature": "4/4",
                    "outro_last_chord": "A",
                },
            },
            {
                "slot_index": 2,
                "target_stage": "declaration",
                "primary": {
                    "song_slug": "s2",
                    "title_ko": "곡2",
                    "worship_team": "팀",
                    "original_key": "G",
                    "target_key": "G",
                    "key_shift": 0,
                    "highest_note": "F#5",
                    "lowest_note": "A3",
                    "bpm": 120,
                    "tempo_tier": "fast",
                    "time_signature": "4/4",
                    "intro_first_chord": "G",
                },
            },
        ],
    }
    plan = plan_setlist_transitions(
        cands,
        user_config={"leader_gender": "male", "vocal_floor": "A2", "vocal_ceiling": "F#4", "session_skill_tier": "intermediate"},
    )
    assert plan["songs"][1]["target_key"] == "G"
    assert plan["songs"][1]["target_key"] != "A"
    assert common.is_within_vocal_range("G", plan["songs"][1]["target_key"], "F#5", "A3", "A2", "F#4", "male")

    # Chord prefix guard: Fm and F#dim must NOT trigger V/IV (C7 -> Fm), while Fmaj7 MUST
    br_fm = compute_bridge_chords("G", "G", "C", "Fm")
    assert "C7" not in br_fm["bridge_chords"]
    br_fdim = compute_bridge_chords("G", "G", "C", "F#dim")
    assert "C7" not in br_fdim["bridge_chords"]
    br_fmaj7 = compute_bridge_chords("G", "G", "C", "Fmaj7")
    assert "C7 -> Fmaj7" in br_fmaj7["bridge_chords"]

    # Guitarless piano_only beginner_friendly team: no capo pad_break, says 피아노 리드
    tr_piano = evaluate_transition(
        {"slot_index": 1, "title_ko": "곡1", "time_signature": "4/4", "bpm": 100, "outro_last_chord": "G"},
        {"slot_index": 2, "title_ko": "곡2", "time_signature": "4/4", "bpm": 110, "intro_first_chord": "A", "tempo_tier": "fast"},
        "G",
        "F",
        skill_tier="beginner_friendly",
        session_instruments=["piano"],
    )
    assert tr_piano["transition_mode"] == "segue"
    assert "카포 이동" not in tr_piano["session_cue"]
    assert "피아노 리드" in tr_piano["session_cue"]

    tr_acoustic = evaluate_transition(
        {"slot_index": 1, "title_ko": "곡1", "time_signature": "4/4", "bpm": 100, "outro_last_chord": "G"},
        {"slot_index": 2, "title_ko": "곡2", "time_signature": "4/4", "bpm": 110, "intro_first_chord": "G", "tempo_tier": "fast"},
        "G",
        "G",
        skill_tier="intermediate",
        session_instruments=["piano", "acoustic_guitar"],
    )
    assert "건반/기타 리드" in tr_acoustic["session_cue"]


def test_vision_recalc_preserves_key_shift_instruments_cache_dedup_and_theology_gate(
    seeded_env: tuple[Path, Path, Path],
) -> None:
    from find_sheets import apply_vision_verification, match_setlist_sheets

    worship_db, local_db, sheets_dir = seeded_env
    sheet_path = sheets_dir / "감사함으로_A.png"
    sheet_path.write_bytes(b"FAKEPNG")

    plan = {
        "service_type": "youth",
        "session_skill_tier": "intermediate",
        "session_instruments": ["piano", "acoustic_guitar"],
        "songs": [
            {
                "slot_index": 1,
                "song_slug": "s1",
                "title_ko": "감사함으로",
                "worship_team": "마커스",
                "original_key": "F",
                "target_key": "A",
                "key_shift": 4,
                "bpm": 115,
                "time_signature": "4/4",
                "outro_last_chord": "A",
                "tempo_tier": "fast",
                "available_sections": ["I", "V1", "C"],
            },
            {
                "slot_index": 2,
                "song_slug": "s2",
                "title_ko": "주께 가까이",
                "worship_team": "마커스",
                "original_key": "A",
                "target_key": "G",
                "key_shift": -2,
                "bpm": 68,
                "time_signature": "4/4",
                "intro_first_chord": "G",
                "tempo_tier": "ballad",
                "available_sections": ["V1", "C"],
            },
        ],
        "transitions": [],
    }
    matched = match_setlist_sheets(plan, sheets_dirs=[sheets_dir], worship_db_path=worship_db, local_db_path=local_db)
    # 1. Accept recalculate_transitions alias, sync key_shift, preserve session_instruments, dedup cache
    for _ in range(2):
        matched, updated = apply_vision_verification(
            matched,
            [
                {
                    "slot_index": 1,
                    "verified_key": "G",
                    "verified_sections": ["I", "V1", "C"],
                    "key_mismatch_action": "recalculate_transitions",
                    "lyrics_theology_ok": True,
                }
            ],
            setlist_plan=plan,
            worship_db_path=worship_db,
            local_db_path=local_db,
        )
    assert updated is not None
    assert updated["songs"][0]["target_key"] == "G"
    assert updated["songs"][0]["key_shift"] == 2
    assert "피아노 잔잔한 코드 유지" in updated["transitions"][0]["session_cue"]

    # Cache deduplicated to 1 row & overrides filename '_A' on future lookup
    conn = common.connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)
    rows = conn.execute("SELECT * FROM local_db.sheet_cache WHERE song_slug = 's1'").fetchall()
    conn.close()
    assert len(rows) == 1

    # Matching again for target_key='A' must NOT falsely say filename '_A' matches 'A'!
    re_matched = match_setlist_sheets(plan, sheets_dirs=[sheets_dir], worship_db_path=worship_db, local_db_path=local_db)
    assert re_matched["sheets"][0]["sheet_key"] == "G"
    assert re_matched["sheets"][0]["key_mismatch"] is True

    # 2. Failed lyrics_theology_ok must reject sheet & purge sheet_cache
    bad_matched, _ = apply_vision_verification(
        re_matched,
        [
            {
                "slot_index": 1,
                "verified_key": "G",
                "verified_sections": ["V1"],
                "lyrics_theology_ok": False,
            }
        ],
        setlist_plan=plan,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert bad_matched["sheets"][0]["match_status"] == "rejected_theology"
    conn2 = common.connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)
    rows2 = conn2.execute("SELECT * FROM local_db.sheet_cache WHERE song_slug = 's1'").fetchall()
    conn2.close()
    assert len(rows2) == 0


def test_stamp_sheets_gates_padless_broadcast_polish_and_orphan_cleanup(tmp_path: Path) -> None:
    from PIL import Image
    from pypdf import PdfReader
    import stamp_sheets

    # 1. Refuses lyrics_theology_ok=False
    bad_plan = {
        "service_type": "youth",
        "service_date": "2026/11/29",
        "songs": [
            {
                "slot_index": 1,
                "song_slug": "s1",
                "title_ko": "감사함으로",
                "worship_team": "마커스",
                "original_key": "F",
                "target_key": "F",
                "key_shift": 0,
                "bpm": 115,
                "time_signature": "4/4",
                "ending_type": "pad_hold",
                "song_form": "I(4m) - V1 - C(x2)",
                "estimated_minutes": 4.0,
            }
        ],
        "transitions": [],
    }
    bad_matched = {"sheets": [{"slot_index": 1, "lyrics_theology_ok": False, "vision_verified": False, "rasterized_pages": []}]}
    with pytest.raises(ValueError):
        stamp_sheets.generate_setlist_package(bad_plan, bad_matched, tmp_path / "bad_out")

    # 2. Adapts padless cues in hero/footer, cleans up slash date, uses 인도·보컬:, cleans orphan PNGs
    png1 = tmp_path / "s1.png"
    Image.new("RGB", (900, 1200), (255, 255, 255)).save(png1)
    good_plan = {
        "service_type": "youth",
        "service_date": "2026/11/29",
        "duration_min": 15,
        "session_instruments": ["piano"],
        "songs": [
            {
                "slot_index": 1,
                "song_slug": "s1",
                "title_ko": "감사함으로",
                "worship_team": "마커스",
                "original_key": "F",
                "target_key": "F",
                "key_shift": 0,
                "bpm": 115,
                "time_signature": "4/4",
                "ending_type": "pad_hold",
                "song_form": "I(4m) - V1 - C(x2)",
                "caution_nuance": "고음 호흡 주의",
                "ensemble_note": "V1(x2)=1·2번 진행 | C/D-D7 전조",
                "capo_info": {"label": "Capo 3 (D 폼)"},
                "estimated_minutes": 4.0,
                "first_line_incipit": "여호와를 즐거이",
            }
        ],
        "transitions": [],
        "post_sermon": {
            "reprise_candidate": {
                "title_ko": "감사함으로",
                "target_key": "F",
                "song_form": "[C only](x2) - Vamp(결단기도)",
                "first_line_incipit": "여호와를 즐거이",
                "chorus_incipit": "주를 높이며",
            },
            "prayer_chord_loops": ["IV - V - iii - vi", "ii7 - V7sus4 - Imaj7"],
        },
    }
    good_matched = {
        "sheets": [
            {
                "slot_index": 1,
                "sheet_key": "F",
                "key_mismatch_action": "ok",
                "lyrics_theology_ok": True,
                "vision_verified": True,
                "rasterized_pages": [str(png1)],
            }
        ]
    }
    # Hero & transition line adapt when synth_pad absent
    hero_nopad = stamp_sheets.format_song_form_visual("I(4m) - V1 - C", transition_mode="pad_break", session_instruments=["piano"])
    assert "잔잔한 코드 유지" in hero_nopad
    assert "패드 홀드" not in hero_nopad

    out_dir = tmp_path / "slash_out"
    # Pre-create orphan old key PNG for slot 1 to confirm cleanup during update_slot=1
    (out_dir / "sheets").mkdir(parents=True, exist_ok=True)
    orphan_png = out_dir / "sheets" / "01_옛곡_G_p1.png"
    orphan_png.write_bytes(b"ORPHAN")

    pkg = stamp_sheets.generate_setlist_package(
        good_plan,
        good_matched,
        out_dir,
        update_slot=1,
        user_config={"show_capo": True, "session_instruments": ["piano"]},
    )
    assert Path(pkg["pdf_path"]).name == "2026_11_29_youth_콘티악보집.pdf"
    assert not orphan_png.exists()

    # show_capo=True loaded automatically from user_config
    kakao = Path(pkg["kakao_notice_path"]).read_text(encoding="utf-8")
    assert "Capo 3" in kakao
    assert "(~4분)" in kakao

    # leader_cuesheet.md includes total runtime & no 보컬·묵상 typo
    leader_md = Path(pkg["leader_cuesheet_path"]).read_text(encoding="utf-8")
    assert "예상 소요시간" in leader_md
    assert "보컬·묵상" not in leader_md

    # broadcast_cue strips chord jargon & adds 자막 내림 + 후렴부터 바로 자막 송출
    bcast = Path(pkg["broadcast_cue_path"]).read_text(encoding="utf-8")
    assert "★ 자막 내림" in bcast
    assert "C/D-D7" not in bcast
    assert "1절 생략 · 후렴부터 바로 자막 송출" in bcast

    # 6-song friday_revival cover sheet renders without crashing or losing songs
    songs_6 = [
        {**good_plan["songs"][0], "slot_index": i, "title_ko": f"테스트찬양{i}"}
        for i in range(1, 7)
    ]
    cover_6 = stamp_sheets.render_cover_cuesheet_page({**good_plan, "songs": songs_6}, {})
    assert cover_6.size == (stamp_sheets.A4_W, stamp_sheets.A4_H)
    assert len(PdfReader(pkg["pdf_path"]).pages) == 2


def test_global_plugin_and_global_skill_workspace_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    active_ws = tmp_path / "church_workspace"
    fake_home.mkdir()
    active_ws.mkdir()

    global_plugin_skill = fake_home / ".gemini" / "config" / "plugins" / "worship" / "skills" / "korean-worship-setlist"
    global_agents_skill = fake_home / ".agents" / "skills" / "korean-worship-setlist"
    npm_global_skill = fake_home / "node_modules" / "korean-worship-setlist"
    ws_plugin_skill = active_ws / ".agents" / "plugins" / "worship" / "skills" / "korean-worship-setlist"
    ws_skill = active_ws / ".agents" / "skills" / "korean-worship-setlist"

    for p in (global_plugin_skill, global_agents_skill, npm_global_skill, ws_plugin_skill, ws_skill):
        p.mkdir(parents=True)

    # 1. Global plugin & global cross-agent/npm skills isolate state into active_ws/.worship-setlist
    for gp in (global_plugin_skill, global_agents_skill, npm_global_skill):
        assert common.is_global_skill_install(gp, home_dir=fake_home) is True
        assert common.detect_workspace_root(gp, cwd=active_ws, home_dir=fake_home) == active_ws.resolve()
        assert common.get_local_state_dir(gp, cwd=active_ws, home_dir=fake_home) == active_ws.resolve() / ".worship-setlist"

    # 2. Workspace plugin (4 levels under .agents/plugins/<name>/skills/<skill>) & workspace skill use workspace root & skill data/
    for wp in (ws_plugin_skill, ws_skill):
        assert common.is_global_skill_install(wp, home_dir=fake_home) is False
        assert common.detect_workspace_root(wp, cwd=tmp_path, home_dir=fake_home) == active_ws.resolve()
        assert common.get_local_state_dir(wp, cwd=tmp_path, home_dir=fake_home) == wp.resolve() / "data"

    # 3. Dynamic CWD resolution when running as global plugin
    monkeypatch.setattr(common, "SKILL_DIR", global_plugin_skill)
    monkeypatch.setattr(common.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(active_ws)
    assert common.resolve_workspace_path("sheet") == active_ws.resolve() / "sheet"
    assert common.get_user_config_path() == active_ws.resolve() / ".worship-setlist" / "user_config.json"
    assert common.get_local_db_path() == active_ws.resolve() / ".worship-setlist" / "church_local.db"




