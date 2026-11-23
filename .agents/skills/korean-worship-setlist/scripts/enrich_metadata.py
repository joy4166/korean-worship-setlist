"""Normalize metadata, enforce copyright/heresy gates, and build E5 passage embeddings."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np

from common import (
    LOCAL_DB_PATH,
    WORSHIP_DB_PATH,
    configure_utf8_stdio,
    connect_dbs,
    is_blocked_source,
    json_dumps_ko,
    load_blocked_sources,
    normalize_scripture_ref,
    normalize_title,
    sync_songs_fts,
)

E5_MODEL_NAME = "intfloat/multilingual-e5-small"
E5_DIMENSION = 384


def build_passage_text(row: sqlite3.Row) -> str:
    """Format non-lyric structured semantics with the required E5 'passage: ' prefix."""
    tags = " ".join(json.loads(row["theme_tags"] or "[]"))
    refs = " ".join(json.loads(row["scripture_refs"] or "[]"))
    stages = " ".join(json.loads(row["liturgical_stages"] or "[]"))
    seasons = " ".join(json.loads(row["church_seasons"] or "[]"))
    parts = [
        row["title_ko"] or "",
        row["theme_summary"] or "",
        row["theological_core"] or "",
        row["caution_nuance"] or "",
        tags,
        refs,
        stages,
        seasons,
        row["theological_tone"] or "",
        row["lyrical_perspective"] or "",
    ]
    body = " | ".join(p.strip() for p in parts if p and p.strip())
    return f"passage: {body}"


def try_load_e5_model(download: bool = False) -> Any | None:
    """Load local sentence-transformers E5 model if installed and cached (or download requested)."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError:
        return None

    try:
        return SentenceTransformer(E5_MODEL_NAME, local_files_only=not download)
    except Exception as exc:
        if download:
            print(f"[warning] Failed to load/download E5 model {E5_MODEL_NAME}: {exc}", file=sys.stderr)
        return None


def enrich_and_index(
    worship_db_path: Path = WORSHIP_DB_PATH,
    local_db_path: Path = LOCAL_DB_PATH,
    download_model: bool = False,
) -> dict[str, Any]:
    """Validate/normalize songs, enforce blocked_sources gate, sync FTS5, and build embeddings."""
    conn = connect_dbs(worship_db_path, local_db_path, attach_local=False, ensure_schemas=True)
    gate = load_blocked_sources()
    rows = conn.execute("SELECT * FROM songs").fetchall()

    updated_count = 0
    blocked_count = 0
    with conn:
        for r in rows:
            incipit = (r["first_line_incipit"] or "").strip()
            if len(incipit) > 15:
                incipit = incipit[:15].rstrip()

            refs_raw = json.loads(r["scripture_refs"] or "[]")
            refs_norm = [normalize_scripture_ref(x) for x in refs_raw if x]
            norm_title = normalize_title(r["title_ko"])

            blocked, term = is_blocked_source(
                r["title_ko"],
                r["original_artist"],
                r["lyricist"],
                r["composer"],
                r["copyright_holder"],
                blocked=gate,
            )
            status = "blocked_heresy" if blocked else r["source_status"]
            if blocked:
                blocked_count += 1

            conn.execute(
                """
                UPDATE songs
                SET normalized_title = ?, first_line_incipit = ?, scripture_refs = ?,
                    source_status = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    norm_title,
                    incipit or None,
                    json_dumps_ko(refs_norm),
                    status,
                    r["id"],
                ),
            )
            updated_count += 1

        sync_songs_fts(conn)

    embedded_count = 0
    model = try_load_e5_model(download=download_model)
    if model is not None:
        fresh_rows = conn.execute("SELECT * FROM songs WHERE source_status != 'blocked_heresy'").fetchall()
        passages = [build_passage_text(r) for r in fresh_rows]
        if passages:
            vectors = model.encode(passages, normalize_embeddings=True)
            with conn:
                for row, vec in zip(fresh_rows, vectors):
                    arr = np.asarray(vec, dtype=np.float32)
                    conn.execute(
                        """
                        INSERT INTO song_embeddings (song_id, model_name, dimension, embedding)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(song_id) DO UPDATE SET
                            model_name = excluded.model_name,
                            dimension = excluded.dimension,
                            embedding = excluded.embedding
                        """,
                        (row["id"], E5_MODEL_NAME, int(arr.shape[0]), arr.tobytes()),
                    )
                    embedded_count += 1

    conn.close()
    return {
        "songs_validated": updated_count,
        "blocked_heresy": blocked_count,
        "fts_synced": True,
        "embeddings_written": embedded_count,
        "embedding_mode": "e5_local" if model is not None else "fts5_fallback",
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Enrich metadata, sync trigram FTS5, and build E5 embeddings")
    parser.add_argument("--worship-db", type=Path, default=WORSHIP_DB_PATH)
    parser.add_argument("--download-model", action="store_true", help="Download E5 model if not cached locally")
    args = parser.parse_args()

    res = enrich_and_index(worship_db_path=args.worship_db, download_model=args.download_model)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
