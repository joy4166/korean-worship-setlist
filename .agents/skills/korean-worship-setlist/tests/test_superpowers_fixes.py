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


def test_rhythm_groove_transitions_and_vision_self_healing(
    seeded_env: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    import sqlite3
    import find_sheets
    import plan_transitions
    import search_candidates
    import stamp_sheets

    worship_db, local_db, _ = seeded_env

    # 1. Incompatible subdivisions at identical 124 BPM force rhythm_reset (never seamless segue)
    tr_clash = plan_transitions.evaluate_transition(
        {
            "slot_index": 1,
            "title_ko": "우리 모두 찬양하세",
            "time_signature": "4/4",
            "bpm": 124,
            "tempo_tier": "fast",
            "groove_style": "shuffle_bounce",
            "outro_last_chord": "G",
        },
        {
            "slot_index": 2,
            "title_ko": "기쁨으로 찬양",
            "time_signature": "4/4",
            "bpm": 124,
            "tempo_tier": "fast",
            "groove_style": "16th_upbeat",
            "intro_first_chord": "G",
        },
        "G",
        "G",
        session_instruments=["piano", "drums"],
    )
    assert tr_clash["transition_mode"] == "rhythm_reset"
    assert "리듬 리셋 전환" in tr_clash["session_cue"]
    assert "4박 카운트" in tr_clash["session_cue"]

    hero_reset = stamp_sheets.format_song_form_visual(
        "I(4m) - V1 - C(x2) -->(Medley 2번곡)",
        transition_mode="rhythm_reset",
    )
    assert "[정박 엔딩 · 카운트 재진입]" in hero_reset
    assert "[바로 연결]" not in hero_reset

    # 2. Unverified catalog_estimated fast songs emit rhythmic safety warning
    tr_unverified = plan_transitions.evaluate_transition(
        {
            "slot_index": 1,
            "title_ko": "미검증빠른곡A",
            "time_signature": "4/4",
            "bpm": 124,
            "tempo_tier": "fast",
            "bpm_source": "catalog_estimated",
            "groove_style": None,
            "outro_last_chord": "G",
        },
        {
            "slot_index": 2,
            "title_ko": "미검증빠른곡B",
            "time_signature": "4/4",
            "bpm": 124,
            "tempo_tier": "fast",
            "bpm_source": "catalog_estimated",
            "groove_style": None,
            "intro_first_chord": "G",
        },
        "G",
        "G",
    )
    assert tr_unverified["rhythm_unverified"] is True
    assert "리듬 미검증(DB 추정치)" in tr_unverified["session_cue"]

    # 3. Older sheet_cache auto-migration + vision rhythm recalculation on same-Key + self-healing search
    legacy_local_db = tmp_path / "legacy_local.db"
    lconn = sqlite3.connect(str(legacy_local_db))
    lconn.executescript(
        """
        CREATE TABLE repertoire (
            song_slug TEXT PRIMARY KEY,
            familiarity TEXT NOT NULL DEFAULT 'unknown',
            preferred_key TEXT,
            times_sung INTEGER NOT NULL DEFAULT 0,
            consecutive_learning_count INTEGER NOT NULL DEFAULT 0,
            last_sung_date TEXT,
            team_memo TEXT
        );
        CREATE TABLE setlist_history (
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
        CREATE TABLE sheet_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_slug TEXT NOT NULL,
            file_path TEXT NOT NULL,
            detected_key TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 1,
            verified_sections_json TEXT DEFAULT '[]',
            verified_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    lconn.commit()
    lconn.close()

    wconn = sqlite3.connect(str(worship_db))
    wconn.row_factory = sqlite3.Row
    fast_row = wconn.execute(
        """
        SELECT s.song_slug, s.title_ko, a.key_signature
        FROM songs s JOIN arrangements a ON a.song_id = s.id
        WHERE a.tempo_tier = 'fast'
        LIMIT 1
        """
    ).fetchone()
    wconn.close()
    assert fast_row is not None
    slug_1 = fast_row["song_slug"]
    title_1 = fast_row["title_ko"]

    dummy_sheet = tmp_path / f"{title_1}_G.png"
    dummy_sheet.write_bytes(b"PNG_PLACEHOLDER")

    matched = {
        "sheets": [
            {
                "slot_index": 1,
                "song_slug": slug_1,
                "title_ko": title_1,
                "target_key": "G",
                "sheet_key": "G",
                "source_file": str(dummy_sheet),
                "page_count": 1,
            }
        ]
    }
    plan = {
        "session_skill_tier": "intermediate",
        "session_instruments": ["piano", "drums"],
        "songs": [
            {
                "slot_index": 1,
                "song_slug": slug_1,
                "title_ko": title_1,
                "original_key": "G",
                "target_key": "G",
                "key_shift": 0,
                "bpm": 124,
                "time_signature": "4/4",
                "tempo_tier": "fast",
                "groove_style": "straight_8th",
                "song_form": "I(4m) - V1 - C(x2) -->(Medley 2번곡)",
                "outro_last_chord": "G",
            },
            {
                "slot_index": 2,
                "song_slug": "slot2-straight-16th",
                "title_ko": "다음빠른곡",
                "original_key": "G",
                "target_key": "G",
                "key_shift": 0,
                "bpm": 124,
                "time_signature": "4/4",
                "tempo_tier": "fast",
                "groove_style": "16th_upbeat",
                "song_form": "V1 - C(x2) - O",
                "intro_first_chord": "G",
            },
        ],
        "transitions": [],
    }
    verifications = [
        {
            "slot_index": 1,
            "verified_key": "G",
            "verified_groove": "shuffle",
            "verified_bpm": 126,
            "verified_time_signature": "4/4",
            "verified_sections": ["I", "V1", "C"],
            "key_mismatch_action": "ok",
            "lyrics_theology_ok": True,
        }
    ]
    _, updated_plan = find_sheets.apply_vision_verification(
        matched,
        verifications,
        setlist_plan=plan,
        worship_db_path=worship_db,
        local_db_path=legacy_local_db,
    )
    assert updated_plan is not None
    assert updated_plan["songs"][0]["groove_style"] == "shuffle_bounce"
    assert updated_plan["songs"][0]["bpm"] == 126
    assert updated_plan["transitions"][0]["transition_mode"] == "rhythm_reset"
    assert "-->" not in updated_plan["songs"][0]["song_form"]

    # Self-healing: subsequent search_setlist_candidates picks up cached groove & BPM from legacy_local_db
    cand_res = search_candidates.search_setlist_candidates(
        service_type="youth",
        theme=title_1,
        custom_tempos=["fast"],
        user_config={"session_instruments": ["piano", "drums"], "local_sheet_dirs": []},
        worship_db_path=worship_db,
        local_db_path=legacy_local_db,
    )
    all_slot1 = [cand_res["pre_sermon"][0]["primary"]] + cand_res["pre_sermon"][0]["plan_b"]
    healed = next((c for c in all_slot1 if c["song_slug"] == slug_1), None)
    assert healed is not None
    assert healed["groove_style"] == "shuffle_bounce"
    assert healed["bpm"] == 126
    assert healed["bpm_source"] == "local_sheet_verified"


def test_skill_update_notifier_throttling_offline_safety_and_cli_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    fake_home = tmp_path / "home"
    ws_dir = tmp_path / "ws"
    fake_home.mkdir()
    ws_dir.mkdir()

    ws_skill = ws_dir / ".agents" / "skills" / "korean-worship-setlist"
    global_skill = fake_home / ".gemini" / "config" / "skills" / "korean-worship-setlist"
    global_plugin = fake_home / ".gemini" / "config" / "plugins" / "korean-worship-setlist" / "skills" / "korean-worship-setlist"
    claude_global = fake_home / ".claude" / "skills" / "korean-worship-setlist"

    for sdir in (ws_skill, global_skill, global_plugin, claude_global):
        sdir.mkdir(parents=True)
        (sdir / "pyproject.toml").write_text('[project]\nname = "korean-worship-setlist"\nversion = "0.1.1"\n', encoding="utf-8")

    cache_file = tmp_path / "update_check.json"

    # 1. Newer stable version returns UPDATE_AVAILABLE and populates cache
    monkeypatch.delenv("WORSHIP_NO_UPDATE_CHECK", raising=False)
    calls = {"count": 0}

    def _fetch_020(url: str, timeout: float) -> dict[str, object]:
        calls["count"] += 1
        return {"version": "0.2.0"}

    upd = common.check_skill_update(
        ws_skill,
        cache_path=cache_file,
        fetcher=_fetch_020,
        home_dir=fake_home,
    )
    assert upd is not None
    assert upd["directive"] == "UPDATE_AVAILABLE"
    assert upd["installed_version"] == "0.1.1"
    assert upd["latest_version"] == "0.2.0"
    assert upd["upgrade_command"] == "npx korean-worship-setlist@latest update"
    assert cache_file.exists()
    assert calls["count"] == 1

    # 2. 24h throttle skips network fetcher completely
    def _should_not_run(url: str, timeout: float) -> dict[str, object]:
        raise AssertionError("should not fetch while cache is fresh")

    upd_cached = common.check_skill_update(
        ws_skill,
        cache_path=cache_file,
        fetcher=_should_not_run,
        home_dir=fake_home,
    )
    assert upd_cached is not None
    assert upd_cached["latest_version"] == "0.2.0"

    # 3. Context-aware upgrade commands for global / plugin / claude global installs
    assert (
        common.check_skill_update(global_skill, cache_path=cache_file, fetcher=_should_not_run, home_dir=fake_home)["upgrade_command"]
        == "npx korean-worship-setlist@latest update --global"
    )
    assert (
        common.check_skill_update(global_plugin, cache_path=cache_file, fetcher=_should_not_run, home_dir=fake_home)["upgrade_command"]
        == "npx korean-worship-setlist@latest update --plugin"
    )
    assert (
        common.check_skill_update(claude_global, cache_path=cache_file, fetcher=_should_not_run, home_dir=fake_home)["upgrade_command"]
        == "npx korean-worship-setlist@latest update --claude --global"
    )

    # 4. Pre-releases and equal/older versions are ignored (return None)
    for bad_ver in ("0.0.0-stage", "0.2.0-beta.1", "0.1.1", "0.1.0"):
        assert (
            common.check_skill_update(
                ws_skill,
                cache_path=tmp_path / f"cache_{bad_ver}.json",
                fetcher=lambda u, t, v=bad_ver: {"version": v},
                force=True,
                home_dir=fake_home,
            )
            is None
        )

    # 5. Offline failures return None silently and write backoff cache
    offline_cache = tmp_path / "offline_cache.json"

    def _raise_offline(url: str, timeout: float) -> dict[str, object]:
        raise OSError("network down")

    assert (
        common.check_skill_update(
            ws_skill,
            cache_path=offline_cache,
            fetcher=_raise_offline,
            force=True,
            home_dir=fake_home,
        )
        is None
    )
    assert offline_cache.exists()

    # 6. Opt-out via env var
    monkeypatch.setenv("WORSHIP_NO_UPDATE_CHECK", "1")
    assert common.check_skill_update(ws_skill, cache_path=cache_file, fetcher=_should_not_run, home_dir=fake_home) is None
    monkeypatch.delenv("WORSHIP_NO_UPDATE_CHECK", raising=False)

    # 7. Node CLI `update --dest` upgrades skill files while preserving church user_config.json & church_local.db
    dest_skill = tmp_path / "existing_skill"
    (dest_skill / "data").mkdir(parents=True)
    (dest_skill / "pyproject.toml").write_text('[project]\nname = "korean-worship-setlist"\nversion = "0.1.0"\n', encoding="utf-8")
    (dest_skill / "data" / "user_config.json").write_text('{"team_name": "보존확인"}', encoding="utf-8")
    (dest_skill / "data" / "church_local.db").write_bytes(b"KEEP_LOCAL_DB")

    repo_root = common.SKILL_DIR.parent.parent.parent
    cli_path = repo_root / "bin" / "korean-worship-setlist.mjs"
    proc = subprocess.run(
        ["node", str(cli_path), "update", "--dest", str(dest_skill)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    expected_ver = common.get_installed_skill_version()
    assert proc.returncode == 0, proc.stderr
    assert f"(v0.1.0 -> v{expected_ver})" in proc.stdout
    assert f'version = "{expected_ver}"' in (dest_skill / "pyproject.toml").read_text(encoding="utf-8")
    assert json.loads((dest_skill / "data" / "user_config.json").read_text(encoding="utf-8"))["team_name"] == "보존확인"
    assert (dest_skill / "data" / "church_local.db").read_bytes() == b"KEEP_LOCAL_DB"


def test_tempo_classification_semantics_and_catalog_mood_genre_accuracy() -> None:
    import analyze_audio
    import build_bulk_catalog

    # 1. classify_tempo_tier across meters and grooves
    assert analyze_audio.classify_tempo_tier(74, "4/4") == "ballad"
    assert analyze_audio.classify_tempo_tier(100, "4/4") == "medium"
    assert analyze_audio.classify_tempo_tier(100, "4/4", groove_hint="16th_upbeat") == "fast"
    assert analyze_audio.classify_tempo_tier(110, "4/4") == "fast"

    assert analyze_audio.classify_tempo_tier(78, "3/4") == "ballad"
    assert analyze_audio.classify_tempo_tier(96, "3/4") == "medium"
    assert analyze_audio.classify_tempo_tier(116, "3/4") == "fast"

    for compound in ("6/8", "9/8", "12/8"):
        assert analyze_audio.classify_tempo_tier(58, compound) == "ballad"
        assert analyze_audio.classify_tempo_tier(74, compound) == "medium"
        assert analyze_audio.classify_tempo_tier(88, compound) == "fast"

    # 2. correct_bpm_and_classify trusted_bpm & half/double/triple recovery
    trusted = analyze_audio.correct_bpm_and_classify(134.0, time_signature="4/4", slot_hint="meditation", trusted_bpm=True)
    assert trusted["bpm"] == 134
    assert trusted["tempo_tier"] == "fast"
    assert trusted["was_corrected"] is False

    recovered_fast = analyze_audio.correct_bpm_and_classify(58.0, time_signature="4/4", slot_hint="opener", groove_hint="rock_upbeat")
    assert recovered_fast["bpm"] == 116
    assert recovered_fast["tempo_tier"] == "fast"
    assert recovered_fast["correction_reason"] == "fast_context_double_time_mul_2"

    sub_48 = analyze_audio.correct_bpm_and_classify(42.0, time_signature="4/4")
    assert sub_48["bpm"] == 84
    assert sub_48["tempo_tier"] == "medium"
    assert sub_48["correction_reason"] == "sub_48_double_time_mul_2"

    m_9_8 = analyze_audio.correct_bpm_and_classify(174.0, time_signature="9/8")
    assert m_9_8["bpm"] == 58
    assert m_9_8["tempo_tier"] == "ballad"

    m_3_4_x3 = analyze_audio.correct_bpm_and_classify(174.0, time_signature="3/4")
    assert m_3_4_x3["bpm"] == 58
    assert m_3_4_x3["tempo_tier"] == "ballad"

    m_3_4_half = analyze_audio.correct_bpm_and_classify(40.0, time_signature="3/4")
    assert m_3_4_half["bpm"] == 80
    assert m_3_4_half["tempo_tier"] == "medium"

    # 3. infer_worship_semantics avoids 1-char collisions and duration-based fast misclassification
    pinancheo = build_bulk_catalog.infer_worship_semantics("내 피난처 예수")
    assert pinancheo["theological_tone"] != "cross_sacrifice"

    janjanhan = build_bulk_catalog.infer_worship_semantics("잔잔한 물가로")
    assert janjanhan["theological_tone"] != "cross_sacrifice"

    bulssang = build_bulk_catalog.infer_worship_semantics("주여 날 불쌍히 여기소서")
    assert bulssang["theological_tone"] == "penitence"

    silent_night = build_bulk_catalog.infer_worship_semantics("고요한 밤 거룩한 밤", category="modern_worship", duration_sec=190)
    assert silent_night["tempo_tier"] == "ballad"
    assert "opener" not in silent_night["liturgical_stages"]

    # 4. Rebuilt worship.db verification for previously misclassified songs & hymn merges
    conn = common.connect_dbs(common.WORSHIP_DB_PATH, attach_local=False, ensure_schemas=False)
    try:
        ballad_titles = [
            "거기 너 있었는가",
            "고요한 밤 거룩한 밤",
            "곤한 내 영혼 편히 쉴 곳과",
            "그 크신 하나님의 사랑",
            "사랑해요 목소리 높여",
            "나 주님의 기쁨 되기 원하네",
            "거룩하신 하나님",
        ]
        for title in ballad_titles:
            rows = conn.execute(
                """
                SELECT s.liturgical_stages, a.tempo_tier, a.worship_team
                FROM songs s JOIN arrangements a ON a.song_id = s.id
                WHERE s.normalized_title = ?
                """,
                (common.normalize_title(title),),
            ).fetchall()
            assert rows, f"Missing ballad song: {title}"
            for r in rows:
                stages = json.loads(r["liturgical_stages"] or "[]")
                assert r["tempo_tier"] == "ballad", f"{title} ({r['worship_team']}) had {r['tempo_tier']}"
                assert "opener" not in stages, f"{title} unexpectedly retained opener: {stages}"

        fast_titles = [
            "주 이름 찬양",
            "오 자유",
            "나는 자유해",
            "죄에서 자유를 얻게 함은",
            "새 노래로 노래하리",
            "기뻐하며 왕께",
        ]
        for title in fast_titles:
            rows = conn.execute(
                """
                SELECT s.liturgical_stages, a.tempo_tier, a.worship_team
                FROM songs s JOIN arrangements a ON a.song_id = s.id
                WHERE s.normalized_title = ?
                """,
                (common.normalize_title(title),),
            ).fetchall()
            assert rows, f"Missing fast song: {title}"
            for r in rows:
                stages = json.loads(r["liturgical_stages"] or "[]")
                assert r["tempo_tier"] == "fast", f"{title} ({r['worship_team']}) had {r['tempo_tier']}"
                assert "opener" in stages, f"{title} missing opener stage: {stages}"

        hymn_309 = conn.execute(
            """
            SELECT s.category, s.hymn_number
            FROM songs s JOIN arrangements a ON a.song_id = s.id
            WHERE s.normalized_title = ? AND a.worship_team = '피아워십'
            """,
            (common.normalize_title("목마른 내 영혼"),),
        ).fetchone()
        assert hymn_309 is not None
        assert hymn_309["category"] == "hymn"
        assert hymn_309["hymn_number"] == 309

        noise_cnt = conn.execute(
            "SELECT COUNT(*) FROM songs WHERE title_ko LIKE '%ASMR%' OR title_ko LIKE '%Big Yellow Taxi%'"
        ).fetchone()[0]
        assert noise_cnt == 0
    finally:
        conn.close()


def test_leader_ment_slots_antipattern_linter_and_cuesheet_styles(tmp_path: Path) -> None:
    import find_sheets
    import init_db
    import init_profile
    import plan_transitions
    import stamp_sheets

    cfg_path = tmp_path / "user_config.json"
    worship_db = tmp_path / "worship.db"
    local_db = tmp_path / "church_local.db"
    init_db.initialize_databases(worship_db, local_db, seed_path=common.SEED_SONGS_PATH)

    # 1. Profile persistence & validation for leader_ment_style
    res = init_profile.apply_profile(
        team_name="멘트테스트팀",
        leader_ment_style="bullet_points",
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert res["config"]["leader_ment_style"] == "bullet_points"

    with pytest.raises(ValueError, match="leader_ment_style"):
        init_profile.apply_profile(
            leader_ment_style="dramatic_sermon",
            config_path=cfg_path,
            worship_db_path=worship_db,
            local_db_path=local_db,
        )

    # 2. ment_slot generation for I(4m), I(8m), segue, rhythm_reset, ballad->Selah, padless
    cand_data = {
        "service_type": "youth",
        "church_season": "ordinary",
        "theme_input": "광야와 은혜",
        "scripture_input": "PSA.23",
        "reference_date": "2026-11-29",
        "pre_sermon": [
            {
                "slot_index": 1,
                "target_stage": "opener",
                "primary": {
                    "song_slug": "song-1",
                    "title_ko": "주 이름 찬양",
                    "target_key": "G",
                    "original_key": "G",
                    "bpm": 120,
                    "time_signature": "4/4",
                    "tempo_tier": "fast",
                    "groove_style": "rock_upbeat",
                    "song_form": "I(4m) - V1 - C(x2)",
                    "theological_core": "주의 이름 높임",
                    "outro_last_chord": "G",
                    "ending_type": "clean_cut",
                    "available_sections": ["I", "V1", "C"],
                },
            },
            {
                "slot_index": 2,
                "target_stage": "declaration",
                "primary": {
                    "song_slug": "song-2",
                    "title_ko": "나는 자유해",
                    "target_key": "G",
                    "original_key": "G",
                    "bpm": 122,
                    "time_signature": "4/4",
                    "tempo_tier": "fast",
                    "groove_style": "rock_upbeat",
                    "song_form": "V1 - C(x2) - O",
                    "theological_core": "복음의 자유 선포",
                    "intro_first_chord": "G",
                    "outro_last_chord": "G",
                    "ending_type": "clean_cut",
                    "available_sections": ["V1", "C", "O"],
                },
            },
            {
                "slot_index": 3,
                "target_stage": "meditation",
                "primary": {
                    "song_slug": "song-3",
                    "title_ko": "광야를 지나며",
                    "target_key": "G",
                    "original_key": "G",
                    "bpm": 68,
                    "time_signature": "4/4",
                    "tempo_tier": "ballad",
                    "groove_style": "16th_ballad",
                    "song_form": "I(4m) - V1 - C - O",
                    "theological_core": "광야 속 인도하심 신뢰",
                    "scripture_refs": ["PSA.23:1-3"],
                    "intro_first_chord": "G",
                    "outro_last_chord": "G",
                    "ending_type": "pad_hold",
                    "available_sections": ["I", "V1", "C", "O"],
                },
            },
            {
                "slot_index": 4,
                "target_stage": "confession",
                "primary": {
                    "song_slug": "song-4",
                    "title_ko": "은혜",
                    "target_key": "C",
                    "original_key": "C",
                    "bpm": 66,
                    "time_signature": "6/8",
                    "tempo_tier": "ballad",
                    "groove_style": "6_8_worship",
                    "song_form": "I(4m) - V1 - C - O",
                    "theological_core": "모든 것이 은혜임 고백",
                    "intro_first_chord": "C",
                    "outro_last_chord": "C",
                    "ending_type": "pad_hold",
                    "available_sections": ["I", "V1", "C", "O"],
                },
            },
        ],
        "post_sermon": {
            "reprise_candidate": {
                "song_slug": "song-4",
                "title_ko": "은혜",
                "target_key": "C",
                "song_form": "I(2m) - [C only](x2) - Vamp(결단기도)",
            },
            "prayer_chord_loops": ["IV - V - iii - vi"],
        },
    }

    plan = plan_transitions.plan_setlist_transitions(
        cand_data,
        user_config={
            "session_skill_tier": "intermediate",
            "session_instruments": ["piano", "synth_pad", "drums"],
            "leader_ment_style": "short_script",
        },
    )
    assert plan["songs"][0]["ment_slot"]["role"] == "opening_call"
    assert plan["songs"][0]["ment_slot"]["allowed"] is True
    assert plan["songs"][0]["ment_slot"]["max_chars"] == 55
    assert plan["songs"][1]["ment_slot"]["role"] == "skip_direct"
    assert plan["songs"][1]["ment_slot"]["allowed"] is False
    assert plan["songs"][1]["ment_slot"]["max_chars"] == 0
    assert plan["songs"][2]["ment_slot"]["role"] == "pad_bridge"
    assert plan["songs"][2]["ment_slot"]["suggested_scripture"] == "PSA.23:1-3"
    assert plan["songs"][3]["ment_slot"]["role"] == "pad_bridge"
    assert "침묵(Selah)" in plan["songs"][3]["ment_slot"]["timing_cue"]
    assert plan["post_sermon"]["ment_slot"]["role"] == "reprise_prayer"

    # Check I(8m) opening & piano-only (padless) cue & rhythm_reset skip_direct
    plan_padless = json.loads(json.dumps(cand_data, ensure_ascii=False))
    plan_padless["pre_sermon"][0]["primary"]["song_form"] = "I(8m) - V1 - C"
    plan_padless["pre_sermon"][1]["primary"]["groove_style"] = "shuffle_bounce"
    res_padless = plan_transitions.plan_setlist_transitions(
        plan_padless,
        user_config={
            "session_skill_tier": "intermediate",
            "session_instruments": ["piano", "drums"],
            "leader_ment_style": "short_script",
        },
    )
    assert res_padless["songs"][0]["ment_slot"]["max_chars"] == 75
    assert res_padless["songs"][1]["ment_slot"]["role"] == "skip_direct"
    assert "4박 카운트 직결" in res_padless["songs"][1]["ment_slot"]["timing_cue"]
    assert "피아노 잔잔한 코드" in res_padless["songs"][3]["ment_slot"]["timing_cue"]

    # 3. find_sheets vision recalc flips pad_break -> segue/rhythm_reset & clears stale leader_ment
    dummy = tmp_path / "dummy.png"
    dummy.write_bytes(b"PNG")
    plan_flip = json.loads(json.dumps(plan, ensure_ascii=False))
    # Force slot 3 temporarily to 120 BPM rock_upbeat via vision recalc so slot 2 -> 3 turns into pad_break or slot 1 -> 2 flips
    # First attach leader_ment on slot 3, then have vision flip slot 3 to 122 BPM rock_upbeat (same key G, same tempo as slot 2) -> segue!
    plan_flip["songs"][2]["leader_ment"] = {
        "focus_point": "호흡 전환",
        "spoken_text": "이제 조용히 주님의 인도하심을 구하며 나아갑시다.",
    }
    matched_flip = {
        "sheets": [
            {
                "slot_index": 3,
                "song_slug": "song-3",
                "title_ko": "광야를 지나며",
                "target_key": "G",
                "sheet_key": "G",
                "source_file": str(dummy),
                "page_count": 1,
            }
        ]
    }
    _, updated_flip = find_sheets.apply_vision_verification(
        matched_flip,
        [
            {
                "slot_index": 3,
                "verified_key": "G",
                "verified_bpm": 122,
                "verified_groove": "rock_upbeat",
                "verified_time_signature": "4/4",
                "lyrics_theology_ok": True,
            }
        ],
        setlist_plan=plan_flip,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert updated_flip is not None
    assert updated_flip["transitions"][1]["transition_mode"] == "segue"
    assert updated_flip["songs"][2]["ment_slot"]["role"] == "skip_direct"
    assert "leader_ment" not in updated_flip["songs"][2]

    # 4. Anti-Pattern Linter hard gates
    bad_cases = [
        ("사랑하는 성도 여러분 함께 찬양합시다.", {"role": "opening_call", "max_chars": 55, "max_sentences": 1}),
        ("은혜의 귀한 시간이 되길 간절히 소망합니다.", {"role": "pad_bridge", "max_chars": 110, "max_sentences": 2}),
        ("다음으로 부르실 찬양은 광야를 지나며입니다.", {"role": "pad_bridge", "max_chars": 110, "max_sentences": 2}),
        ("목소리가 왜 이렇게 작으세요?", {"role": "opening_call", "max_chars": 55, "max_sentences": 1}),
        ("할렐루야 아멘이십니까!", {"role": "opening_call", "max_chars": 55, "max_sentences": 1}),
        ("짧은 멘트입니다.", {"role": "skip_direct", "allowed": False, "max_chars": 0, "max_sentences": 0}),
        ("첫 문장입니다. 두 번째 문장입니다. 세 번째 문장이 넘쳤습니다.", {"role": "pad_bridge", "max_chars": 110, "max_sentences": 2}),
        ("분주했던 우리의 일상을 지나며 주님 앞에 멈춰 서서 우리 마음을 다시 주님께 올려드리며 찬양하기 원합니다." + " 가" * 20, {"role": "opening_call", "max_chars": 55, "max_sentences": 1}),
    ]
    for bad_text, slot_spec in bad_cases:
        with pytest.raises(ValueError):
            stamp_sheets.validate_leader_ment(bad_text, slot_spec, song_title="광야를 지나며")

    # 5. Render modes in build_leader_cuesheet_md ('short_script' vs 'bullet_points' vs 'none')
    plan["songs"][0]["leader_ment"] = {
        "focus_point": "예배 자리로의 시선 전환",
        "spoken_text": "분주한 한 주를 내려놓고 주님만 바라보며 찬양합시다.",
    }
    plan["songs"][2]["leader_ment"] = {
        "focus_point": "승리 선포에서 광야의 신뢰로 호흡 전환",
        "spoken_text": "기쁨으로 주를 높였던 마음 그대로, 광야에서도 우리를 이끄시는 주님을 고백합시다.",
        "scripture_callout": "시편 23:1-3",
    }
    plan["post_sermon"]["leader_ment"] = {
        "focus_point": "설교 말씀 붙들고 결단 기도 전환",
        "spoken_text": "들은 말씀 기억하며 함께 고백합시다. 삶의 자리를 주께 드리며 기도합시다.",
    }

    md_script = stamp_sheets.build_leader_cuesheet_md(plan, ment_style="short_script")
    assert "인도 포인트" in md_script
    assert "예시 멘트" in md_script
    assert "[멘트 생략 — 음악으로 바로 연결]" in md_script
    assert "시편 23:1-3" in md_script

    md_bullet = stamp_sheets.build_leader_cuesheet_md(plan, ment_style="bullet_points")
    assert "인도 포인트" in md_bullet
    assert "예시 멘트" not in md_bullet
    assert "[멘트 생략 — 음악으로 바로 연결]" in md_bullet

    md_none = stamp_sheets.build_leader_cuesheet_md(plan, ment_style="none")
    assert "인도 포인트" not in md_none
    assert "예시 멘트" not in md_none
    assert "[멘트 생략" not in md_none








