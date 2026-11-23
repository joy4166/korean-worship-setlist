"""Initialize dual SQLite databases (worship.db & church_local.db) and load seed songs."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from common import (
    LOCAL_DB_PATH,
    SEED_SONGS_PATH,
    WORSHIP_DB_PATH,
    configure_utf8_stdio,
    connect_dbs,
    is_blocked_source,
    json_dumps_ko,
    normalize_nfc,
    normalize_scripture_ref,
    normalize_title,
    parse_key_signature,
    slugify_song,
    sync_songs_fts,
)


def merge_arrangement(conn: sqlite3.Connection, song_id: int, arr: dict[str, Any]) -> None:
    """Insert an arrangement or update an existing one for the same team only if confidence is higher."""
    team = normalize_nfc(arr["worship_team"]).strip()
    disp_key, _, _, _ = parse_key_signature(arr["key_signature"])
    new_conf = float(arr.get("confidence", 0.95))

    existing = conn.execute(
        "SELECT id, confidence FROM arrangements WHERE song_id = ? AND worship_team = ?",
        (song_id, team),
    ).fetchone()
    if existing is not None:
        if new_conf <= float(existing["confidence"] or 0.0):
            return
        conn.execute(
            """
            UPDATE arrangements SET
                key_signature = ?, time_signature = ?, bpm = ?, tempo_tier = ?,
                groove_style = COALESCE(?, groove_style),
                intro_first_chord = COALESCE(?, intro_first_chord),
                outro_last_chord = COALESCE(?, outro_last_chord),
                ending_type = ?, drum_grooves = ?, section_dynamics = ?,
                lowest_note = COALESCE(?, lowest_note),
                highest_note = COALESCE(?, highest_note),
                vocal_range_fit = ?, congregation_singability = ?, band_dependency = ?,
                session_fit = ?, arrangement_song_form = COALESCE(?, arrangement_song_form),
                youtube_url = COALESCE(?, youtube_url),
                youtube_start_sec = ?,
                recorded_year = COALESCE(?, recorded_year),
                key_source = ?, bpm_source = ?, confidence = ?
            WHERE id = ?
            """,
            (
                disp_key,
                arr.get("time_signature", "4/4"),
                int(arr["bpm"]),
                arr["tempo_tier"],
                arr.get("groove_style"),
                arr.get("intro_first_chord"),
                arr.get("outro_last_chord"),
                arr.get("ending_type", "pad_hold"),
                json_dumps_ko(arr.get("drum_grooves", [])),
                json_dumps_ko(arr.get("section_dynamics", {})),
                arr.get("lowest_note"),
                arr.get("highest_note"),
                arr.get("vocal_range_fit", "congregation_comfortable"),
                arr.get("congregation_singability", "modern_simple"),
                arr.get("band_dependency", "medium"),
                json_dumps_ko(arr.get("session_fit", ["full_band"])),
                arr.get("arrangement_song_form"),
                arr.get("youtube_url"),
                int(arr.get("youtube_start_sec", 0)),
                arr.get("recorded_year"),
                arr.get("key_source", "manual_seed"),
                arr.get("bpm_source", "manual_seed"),
                new_conf,
                existing["id"],
            ),
        )
        return

    has_default = conn.execute(
        "SELECT 1 FROM arrangements WHERE song_id = ? AND is_default = 1", (song_id,)
    ).fetchone()
    is_def = int(arr.get("is_default", 0 if has_default else 1))
    conn.execute(
        """
        INSERT INTO arrangements (
            song_id, worship_team, key_signature, time_signature, bpm, tempo_tier,
            groove_style, intro_first_chord, outro_last_chord, ending_type,
            drum_grooves, section_dynamics, lowest_note, highest_note,
            vocal_range_fit, congregation_singability, band_dependency,
            session_fit, arrangement_song_form, youtube_url, youtube_start_sec,
            recorded_year, key_source, bpm_source, confidence, is_default
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            song_id,
            team,
            disp_key,
            arr.get("time_signature", "4/4"),
            int(arr["bpm"]),
            arr["tempo_tier"],
            arr.get("groove_style"),
            arr.get("intro_first_chord"),
            arr.get("outro_last_chord"),
            arr.get("ending_type", "pad_hold"),
            json_dumps_ko(arr.get("drum_grooves", [])),
            json_dumps_ko(arr.get("section_dynamics", {})),
            arr.get("lowest_note"),
            arr.get("highest_note"),
            arr.get("vocal_range_fit", "congregation_comfortable"),
            arr.get("congregation_singability", "modern_simple"),
            arr.get("band_dependency", "medium"),
            json_dumps_ko(arr.get("session_fit", ["full_band"])),
            arr.get("arrangement_song_form"),
            arr.get("youtube_url"),
            int(arr.get("youtube_start_sec", 0)),
            arr.get("recorded_year"),
            arr.get("key_source", "manual_seed"),
            arr.get("bpm_source", "manual_seed"),
            new_conf,
            is_def,
        ),
    )


def upsert_song_record(conn: sqlite3.Connection, item: dict[str, Any], sync_fts: bool = True) -> int:
    """Insert or update a song and its arrangements in worship.db, enforcing NFC and safety gates."""
    title_ko = normalize_nfc(item["title_ko"]).strip()
    title_en = normalize_nfc(item.get("title_en")).strip() or None
    orig_artist = normalize_nfc(item.get("original_artist")).strip() or None
    alt_titles = [normalize_nfc(a).strip() for a in item.get("alternate_titles", []) if a]
    incipit = normalize_nfc(item.get("first_line_incipit") or "").strip()
    if len(incipit) > 15:
        raise ValueError(f"first_line_incipit exceeds 15 chars for '{title_ko}': '{incipit}'")

    if "lyrics" in item or "full_lyrics" in item:
        raise ValueError(f"Copyright violation: raw lyrics field is forbidden for '{title_ko}'")

    lyricist = normalize_nfc(item.get("lyricist")).strip() or None
    composer = normalize_nfc(item.get("composer")).strip() or None
    copyright_holder = normalize_nfc(item.get("copyright_holder")).strip() or None

    blocked, term = is_blocked_source(title_ko, orig_artist, lyricist, composer, copyright_holder)
    source_status = "blocked_heresy" if blocked else item.get("source_status", "verified_official")
    if blocked:
        notes = f"Blocked source keyword: {term}"
    else:
        notes = normalize_nfc(item.get("theological_notes")) or None

    slug = item.get("song_slug") or slugify_song(title_ko, orig_artist)
    norm_title = normalize_title(title_ko)
    scripture_refs = [normalize_scripture_ref(r) for r in item.get("scripture_refs", []) if r]
    theme_summary = normalize_nfc(item["theme_summary"]).strip()
    theological_core = normalize_nfc(item.get("theological_core", "")).strip()
    caution_nuance = normalize_nfc(item.get("caution_nuance", "")).strip()
    theme_tags = [normalize_nfc(t).strip() for t in item.get("theme_tags", []) if t]

    conn.execute(
        """
        INSERT INTO songs (
            song_slug, title_ko, title_en, normalized_title, alternate_titles,
            first_line_incipit, category, hymn_number, hymn_number_unified, era,
            original_artist, release_year, adoption_tier, lyric_accessibility,
            church_seasons, service_slots, liturgical_stages, theological_tone,
            lyrical_perspective, theological_core, caution_nuance, theme_summary,
            theme_tags, scripture_refs, available_sections, default_song_form,
            lyricist, composer, copyright_holder, ccli_number, copyright_scope,
            source_status, theological_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(song_slug) DO UPDATE SET
            title_ko = excluded.title_ko,
            title_en = excluded.title_en,
            normalized_title = excluded.normalized_title,
            alternate_titles = excluded.alternate_titles,
            first_line_incipit = excluded.first_line_incipit,
            category = excluded.category,
            hymn_number = excluded.hymn_number,
            hymn_number_unified = excluded.hymn_number_unified,
            era = excluded.era,
            original_artist = excluded.original_artist,
            release_year = excluded.release_year,
            adoption_tier = excluded.adoption_tier,
            lyric_accessibility = excluded.lyric_accessibility,
            church_seasons = excluded.church_seasons,
            service_slots = excluded.service_slots,
            liturgical_stages = excluded.liturgical_stages,
            theological_tone = excluded.theological_tone,
            lyrical_perspective = excluded.lyrical_perspective,
            theological_core = excluded.theological_core,
            caution_nuance = excluded.caution_nuance,
            theme_summary = excluded.theme_summary,
            theme_tags = excluded.theme_tags,
            scripture_refs = excluded.scripture_refs,
            available_sections = excluded.available_sections,
            default_song_form = excluded.default_song_form,
            lyricist = excluded.lyricist,
            composer = excluded.composer,
            copyright_holder = excluded.copyright_holder,
            ccli_number = excluded.ccli_number,
            copyright_scope = excluded.copyright_scope,
            source_status = excluded.source_status,
            theological_notes = excluded.theological_notes,
            updated_at = datetime('now')
        """,
        (
            slug,
            title_ko,
            title_en,
            norm_title,
            json_dumps_ko(alt_titles),
            incipit or None,
            item["category"],
            item.get("hymn_number"),
            item.get("hymn_number_unified"),
            item["era"],
            orig_artist,
            item.get("release_year"),
            item.get("adoption_tier", "widely_sung"),
            item.get("lyric_accessibility", "standard_church"),
            json_dumps_ko(item.get("church_seasons", ["ordinary"])),
            json_dumps_ko(item.get("service_slots", ["worship_set"])),
            json_dumps_ko(item.get("liturgical_stages", [])),
            item.get("theological_tone", "adoration"),
            item.get("lyrical_perspective", "god_centered_vertical"),
            theological_core,
            caution_nuance,
            theme_summary,
            json_dumps_ko(theme_tags),
            json_dumps_ko(scripture_refs),
            json_dumps_ko(item.get("available_sections", [])),
            item.get("default_song_form"),
            lyricist,
            composer,
            copyright_holder,
            item.get("ccli_number"),
            item.get("copyright_scope", "komca_separate"),
            source_status,
            notes,
        ),
    )

    song_id = conn.execute("SELECT id FROM songs WHERE song_slug = ?", (slug,)).fetchone()["id"]

    arrangements = item.get("arrangements", [])
    if arrangements:
        conn.execute("DELETE FROM arrangements WHERE song_id = ?", (song_id,))
        for idx, arr in enumerate(arrangements):
            disp_key, _, _, _ = parse_key_signature(arr["key_signature"])
            conn.execute(
                """
                INSERT INTO arrangements (
                    song_id, worship_team, key_signature, time_signature, bpm, tempo_tier,
                    groove_style, intro_first_chord, outro_last_chord, ending_type,
                    drum_grooves, section_dynamics, lowest_note, highest_note,
                    vocal_range_fit, congregation_singability, band_dependency,
                    session_fit, arrangement_song_form, youtube_url, youtube_start_sec,
                    recorded_year, key_source, bpm_source, confidence, is_default
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    song_id,
                    normalize_nfc(arr["worship_team"]).strip(),
                    disp_key,
                    arr.get("time_signature", "4/4"),
                    int(arr["bpm"]),
                    arr["tempo_tier"],
                    arr.get("groove_style"),
                    arr.get("intro_first_chord"),
                    arr.get("outro_last_chord"),
                    arr.get("ending_type", "pad_hold"),
                    json_dumps_ko(arr.get("drum_grooves", [])),
                    json_dumps_ko(arr.get("section_dynamics", {})),
                    arr.get("lowest_note"),
                    arr.get("highest_note"),
                    arr.get("vocal_range_fit", "congregation_comfortable"),
                    arr.get("congregation_singability", "modern_simple"),
                    arr.get("band_dependency", "medium"),
                    json_dumps_ko(arr.get("session_fit", ["full_band"])),
                    arr.get("arrangement_song_form") or item.get("default_song_form"),
                    arr.get("youtube_url"),
                    int(arr.get("youtube_start_sec", 0)),
                    arr.get("recorded_year"),
                    arr.get("key_source", "manual_seed"),
                    arr.get("bpm_source", "manual_seed"),
                    float(arr.get("confidence", 0.95)),
                    int(arr.get("is_default", 1 if idx == 0 else 0)),
                ),
            )

    if sync_fts:
        sync_songs_fts(conn, song_id=song_id)
    return song_id


def initialize_databases(
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path | None = None,
    seed_path: Path | None = None,
) -> dict[str, int]:
    """Initialize both DBs and optionally load seed songs."""
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=True, ensure_schemas=True)
    loaded = 0
    if seed_path and seed_path.exists():
        seeds = json.loads(seed_path.read_text(encoding="utf-8"))
        with conn:
            for item in seeds:
                upsert_song_record(conn, item)
                loaded += 1
    song_count = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    arr_count = conn.execute("SELECT COUNT(*) FROM arrangements").fetchone()[0]
    conn.close()
    return {"loaded_seeds": loaded, "songs": song_count, "arrangements": arr_count}


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Initialize worship.db and church_local.db")
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    parser.add_argument("--local-db", type=Path, default=None)
    parser.add_argument("--seed", action="store_true", help="Load seed_songs.json into worship.db")
    parser.add_argument("--seed-file", type=Path, default=SEED_SONGS_PATH)
    args = parser.parse_args()

    stats = initialize_databases(
        worship_db_path=args.worship_db,
        local_db_path=args.local_db,
        seed_path=args.seed_file if args.seed else None,
    )
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
