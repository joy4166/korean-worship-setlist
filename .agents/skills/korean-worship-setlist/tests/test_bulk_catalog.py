"""Integrity and performance tests for the bulk 3,000+ song Korean worship catalog in worship.db."""

from __future__ import annotations

import sys
import time
import unicodedata
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import WORSHIP_DB_PATH, connect_dbs
from search_candidates import search_setlist_candidates


def test_bulk_catalog_integrity_and_search_speed(tmp_path: Path) -> None:
    if not WORSHIP_DB_PATH.exists():
        pytest.skip("worship.db not found")

    conn = connect_dbs(WORSHIP_DB_PATH, attach_local=False, ensure_schemas=False)
    total_songs = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    total_arrs = conn.execute("SELECT COUNT(*) FROM arrangements").fetchone()[0]

    assert total_songs >= 3000, f"Expected >= 3000 songs, got {total_songs}"
    assert total_arrs >= 3800, f"Expected >= 3800 arrangements, got {total_arrs}"

    # All 645 New Hymnal entries (1..645) must exist
    hymn_nums = {
        r[0]
        for r in conn.execute(
            "SELECT hymn_number FROM songs WHERE category = 'hymn' AND hymn_number BETWEEN 1 AND 645"
        ).fetchall()
    }
    assert len(hymn_nums) == 645, f"Missing hymns: {sorted(set(range(1, 646)) - hymn_nums)[:10]}"

    # Every title, summary, and artist must be canonical NFC (no macOS NFD Jamo decomposition)
    text_rows = conn.execute(
        "SELECT id, title_ko, theme_summary, COALESCE(first_line_incipit, '') AS incipit FROM songs"
    ).fetchall()
    nfd_violations = [
        r["id"]
        for r in text_rows
        if r["title_ko"] != unicodedata.normalize("NFC", r["title_ko"])
        or r["theme_summary"] != unicodedata.normalize("NFC", r["theme_summary"])
        or len(r["incipit"]) > 15
    ]
    assert nfd_violations == []

    # Zero blocked_heresy entries leaked into active catalog
    heresy_cnt = conn.execute("SELECT COUNT(*) FROM songs WHERE source_status = 'blocked_heresy'").fetchone()[0]
    assert heresy_cnt == 0
    conn.close()

    # Search latency across 3,000+ songs must stay under 1.0 second
    temp_local = tmp_path / "church_local.db"
    t0 = time.perf_counter()
    res = search_setlist_candidates(
        service_type="youth",
        theme="은혜와 광야의 인도하심",
        scripture="시편 23편",
        worship_db_path=WORSHIP_DB_PATH,
        local_db_path=temp_local,
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"Search took {elapsed:.3f}s (>= 1.0s)"
    assert len(res["pre_sermon"]) == 4
    assert "key_verified" in res["pre_sermon"][0]["primary"]
    assert "key_source" in res["pre_sermon"][0]["primary"]
