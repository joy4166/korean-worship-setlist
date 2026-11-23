"""Pinpoint rhythm/groove & BPM curation for iconic Korean worship songs in worship.db."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from common import WORSHIP_DB_PATH, configure_utf8_stdio


# Each entry updates matching arrangements in worship.db cleanly and idempotently.
CURATED_RHYTHM_RULES: list[dict[str, Any]] = [
    # --- Shuffle / Bounce fast songs (triplet subdivision feel) ---
    {
        "slug": "우리-모두-찬양하세-옹기장이-94cf07",
        "min_bpm": 100,
        "bpm": 124,
        "tempo_tier": "fast",
        "groove_style": "shuffle_bounce",
        "confidence": 0.90,
    },
    {
        "slug": "오늘-집을-나서기-전-복음성가-모음-508606",
        "bpm": 120,
        "tempo_tier": "fast",
        "groove_style": "shuffle_bounce",
        "confidence": 0.88,
    },
    {
        "slug": "내-영혼이-은총-입어-hymn-438-나무엔-004903",
        "min_bpm": 100,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-438-bcc185",
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-405-90a64a",
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-091-f4b9c8",
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-183-17910f",
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    # --- 16th Upbeat / Syncopated Straight fast songs ---
    {
        "slug": "기쁨으로-찬양-옹기장이-5a13cb",
        "bpm": 100,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "나는-주만-높이리-마커스-markers-55d9ac",
        "bpm": 106,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "기쁨의-노래-어노인팅-1aff27",
        "min_bpm": 100,
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "나-기쁨의-노래하리-어노인팅-33ea06",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "기쁨으로-김윤진-f7563d",
        "bpm": 120,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.88,
    },
    # --- Country / Go-Go 2-beat fast songs ---
    {
        "slug": "우리-모두-손뼉-치며-함께-옹기장이-8ed83c",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "기뻐하며-왕께-어노인팅-be0d08",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "기뻐하며-왕께-노래-부르리-마커스-markers-266787",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "기뻐하며-왕께-rejoice-to-the-king-찬양하세-praise-옹기장이-575670",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "생명-주께-있네-올네이션스-경배와찬양-1539e0",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "실로암-마커스워십-8a839b",
        "bpm": 132,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    # --- Rock / Straight 8th Upbeat fast songs ---
    {
        "slug": "거리마다-기쁨으로-어노인팅-12bd70",
        "min_bpm": 100,
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "주-예수의-이름-높이세-어노인팅-de57dd",
        "min_bpm": 100,
        "bpm": 116,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "주-예수의-이름-높이세-we-want-to-see-jesus-lifted-high-korean-version-옹기장이-94df00",
        "min_bpm": 100,
        "bpm": 116,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "살아계신-주-피아워십-53ee1c",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "할렐루야-살아계신-주-어노인팅-cf2a5f",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.88,
    },
    # --- Classic Ballads misclassified as 120-124 BPM fast by keyword heuristic ---
    {
        "slug": "사랑해요-목소리-높여-어노인팅-4f4063",
        "bpm": 68,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "나-주님의-기쁨-되기-원하네-김보라-98b4b3",
        "bpm": 72,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "왕이신-나의-하나님-달빛마을-4f7eb9",
        "bpm": 70,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.90,
    },
    {
        "slug": "하나님은-너를-지키시는-자-옹기장이-df8ecc",
        "worship_team_like": "%옹기장이%",
        "bpm": 72,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
]


def apply_curated_rhythm_updates(conn: sqlite3.Connection) -> dict[str, int]:
    """Apply curated rhythm/groove and BPM fixes to arrangements in worship.db."""
    rules_matched = 0
    arrangements_updated = 0

    for rule in CURATED_RHYTHM_RULES:
        row = conn.execute("SELECT id FROM songs WHERE song_slug = ?", (rule["slug"],)).fetchone()
        if not row:
            continue
        song_id = int(row[0])

        set_parts: list[str] = [
            "groove_style = ?",
            "bpm_source = 'curated_rhythm'",
            "confidence = MAX(confidence, ?)",
        ]
        params: list[Any] = [rule["groove_style"], float(rule.get("confidence", 0.90))]

        if "bpm" in rule:
            set_parts.append("bpm = ?")
            params.append(int(rule["bpm"]))
        if "tempo_tier" in rule:
            set_parts.append("tempo_tier = ?")
            params.append(str(rule["tempo_tier"]))

        where_parts: list[str] = ["song_id = ?"]
        where_params: list[Any] = [song_id]
        if "min_bpm" in rule:
            where_parts.append("bpm >= ?")
            where_params.append(int(rule["min_bpm"]))
        if "worship_team_like" in rule:
            where_parts.append("worship_team LIKE ?")
            where_params.append(str(rule["worship_team_like"]))

        sql = f"UPDATE arrangements SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}"
        cur = conn.execute(sql, params + where_params)
        if cur.rowcount > 0:
            rules_matched += 1
            arrangements_updated += cur.rowcount

    return {
        "rules_total": len(CURATED_RHYTHM_RULES),
        "rules_matched": rules_matched,
        "arrangements_updated": arrangements_updated,
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Apply curated rhythm/groove and BPM updates to worship.db")
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.worship_db))
    try:
        with conn:
            stats = apply_curated_rhythm_updates(conn)
        print(f"Curated rhythm update complete: {stats}")
        sample = conn.execute(
            """
            SELECT s.title_ko, a.worship_team, a.bpm, a.tempo_tier, a.groove_style, a.bpm_source
            FROM songs s JOIN arrangements a ON a.song_id = s.id
            WHERE s.song_slug IN (
                '우리-모두-찬양하세-옹기장이-94cf07',
                '기쁨으로-찬양-옹기장이-5a13cb',
                '하나님은-너를-지키시는-자-옹기장이-df8ecc'
            )
            ORDER BY s.song_slug, a.bpm DESC
            """
        ).fetchall()
        for row in sample:
            print(" ", tuple(row))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
