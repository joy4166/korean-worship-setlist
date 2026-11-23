"""Match setlist songs to local sheet music (with fallback & PDF rasterization) and guide official web lookup."""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pypdfium2 as pdfium

from common import (
    WORSHIP_DB_PATH,
    assert_no_raw_lyrics_text,
    configure_utf8_stdio,
    connect_dbs,
    get_local_db_path,
    get_local_state_dir,
    is_blocked_source,
    json_dumps_ko,
    load_user_config,
    normalize_groove_style,
    normalize_title,
    parse_key_signature,
    resolve_workspace_path,
    semitone_distance,
)
from manage_history import SHEET_EXTENSIONS, extract_title_and_key_from_label
from plan_transitions import evaluate_transition, get_capo_info


def _scan_local_sheets(sheets_dirs: list[Path]) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for base in sheets_dirs:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in SHEET_EXTENSIONS:
                continue
            resolved = str(p.resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            title_guess, key_guess = extract_title_and_key_from_label(p.stem)
            # Also detect hymn number if filename starts or includes e.g. '305장' or '찬송가 305'
            hymn_m = re.search(r"(?:찬송가\s*)?(\d{1,3})\s*장", p.stem)
            hymn_num = int(hymn_m.group(1)) if hymn_m else None
            discovered.append(
                {
                    "path": p,
                    "filename": p.name,
                    "stem": p.stem,
                    "title_guess": title_guess,
                    "norm_guess": normalize_title(title_guess),
                    "key_guess": key_guess,
                    "hymn_number": hymn_num,
                }
            )
    return discovered


def _rasterize_sheet_pages(file_path: Path, work_dir: Path, slug: str) -> list[str]:
    """Render PDF pages to PNG via pypdfium2 so the agent can visually inspect every page."""
    if file_path.suffix.lower() != ".pdf":
        return [str(file_path)]

    work_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(file_path))
    pages: list[str] = []
    try:
        for idx in range(len(doc)):
            page = doc[idx]
            pil_img = page.render(scale=2.0).to_pil().convert("RGB")
            out_png = work_dir / f"{slug}_raw_p{idx + 1}.png"
            pil_img.save(out_png, format="PNG")
            pages.append(str(out_png))
    finally:
        doc.close()
    return pages


def _build_official_search_links(song: dict[str, Any]) -> list[dict[str, str]]:
    title = song.get("title_ko", "")
    team = song.get("worship_team", "")
    target_key = song.get("target_key", "")
    q_official = quote_plus(f"{title} {team} {target_key} 공식 악보")
    q_ccli = quote_plus(song.get("title_en") or title)
    return [
        {
            "label": "CCLI SongSelect (공식 라이선스 악보/조옮김)",
            "url": f"https://songselect.ccli.com/search/results?SearchText={q_ccli}",
        },
        {
            "label": "공식 워십팀·출판사 악보 검색 (비공식 블로그 이미지 자동 병합 금지)",
            "url": f"https://www.google.com/search?q={q_official}",
        },
    ]


def _score_title_match(song: dict[str, Any], candidate: dict[str, Any]) -> float:
    song_hymn = song.get("hymn_number")
    if song_hymn and candidate.get("hymn_number") == song_hymn:
        return 0.96

    targets = [normalize_title(song["title_ko"])]
    norm_guess = candidate["norm_guess"]
    if not norm_guess:
        return 0.0
    # Strip common team prefixes from filename
    cleaned_guess = re.sub(
        r"^(마커스워십|마커스|어노인팅|제이어스|위러브|아이자야씩스티원|예수전도단|히즈윌)[_\-\s]+",
        "",
        candidate["title_guess"],
    )
    norm_clean = normalize_title(cleaned_guess)

    best = 0.0
    for t in targets:
        if not t:
            continue
        if norm_guess == t or norm_clean == t:
            return 1.0
        if len(t) >= 3 and (t in norm_clean or norm_clean in t):
            best = max(best, 0.88)
        else:
            best = max(best, difflib.SequenceMatcher(None, norm_clean, t).ratio())
    return best


def match_setlist_sheets(
    setlist_plan: dict[str, Any],
    sheets_dirs: list[Path] | None = None,
    manual_sheets: dict[int, Path] | None = None,
    work_dir: Path | None = None,
    user_config: dict[str, Any] | None = None,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
) -> dict[str, Any]:
    """
    Match setlist songs against local_sheet_dirs (and sheet_cache) prioritizing exact Key,
    falling back to original Key local sheets when missing, and listing official links for missing songs.
    """
    cfg = user_config if user_config is not None else load_user_config()
    if sheets_dirs is None:
        dirs = [resolve_workspace_path(p) for p in cfg.get("local_sheet_dirs", [])]
    else:
        dirs = list(sheets_dirs)

    raster_dir = work_dir or (get_local_state_dir() / "_raster_cache")
    manual_map = manual_sheets or {}
    scanned = _scan_local_sheets(dirs)

    l_db = local_db_path if local_db_path is not None else get_local_db_path()
    conn = connect_dbs(worship_db_path, l_db, attach_local=True, ensure_schemas=True)
    cached_rows = conn.execute("SELECT * FROM local_db.sheet_cache ORDER BY verified_at DESC").fetchall()
    cache_by_slug: dict[str, list[dict[str, Any]]] = {}
    cache_by_path: dict[str, dict[str, Any]] = {}
    for cr in cached_rows:
        p = Path(cr["file_path"])
        if p.exists():
            row_dict = dict(cr)
            cache_by_slug.setdefault(cr["song_slug"], []).append(row_dict)
            cache_by_path.setdefault(str(p.resolve()), row_dict)
    conn.close()

    for cand in scanned:
        cached_entry = cache_by_path.get(str(cand["path"].resolve()))
        if cached_entry and cached_entry.get("detected_key"):
            cand["key_guess"] = cached_entry["detected_key"]

    matched_entries: list[dict[str, Any]] = []
    for song in setlist_plan.get("songs", []):
        slot_idx = int(song["slot_index"])
        slug = song["song_slug"]
        target_key = song["target_key"]
        _, target_maj, _, _ = parse_key_signature(target_key)

        chosen_path: Path | None = None
        detected_key: str | None = None
        match_status = "missing_local"

        if slot_idx in manual_map and manual_map[slot_idx].exists():
            chosen_path = manual_map[slot_idx]
            blocked_m, term_m = is_blocked_source(chosen_path.name)
            if blocked_m:
                raise ValueError(f"Blocked source keyword in manual sheet ({term_m}): {chosen_path.name}")
            cached_m = cache_by_path.get(str(chosen_path.resolve()))
            if cached_m and cached_m.get("detected_key"):
                detected_key = cached_m["detected_key"]
            else:
                _, detected_key = extract_title_and_key_from_label(chosen_path.stem)
            match_status = "manual_assigned"
        else:
            # Check sheet_cache first
            cached_list = cache_by_slug.get(slug, [])
            for ce in cached_list:
                if parse_key_signature(ce["detected_key"])[1] == target_maj:
                    chosen_path = Path(ce["file_path"])
                    detected_key = ce["detected_key"]
                    match_status = "matched_exact_key"
                    break

            # Search scanned directories
            if chosen_path is None:
                title_matches: list[tuple[float, bool, dict[str, Any]]] = []
                for cand in scanned:
                    t_score = _score_title_match(song, cand)
                    if t_score >= 0.72:
                        k_guess = cand["key_guess"]
                        k_exact = bool(k_guess and parse_key_signature(k_guess)[1] == target_maj)
                        title_matches.append((t_score, k_exact, cand))

                if title_matches:
                    # Prefer exact key match first, then highest title score
                    title_matches.sort(key=lambda x: (x[1], x[0]), reverse=True)
                    _, is_exact, best_cand = title_matches[0]
                    chosen_path = best_cand["path"]
                    detected_key = best_cand["key_guess"] or song.get("original_key")
                    if is_exact or best_cand["key_guess"] is None:
                        match_status = "matched_exact_key"
                    else:
                        match_status = "matched_fallback_key"

        if chosen_path is None:
            matched_entries.append(
                {
                    "slot_index": slot_idx,
                    "song_slug": slug,
                    "title_ko": song["title_ko"],
                    "target_key": target_key,
                    "sheet_key": None,
                    "match_status": "missing_local",
                    "key_mismatch": False,
                    "source_file": None,
                    "rasterized_pages": [],
                    "vision_verified": False,
                    "official_search_links": _build_official_search_links(song),
                    "note": "로컬 악보 없음: 공식 링크 확인 후 --manual-sheet 로 지정하세요.",
                }
            )
            continue

        pages = _rasterize_sheet_pages(chosen_path, raster_dir, f"{slot_idx:02d}_{slug}")
        sheet_key = detected_key or target_key
        _, sheet_maj, _, _ = parse_key_signature(sheet_key)
        mismatch = sheet_maj != target_maj

        entry: dict[str, Any] = {
            "slot_index": slot_idx,
            "song_slug": slug,
            "title_ko": song["title_ko"],
            "target_key": target_key,
            "sheet_key": sheet_key,
            "match_status": "matched_fallback_key" if (mismatch and match_status == "matched_exact_key") else match_status,
            "key_mismatch": mismatch,
            "source_file": str(chosen_path),
            "rasterized_pages": pages,
            "page_count": len(pages),
            "vision_verified": False,
            "verified_sections": song.get("available_sections", []),
            "key_mismatch_action": "stamp_transpose_warning" if mismatch else "ok",
        }
        if mismatch:
            entry["recommended_branches"] = ["recalculate", "stamp_transpose_warning"]
        matched_entries.append(entry)

    return {
        "service_type": setlist_plan.get("service_type"),
        "service_date": setlist_plan.get("service_date"),
        "sheets": matched_entries,
    }


def apply_vision_verification(
    matched_data: dict[str, Any],
    verifications: list[dict[str, Any]] | dict[str, Any],
    setlist_plan: dict[str, Any] | None = None,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """
    Record multimodal vision verification results (verified_key, verified_sections, key_mismatch_action,
    lyrics_theology_ok) into matched_data and local_db.sheet_cache (zero raw lyrics saved).
    If key_mismatch_action == 'recalculate', updates setlist_plan target_key and recomputes transitions.
    """
    ver_list = verifications.get("sheets", []) if isinstance(verifications, dict) else verifications
    if not isinstance(ver_list, list):
        raise ValueError("vision-file JSON must be a list of verification objects")
    ver_by_slot = {int(v["slot_index"]): v for v in ver_list}
    l_db = local_db_path if local_db_path is not None else get_local_db_path()
    conn = connect_dbs(worship_db_path, l_db, attach_local=True, ensure_schemas=True)

    recalc_slots: set[int] = set()
    rhythm_recalc_slots: set[int] = set()
    with conn:
        for sheet in matched_data.get("sheets", []):
            slot_idx = int(sheet["slot_index"])
            v = ver_by_slot.get(slot_idx)
            if not v:
                continue

            if v.get("chorus_incipit") is not None:
                c_inc = str(v["chorus_incipit"]).strip()
                assert_no_raw_lyrics_text(c_inc, field_name="chorus_incipit", max_len=25)
                if len(c_inc) > 15:
                    v = {**v, "chorus_incipit": c_inc[:15].rstrip()}

            v_key = parse_key_signature(v.get("verified_key") or sheet.get("sheet_key") or sheet["target_key"])[0]
            v_sections = v.get("verified_sections") or sheet.get("verified_sections", [])
            v_groove = normalize_groove_style(v.get("verified_groove")) if v.get("verified_groove") else None
            v_bpm = int(v["verified_bpm"]) if v.get("verified_bpm") is not None else None
            v_time_sig = str(v["verified_time_signature"]).strip() if v.get("verified_time_signature") else None

            action = v.get("key_mismatch_action")
            if action == "recalculate_transitions":
                action = "recalculate"
            lyrics_ok = bool(v.get("lyrics_theology_ok", True))

            _, target_maj, _, _ = parse_key_signature(sheet["target_key"])
            _, sheet_maj, _, _ = parse_key_signature(v_key)
            mismatch = sheet_maj != target_maj

            sheet["sheet_key"] = v_key
            sheet["verified_sections"] = v_sections
            sheet["lyrics_theology_ok"] = lyrics_ok
            sheet["vision_verified"] = lyrics_ok
            sheet["key_mismatch"] = mismatch
            if v_groove is not None:
                sheet["verified_groove"] = v_groove
            if v_bpm is not None:
                sheet["verified_bpm"] = v_bpm
            if v_time_sig is not None:
                sheet["verified_time_signature"] = v_time_sig
            if v.get("score_annotations") is not None:
                sheet["score_annotations"] = list(v["score_annotations"])
            if v.get("meter_note"):
                sheet["meter_note"] = v["meter_note"]
            if v.get("chorus_incipit"):
                sheet["chorus_incipit"] = v["chorus_incipit"]

            if not lyrics_ok:
                sheet["match_status"] = "rejected_theology"
                sheet["key_mismatch_action"] = "rejected"
                if sheet.get("source_file"):
                    conn.execute(
                        "DELETE FROM local_db.sheet_cache WHERE song_slug = ? AND file_path = ?",
                        (sheet["song_slug"], sheet["source_file"]),
                    )
                continue

            if action:
                sheet["key_mismatch_action"] = action
            elif mismatch:
                sheet["key_mismatch_action"] = "stamp_transpose_warning"
            else:
                sheet["key_mismatch_action"] = "ok"

            if sheet["key_mismatch_action"] == "recalculate":
                recalc_slots.add(slot_idx)
                sheet["target_key"] = v_key
                sheet["key_mismatch"] = False

            if v_groove is not None or v_bpm is not None or v_time_sig is not None:
                rhythm_recalc_slots.add(slot_idx)

            if sheet.get("source_file"):
                prev_cache = conn.execute(
                    """
                    SELECT detected_time_signature, detected_bpm, detected_groove
                    FROM local_db.sheet_cache
                    WHERE song_slug = ? AND file_path = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (sheet["song_slug"], sheet["source_file"]),
                ).fetchone()
                eff_time_sig = v_time_sig if v_time_sig is not None else (prev_cache["detected_time_signature"] if prev_cache else None)
                eff_bpm = v_bpm if v_bpm is not None else (prev_cache["detected_bpm"] if prev_cache else None)
                eff_groove = v_groove if v_groove is not None else (prev_cache["detected_groove"] if prev_cache else None)

                conn.execute(
                    "DELETE FROM local_db.sheet_cache WHERE song_slug = ? AND file_path = ?",
                    (sheet["song_slug"], sheet["source_file"]),
                )
                conn.execute(
                    """
                    INSERT INTO local_db.sheet_cache (
                        song_slug, file_path, detected_key, page_count, verified_sections_json,
                        detected_time_signature, detected_bpm, detected_groove
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sheet["song_slug"],
                        sheet["source_file"],
                        v_key,
                        int(sheet.get("page_count", 1)),
                        json_dumps_ko(v_sections),
                        eff_time_sig,
                        eff_bpm,
                        eff_groove,
                    ),
                )
    conn.close()

    has_form_updates = any(
        v.get("confirmed_song_form")
        or v.get("verified_sections")
        or v.get("meter_note")
        or v.get("chorus_incipit")
        or v.get("ensemble_note")
        or v.get("caution_nuance")
        for v in ver_by_slot.values()
    )
    updated_plan = setlist_plan
    if setlist_plan is not None and (recalc_slots or rhythm_recalc_slots or has_form_updates):
        updated_plan = json.loads(json.dumps(setlist_plan, ensure_ascii=False))
        songs = updated_plan.get("songs", [])
        for song in songs:
            s_idx = int(song["slot_index"])
            if s_idx in recalc_slots:
                v_key = ver_by_slot[s_idx]["verified_key"]
                song["target_key"] = parse_key_signature(v_key)[0]
                song["key_shift"] = semitone_distance(song.get("original_key") or song["target_key"], song["target_key"])
                song["capo_info"] = get_capo_info(song["target_key"])
                song["transposition_reason"] = "sheet_key_recalculate"
            v = ver_by_slot.get(s_idx)
            if v:
                if v.get("verified_sections"):
                    song["available_sections"] = list(v["verified_sections"])
                if v.get("confirmed_song_form"):
                    song["song_form"] = v["confirmed_song_form"]
                v_gr = normalize_groove_style(v.get("verified_groove")) if v.get("verified_groove") else None
                if v_gr is not None:
                    song["groove_style"] = v_gr
                    song["bpm_source"] = "sheet_verified"
                if v.get("verified_bpm") is not None:
                    v_bpm_val = int(v["verified_bpm"])
                    song["bpm"] = v_bpm_val
                    song["tempo_tier"] = "fast" if v_bpm_val >= 100 else ("medium" if v_bpm_val >= 80 else "ballad")
                    song["bpm_source"] = "sheet_verified"
                if v.get("verified_time_signature"):
                    song["time_signature"] = str(v["verified_time_signature"]).strip()
                    song["bpm_source"] = "sheet_verified"
                for field in ("meter_note", "chorus_incipit", "ensemble_note", "caution_nuance"):
                    if v.get(field) is not None:
                        song[field] = v[field]

        rep = (updated_plan.get("post_sermon") or {}).get("reprise_candidate")
        if rep:
            rep_match = next((s for s in songs if s.get("song_slug") == rep.get("song_slug")), None)
            if rep_match:
                if rep_match.get("chorus_incipit"):
                    rep["chorus_incipit"] = rep_match["chorus_incipit"]
                if recalc_slots:
                    rep["target_key"] = rep_match["target_key"]
                    rep["key_shift"] = rep_match.get("key_shift", 0)
                    rep["capo_info"] = rep_match.get("capo_info")

        if recalc_slots or rhythm_recalc_slots:
            skill_tier = updated_plan.get("session_skill_tier", "intermediate")
            inst_list = updated_plan.get("session_instruments")
            if inst_list is None:
                inst_list = load_user_config().get("session_instruments")
            new_transitions = [
                evaluate_transition(
                    songs[i],
                    songs[i + 1],
                    songs[i]["target_key"],
                    songs[i + 1]["target_key"],
                    skill_tier=skill_tier,
                    session_instruments=inst_list,
                )
                for i in range(len(songs) - 1)
            ]
            for i, tr in enumerate(new_transitions):
                s_v = ver_by_slot.get(int(songs[i]["slot_index"]), {})
                if (
                    tr["transition_mode"] == "rhythm_reset"
                    and not s_v.get("confirmed_song_form")
                    and re.search(r"-->\s*\(Medley", str(songs[i].get("song_form") or ""), re.IGNORECASE)
                ):
                    songs[i]["song_form"] = re.sub(
                        r"\s*-->\s*\(Medley[^)]*\)",
                        " - O",
                        str(songs[i]["song_form"]),
                        flags=re.IGNORECASE,
                    ).strip()
            updated_plan["transitions"] = new_transitions

    return matched_data, updated_plan


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Match local sheet music and rasterize PDF pages")
    parser.add_argument("--setlist", type=Path, required=True)
    parser.add_argument("--sheets-dir", type=Path, action="append", default=None)
    parser.add_argument("--manual-sheet", action="append", default=[], help="Format: slot_index=path")
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--vision-file", type=Path, default=None, help="JSON list of vision verification objects")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    plan = json.loads(args.setlist.read_text(encoding="utf-8"))
    manual: dict[int, Path] = {}
    for item in args.manual_sheet:
        if "=" in item:
            k, p = item.split("=", 1)
            manual[int(k)] = Path(p)

    matched = match_setlist_sheets(
        plan,
        sheets_dirs=args.sheets_dir,
        manual_sheets=manual,
        work_dir=args.work_dir,
    )
    if args.vision_file and args.vision_file.exists():
        vers = json.loads(args.vision_file.read_text(encoding="utf-8"))
        matched, updated_plan = apply_vision_verification(matched, vers, setlist_plan=plan)
        if updated_plan is not None:
            args.setlist.write_text(json.dumps(updated_plan, ensure_ascii=False, indent=2), encoding="utf-8")

    out_text = json.dumps(matched, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out_text, encoding="utf-8")
    else:
        print(out_text)


if __name__ == "__main__":
    main()
