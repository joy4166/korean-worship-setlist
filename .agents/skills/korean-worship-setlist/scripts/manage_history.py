"""Manage local church repertoire, cold-start bootstrapping, cooldown history, and learning progression."""

from __future__ import annotations

import argparse
import difflib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from common import (
    KOREAN_KEY_MAP,
    LOCAL_DB_PATH,
    WORSHIP_DB_PATH,
    assert_no_raw_lyrics_text,
    configure_utf8_stdio,
    connect_dbs,
    json_dumps_ko,
    load_user_config,
    normalize_scripture_ref,
    normalize_title,
    parse_key_signature,
    resolve_workspace_path,
)

SHEET_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_KR_KEY_ALT = "|".join(sorted((re.escape(k) for k in KOREAN_KEY_MAP), key=len, reverse=True))


def extract_title_and_key_from_label(raw_label: str) -> tuple[str, str | None]:
    """Parse a song filename or setlist line (e.g. '01_주가 일하시네(G키)_p1') into (title, key)."""
    text = raw_label.strip()
    detected_key: str | None = None

    # Strip leading track numbering ('01_', '1.', '02 -') & trailing page markers ('_p1', '-p2', '(1)') FIRST
    text = re.sub(r"^\d{1,3}\s*[._\-\)]\s*", "", text).strip()
    text = re.sub(r"(?:[_\-\s]+p\d+|\s*\(\d+\))\s*$", "", text, flags=re.IGNORECASE).strip()

    # Check Korean key names first, then bracketed/delimited musical keys (avoiding trailing English words like 'I Am')
    key_patterns = [
        rf"[\(\[]\s*({_KR_KEY_ALT})\s*[\)\]]",
        rf"[_\-\s]+({_KR_KEY_ALT})\s*$",
        r"[\(\[]\s*([A-G][#b]?m?)\s*(?:Key|키)?\s*[\)\]]",
        r"[_\-]+\s*([A-G][#b]?m?)\s*(?:Key|키)?\s*$",
        r"\s+([A-G][#b]?m?)\s*(?:Key|키)\s*$",
        r"(?<=[가-힣])\s+([A-G][#b]?m?)\s*$",
    ]
    for pat in key_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            detected_key = parse_key_signature(candidate)[0]
            text = (text[: m.start()] + " " + text[m.end() :]).strip()
            break

    text = re.sub(r"(?:[_\-\s]+p\d+|\s*\(\d+\))\s*$", "", text, flags=re.IGNORECASE).strip()
    return text, detected_key


def _match_song_by_title(
    songs: list[dict[str, Any]], raw_title: str
) -> dict[str, Any] | None:
    norm_q = normalize_title(raw_title)
    if not norm_q:
        return None

    # Remove common worship team prefixes like '마커스 - ', '어노인팅_'
    cleaned_q = re.sub(
        r"^(마커스워십|마커스|어노인팅|제이어스|위러브|아이자야씩스티원|예수전도단|히즈윌)[_\-\s]+",
        "",
        raw_title.strip(),
    )
    norm_clean = normalize_title(cleaned_q)

    best_song: dict[str, Any] | None = None
    best_score = 0.0

    for s in songs:
        candidates = [s["normalized_title"]] + [normalize_title(a) for a in s["alternate_titles"]]
        for c in candidates:
            if not c:
                continue
            if norm_q == c or norm_clean == c:
                return s
            if len(norm_clean) >= 3 and (c in norm_clean or norm_clean in c):
                score = max(0.85, len(c) / max(len(norm_clean), len(c)))
            else:
                score = difflib.SequenceMatcher(None, norm_clean, c).ratio()
            if score > best_score:
                best_score = score
                best_song = s

    return best_song if best_score >= 0.72 else None


def bootstrap_repertoire(
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
    sheets_dir: Path | None = None,
    history_text_file: Path | None = None,
    default_familiarity: str = "familiar",
    lock_keys: bool = False,
) -> dict[str, Any]:
    """Seed local_db.repertoire from existing sheet music filenames and/or past setlist text."""
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=True, ensure_schemas=True)
    rows = conn.execute(
        "SELECT id, song_slug, title_ko, normalized_title, alternate_titles FROM songs WHERE source_status != 'blocked_heresy'"
    ).fetchall()
    songs = [
        {
            "id": r["id"],
            "song_slug": r["song_slug"],
            "title_ko": r["title_ko"],
            "normalized_title": r["normalized_title"],
            "alternate_titles": json.loads(r["alternate_titles"] or "[]"),
        }
        for r in rows
    ]

    matched_items: list[tuple[str, str | None, str]] = []

    if sheets_dir and sheets_dir.exists():
        for p in sheets_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in SHEET_EXTENSIONS:
                title_guess, key_guess = extract_title_and_key_from_label(p.stem)
                matched = _match_song_by_title(songs, title_guess)
                if matched:
                    matched_items.append((matched["song_slug"], key_guess, f"sheet:{p.name}"))

    if history_text_file and history_text_file.exists():
        text = history_text_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            line_clean = line.strip()
            if not line_clean:
                continue
            title_guess, key_guess = extract_title_and_key_from_label(line_clean)
            matched = _match_song_by_title(songs, title_guess)
            if matched:
                matched_items.append((matched["song_slug"], key_guess, f"history:{line_clean[:30]}"))

    seeded = 0
    seen_slugs: set[str] = set()
    with conn:
        for slug, key_guess, source_note in matched_items:
            seen_slugs.add(slug)
            existing = conn.execute(
                "SELECT familiarity, preferred_key FROM local_db.repertoire WHERE song_slug = ?",
                (slug,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO local_db.repertoire (song_slug, familiarity, preferred_key, team_memo)
                    VALUES (?, ?, ?, ?)
                    """,
                    (slug, default_familiarity, key_guess if lock_keys else None, f"bootstrapped ({source_note})"),
                )
                seeded += 1
            else:
                new_fam = default_familiarity if existing["familiarity"] == "unknown" else existing["familiarity"]
                if lock_keys and key_guess and not existing["preferred_key"]:
                    conn.execute(
                        "UPDATE local_db.repertoire SET familiarity = ?, preferred_key = ? WHERE song_slug = ?",
                        (new_fam, key_guess, slug),
                    )
                else:
                    conn.execute(
                        "UPDATE local_db.repertoire SET familiarity = ? WHERE song_slug = ?",
                        (new_fam, slug),
                    )
    conn.close()
    return {"matched_files_or_lines": len(matched_items), "unique_songs_seeded": len(seen_slugs), "newly_inserted": seeded}


def set_repertoire_entry(
    song_slug: str,
    familiarity: str | None = None,
    preferred_key: str | None = None,
    team_memo: str | None = None,
    clear_preferred_key: bool = False,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
) -> dict[str, Any]:
    """Manually set repertoire status (pinned_ritual, favorite, familiar, learning, new, excluded) or preferred_key."""
    assert_no_raw_lyrics_text(team_memo, field_name="team_memo")
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=True, ensure_schemas=True)
    existing = conn.execute(
        "SELECT * FROM local_db.repertoire WHERE song_slug = ?", (song_slug,)
    ).fetchone()
    if clear_preferred_key:
        norm_key = None
    elif preferred_key:
        norm_key = parse_key_signature(preferred_key)[0]
    else:
        norm_key = existing["preferred_key"] if existing else None
    fam = familiarity or (existing["familiarity"] if existing else "familiar")
    memo = team_memo if team_memo is not None else (existing["team_memo"] if existing else None)

    with conn:
        conn.execute(
            """
            INSERT INTO local_db.repertoire (song_slug, familiarity, preferred_key, team_memo)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(song_slug) DO UPDATE SET
                familiarity = excluded.familiarity,
                preferred_key = excluded.preferred_key,
                team_memo = excluded.team_memo
            """,
            (song_slug, fam, norm_key, memo),
        )
    row = dict(conn.execute("SELECT * FROM local_db.repertoire WHERE song_slug = ?", (song_slug,)).fetchone())
    conn.close()
    return row


def _assert_payload_no_raw_lyrics(obj: Any) -> None:
    if isinstance(obj, dict):
        if "lyrics" in obj or "full_lyrics" in obj:
            raise ValueError("Raw lyrics fields ('lyrics', 'full_lyrics') cannot be recorded into history DB")
        for val in obj.values():
            _assert_payload_no_raw_lyrics(val)
    elif isinstance(obj, list):
        for item in obj:
            _assert_payload_no_raw_lyrics(item)


def _extract_songs_from_payload(setlist_payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = setlist_payload.get("songs")
    if isinstance(raw, list) and raw:
        return [x.get("primary", x) for x in raw if isinstance(x, dict)]
    collected: list[dict[str, Any]] = []
    pre = setlist_payload.get("pre_sermon")
    if isinstance(pre, list):
        for item in pre:
            if isinstance(item, dict):
                collected.append(item.get("primary", item))
    post = setlist_payload.get("post_sermon")
    if isinstance(post, list):
        for item in post:
            if isinstance(item, dict):
                collected.append(item.get("primary", item))
    elif isinstance(post, dict):
        rep = post.get("reprise_candidate")
        if isinstance(rep, dict):
            collected.append(rep)
    return collected


def record_setlist(
    setlist_payload: dict[str, Any],
    output_dir: str | None = None,
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
) -> dict[str, Any]:
    """
    Record a confirmed setlist into setlist_history and update repertoire stats.
    - Prevents double-counting times_sung across 1부/2부/3부 on the same (service_date, cooldown_scope).
    - Advances 'learning' songs and auto-promotes to 'familiar' at >= 3 consecutive appearances.
    """
    _assert_payload_no_raw_lyrics(setlist_payload)
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=True, ensure_schemas=True)

    service_date = setlist_payload.get("service_date") or date.today().isoformat()
    service_type = setlist_payload.get("service_type") or "youth"
    cooldown_scope = setlist_payload.get("cooldown_scope") or service_type
    service_part = setlist_payload.get("service_part") or "1부"
    preacher = setlist_payload.get("preacher")
    sermon_title = setlist_payload.get("sermon_title")
    sermon_scripture = normalize_scripture_ref(setlist_payload.get("sermon_scripture") or "")
    church_season = setlist_payload.get("church_season") or "ordinary"
    theme_input = setlist_payload.get("theme_input") or ""

    # Check if this date + scope was already counted (e.g. 1부 recorded before 2부)
    prior_same_day = conn.execute(
        """
        SELECT setlist_json FROM local_db.setlist_history
        WHERE service_date = ? AND cooldown_scope = ?
        """,
        (service_date, cooldown_scope),
    ).fetchall()
    already_counted_slugs: set[str] = set()
    for pr in prior_same_day:
        try:
            prev_data = json.loads(pr["setlist_json"])
            for s in _extract_songs_from_payload(prev_data):
                if s.get("song_slug"):
                    already_counted_slugs.add(s["song_slug"])
        except Exception:
            pass

    songs_in_setlist = _extract_songs_from_payload(setlist_payload)

    unique_slugs: list[str] = []
    seen: set[str] = set()
    for entry in songs_in_setlist:
        slug = entry.get("song_slug")
        if slug and slug not in seen and not entry.get("is_reprise", False):
            seen.add(slug)
            unique_slugs.append(slug)

    promoted: list[str] = []
    counted: list[str] = []
    skipped_duplicate_part: list[str] = []

    with conn:
        conn.execute(
            """
            INSERT INTO local_db.setlist_history (
                service_date, service_type, cooldown_scope, service_part,
                preacher, sermon_title, sermon_scripture, church_season,
                theme_input, setlist_json, output_dir
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service_date,
                service_type,
                cooldown_scope,
                service_part,
                preacher,
                sermon_title,
                sermon_scripture or None,
                church_season,
                theme_input,
                json_dumps_ko(setlist_payload),
                output_dir,
            ),
        )

        for slug in unique_slugs:
            if slug in already_counted_slugs:
                skipped_duplicate_part.append(slug)
                continue

            rep = conn.execute(
                "SELECT * FROM local_db.repertoire WHERE song_slug = ?", (slug,)
            ).fetchone()
            if rep is None:
                conn.execute(
                    """
                    INSERT INTO local_db.repertoire (
                        song_slug, familiarity, times_sung, consecutive_learning_count, last_sung_date
                    ) VALUES (?, 'familiar', 1, 0, ?)
                    """,
                    (slug, service_date),
                )
            else:
                fam = rep["familiarity"]
                times_sung = int(rep["times_sung"]) + 1
                learning_cnt = int(rep["consecutive_learning_count"] or 0)
                if fam == "learning":
                    learning_cnt += 1
                    if learning_cnt >= 3:
                        fam = "familiar"
                        promoted.append(slug)
                elif fam in ("unknown", "new"):
                    fam = "familiar"
                conn.execute(
                    """
                    UPDATE local_db.repertoire
                    SET familiarity = ?, times_sung = ?, consecutive_learning_count = ?, last_sung_date = ?
                    WHERE song_slug = ?
                    """,
                    (fam, times_sung, learning_cnt, service_date, slug),
                )
            counted.append(slug)

    conn.close()
    return {
        "recorded_date": service_date,
        "cooldown_scope": cooldown_scope,
        "service_part": service_part,
        "counted_songs": counted,
        "skipped_duplicate_part": skipped_duplicate_part,
        "promoted_to_familiar": promoted,
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Manage local repertoire and setlist history")
    sub = parser.add_subparsers(dest="command", required=True)

    p_boot = sub.add_parser("bootstrap", help="Seed repertoire from sheet filenames and/or past setlist text")
    p_boot.add_argument("--sheets-dir", type=Path, default=None)
    p_boot.add_argument("--history-file", type=Path, default=None)
    p_boot.add_argument("--familiarity", default="familiar")
    p_boot.add_argument("--lock-keys", action="store_true")
    p_boot.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    p_boot.add_argument("--local-db", type=Path, default=None)

    p_rec = sub.add_parser("record", help="Record confirmed setlist JSON to setlist_history")
    p_rec.add_argument("--setlist", type=Path, required=True)
    p_rec.add_argument("--output-dir", default=None)
    p_rec.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    p_rec.add_argument("--local-db", type=Path, default=None)

    p_set = sub.add_parser("set-repertoire", help="Set repertoire familiarity or preferred key")
    p_set.add_argument("--slug", required=True)
    p_set.add_argument("--familiarity", choices=["pinned_ritual", "favorite", "familiar", "learning", "new", "unknown", "excluded"])
    p_set.add_argument("--preferred-key", default=None)
    p_set.add_argument("--clear-preferred-key", action="store_true")
    p_set.add_argument("--memo", default=None)
    p_set.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    p_set.add_argument("--local-db", type=Path, default=None)

    p_list = sub.add_parser("list", help="List repertoire or history")
    p_list.add_argument("--target", choices=["repertoire", "history"], default="repertoire")
    p_list.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    p_list.add_argument("--local-db", type=Path, default=None)

    args = parser.parse_args()
    if args.command == "bootstrap":
        sheets_dir = args.sheets_dir
        if sheets_dir is None:
            cfg = load_user_config()
            dirs = cfg.get("local_sheet_dirs") or []
            if dirs:
                sheets_dir = resolve_workspace_path(dirs[0])
        res = bootstrap_repertoire(
            worship_db_path=args.worship_db,
            local_db_path=args.local_db,
            sheets_dir=sheets_dir,
            history_text_file=args.history_file,
            default_familiarity=args.familiarity,
            lock_keys=args.lock_keys,
        )
    elif args.command == "record":
        payload = json.loads(args.setlist.read_text(encoding="utf-8"))
        res = record_setlist(payload, output_dir=args.output_dir, worship_db_path=args.worship_db, local_db_path=args.local_db)
    elif args.command == "set-repertoire":
        res = set_repertoire_entry(
            song_slug=args.slug,
            familiarity=args.familiarity,
            preferred_key=args.preferred_key,
            team_memo=args.memo,
            clear_preferred_key=args.clear_preferred_key,
            worship_db_path=args.worship_db,
            local_db_path=args.local_db,
        )
    else:
        conn = connect_dbs(args.worship_db, args.local_db, attach_local=True, ensure_schemas=True)
        table = "local_db.repertoire" if args.target == "repertoire" else "local_db.setlist_history"
        res = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        conn.close()

    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
