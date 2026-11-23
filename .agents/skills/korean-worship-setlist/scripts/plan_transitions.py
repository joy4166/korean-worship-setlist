"""Optimize key transitions, bridge chords, meter/BPM gaps, and acoustic capo plans under fixed worship flow."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from common import (
    MAJOR_NAMES,
    configure_utf8_stdio,
    is_within_vocal_range,
    load_user_config,
    parse_key_signature,
    semitone_distance,
    transpose_key,
)

CAPO_GUIDE: dict[str, dict[str, Any]] = {
    "Ab": {"capo": 1, "shape": "G 폼", "note": "Capo 1 (G 폼)"},
    "Bb": {"capo": 3, "shape": "G 폼", "note": "Capo 3 (G 폼) 또는 Capo 1 (A 폼)"},
    "Db": {"capo": 1, "shape": "C 폼", "note": "Capo 1 (C 폼)"},
    "Eb": {"capo": 1, "shape": "D 폼", "note": "Capo 1 (D 폼) 또는 Capo 3 (C 폼)"},
    "F": {"capo": 3, "shape": "D 폼", "note": "Capo 3 (D 폼, 오픈코드 권장 시)"},
    "F#": {"capo": 2, "shape": "E 폼", "note": "Capo 2 (E 폼)"},
    "Gb": {"capo": 2, "shape": "E 폼", "note": "Capo 2 (E 폼)"},
    "B": {"capo": 2, "shape": "A 폼", "note": "Capo 2 (A 폼)"},
}

DIFFICULT_FLAT_KEYS = {"Ab", "Db", "Gb", "F#", "B"}


def _chord_in_major(major_key: str, degree: str) -> str:
    """Return chord symbol in the given major key for common functional degrees."""
    _, _, pc, _ = parse_key_signature(major_key)
    root_table = {
        "I": MAJOR_NAMES[pc],
        "Imaj7": MAJOR_NAMES[pc] + "maj7",
        "ii": MAJOR_NAMES[(pc + 2) % 12] + "m",
        "ii7": MAJOR_NAMES[(pc + 2) % 12] + "m7",
        "iii": MAJOR_NAMES[(pc + 4) % 12] + "m",
        "iii7": MAJOR_NAMES[(pc + 4) % 12] + "m7",
        "IV": MAJOR_NAMES[(pc + 5) % 12],
        "IVmaj7": MAJOR_NAMES[(pc + 5) % 12] + "maj7",
        "V": MAJOR_NAMES[(pc + 7) % 12],
        "V7": MAJOR_NAMES[(pc + 7) % 12] + "7",
        "V7sus4": MAJOR_NAMES[(pc + 7) % 12] + "7sus4",
        "vi": MAJOR_NAMES[(pc + 9) % 12] + "m",
        "vi7": MAJOR_NAMES[(pc + 9) % 12] + "m7",
        "V/IV": MAJOR_NAMES[pc] + "7",
        "V/vi": MAJOR_NAMES[(pc + 4) % 12] + "7",
        "IVm6": MAJOR_NAMES[(pc + 5) % 12] + "m6",
    }
    return root_table.get(degree, MAJOR_NAMES[pc])


def realize_roman_progression(progression: str, key_sig: str) -> str:
    """Realize a Roman-numeral chord loop into actual chords in key_sig alongside the Roman label."""
    if not progression or not progression.strip():
        return ""
    _, major_key, _, _ = parse_key_signature(key_sig)
    tokens = [t.strip() for t in progression.split("-")]
    known = {
        "I", "Imaj7", "ii", "ii7", "iii", "iii7",
        "IV", "IVmaj7", "IVm6", "V", "V7", "V7sus4", "vi", "vi7",
    }
    realized = [_chord_in_major(major_key, tok) if tok in known else tok for tok in tokens]
    compact_roman = "-".join(tokens)
    return f"{' - '.join(realized)} ({compact_roman})"


def _parse_chord_root_quality(chord_str: str | None) -> tuple[str, bool, str] | None:
    """Parse normalized root, is_minor, and quality suffix (preventing 'F' prefix matching 'Fm' or 'F#dim')."""
    raw = (chord_str or "").strip()
    m = re.match(r"^([A-G](?:[#b♯♭])?)(m(?!aj)|dim|°)?([^\s/]*)", raw, re.IGNORECASE)
    if not m:
        return None
    root = m.group(1)[0].upper() + m.group(1)[1:].replace("♯", "#").replace("♭", "b").lower()
    mod = (m.group(2) or "").lower()
    rest = (m.group(3) or "").lower()
    is_minor = mod == "m"
    qual = mod + rest
    return root, is_minor, qual


def compute_bridge_chords(
    prev_outro_chord: str | None,
    prev_key: str,
    next_key: str,
    next_intro_chord: str | None,
    next_tempo_tier: str = "medium",
) -> dict[str, Any]:
    """
    Generate harmonically accurate transition bridge chords (V7sus4=IV/V, V7, IVm6, V/IV, V/vi)
    considering the next song's opening chord.
    """
    _, prev_maj, pc_prev, _ = parse_key_signature(prev_key)
    _, next_maj, pc_next, next_is_minor = parse_key_signature(next_key)
    diff = (pc_next - pc_prev) % 12

    if diff == 0:
        anchor_chord = _chord_in_major(prev_maj, "I")
        outro_parsed = _parse_chord_root_quality(prev_outro_chord)
        prev_vi_root = _chord_in_major(prev_maj, "vi")[:-1]
        if prev_outro_chord and outro_parsed and (
            outro_parsed[0] == prev_maj or (outro_parsed[0] == prev_vi_root and outro_parsed[1])
        ):
            anchor_chord = prev_outro_chord
        return {
            "relation": "same_key",
            "bridge_chords": f"{anchor_chord} 유지",
            "harmony_tip": "동일 키 유지: 패드/코드 유지 상태에서 자연스럽게 진입",
        }

    iv_chord = _chord_in_major(next_maj, "IV")
    v_chord = _chord_in_major(next_maj, "V")
    i_chord = _chord_in_major(next_maj, "I")
    vi_chord = _chord_in_major(next_maj, "vi")
    vi_root = vi_chord[:-1]
    iv_m6 = _chord_in_major(next_maj, "IVm6")
    v7sus4_slash = f"{iv_chord}/{v_chord}"
    v7_chord = f"{v_chord}7"

    intro_clean = (next_intro_chord or "").strip()
    intro_parsed = _parse_chord_root_quality(intro_clean)
    # Inspect whether target song starts on major IV / IVmaj7 or VIm7 instead of I
    starts_on_iv = bool(
        intro_parsed
        and intro_parsed[0] == iv_chord
        and not intro_parsed[1]
        and "dim" not in intro_parsed[2]
        and "°" not in intro_parsed[2]
    )
    starts_on_vi = bool(
        next_is_minor
        or (intro_parsed and intro_parsed[0] == vi_root and intro_parsed[1])
    )

    if starts_on_iv:
        sec_dom = _chord_in_major(next_maj, "V/IV")
        i_over_3 = f"{i_chord}/{MAJOR_NAMES[(pc_next + 4) % 12]}"
        chords = f"{i_over_3} -> {sec_dom} -> {intro_clean}"
        tip = f"뒷곡이 IV({intro_clean})로 시작하므로 V7({v7_chord}) 대신 V/IV({sec_dom})로 착지"
    elif starts_on_vi:
        v_of_vi = _chord_in_major(next_maj, "V/vi")
        chords = f"{v_of_vi} -> {intro_clean or vi_chord}"
        tip = f"뒷곡이 VIm({intro_clean or vi_chord}) 시작이므로 V/vi({v_of_vi}) 경유"
    elif next_tempo_tier == "ballad" and diff in (1, 2, 5, 7, 10, 11):
        chords = f"{v7sus4_slash} (V7sus4) -> {v7_chord} (또는 {iv_m6} [IVm6]) -> {i_chord}"
        tip = f"발라드 전조: {v7sus4_slash}(IV/V 보이싱) -> {v7_chord} 또는 서브도미넌트 마이너 {iv_m6} 활용"
    else:
        chords = f"{v7sus4_slash} (V7sus4) -> {v7_chord} -> {i_chord}"
        tip = f"{v7sus4_slash}(IV/V 분수코드) -> {v7_chord} 해결로 {next_maj} 키 진입"

    relation_map = {
        2: "whole_step_up",
        1: "half_step_up",
        5: "fourth_up",
        7: "fifth_up",
        3: "minor_third_up",
        9: "relative_shift",
        6: "tritone_avoid",
    }
    return {
        "relation": relation_map.get(diff, "modulating"),
        "bridge_chords": chords,
        "harmony_tip": tip,
    }


def get_capo_info(key_sig: str) -> dict[str, Any]:
    _, major_key, _, _ = parse_key_signature(key_sig)
    info = CAPO_GUIDE.get(major_key)
    if info:
        return {"capo": info["capo"], "guitar_shape": info["shape"], "label": info["note"]}
    return {"capo": 0, "guitar_shape": f"{major_key} 폼", "label": "No Capo"}


def evaluate_transition(
    song_a: dict[str, Any],
    song_b: dict[str, Any],
    key_a: str,
    key_b: str,
    skill_tier: str = "intermediate",
    session_instruments: list[str] | None = None,
) -> dict[str, Any]:
    """Compute harmonic + meter + BPM + capo cost and transition mode ('segue' vs 'pad_break')."""
    _, maj_a, pc_a, _ = parse_key_signature(key_a)
    _, maj_b, pc_b, _ = parse_key_signature(key_b)
    diff = (pc_b - pc_a) % 12

    if diff == 0:
        key_cost = 0.0
    elif diff == 2:
        key_cost = 0.5
    elif diff in (5, 7):
        key_cost = 1.0
    elif diff in (3, 9):
        key_cost = 1.2
    elif diff == 1:
        key_cost = 1.5
    elif diff == 6:
        key_cost = 6.0
    else:
        key_cost = 2.8

    meter_a = song_a.get("time_signature", "4/4")
    meter_b = song_b.get("time_signature", "4/4")
    meter_changed = meter_a != meter_b
    meter_cost = 2.5 if meter_changed else 0.0

    bpm_a = int(song_a.get("bpm", 80))
    bpm_b = int(song_b.get("bpm", 80))
    bpm_gap = abs(bpm_a - bpm_b)
    bpm_cost = 2.0 if bpm_gap >= 25 else (0.6 if bpm_gap >= 15 else 0.0)

    has_inst_cfg = isinstance(session_instruments, list) and len(session_instruments) > 0
    has_guitar = (not has_inst_cfg) or ("acoustic_guitar" in session_instruments) or ("electric_guitar" in session_instruments)

    capo_a = get_capo_info(key_a)
    capo_b = get_capo_info(key_b)
    capo_changed = has_guitar and (capo_a["capo"] != capo_b["capo"])

    difficulty_cost = 0.0
    capo_warning = ""
    if skill_tier == "beginner_friendly":
        if maj_b in DIFFICULT_FLAT_KEYS:
            difficulty_cost += 1.2
        if capo_changed:
            capo_warning = f"카포 이동 필요 ({capo_a['label']} -> {capo_b['label']})"

    # Determine segue vs pad_break
    force_pad = meter_changed or (bpm_gap >= 25) or (diff == 6) or (capo_changed and skill_tier == "beginner_friendly")
    mode = "pad_break" if force_pad else "segue"

    bridge = compute_bridge_chords(
        prev_outro_chord=song_a.get("outro_last_chord"),
        prev_key=key_a,
        next_key=key_b,
        next_intro_chord=song_b.get("intro_first_chord"),
        next_tempo_tier=song_b.get("tempo_tier", "medium"),
    )

    has_pad = not has_inst_cfg or "synth_pad" in session_instruments
    pad_hold_label = "건반 패드 유지" if has_pad else "피아노 잔잔한 코드 유지"
    pad_prefix = "[패드 홀드]" if has_pad else "[잔잔한 코드 유지]"
    tempo_lead_label = (
        "드럼 카운트/필인"
        if (not has_inst_cfg or "drums" in session_instruments)
        else ("건반/기타 리드" if has_guitar else "피아노 리드")
    )

    cues: list[str] = []
    if mode == "pad_break":
        cues.append(f"{pad_prefix} {pad_hold_label} + 인도자 멘트/기도")
        if meter_changed:
            cues.append(f"박자 리셋({meter_a} -> {meter_b} 카운트)")
        if bpm_gap >= 25:
            if bpm_a >= 110 and bpm_b <= 75 and abs(bpm_a / 2.0 - bpm_b) <= 5:
                cues.append(f"Half-time 전환({bpm_a} -> {bpm_b} BPM)")
            elif bpm_a >= 110 and bpm_b <= 75:
                cues.append(f"템포 전환({bpm_a} -> {bpm_b} BPM, 호흡 정리 후 진입)")
            else:
                cues.append(f"템포 정리({bpm_a} -> {bpm_b} BPM)")
        if capo_warning:
            cues.append(capo_warning)
        if diff == 6:
            cues.append("원격조(증4도) 전환: 전조 코드 대신 패드 호흡 후 진입")
    else:
        cues.append(f"[바로 연결 Segue] {bridge['bridge_chords']}")
        if abs(bpm_b - bpm_a) >= 5:
            cues.append(f"템포 전환({bpm_a} -> {bpm_b} BPM {tempo_lead_label})")
        if capo_changed and skill_tier == "beginner_friendly":
            cues.append(f"주의: 기타 카포 이동 시간 확인 ({capo_a['label']} -> {capo_b['label']})")

    total_cost = round(key_cost + meter_cost + bpm_cost + difficulty_cost, 3)
    return {
        "from_slot": int(song_a.get("slot_index", 0)),
        "to_slot": int(song_b.get("slot_index", 0)),
        "from_song": song_a["title_ko"],
        "to_song": song_b["title_ko"],
        "from_key": key_a,
        "to_key": key_b,
        "transition_mode": mode,
        "relation": bridge["relation"],
        "bridge_chords": bridge["bridge_chords"],
        "harmony_tip": bridge["harmony_tip"],
        "session_cue": " / ".join(cues),
        "capo_warning": capo_warning,
        "cost": total_cost,
    }


def plan_setlist_transitions(
    candidates_data: dict[str, Any],
    skill_tier: str | None = None,
    user_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Optimize keys (and optionally swap only identical stage/tempo slots) while keeping worship narrative
    and tempo direction strictly fixed. Generates transition cues and acoustic capo mappings.
    """
    cfg = user_config if user_config is not None else load_user_config()
    tier = skill_tier or cfg.get("session_skill_tier", "intermediate")
    inst_list = cfg.get("session_instruments")
    vocal_floor = cfg.get("vocal_floor")
    vocal_ceiling = cfg.get("vocal_ceiling")
    leader_gender = cfg.get("leader_gender", "male")

    raw_slots = candidates_data.get("pre_sermon", [])
    ordered = sorted(raw_slots, key=lambda x: x["slot_index"])

    chosen_songs: list[dict[str, Any]] = []
    for idx, sl in enumerate(ordered):
        song = dict(sl["primary"])
        song["slot_index"] = sl["slot_index"]
        song["target_stage"] = sl.get("target_stage", song.get("liturgical_stage"))

        # Check if shifting within +/- 2 semitones improves continuity without violating vocal_ceiling/vocal_floor
        if idx > 0 and song.get("transposition_reason") != "preferred_key":
            prev = chosen_songs[idx - 1]
            prev_key = prev["target_key"]
            base_key = song["target_key"]
            best_key = base_key
            best_shift_delta = 0
            best_score = evaluate_transition(
                prev, song, prev_key, base_key, skill_tier=tier, session_instruments=inst_list
            )["cost"]

            for delta in (-2, -1, 1, 2):
                total_shift = int(song.get("key_shift", 0)) + delta
                if total_shift < -3 or total_shift > 2:
                    continue
                cand_key = transpose_key(base_key, delta)
                if not is_within_vocal_range(
                    song.get("original_key") or base_key,
                    cand_key,
                    song.get("highest_note"),
                    song.get("lowest_note"),
                    vocal_floor,
                    vocal_ceiling,
                    leader_gender,
                ):
                    continue
                ev = evaluate_transition(
                    prev, song, prev_key, cand_key, skill_tier=tier, session_instruments=inst_list
                )
                # Slightly prefer keeping original auto-transposed key unless transition noticeably improves
                adj_cost = ev["cost"] + 0.15 * abs(delta)
                if adj_cost < best_score:
                    best_score = adj_cost
                    best_key = cand_key
                    best_shift_delta = delta

            if best_shift_delta != 0:
                song["target_key"] = best_key
                song["key_shift"] = int(song.get("key_shift", 0)) + best_shift_delta
                song["transposition_reason"] = f"{song.get('transposition_reason', 'auto')}+transition_opt"

        song["capo_info"] = get_capo_info(song["target_key"])
        chosen_songs.append(song)

    transitions: list[dict[str, Any]] = []
    for idx in range(len(chosen_songs) - 1):
        tr = evaluate_transition(
            chosen_songs[idx],
            chosen_songs[idx + 1],
            chosen_songs[idx]["target_key"],
            chosen_songs[idx + 1]["target_key"],
            skill_tier=tier,
            session_instruments=inst_list,
        )
        transitions.append(tr)

    post_sermon = json.loads(json.dumps(candidates_data.get("post_sermon", {}), ensure_ascii=False))
    rep = post_sermon.get("reprise_candidate")
    if rep:
        matched_song = next((s for s in chosen_songs if s["song_slug"] == rep.get("song_slug")), None)
        if matched_song:
            rep["target_key"] = matched_song["target_key"]
            rep["key_shift"] = matched_song.get("key_shift", 0)
            rep["capo_info"] = matched_song.get("capo_info")
            rep["transposition_reason"] = matched_song.get("transposition_reason")

    return {
        "service_type": candidates_data.get("service_type", "youth"),
        "church_season": candidates_data.get("church_season", "ordinary"),
        "cooldown_scope": candidates_data.get("cooldown_scope"),
        "service_date": candidates_data.get("reference_date"),
        "theme_input": candidates_data.get("theme_input", ""),
        "sermon_scripture": candidates_data.get("scripture_input", ""),
        "duration_min": candidates_data.get("duration_min"),
        "estimated_total_minutes": candidates_data.get("estimated_total_minutes"),
        "session_skill_tier": tier,
        "session_instruments": inst_list,
        "vocal_ensemble": cfg.get("vocal_ensemble", "small_team"),
        "songs": chosen_songs,
        "transitions": transitions,
        "post_sermon": post_sermon,
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Optimize setlist key transitions, capo forms, and meter cues")
    parser.add_argument("--input", type=Path, required=True, help="Input candidates JSON from search_candidates.py")
    parser.add_argument("--skill-tier", default=None, choices=["beginner_friendly", "intermediate", "advanced"])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    candidates = json.loads(args.input.read_text(encoding="utf-8"))
    plan = plan_setlist_transitions(candidates, skill_tier=args.skill_tier)
    out_text = json.dumps(plan, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(out_text, encoding="utf-8")
    else:
        print(out_text)


if __name__ == "__main__":
    main()
