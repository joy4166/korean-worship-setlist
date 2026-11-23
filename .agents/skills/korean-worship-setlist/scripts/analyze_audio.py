"""Analyze audio tempo (with half/triple-time correction) and chroma key estimation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import MAJOR_NAMES, MINOR_NAMES, configure_utf8_stdio, normalize_groove_style, parse_key_signature

# Krumhansl-Schmuckler key profiles
_KS_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KS_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

_UPBEAT_BAND_GROOVES = {"16th_upbeat", "shuffle_bounce", "country_gogo", "rock_upbeat"}


def classify_tempo_tier(
    bpm: float,
    time_signature: str = "4/4",
    groove_hint: str | None = None,
) -> str:
    """Classify BPM into ballad/medium/fast using compound (6/8,9/8,12/8), triple (3/4), or 4/4 thresholds."""
    meter = (time_signature or "4/4").strip()
    groove = normalize_groove_style(groove_hint)

    if meter in {"6/8", "9/8", "12/8"}:
        if bpm <= 62:
            return "ballad"
        if bpm <= 84:
            return "medium"
        return "fast"

    if meter in {"3/4", "3/8", "3/2"}:
        if bpm <= 78:
            return "ballad"
        if bpm <= 112:
            return "medium"
        return "fast"

    if bpm <= 76:
        return "ballad"
    if groove in _UPBEAT_BAND_GROOVES and bpm >= 98:
        return "fast"
    if bpm <= 108:
        return "medium"
    return "fast"


def correct_bpm_and_classify(
    raw_bpm: float,
    time_signature: str = "4/4",
    slot_hint: str | None = None,
    groove_hint: str | None = None,
    onset_density: float | None = None,
    trusted_bpm: bool = False,
) -> dict[str, Any]:
    """
    Correct librosa half/double/triple-time tracking artifacts and classify tempo_tier.
    Handles compound eighth-note x3 inflation, triple-meter limits, and half-time recovery.
    """
    if raw_bpm <= 0:
        raise ValueError(f"raw_bpm must be positive, got {raw_bpm}")

    meter = (time_signature or "4/4").strip()
    norm_groove = normalize_groove_style(groove_hint) or groove_hint

    ballad_slots = {"meditation", "confession", "dedication", "ballad"}
    fast_slots = {"opener", "fast"}
    ballad_grooves = {"16th_ballad", "6_8_worship", "ballad", "acoustic"}
    fast_grooves = _UPBEAT_BAND_GROOVES

    has_ballad_context = (
        (
            (slot_hint in ballad_slots)
            or (norm_groove in ballad_grooves)
            or (onset_density is not None and onset_density < 0.5)
        )
        and (slot_hint not in fast_slots)
        and (norm_groove not in fast_grooves)
    )
    has_fast_context = (
        (slot_hint in fast_slots)
        or (norm_groove in fast_grooves)
        or (onset_density is not None and onset_density >= 0.65)
    )

    corrected = float(raw_bpm)
    reason: str | None = None

    if meter in {"6/8", "9/8", "12/8"}:
        if corrected >= 135:
            corrected = corrected / 3.0
            reason = "triple_time_6_8_div_3"
        elif not trusted_bpm and corrected >= 110 and has_ballad_context:
            corrected = corrected / 2.0
            reason = "double_time_6_8_div_2"
        elif corrected < 36:
            corrected = corrected * 2.0
            reason = "under_detected_6_8_mul_2"
    elif meter in {"3/4", "3/8", "3/2"}:
        if corrected >= 168:
            corrected = corrected / 3.0
            reason = "triple_time_3_4_div_3"
        elif not trusted_bpm and corrected >= 132 and has_ballad_context:
            corrected = corrected / 2.0
            reason = "double_time_3_4_div_2"
        elif corrected < 45:
            corrected = corrected * 2.0
            reason = "half_time_3_4_mul_2"
    else:
        if corrected >= 160 and not (trusted_bpm and has_fast_context):
            corrected = corrected / 2.0
            reason = "extreme_double_time_div_2"
        elif not trusted_bpm and corrected >= 150 and slot_hint != "fast" and norm_groove not in fast_grooves:
            corrected = corrected / 2.0
            reason = "extreme_double_time_div_2"
        elif not trusted_bpm and corrected >= 110 and has_ballad_context:
            corrected = corrected / 2.0
            reason = "ballad_context_half_time_div_2"
        elif not trusted_bpm and 48 <= corrected <= 64 and has_fast_context:
            corrected = corrected * 2.0
            reason = "fast_context_double_time_mul_2"
        elif corrected < 48:
            corrected = corrected * 2.0
            reason = "sub_48_double_time_mul_2"

    final_bpm = int(round(corrected))
    tier = classify_tempo_tier(final_bpm, time_signature=meter, groove_hint=norm_groove)
    return {
        "raw_bpm": round(float(raw_bpm), 2),
        "bpm": final_bpm,
        "tempo_tier": tier,
        "was_corrected": reason is not None,
        "correction_reason": reason,
    }


def _estimate_key_from_chroma(chroma_mean: Any) -> tuple[str, float]:
    import numpy as np

    vec = np.asarray(chroma_mean, dtype=float)
    if vec.sum() == 0:
        return "C", 0.0
    vec = (vec - vec.mean()) / (vec.std() + 1e-8)

    best_score = -999.0
    second_score = -999.0
    best_label = "C"

    for idx in range(12):
        maj_prof = np.roll(_KS_MAJOR, idx)
        maj_prof = (maj_prof - maj_prof.mean()) / (maj_prof.std() + 1e-8)
        score_maj = float(np.dot(vec, maj_prof) / 12.0)

        min_prof = np.roll(_KS_MINOR, idx)
        min_prof = (min_prof - min_prof.mean()) / (min_prof.std() + 1e-8)
        score_min = float(np.dot(vec, min_prof) / 12.0)

        for label, sc in ((MAJOR_NAMES[idx], score_maj), (MINOR_NAMES[idx], score_min)):
            if sc > best_score:
                second_score = best_score
                best_score = sc
                best_label = label
            elif sc > second_score:
                second_score = sc

    normalized_key = parse_key_signature(best_label)[0]
    confidence = max(0.2, min(0.95, 0.5 + (best_score - second_score)))
    return normalized_key, round(confidence, 3)


def analyze_audio_file(
    audio_path: Path,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    time_signature: str = "4/4",
    slot_hint: str | None = None,
    groove_hint: str | None = None,
) -> dict[str, Any]:
    """
    Sample front(20%), middle(50%), and back(80%) windows inside [start_sec, end_sec]
    to compute median BPM and chroma-based Key estimate. Never extracts vocal range.
    """
    try:
        import librosa  # type: ignore
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "librosa is part of the optional 'builder' extra. Install with: uv sync --extra builder"
        ) from exc

    total_dur = float(librosa.get_duration(path=str(audio_path)))
    win_start = max(0.0, float(start_sec))
    win_end = min(total_dur, float(end_sec)) if end_sec else total_dur
    span = max(15.0, win_end - win_start)
    sample_dur = min(20.0, span / 3.0) if span > 30.0 else min(20.0, span)

    ratios = (0.2, 0.5, 0.8) if span >= 45.0 else (0.5,)
    bpms: list[float] = []
    densities: list[float] = []
    chroma_acc = None

    for r in ratios:
        offset = max(win_start, min(win_end - sample_dur, win_start + span * r - sample_dur / 2.0))
        y, sr = librosa.load(str(audio_path), offset=offset, duration=sample_dur, mono=True)
        if len(y) == 0:
            continue
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        bpms.append(float(np.atleast_1d(tempo)[0]))
        densities.append(float(np.mean(onset_env) / (np.max(onset_env) + 1e-8)))
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        chroma_acc = chroma if chroma_acc is None else chroma_acc + chroma

    if not bpms:
        raise ValueError(f"Could not extract audio samples from {audio_path}")

    median_raw_bpm = float(np.median(bpms))
    median_density = float(np.median(densities))
    bpm_info = correct_bpm_and_classify(
        raw_bpm=median_raw_bpm,
        time_signature=time_signature,
        slot_hint=slot_hint,
        groove_hint=groove_hint,
        onset_density=median_density,
    )
    est_key, conf = _estimate_key_from_chroma(chroma_acc)
    return {
        **bpm_info,
        "estimated_key": est_key,
        "key_source": "audio_estimated",
        "bpm_source": "librosa_verified",
        "confidence": conf,
        "sampled_windows": len(bpms),
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Analyze audio BPM (with correction) and Key")
    parser.add_argument("--audio", type=Path, help="Path to local audio file")
    parser.add_argument("--raw-bpm", type=float, help="Test pure BPM correction without audio file")
    parser.add_argument("--meter", default="4/4", choices=["4/4", "3/4", "2/4", "2/2", "6/8", "9/8", "12/8"])
    parser.add_argument("--slot-hint", default=None)
    parser.add_argument("--groove-hint", default=None)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--end-sec", type=float, default=None)
    args = parser.parse_args()

    if args.raw_bpm is not None:
        result = correct_bpm_and_classify(
            raw_bpm=args.raw_bpm,
            time_signature=args.meter,
            slot_hint=args.slot_hint,
            groove_hint=args.groove_hint,
        )
    elif args.audio:
        result = analyze_audio_file(
            audio_path=args.audio,
            start_sec=args.start_sec,
            end_sec=args.end_sec,
            time_signature=args.meter,
            slot_hint=args.slot_hint,
            groove_hint=args.groove_hint,
        )
    else:
        parser.error("Provide --audio or --raw-bpm")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
