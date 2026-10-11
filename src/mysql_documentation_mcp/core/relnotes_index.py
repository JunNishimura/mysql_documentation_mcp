"""SQLite FTS5 index of release-notes items, one file per series directory (§5.5).

There is no archive of the release notes (only PDF), so the index is built from the
``news-X-Y-Z.html`` pages of one directory (e.g. ``8.4/``, which also holds 8.1-8.3).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mysql_documentation_mcp.core.indexer import query_terms
from mysql_documentation_mcp.core.relnotes_parser import ReleasePage

SCHEMA_VERSION = 1
# bm25 weights: version, series, date, category, url (unindexed), category_title, flags, text.
_BM25_WEIGHTS = "0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 4.0, 1.0"
# bm25 scores are negative (lower is better); this scales Bugs Fixed items toward zero.
_BUG_WEIGHT = 0.7


@dataclass(frozen=True)
class RelnotesIndexMeta:
    directory: str
    built_at: float
    releases: tuple[str, ...]  # versions indexed
    items: int


@dataclass(frozen=True)
class RelnotesHit:
    version: str
    date: str
    category: str  # slug
    category_title: str
    url: str  # page URL with the category anchor
    flags: str
    snippet: str


def build_relnotes_index(
    path: Path, directory: str, pages: Iterable[ReleasePage], reuse: Iterable[str] = ()
) -> int:
    """Write the index for ``directory`` atomically; returns the number of items.

    ``reuse`` names releases whose items are copied from the existing index at ``path``
    instead of being parsed again (published release notes rarely change, so a new
    release only needs its own page).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    versions: list[str] = []
    count = 0
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE VIRTUAL TABLE items USING fts5(
              version UNINDEXED, series UNINDEXED, date UNINDEXED, category UNINDEXED,
              url UNINDEXED, category_title, flags, text,
              tokenize = "porter unicode61 tokenchars '_'"
            );
            """
        )
        reuse = list(reuse)
        if reuse and path.exists():
            conn.execute("ATTACH DATABASE ? AS old", (str(path),))
            for version in reuse:
                copied = conn.execute(
                    "INSERT INTO items SELECT * FROM old.items WHERE version = ?", (version,)
                ).rowcount
                versions.append(version)
                count += copied
            conn.commit()
            conn.execute("DETACH DATABASE old")
        for page in pages:
            versions.append(page.version)
            series = ".".join(page.version.split(".")[:2])
            conn.executemany(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.version,
                        series,
                        item.date,
                        item.category.slug,
                        item.url,
                        item.category.title,
                        "; ".join(item.flags),
                        item.text,
                    )
                    for item in page.items
                ],
            )
            count += len(page.items)
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("schema", str(SCHEMA_VERSION)),
                ("directory", directory),
                ("built_at", str(time.time())),
                ("releases", json.dumps(versions)),
                ("items", str(count)),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    return count


def read_relnotes_meta(path: Path) -> RelnotesIndexMeta | None:
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema") != str(SCHEMA_VERSION):
            return None
        return RelnotesIndexMeta(
            directory=meta["directory"],
            built_at=float(meta["built_at"]),
            releases=tuple(json.loads(meta["releases"])),
            items=int(meta["items"]),
        )
    except (sqlite3.Error, KeyError, ValueError):
        return None


def search_relnotes(
    path: Path, query: str, limit: int, series: str | None = None
) -> list[RelnotesHit]:
    """Matching items, best first; newer releases win ties."""
    terms = query_terms(query)
    if not terms:
        return []
    expressions = [" AND ".join(terms)]
    if len(terms) > 1:
        expressions.append(" OR ".join(terms))
    hits: dict[int, RelnotesHit] = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        for expression in expressions:
            sql = f"""
                SELECT version, date, category, category_title, url, flags,
                       snippet(items, 7, '**', '**', '...', 24),
                       bm25(items, {_BM25_WEIGHTS}) AS score, rowid
                FROM items WHERE items MATCH ?
            """
            params: list[object] = [expression]
            if series:
                sql += " AND series = ?"
                params.append(series)
            # Bug fixes are numerous and usually not what is asked for ("when did X
            # change"), so they rank a little lower than other items.
            sql += (
                " ORDER BY score * (CASE category WHEN 'bug' THEN ? ELSE 1.0 END), rowid DESC"
                " LIMIT ?"
            )
            params.append(_BUG_WEIGHT)
            params.append(limit * 4)
            for version, date, cat, cat_title, url, flags, snip, _score, rowid in conn.execute(
                sql, params
            ):
                if rowid not in hits:
                    hits[rowid] = RelnotesHit(
                        version=version,
                        date=date,
                        category=cat,
                        category_title=cat_title,
                        url=url,
                        flags=flags,
                        snippet=re.sub(r"\s+", " ", snip).strip(),
                    )
            if len(hits) >= limit:
                break
    return list(hits.values())[:limit]
