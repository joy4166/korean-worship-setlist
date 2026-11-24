"""Inspect and initialize persistent church worship profile (data/user_config.json & church_local.db)."""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path
from typing import Any

from common import (
    DEFAULT_VOCAL_RANGES,
    USER_CONFIG_EXAMPLE_PATH,
    WORSHIP_DB_PATH,
    check_skill_update,
    configure_utf8_stdio,
    connect_dbs,
    get_installed_skill_version,
    get_local_db_path,
    get_user_config_path,
    load_json_file,
    normalize_nfc,
    resolve_workspace_path,
)
from manage_history import SHEET_EXTENSIONS, bootstrap_repertoire

CHURCH_SCALE_CHOICES = ("small", "medium", "large")
AGE_GROUP_CHOICES = ("youth_young_adult", "intergenerational", "senior_adult")
VOCAL_ENSEMBLE_CHOICES = ("solo_leader", "small_team", "full_choir")
LEADER_MENT_STYLE_CHOICES = ("short_script", "bullet_points", "none")
INSTRUMENT_CHOICES = (
    "piano",
    "synth_pad",
    "acoustic_guitar",
    "electric_guitar",
    "bass",
    "drums",
)

CHURCH_SCALE_DEFAULTS: dict[str, tuple[float, str]] = {
    "small": (0.85, "solo_leader"),
    "medium": (0.75, "small_team"),
    "large": (0.70, "full_choir"),
}


def derive_default_session(instruments: list[str] | None) -> str:
    """Infer default_session ('piano_only' | 'acoustic' | 'full_band') from selected instruments."""
    inst_set = {i.strip() for i in (instruments or []) if i.strip()}
    if not inst_set:
        return "full_band"
    if "drums" in inst_set or "bass" in inst_set or "electric_guitar" in inst_set:
        return "full_band"
    if "acoustic_guitar" in inst_set:
        return "acoustic"
    return "piano_only"


def inspect_profile(
    config_path: Path | None = None,
    example_path: Path = USER_CONFIG_EXAMPLE_PATH,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
    sheet_dir: Path | None = None,
) -> dict[str, Any]:
    """Return current initialization status without modifying any files."""
    cfg_path = config_path or get_user_config_path()
    l_db_path = local_db_path or get_local_db_path()
    cfg_exists = cfg_path.exists()
    active_cfg = load_json_file(cfg_path if cfg_exists else example_path, default={}) or {}

    if sheet_dir is not None:
        scan_dirs = [sheet_dir]
    else:
        raw_dirs = active_cfg.get("local_sheet_dirs") or ["sheet"]
        scan_dirs = [resolve_workspace_path(d) for d in raw_dirs]

    seen_files: set[Path] = set()
    for d_path in scan_dirs:
        if d_path.exists():
            for p in d_path.rglob("*"):
                if p.is_file() and p.suffix.lower() in SHEET_EXTENSIONS:
                    seen_files.add(p.resolve())
    sheet_count = len(seen_files)

    worship_songs = 0
    if worship_db_path.exists():
        with contextlib.closing(connect_dbs(worship_db_path, l_db_path, attach_local=False, ensure_schemas=False)) as conn:
            worship_songs = int(conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0])

    repertoire_count = 0
    history_count = 0
    if l_db_path.exists() and worship_db_path.exists():
        with contextlib.closing(connect_dbs(worship_db_path, l_db_path, attach_local=True, ensure_schemas=True)) as conn:
            repertoire_count = int(conn.execute("SELECT COUNT(*) FROM local_db.repertoire").fetchone()[0])
            history_count = int(conn.execute("SELECT COUNT(*) FROM local_db.setlist_history").fetchone()[0])

    result: dict[str, Any] = {
        "initialized": cfg_exists,
        "config_path": str(cfg_path),
        "config": active_cfg,
        "sheet_dir": str(scan_dirs[0]) if scan_dirs else str(resolve_workspace_path("sheet")),
        "sheet_dirs": [str(d) for d in scan_dirs],
        "local_sheet_count": sheet_count,
        "worship_db_exists": worship_db_path.exists(),
        "worship_song_count": worship_songs,
        "local_db_exists": l_db_path.exists(),
        "repertoire_count": repertoire_count,
        "history_count": history_count,
        "skill_version": get_installed_skill_version(),
    }
    upd = check_skill_update()
    if upd is not None:
        result["update_available"] = upd
    return result


def apply_profile(
    *,
    team_name: str | None = None,
    church_scale: str | None = None,
    congregation_age_group: str | None = None,
    session_instruments: list[str] | None = None,
    default_session: str | None = None,
    session_skill_tier: str | None = None,
    leader_gender: str | None = None,
    vocal_floor: str | None = None,
    vocal_ceiling: str | None = None,
    vocal_ensemble: str | None = None,
    seeker_emphasis: bool | None = None,
    cooldown_weeks: int | None = None,
    familiar_ratio: float | None = None,
    show_capo: bool | None = None,
    lock_past_keys: bool | None = None,
    leader_ment_style: str | None = None,
    sheet_dirs: list[str] | None = None,
    output_dir: str | None = None,
    config_path: Path | None = None,
    example_path: Path = USER_CONFIG_EXAMPLE_PATH,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
) -> dict[str, Any]:
    """Merge confirmed setup answers into user_config.json and ensure local DB + sheet bootstrap."""
    cfg_path = config_path or get_user_config_path()
    l_db_path = local_db_path or get_local_db_path()
    base = load_json_file(example_path, default={}) or {}
    if cfg_path.exists():
        existing = load_json_file(cfg_path, default={}) or {}
        base.update(existing)
    prev_gender = base.get("leader_gender")
    prev_scale = base.get("church_scale")

    base["config_schema"] = 1
    if team_name is not None:
        base["team_name"] = normalize_nfc(team_name).strip()
    if church_scale is not None:
        if church_scale not in CHURCH_SCALE_CHOICES:
            raise ValueError(f"Unsupported church_scale: {church_scale}")
        base["church_scale"] = church_scale
        default_fam, default_ens = CHURCH_SCALE_DEFAULTS[church_scale]
        if familiar_ratio is None and (not config_path.exists() or prev_scale != church_scale or base.get("familiar_ratio") in (None, 0.85, 0.75, 0.70)):
            base["familiar_ratio"] = default_fam
        if vocal_ensemble is None and (not config_path.exists() or prev_scale != church_scale or base.get("vocal_ensemble") in (None, "solo_leader", "small_team", "full_choir")):
            base["vocal_ensemble"] = default_ens
    if congregation_age_group is not None:
        if congregation_age_group not in AGE_GROUP_CHOICES:
            raise ValueError(f"Unsupported congregation_age_group: {congregation_age_group}")
        base["congregation_age_group"] = congregation_age_group

    if session_instruments is not None:
        clean_insts = [i.strip() for i in session_instruments if i.strip()]
        invalid = [i for i in clean_insts if i not in INSTRUMENT_CHOICES]
        if invalid:
            raise ValueError(f"Unsupported instruments: {invalid}")
        base["session_instruments"] = clean_insts
        if default_session is None:
            base["default_session"] = derive_default_session(clean_insts)

    if default_session is not None:
        base["default_session"] = default_session
    if session_skill_tier is not None:
        base["session_skill_tier"] = session_skill_tier
    if vocal_ensemble is not None:
        if vocal_ensemble not in VOCAL_ENSEMBLE_CHOICES:
            raise ValueError(f"Unsupported vocal_ensemble: {vocal_ensemble}")
        base["vocal_ensemble"] = vocal_ensemble
    if seeker_emphasis is not None:
        base["seeker_emphasis"] = bool(seeker_emphasis)

    if leader_gender is not None:
        base["leader_gender"] = leader_gender
        if leader_gender in DEFAULT_VOCAL_RANGES:
            def_floor, def_ceil = DEFAULT_VOCAL_RANGES[leader_gender]
            gender_changed = prev_gender != leader_gender
            if vocal_floor is None and (gender_changed or base.get("vocal_floor") in (None, "A2", "G3")):
                base["vocal_floor"] = def_floor
            if vocal_ceiling is None and (gender_changed or base.get("vocal_ceiling") in (None, "F#4", "D5")):
                base["vocal_ceiling"] = def_ceil

    if vocal_floor is not None:
        base["vocal_floor"] = vocal_floor
    if vocal_ceiling is not None:
        base["vocal_ceiling"] = vocal_ceiling
    if cooldown_weeks is not None:
        base["cooldown_weeks"] = int(cooldown_weeks)
    if familiar_ratio is not None:
        base["familiar_ratio"] = float(familiar_ratio)
    if show_capo is not None:
        base["show_capo"] = bool(show_capo)
    if lock_past_keys is not None:
        base["lock_past_keys"] = bool(lock_past_keys)
    if leader_ment_style is not None:
        if leader_ment_style not in LEADER_MENT_STYLE_CHOICES:
            raise ValueError(f"Unsupported leader_ment_style: {leader_ment_style}")
        base["leader_ment_style"] = leader_ment_style
    elif not cfg_path.exists() and "leader_ment_style" not in base:
        base["leader_ment_style"] = "short_script"

    if sheet_dirs is not None:
        base["local_sheet_dirs"] = [str(Path(p)).replace("\\", "/") for p in sheet_dirs if p.strip()]
    elif not cfg_path.exists():
        base["local_sheet_dirs"] = ["sheet"]

    if output_dir is not None:
        base["default_output_dir"] = str(Path(output_dir)).replace("\\", "/")
    elif not cfg_path.exists():
        base["default_output_dir"] = "output"

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Ensure church_local.db exists and bootstrap from configured sheet dirs if present
    boot_summary: dict[str, int] | None = None
    if worship_db_path.exists():
        with contextlib.closing(connect_dbs(worship_db_path, l_db_path, attach_local=True, ensure_schemas=True)):
            pass
        lock_keys = bool(base.get("lock_past_keys", False))
        for d_str in base.get("local_sheet_dirs", []):
            d_path = resolve_workspace_path(d_str)
            if d_path.exists():
                part = bootstrap_repertoire(
                    worship_db_path=worship_db_path,
                    local_db_path=l_db_path,
                    sheets_dir=d_path,
                    lock_keys=lock_keys,
                )
                if boot_summary is None:
                    boot_summary = {
                        "matched_files_or_lines": int(part.get("matched_files_or_lines", 0)),
                        "unique_songs_seeded": int(part.get("unique_songs_seeded", 0)),
                        "newly_inserted": int(part.get("newly_inserted", 0)),
                    }
                else:
                    for k in ("matched_files_or_lines", "unique_songs_seeded", "newly_inserted"):
                        boot_summary[k] += int(part.get(k, 0))

    return {
        "status": "initialized",
        "config_path": str(cfg_path),
        "config": base,
        "bootstrapped": boot_summary,
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Inspect or initialize korean-worship-setlist user profile")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true", help="Inspect current user_config and DB state")
    mode.add_argument("--apply", action="store_true", help="Write/update confirmed settings in user_config.json")

    parser.add_argument("--team-name", default=None)
    parser.add_argument("--church-scale", choices=CHURCH_SCALE_CHOICES, default=None)
    parser.add_argument("--age-group", choices=AGE_GROUP_CHOICES, default=None)
    parser.add_argument("--instruments", default=None, help="Comma-separated list from: " + ",".join(INSTRUMENT_CHOICES))
    parser.add_argument("--default-session", choices=["full_band", "acoustic", "piano_only"], default=None)
    parser.add_argument("--skill-tier", choices=["beginner_friendly", "intermediate", "advanced"], default=None)
    parser.add_argument("--leader-gender", choices=["male", "female"], default=None)
    parser.add_argument("--vocal-floor", default=None)
    parser.add_argument("--vocal-ceiling", default=None)
    parser.add_argument("--vocal-ensemble", choices=VOCAL_ENSEMBLE_CHOICES, default=None)
    parser.add_argument("--seeker-emphasis", action="store_true", default=None)
    parser.add_argument("--no-seeker-emphasis", dest="seeker_emphasis", action="store_false")
    parser.add_argument("--cooldown-weeks", type=int, default=None)
    parser.add_argument("--familiar-ratio", type=float, default=None)
    parser.add_argument("--sheet-dir", action="append", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--show-capo", action="store_true", default=None)
    parser.add_argument("--no-show-capo", dest="show_capo", action="store_false")
    parser.add_argument("--lock-past-keys", action="store_true", default=None)
    parser.add_argument("--no-lock-past-keys", dest="lock_past_keys", action="store_false")
    parser.add_argument(
        "--ment-style",
        choices=LEADER_MENT_STYLE_CHOICES,
        default=None,
        help="Leader ment output mode in leader_cuesheet.md (short_script | bullet_points | none)",
    )
    parser.add_argument("--config-path", type=Path, default=None)
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    parser.add_argument("--local-db", type=Path, default=None)
    args = parser.parse_args()

    if args.inspect:
        res = inspect_profile(
            config_path=args.config_path,
            worship_db_path=args.worship_db,
            local_db_path=args.local_db,
        )
    else:
        insts = [x.strip() for x in args.instruments.split(",") if x.strip()] if args.instruments is not None else None
        res = apply_profile(
            team_name=args.team_name,
            church_scale=args.church_scale,
            congregation_age_group=args.age_group,
            session_instruments=insts,
            default_session=args.default_session,
            session_skill_tier=args.skill_tier,
            leader_gender=args.leader_gender,
            vocal_floor=args.vocal_floor,
            vocal_ceiling=args.vocal_ceiling,
            vocal_ensemble=args.vocal_ensemble,
            seeker_emphasis=args.seeker_emphasis,
            cooldown_weeks=args.cooldown_weeks,
            familiar_ratio=args.familiar_ratio,
            show_capo=args.show_capo,
            lock_past_keys=args.lock_past_keys,
            leader_ment_style=args.ment_style,
            sheet_dirs=args.sheet_dir,
            output_dir=args.output_dir,
            config_path=args.config_path,
            worship_db_path=args.worship_db,
            local_db_path=args.local_db,
        )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
