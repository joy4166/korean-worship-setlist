"""Collect worship team video metadata, medley chapters, and key hints via yt-dlp or text."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from common import (
    KOREAN_KEY_MAP,
    LOCAL_DB_PATH,
    WORSHIP_DB_PATH,
    configure_utf8_stdio,
    connect_dbs,
    is_blocked_source,
    load_blocked_sources,
    normalize_title,
    parse_key_signature,
    slugify_song,
    sync_songs_fts,
)
from init_db import merge_arrangement, upsert_song_record

TIMESTAMP_RE = re.compile(r"(?:^|\s)(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\s|$)")
KEY_HINT_RE = re.compile(
    r"(?:\(|\[|\b)([A-G][#b]?m?)\s*(?:Key|키)?(?:\)|\]|\b)|"
    r"(다장조|라장조|마장조|바장조|사장조|가장조|나장조|내림나장조|내림마장조|내림가장조|내림라장조|올림바장조|"
    r"가단조|마단조|라단조|나단조|사단조|다단조|올림바단조|올림다단조)",
    re.IGNORECASE,
)


def parse_timestamp_sec(ts_match: re.Match[str]) -> int:
    hours = int(ts_match.group(1) or 0)
    minutes = int(ts_match.group(2))
    seconds = int(ts_match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def build_deeplink_url(video_url: str, start_sec: int) -> str:
    if not video_url:
        return ""
    sep = "&" if "?" in video_url else "?"
    base = re.sub(r"([?&])t=\d+s?", "", video_url).rstrip("?&")
    if start_sec <= 0:
        return base
    return f"{base}{sep}t={start_sec}"


def extract_video_id(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        return parsed.path.strip("/") or None
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    return None


def _clean_song_title_and_key(raw_text: str) -> tuple[str, str]:
    detected_key = "C"
    # Check Korean key names first
    for ko_key, std_key in KOREAN_KEY_MAP.items():
        if ko_key in raw_text:
            detected_key = std_key
            raw_text = raw_text.replace(ko_key, " ")
            break

    # Check explicit Key markers like (G Key), [Bb], - A키, G key
    explicit_re = re.compile(
        r"[\(\[\|]?\s*([A-G][#b]?m?)\s*(?:Key|키)\s*[\)\]\|\s]?|"
        r"[\(\[]\s*([A-G][#b]?m?)\s*[\)\]]",
        re.IGNORECASE,
    )
    m = explicit_re.search(raw_text)
    if m:
        raw_k = m.group(1) or m.group(2)
        detected_key = parse_key_signature(raw_k)[0]
        raw_text = explicit_re.sub(" ", raw_text)

    title = re.sub(r"^[\s\d.\-–—•*]+", "", raw_text)
    title = re.sub(r"[\-–—\|/]+$", "", title).strip()
    title = re.sub(r"\s{2,}", " ", title)
    return title, detected_key


def parse_worship_description(
    description: str,
    chapters: list[dict[str, Any]] | None = None,
    video_url: str = "",
    upload_year: int | None = None,
    channel_team: str = "",
) -> list[dict[str, Any]]:
    """Parse setlist entries, keys, and medley [start_sec, end_sec] spans from video metadata."""
    blocked_gate = load_blocked_sources()
    entries: list[dict[str, Any]] = []

    if chapters:
        for ch in chapters:
            raw_title = (ch.get("title") or "").strip()
            if not raw_title:
                continue
            title, key_sig = _clean_song_title_and_key(raw_title)
            if not title or len(title) < 2:
                continue
            start_sec = int(ch.get("start_time", 0) or 0)
            end_sec = int(ch.get("end_time", 0) or 0) or None
            blocked, term = is_blocked_source(title, channel_team, raw_title, blocked=blocked_gate)
            entries.append(
                {
                    "title_ko": title,
                    "key_signature": key_sig,
                    "worship_team": channel_team or "미분류 워십팀",
                    "youtube_start_sec": start_sec,
                    "youtube_end_sec": end_sec,
                    "youtube_url": build_deeplink_url(video_url, start_sec),
                    "recorded_year": upload_year,
                    "key_source": "youtube_parsed",
                    "source_status": "blocked_heresy" if blocked else "community_reviewed",
                    "blocked_reason": term,
                }
            )
        if entries:
            return entries

    # Fallback: parse lines from video description or pinned comment
    parsed_lines: list[dict[str, Any]] = []
    for line in (description or "").splitlines():
        line_str = line.strip()
        if not line_str:
            continue
        ts_m = TIMESTAMP_RE.search(line_str)
        if not ts_m:
            continue
        start_sec = parse_timestamp_sec(ts_m)
        remainder = TIMESTAMP_RE.sub(" ", line_str, count=1).strip()
        if any(skip in remainder.lower() for skip in ("설교", "기도", "광고", "축도", "멘트", "intro", "outro")):
            if len(remainder) < 8:
                continue
        title, key_sig = _clean_song_title_and_key(remainder)
        if not title or len(title) < 2:
            continue
        blocked, term = is_blocked_source(title, channel_team, line_str, blocked=blocked_gate)
        parsed_lines.append(
            {
                "title_ko": title,
                "key_signature": key_sig,
                "worship_team": channel_team or "미분류 워십팀",
                "youtube_start_sec": start_sec,
                "youtube_end_sec": None,
                "youtube_url": build_deeplink_url(video_url, start_sec),
                "recorded_year": upload_year,
                "key_source": "youtube_parsed",
                "source_status": "blocked_heresy" if blocked else "community_reviewed",
                "blocked_reason": term,
            }
        )

    for idx in range(len(parsed_lines) - 1):
        parsed_lines[idx]["youtube_end_sec"] = parsed_lines[idx + 1]["youtube_start_sec"]
    return parsed_lines


def ingest_parsed_entries(
    entries: list[dict[str, Any]],
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path = LOCAL_DB_PATH,
) -> dict[str, int]:
    """Store parsed YouTube entries into worship.db without raw lyrics."""
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=False, ensure_schemas=True)
    inserted = 0
    blocked_cnt = 0
    with conn:
        for item in entries:
            if item.get("source_status") == "blocked_heresy":
                blocked_cnt += 1
            norm = normalize_title(item["title_ko"])
            existing = conn.execute(
                "SELECT id, song_slug, source_status FROM songs WHERE normalized_title = ? LIMIT 1",
                (norm,),
            ).fetchone()
            year = item.get("recorded_year") or 2024
            arr_payload = {
                "worship_team": item.get("worship_team", "미분류 워십팀"),
                "key_signature": item.get("key_signature", "C"),
                "time_signature": "4/4",
                "bpm": int(item.get("bpm", 80)),
                "tempo_tier": item.get("tempo_tier", "medium"),
                "youtube_url": item.get("youtube_url"),
                "youtube_start_sec": int(item.get("youtube_start_sec", 0)),
                "recorded_year": year,
                "key_source": item.get("key_source", "youtube_parsed"),
                "bpm_source": item.get("bpm_source", "manual_seed"),
                "confidence": 0.75,
            }
            if existing is not None:
                if item.get("source_status") == "blocked_heresy":
                    conn.execute(
                        "UPDATE songs SET source_status = 'blocked_heresy', theological_notes = COALESCE(?, theological_notes), updated_at = datetime('now') WHERE id = ?",
                        (item.get("blocked_reason"), existing["id"]),
                    )
                merge_arrangement(conn, int(existing["id"]), arr_payload)
                sync_songs_fts(conn, song_id=int(existing["id"]))
                inserted += 1
                continue

            slug = slugify_song(item["title_ko"], item.get("worship_team"))
            era = f"{(year // 10) * 10}s" if 1980 <= year <= 2029 else "2020s"
            record = {
                "song_slug": slug,
                "title_ko": item["title_ko"],
                "category": "modern_worship",
                "era": era,
                "original_artist": item.get("worship_team"),
                "release_year": year,
                "theme_summary": f"{item.get('worship_team', '워십팀')} 예배 실황 선곡 ({item['title_ko']})",
                "theme_tags": ["예배", "찬양"],
                "liturgical_stages": ["declaration"],
                "available_sections": ["I", "V1", "C", "O"],
                "default_song_form": "I(4m) - V1 - C(x2) - O",
                "source_status": item.get("source_status", "community_reviewed"),
                "theological_notes": item.get("blocked_reason"),
                "arrangements": [arr_payload],
            }
            upsert_song_record(conn, record)
            inserted += 1
    conn.close()
    return {"processed": inserted, "blocked_heresy": blocked_cnt}


def collect_channel_incremental(
    channel_url: str,
    channel_team: str,
    existing_video_ids: set[str],
    limit: int = 10,
    sleep_sec: float = 2.0,
) -> list[dict[str, Any]]:
    """Use yt-dlp extract_flat diff + throttled metadata fetch for new videos only."""
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is part of the optional 'builder' extra. Install with: uv sync --extra builder"
        ) from exc

    flat_opts = {"extract_flat": True, "quiet": True, "playlistend": limit * 2}
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
    candidates = info.get("entries", []) if info else []
    new_urls: list[str] = []
    for entry in candidates:
        vid = entry.get("id")
        if vid and vid not in existing_video_ids:
            new_urls.append(entry.get("url") or f"https://www.youtube.com/watch?v={vid}")
        if len(new_urls) >= limit:
            break

    all_parsed: list[dict[str, Any]] = []
    detail_opts = {"skip_download": True, "quiet": True}
    with yt_dlp.YoutubeDL(detail_opts) as ydl:
        for i, url in enumerate(new_urls):
            if i > 0 and sleep_sec > 0:
                time.sleep(sleep_sec)
            meta = ydl.extract_info(url, download=False)
            if not meta:
                continue
            upload_date = meta.get("upload_date") or ""
            year = int(upload_date[:4]) if len(upload_date) >= 4 and upload_date[:4].isdigit() else None
            all_parsed.extend(
                parse_worship_description(
                    description=meta.get("description") or "",
                    chapters=meta.get("chapters"),
                    video_url=meta.get("webpage_url") or url,
                    upload_year=year,
                    channel_team=channel_team,
                )
            )
    return all_parsed


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Collect worship metadata from YouTube descriptions or channels")
    parser.add_argument("--description-file", type=Path, help="Local text file of video description/timestamps")
    parser.add_argument("--channel-url", help="YouTube channel/playlist URL (requires builder extra)")
    parser.add_argument("--team", default="마커스워십", help="Worship team name")
    parser.add_argument("--video-url", default="", help="Source video URL for deeplinks")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--sleep-sec", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    args = parser.parse_args()

    entries: list[dict[str, Any]] = []
    if args.description_file:
        text = args.description_file.read_text(encoding="utf-8")
        entries = parse_worship_description(
            description=text,
            video_url=args.video_url,
            upload_year=args.year,
            channel_team=args.team,
        )
    elif args.channel_url:
        conn = connect_dbs(args.worship_db, attach_local=False, ensure_schemas=True)
        urls = [r[0] for r in conn.execute("SELECT youtube_url FROM arrangements WHERE youtube_url IS NOT NULL").fetchall()]
        conn.close()
        existing_ids = {vid for u in urls if (vid := extract_video_id(u))}
        entries = collect_channel_incremental(
            args.channel_url, args.team, existing_ids, limit=args.limit, sleep_sec=args.sleep_sec
        )
    else:
        parser.error("Provide --description-file or --channel-url")

    if args.dry_run:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return

    res = ingest_parsed_entries(entries, worship_db_path=args.worship_db)
    print(json.dumps({"entries": len(entries), **res}, ensure_ascii=False))


if __name__ == "__main__":
    main()
