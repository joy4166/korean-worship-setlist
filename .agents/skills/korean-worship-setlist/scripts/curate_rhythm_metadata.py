"""Pinpoint rhythm/groove & BPM curation for iconic Korean worship songs in worship.db."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from common import WORSHIP_DB_PATH, configure_utf8_stdio, json_dumps_ko, normalize_title


# Each entry updates matching arrangements in worship.db cleanly and idempotently.
# Lookup tries `slug`, then optional `hymn_number`, then fallback `title` (normalized_title).
CURATED_RHYTHM_RULES: list[dict[str, Any]] = [
    # --- Shuffle / Bounce fast songs (triplet subdivision feel) ---
    {
        "slug": "우리-모두-찬양하세-옹기장이-94cf07",
        "title": "우리 모두 찬양하세",
        "min_bpm": 100,
        "bpm": 124,
        "tempo_tier": "fast",
        "groove_style": "shuffle_bounce",
        "confidence": 0.90,
    },
    {
        "slug": "오늘-집을-나서기-전-복음성가-모음-508606",
        "title": "오늘 집을 나서기 전",
        "bpm": 120,
        "tempo_tier": "fast",
        "groove_style": "shuffle_bounce",
        "confidence": 0.88,
    },
    {
        "slug": "내가-매일-기쁘게-한국찬송가-191",
        "title": "내가 매일 기쁘게",
        "hymn_number": 191,
        "bpm": 118,
        "tempo_tier": "fast",
        "groove_style": "shuffle_bounce",
        "confidence": 0.90,
    },
    {
        "slug": "주와-같이-길-가는-것-한국찬송가-430",
        "title": "주와 같이 길 가는 것",
        "hymn_number": 430,
        "bpm": 108,
        "tempo_tier": "medium",
        "groove_style": "shuffle_bounce",
        "confidence": 0.90,
    },
    {
        "slug": "내-영혼이-은총-입어-hymn-438-나무엔-004903",
        "title": "내 영혼이 은총 입어",
        "hymn_number": 438,
        "min_bpm": 100,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-438-bcc185",
        "hymn_number": 438,
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-405-90a64a",
        "hymn_number": 405,
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-091-f4b9c8",
        "hymn_number": 91,
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    {
        "slug": "hymn-183-17910f",
        "hymn_number": 183,
        "min_bpm": 110,
        "groove_style": "shuffle_bounce",
        "confidence": 0.86,
    },
    # --- 16th Upbeat / Syncopated Straight fast songs ---
    {
        "slug": "기쁨으로-찬양-옹기장이-5a13cb",
        "title": "기쁨으로 찬양",
        "bpm": 100,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "나는-주만-높이리-마커스-markers-55d9ac",
        "title": "나는 주만 높이리",
        "bpm": 106,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "기쁨의-노래-어노인팅-1aff27",
        "title": "기쁨의 노래",
        "min_bpm": 100,
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "나-기쁨의-노래하리-어노인팅-33ea06",
        "title": "나 기쁨의 노래하리",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "기쁨으로-김윤진-f7563d",
        "title": "기쁨으로",
        "bpm": 120,
        "tempo_tier": "fast",
        "groove_style": "16th_upbeat",
        "confidence": 0.88,
    },
    # --- Country / Go-Go 2-beat fast songs ---
    {
        "slug": "우리-모두-손뼉-치며-함께-옹기장이-8ed83c",
        "title": "우리 모두 손뼉 치며 함께",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "기뻐하며-왕께-어노인팅-be0d08",
        "title": "기뻐하며 왕께",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "기뻐하며-왕께-노래-부르리-마커스-markers-266787",
        "title": "기뻐하며 왕께 노래 부르리",
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
        "title": "생명 주께 있네",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "실로암-마커스워십-8a839b",
        "title": "실로암",
        "bpm": 132,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "오-자유-마커스워십-free",
        "title": "오 자유",
        "bpm": 120,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "죄에서-자유를-얻게-함은-새찬송가-268",
        "title": "죄에서 자유를 얻게 함은",
        "hymn_number": 268,
        "bpm": 116,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    {
        "slug": "주의-보혈-능력있도다-새찬송가-268",
        "title": "주의 보혈 능력있도다",
        "bpm": 118,
        "tempo_tier": "fast",
        "groove_style": "country_gogo",
        "confidence": 0.90,
    },
    # --- Rock / Straight 8th Upbeat fast songs ---
    {
        "slug": "주-이름-찬양-마커스워십-blessed-be",
        "title": "주 이름 찬양",
        "bpm": 118,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "나는-자유해-어노인팅-i-am-free",
        "title": "나는 자유해",
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.90,
    },
    {
        "slug": "거리마다-기쁨으로-어노인팅-12bd70",
        "title": "거리마다 기쁨으로",
        "min_bpm": 100,
        "bpm": 126,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "주-예수의-이름-높이세-어노인팅-de57dd",
        "title": "주 예수의 이름 높이세",
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
        "title": "살아계신 주",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.88,
    },
    {
        "slug": "할렐루야-살아계신-주-어노인팅-cf2a5f",
        "title": "할렐루야 살아계신 주",
        "bpm": 122,
        "tempo_tier": "fast",
        "groove_style": "rock_upbeat",
        "confidence": 0.88,
    },
    # --- Classic Ballads protected from keyword false-positives ---
    {
        "slug": "사랑해요-목소리-높여-어노인팅-4f4063",
        "title": "사랑해요 목소리 높여",
        "bpm": 68,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "나-주님의-기쁨-되기-원하네-김보라-98b4b3",
        "title": "나 주님의 기쁨 되기 원하네",
        "bpm": 72,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "왕이신-나의-하나님-달빛마을-4f7eb9",
        "title": "왕이신 나의 하나님",
        "bpm": 70,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.90,
    },
    {
        "slug": "하나님은-너를-지키시는-자-옹기장이-df8ecc",
        "title": "하나님은 너를 지키시는 자",
        "worship_team_like": "%옹기장이%",
        "bpm": 72,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "거룩하신-하나님-어노인팅-give-thanks",
        "title": "거룩하신 하나님",
        "bpm": 68,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "예수-우리-왕이여-마커스워십-jesus-we-enthrone-you",
        "title": "예수 우리 왕이여",
        "bpm": 74,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.90,
    },
    {
        "slug": "꽃들도-마커스워십-hanamo",
        "title": "꽃들도",
        "bpm": 72,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
    {
        "slug": "전능하신-나의-주-하나님은-마커스워십-almighty",
        "title": "전능하신 나의 주 하나님은",
        "bpm": 68,
        "tempo_tier": "ballad",
        "groove_style": "16th_ballad",
        "confidence": 0.92,
    },
]


def _resolve_rule_song_ids(conn: sqlite3.Connection, rule: dict[str, Any]) -> list[int]:
    """Resolve target song_id(s) by slug, then hymn_number, then fallback normalized title."""
    slug = rule.get("slug")
    if slug:
        rows = conn.execute("SELECT id FROM songs WHERE song_slug = ?", (slug,)).fetchall()
        if rows:
            return [int(r[0]) for r in rows]
    hymn_num = rule.get("hymn_number")
    if hymn_num is not None:
        rows = conn.execute("SELECT id FROM songs WHERE hymn_number = ?", (int(hymn_num),)).fetchall()
        if rows:
            return [int(r[0]) for r in rows]
    title = rule.get("title")
    if title:
        rows = conn.execute("SELECT id FROM songs WHERE normalized_title = ?", (normalize_title(title),)).fetchall()
        if rows:
            return [int(r[0]) for r in rows]
    return []


def _reconcile_song_stages_for_tier(conn: sqlite3.Connection, song_id: int) -> None:
    """Reconcile songs.liturgical_stages and tone when all arrangements share a unified ballad or fast tier."""
    tiers = {
        str(r[0])
        for r in conn.execute("SELECT DISTINCT tempo_tier FROM arrangements WHERE song_id = ?", (song_id,)).fetchall()
        if r[0]
    }
    if tiers not in ({"ballad"}, {"fast"}):
        return
    song_row = conn.execute(
        "SELECT liturgical_stages, service_slots, theological_tone FROM songs WHERE id = ?",
        (song_id,),
    ).fetchone()
    if not song_row:
        return
    try:
        stages: list[str] = json.loads(song_row[0] or "[]")
    except Exception:
        stages = []
    try:
        slots: list[str] = json.loads(song_row[1] or "[]")
    except Exception:
        slots = []
    tone = str(song_row[2] or "adoration")

    if tiers == {"ballad"}:
        if "introit_doxology" not in slots:
            stages = [st for st in stages if st != "opener"]
        if not any(st in ("meditation", "confession", "dedication") for st in stages):
            stages.append("meditation")
        conn.execute(
            "UPDATE songs SET liturgical_stages = ? WHERE id = ?",
            (json_dumps_ko(stages), song_id),
        )
    elif tiers == {"fast"}:
        stages = [st for st in stages if st not in ("meditation", "confession")]
        for req in ("declaration", "opener"):
            if req in stages:
                stages.remove(req)
            stages.insert(0, req)
        if tone in {"penitence", "comfort_trust"}:
            tone = "adoration"
        conn.execute(
            "UPDATE songs SET liturgical_stages = ?, theological_tone = ? WHERE id = ?",
            (json_dumps_ko(stages), tone, song_id),
        )


def apply_curated_rhythm_updates(conn: sqlite3.Connection) -> dict[str, int]:
    """Apply curated rhythm/groove and BPM fixes to arrangements in worship.db."""
    rules_matched = 0
    arrangements_updated = 0

    for rule in CURATED_RHYTHM_RULES:
        song_ids = _resolve_rule_song_ids(conn, rule)
        if not song_ids:
            continue

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

        rule_hit = False
        for song_id in song_ids:
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
                rule_hit = True
                arrangements_updated += cur.rowcount
                if "tempo_tier" in rule:
                    _reconcile_song_stages_for_tier(conn, song_id)
        if rule_hit:
            rules_matched += 1

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
