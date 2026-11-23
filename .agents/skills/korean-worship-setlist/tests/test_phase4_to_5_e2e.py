"""End-to-End integration tests for Phase 4-5: sheet matching, vision branching, stamping, PDF outlines, and 3 worship scenarios."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import SEED_SONGS_PATH, connect_dbs
from fetch_sheets import _inspect_and_normalize_image, _normalize_public_url, extract_media_candidates
from find_sheets import apply_vision_verification, match_setlist_sheets
from init_db import initialize_databases
from init_profile import apply_profile, derive_default_session, inspect_profile
from manage_history import bootstrap_repertoire, record_setlist, set_repertoire_entry
from plan_transitions import evaluate_transition, plan_setlist_transitions, realize_roman_progression
from search_candidates import search_setlist_candidates
from stamp_sheets import build_broadcast_cue, build_kakao_notice, format_song_form_visual, generate_setlist_package


def _make_rgba_exif_png(path: Path, label: str) -> None:
    """Create a transparent RGBA PNG with EXIF orientation metadata to test normalization."""
    img = Image.new("RGBA", (900, 1200), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 860, 1160], fill=(255, 255, 255, 255), outline=(20, 20, 20, 255), width=4)
    draw.line([(80, 250), (820, 250)], fill=(0, 0, 0, 255), width=3)
    draw.line([(80, 320), (820, 320)], fill=(0, 0, 0, 255), width=3)
    draw.text((100, 100), label, fill=(0, 0, 0, 255))
    exif = img.getexif()
    exif[0x0112] = 3  # 180-degree rotation tag to verify ImageOps.exif_transpose runs safely
    img.save(path, format="PNG", exif=exif)


def _make_multipage_pdf(path: Path, label: str, pages: int = 2) -> None:
    imgs: list[Image.Image] = []
    for p in range(1, pages + 1):
        im = Image.new("RGB", (1000, 1400), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([50, 50, 950, 1350], outline=(30, 30, 30), width=4)
        d.text((100, 120), f"{label} - Page {p}", fill=(0, 0, 0))
        d.line([(90, 300), (910, 300)], fill=(0, 0, 0), width=3)
        imgs.append(im)
    imgs[0].save(path, format="PDF", save_all=True, append_images=imgs[1:])


@pytest.fixture()
def e2e_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    worship_db = tmp_path / "worship.db"
    local_db = tmp_path / "church_local.db"
    initialize_databases(worship_db, local_db, seed_path=SEED_SONGS_PATH)
    sheets_dir = tmp_path / "local_sheets"
    sheets_dir.mkdir()
    return worship_db, local_db, sheets_dir


def test_scenario1_sunday_main_lent_e2e(e2e_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    worship_db, local_db, sheets_dir = e2e_env

    cands = search_setlist_candidates(
        service_type="sunday_main",
        season="lent",
        theme="십자가 보혈 회개",
        scripture="갈라디아서 6:14",
        reference_date="2026-03-08",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert len(cands["pre_sermon"]) == 3
    for sl in cands["pre_sermon"]:
        assert sl["primary"]["theological_tone"] != "resurrection_victory"
        assert sl["primary"]["tempo_tier"] != "fast"

    plan = plan_setlist_transitions(cands, skill_tier="intermediate")
    plan["service_date"] = "2026-03-08"

    # Create local sheet fixtures for each selected song (first song is 2-page PDF, others RGBA EXIF PNG)
    for idx, song in enumerate(plan["songs"]):
        key_clean = song["target_key"].split()[0]
        if idx == 0:
            _make_multipage_pdf(sheets_dir / f"{song['title_ko']}_{key_clean}.pdf", song["title_ko"], pages=2)
        else:
            _make_rgba_exif_png(sheets_dir / f"{song['title_ko']}_{key_clean}.png", song["title_ko"])

    matched = match_setlist_sheets(
        plan,
        sheets_dirs=[sheets_dir],
        work_dir=tmp_path / "raster",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert all(s["match_status"] == "matched_exact_key" for s in matched["sheets"])
    assert matched["sheets"][0]["page_count"] == 2
    assert len(matched["sheets"][0]["rasterized_pages"]) == 2

    out_dir = tmp_path / "out_sunday_lent"
    pkg = generate_setlist_package(plan, matched, out_dir, candidates_data=cands)

    pdf_file = Path(pkg["pdf_path"])
    assert pdf_file.exists()
    reader = PdfReader(str(pdf_file))
    # 1 cover + 2 pages (song 1) + 1 page (song 2) + 1 page (song 3) = 5 pages
    expected_pages = 1 + sum(s["page_count"] for s in matched["sheets"])
    assert len(reader.pages) == expected_pages
    assert len(reader.outline) >= 1 + len(plan["songs"])

    # Verify A4 MediaBox (~595 x 842 pt)
    mb = reader.pages[0].mediabox
    assert 594.0 <= float(mb.width) <= 597.0
    assert 840.0 <= float(mb.height) <= 844.0

    # Verify all 4 deliverables exist
    assert Path(pkg["kakao_notice_path"]).exists()
    assert Path(pkg["leader_cuesheet_path"]).exists()
    assert Path(pkg["broadcast_cue_path"]).exists()

    # Record history
    rec = record_setlist(plan, output_dir=str(out_dir), worship_db_path=worship_db, local_db_path=local_db)
    assert len(rec["counted_songs"]) == len(plan["songs"])


def test_scenario2_youth_key_mismatch_branches_and_partial_diff(e2e_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    worship_db, local_db, sheets_dir = e2e_env

    # Mark majority of songs familiar and 1 song 'learning' to exercise familiar_ratio ~75%:25%
    conn = connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)
    slugs = [r[0] for r in conn.execute("SELECT song_slug FROM songs").fetchall()]
    conn.close()
    for s in slugs:
        set_repertoire_entry(s, familiarity="familiar", worship_db_path=worship_db, local_db_path=local_db)
    set_repertoire_entry("naemam-gip-eun-gose-welove", familiarity="learning", worship_db_path=worship_db, local_db_path=local_db)

    cands = search_setlist_candidates(
        service_type="youth",
        theme="임재와 은혜의 예배",
        reference_date="2026-11-29",
        user_config={
            "leader_gender": "male",
            "vocal_floor": "A2",
            "vocal_ceiling": "F#4",
            "default_session": "full_band",
            "session_skill_tier": "beginner_friendly",
            "familiar_ratio": 0.75,
        },
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert len(cands["pre_sermon"]) == 4
    assert cands["post_sermon"]["reprise_candidate"] is not None

    plan = plan_setlist_transitions(cands, skill_tier="beginner_friendly")
    plan["service_date"] = "2026-11-29"

    # Give slot 1 a youtube timestamp & original_key mismatch to test kakao warning + ?t= deeplink
    plan["songs"][0]["youtube_url"] = "https://www.youtube.com/watch?v=youth_live"
    plan["songs"][0]["youtube_start_sec"] = 185
    plan["songs"][0]["original_key"] = "A" if plan["songs"][0]["target_key"] != "A" else "G"
    plan["songs"][0]["key_shift"] = -2

    # Put mismatched sheet for slot 2 (e.g. G sheet for non-G target, or A sheet for G target)
    s2 = plan["songs"][1]
    mismatch_key = "A" if s2["target_key"] != "A" else "G"
    _make_rgba_exif_png(sheets_dir / f"{s2['title_ko']}_{mismatch_key}.png", s2["title_ko"])
    # Leave slot 3 missing to verify missing_local + official links
    _make_rgba_exif_png(sheets_dir / f"{plan['songs'][0]['title_ko']}_{plan['songs'][0]['target_key']}.png", plan["songs"][0]["title_ko"])

    matched = match_setlist_sheets(
        plan,
        sheets_dirs=[sheets_dir],
        work_dir=tmp_path / "raster2",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert matched["sheets"][1]["match_status"] == "matched_fallback_key"
    assert matched["sheets"][1]["key_mismatch"] is True
    assert matched["sheets"][2]["match_status"] == "missing_local"
    assert len(matched["sheets"][2]["official_search_links"]) >= 1

    # Branch [B]: Vision verification approves fallback sheet with stamp_transpose_warning
    matched, _ = apply_vision_verification(
        matched,
        [
            {
                "slot_index": s2["slot_index"],
                "verified_key": mismatch_key,
                "verified_sections": ["I", "V1", "C", "O"],
                "key_mismatch_action": "stamp_transpose_warning",
                "lyrics_theology_ok": True,
            }
        ],
        setlist_plan=plan,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert matched["sheets"][1]["vision_verified"] is True

    out_dir = tmp_path / "out_youth"
    pkg = generate_setlist_package(plan, matched, out_dir, candidates_data=cands)

    kakao_text = Path(pkg["kakao_notice_path"]).read_text(encoding="utf-8")
    assert "[주의: 유튜브 원곡" in kakao_text
    assert "&t=185" in kakao_text

    broadcast_text = Path(pkg["broadcast_cue_path"]).read_text(encoding="utf-8")
    assert "진행 순서:" in broadcast_text
    assert "첫 줄 확인:" in broadcast_text

    # Rehearsal partial update (--update-slot 2): swap song form & key on slot 2
    slot1_png = [p for p in pkg["individual_pngs"] if "01_" in Path(p).name][0]
    slot3_png = [p for p in pkg["individual_pngs"] if "03_" in Path(p).name][0]
    before_slot1_bytes = Path(slot1_png).read_bytes()
    before_slot3_bytes = Path(slot3_png).read_bytes()

    plan_mod = json.loads(json.dumps(plan, ensure_ascii=False))
    plan_mod["songs"][1]["song_form"] = "I(4m) - V1 - [C only](x2) - Vamp -->(Medley)"
    # Test Branch [A] recalculate on slot 2
    matched, plan_recalc = apply_vision_verification(
        matched,
        [
            {
                "slot_index": s2["slot_index"],
                "verified_key": mismatch_key,
                "verified_sections": ["I", "V1", "C"],
                "key_mismatch_action": "recalculate",
                "confirmed_song_form": plan_mod["songs"][1]["song_form"],
                "lyrics_theology_ok": True,
            }
        ],
        setlist_plan=plan_mod,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert plan_recalc is not None
    assert plan_recalc["songs"][1]["target_key"] == mismatch_key

    pkg_diff = generate_setlist_package(plan_recalc, matched, out_dir, candidates_data=cands, update_slot=2)
    assert pkg_diff["kakao_diff_notice_path"] is not None
    diff_text = Path(pkg_diff["kakao_diff_notice_path"]).read_text(encoding="utf-8")
    assert "[리허설 변경 공지]" in diff_text
    assert "2번" in diff_text
    # Adjacent slot 1 PNG was refreshed with new 1->2 transition; unrelated slot 3 PNG was reused untouched
    assert Path(slot1_png).read_bytes() != before_slot1_bytes
    assert Path(slot3_png).read_bytes() == before_slot3_bytes
    # Medley Korean translation appears in updated broadcast cue
    bc_updated = Path(pkg_diff["broadcast_cue_path"]).read_text(encoding="utf-8")
    assert "무공백 바로 다음 곡 진입" in bc_updated


def test_scenario3_small_group_piano_only_e2e(e2e_env: tuple[Path, Path, Path], tmp_path: Path) -> None:
    worship_db, local_db, sheets_dir = e2e_env

    cands = search_setlist_candidates(
        service_type="small_group",
        session_mode="piano_only",
        theme="위로와 평안",
        reference_date="2026-11-25",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert 2 <= len(cands["pre_sermon"]) <= 3
    for sl in cands["pre_sermon"]:
        assert sl["primary"]["band_dependency"] != "high"
        assert "I(8m)" not in sl["primary"]["song_form"]

    plan = plan_setlist_transitions(cands, skill_tier="intermediate")
    plan["service_date"] = "2026-11-25"
    for song in plan["songs"]:
        _make_rgba_exif_png(sheets_dir / f"{song['title_ko']}_{song['target_key']}.png", song["title_ko"])

    matched = match_setlist_sheets(
        plan,
        sheets_dirs=[sheets_dir],
        work_dir=tmp_path / "raster3",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    out_dir = tmp_path / "out_small_group"
    pkg = generate_setlist_package(plan, matched, out_dir, candidates_data=cands)
    assert Path(pkg["pdf_path"]).exists()
    assert Path(pkg["kakao_notice_path"]).exists()
    assert Path(pkg["leader_cuesheet_path"]).exists()
    assert Path(pkg["broadcast_cue_path"]).exists()


def test_fetch_sheets_helpers_and_confirmed_form_without_key_recalc(
    e2e_env: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    worship_db, local_db, sheets_dir = e2e_env

    # 1. URL normalization for Korean blog image filenames
    encoded = _normalize_public_url("https://score.hwang-ee.com/wp-content/uploads/2024/08/주께-가까이G코드.png")
    assert "%EC%A3%BC%EA%BB%98" in encoded

    # 2. HTML media extraction filters UI assets and prefers article G-key images
    sample_html = """
    <html><head><title>새 힘 얻으리 악보</title>
    <meta property="og:image" content="https://blog.kakaocdn.net/dn/thumb/sample_thumb.png" />
    </head><body>
    <img src="/static/images/default/favicon.png" />
    <img data-original="https://blog.kakaocdn.net/dn/abc123/img.png" alt="새 힘 얻으리 G코드 악보" />
    </body></html>
    """
    page_title, cands = extract_media_candidates(sample_html, "https://example.tistory.com/1", target_key="G")
    assert page_title == "새 힘 얻으리 악보"
    assert len(cands) == 2
    assert cands[0]["url"] == "https://blog.kakaocdn.net/dn/abc123/img.png"

    # 3. Size gate rejects < 500x500 thumbnails and normalizes RGBA/EXIF sheets
    tiny_path = tmp_path / "tiny.png"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(tiny_path, format="PNG")
    assert _inspect_and_normalize_image(tiny_path.read_bytes()) is None

    valid_path = tmp_path / "valid.png"
    _make_rgba_exif_png(valid_path, "Valid Sheet")
    norm_res = _inspect_and_normalize_image(valid_path.read_bytes())
    assert norm_res is not None
    _, ext, w, h = norm_res
    assert ext == "png" and w == 900 and h == 1200

    # 4. apply_vision_verification updates confirmed_song_form even when keys already match
    plan = {
        "service_type": "youth",
        "session_skill_tier": "intermediate",
        "songs": [
            {
                "slot_index": 1,
                "song_slug": "juga-ilhasine-kim-brian",
                "title_ko": "주가 일하시네",
                "target_key": "G",
                "available_sections": ["I", "V1", "PC", "C", "B", "O"],
                "song_form": "I(4m) - V1 - PC - C(x2) - B - O",
            }
        ],
        "transitions": [],
    }
    _make_rgba_exif_png(sheets_dir / "주가 일하시네_G.png", "주가 일하시네 G")
    matched = match_setlist_sheets(
        plan,
        sheets_dirs=[sheets_dir],
        work_dir=tmp_path / "raster_form",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    matched, updated = apply_vision_verification(
        matched,
        [
            {
                "slot_index": 1,
                "verified_key": "G",
                "verified_sections": ["I", "V1", "C"],
                "confirmed_song_form": "I(4m) - V1 - C(x2)",
                "key_mismatch_action": "ok",
                "lyrics_theology_ok": True,
            }
        ],
        setlist_plan=plan,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert updated is not None
    assert updated["songs"][0]["song_form"] == "I(4m) - V1 - C(x2)"
    assert updated["songs"][0]["available_sections"] == ["I", "V1", "C"]


def test_swarm_layout_improvements_capo_off_and_roman_realization(
    e2e_env: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    worship_db, local_db, sheets_dir = e2e_env

    # 1. Realize Roman prayer loops in G Key
    realized = realize_roman_progression("IV - V - iii - vi", "G")
    assert realized == "C - D - Bm - Em (IV-V-iii-vi)"
    realized2 = realize_roman_progression("ii7 - V7sus4 - Imaj7", "G")
    assert realized2 == "Am7 - D7sus4 - Gmaj7 (ii7-V7sus4-Imaj7)"

    # 2. Hero song progression arrow formatter
    hero = format_song_form_visual("I(4m) - V1 - PC - C(x2) -->(Medley)", transition_mode="segue")
    assert hero == "I(4m)  →  V1  →  PC  →  C(x2)  ⇒  [바로 연결]"

    # 3. Capo suppressed by default, score_annotations/chorus_incipit/meter_note preserved, no clean_cut contradiction
    cands = search_setlist_candidates(
        era="2000s,2010s",
        custom_tempos="fast,fast,fast,ballad",
        intergenerational=True,
        duration_min=15,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    plan = plan_setlist_transitions(cands, skill_tier="intermediate")
    plan["service_date"] = "2026-11-23"
    plan["service_label"] = "세대통합 예배"

    for s in plan["songs"]:
        _make_rgba_exif_png(sheets_dir / f"{s['title_ko']}_{s['target_key']}.png", s["title_ko"])

    matched = match_setlist_sheets(
        plan,
        sheets_dirs=[sheets_dir],
        work_dir=tmp_path / "raster_swarm",
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    matched, plan = apply_vision_verification(
        matched,
        [
            {
                "slot_index": 2,
                "verified_key": plan["songs"][1]["target_key"],
                "meter_note": "19마디 2/4 변박 주의",
                "score_annotations": [
                    {"page": 1, "rx": 0.2, "ry": 0.2, "text": "[주의] 2/4 변박", "style": "caution"}
                ],
                "lyrics_theology_ok": True,
            },
            {
                "slot_index": 4,
                "verified_key": plan["songs"][3]["target_key"],
                "chorus_incipit": "후렴 첫줄 테스트",
                "score_annotations": [
                    {"page": 1, "rx": 0.1, "ry": 0.1, "text": "[Verse]", "style": "section"}
                ],
                "lyrics_theology_ok": True,
            },
        ],
        setlist_plan=plan,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )

    out_dir = tmp_path / "out_swarm_layout"
    pkg = generate_setlist_package(plan, matched, out_dir, candidates_data=cands)
    kakao = Path(pkg["kakao_notice_path"]).read_text(encoding="utf-8")
    leader_md = Path(pkg["leader_cuesheet_path"]).read_text(encoding="utf-8")
    bcast = Path(pkg["broadcast_cue_path"]).read_text(encoding="utf-8")

    expected_loop = realize_roman_progression("IV - V - iii - vi", plan["post_sermon"]["reprise_candidate"]["target_key"])
    assert "No Capo" not in kakao
    assert "Capo" not in kakao
    assert "세대통합 예배" in kakao
    assert expected_loop in kakao
    assert expected_loop in leader_md
    assert "19마디 2/4 변박 주의" in bcast
    assert "후렴 시작: \"후렴 첫줄 테스트\"" in bcast


def test_init_profile_age_group_unlocked_keys_and_instrument_cues(
    e2e_env: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    worship_db, local_db, sheets_dir = e2e_env
    cfg_path = tmp_path / "user_config.json"

    # 1. Inspect before init -> initialized is False
    pre = inspect_profile(
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
        sheet_dir=sheets_dir,
    )
    assert pre["initialized"] is False

    # 2. Create a local sheet file and apply small-church piano+acoustic profile
    _make_rgba_exif_png(sheets_dir / "감사함으로_G.png", "감사함으로 G")
    assert derive_default_session(["piano", "acoustic_guitar"]) == "acoustic"

    res = apply_profile(
        team_name="소형교회 통합찬양팀",
        church_scale="small",
        congregation_age_group="intergenerational",
        session_instruments=["piano", "acoustic_guitar"],
        session_skill_tier="beginner_friendly",
        leader_gender="male",
        vocal_ceiling="F#4",
        sheet_dirs=[str(sheets_dir)],
        output_dir=str(tmp_path / "out"),
        config_path=cfg_path,
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert res["status"] == "initialized"
    assert res["config"]["default_session"] == "acoustic"
    assert res["config"]["lock_past_keys"] is False

    # 3. Verify bootstrap did NOT lock preferred_key from filename
    conn = connect_dbs(worship_db, local_db, attach_local=True, ensure_schemas=False)
    rep = conn.execute(
        "SELECT familiarity, preferred_key FROM local_db.repertoire WHERE song_slug = ?",
        ("haengbok-han-hananim-markers",),
    ).fetchone()
    conn.close()
    assert rep is not None
    assert rep["familiarity"] == "familiar"
    assert rep["preferred_key"] is None

    # 4. Verify transition cues adapt when synth_pad / drums are absent
    tr_pad = evaluate_transition(
        {"title_ko": "A", "time_signature": "4/4", "bpm": 120, "outro_last_chord": "G"},
        {"title_ko": "B", "time_signature": "4/4", "bpm": 68, "intro_first_chord": "G", "tempo_tier": "ballad"},
        "G",
        "G",
        session_instruments=["piano", "acoustic_guitar"],
    )
    assert "피아노 잔잔한 코드 유지" in tr_pad["session_cue"]

    tr_segue = evaluate_transition(
        {"title_ko": "A", "time_signature": "4/4", "bpm": 110, "outro_last_chord": "G"},
        {"title_ko": "B", "time_signature": "4/4", "bpm": 120, "intro_first_chord": "G", "tempo_tier": "fast"},
        "G",
        "G",
        session_instruments=["piano", "acoustic_guitar"],
    )
    assert "건반/기타 리드" in tr_segue["session_cue"]

    # 5. Verify senior_adult age group works cleanly
    senior_cands = search_setlist_candidates(
        service_type="sunday_main",
        theme="은혜",
        user_config={**res["config"], "congregation_age_group": "senior_adult"},
        worship_db_path=worship_db,
        local_db_path=local_db,
    )
    assert len(senior_cands["pre_sermon"]) == 3
