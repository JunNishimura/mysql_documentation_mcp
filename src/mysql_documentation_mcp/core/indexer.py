"""SQLite FTS5 search index of the reference manual (docs/02_design.md §4.3, §4.4)."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mysql_documentation_mcp.core.info import Node, parse_info
from mysql_documentation_mcp.core.urls import refman_url

SCHEMA_VERSION = 1
# Anchor prefixes and the kind of reference entry they mark.
_ANCHOR_PREFIXES = (
    ("sysvar_", "system variable"),
    ("statvar_", "status variable"),
    ("option_", "option"),
    ("function_", "function"),
    ("operator_", "operator"),
    ("priv_", "privilege"),
    ("optflag_", "optimizer flag"),
)
# bm25 weights per column: page_id, anchor (unindexed), title, heading, identifiers, body.
_BM25_WEIGHTS = "0.0, 0.0, 8.0, 6.0, 10.0, 1.0"
_TOKEN_RE = re.compile(r"[\w]+(?:[.$][\w]+)*", re.UNICODE)
_PHRASE_RE = re.compile(r'"([^"]+)"')
# Common English words dropped from queries so that "how to ..." questions still match
# with all remaining terms required.
_STOPWORDS = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for"),
        *("from", "how", "i", "in", "is", "it", "of", "on", "or", "should", "the", "to"),
        *("what", "when", "which", "with"),
    }
)


@dataclass(frozen=True)
class IndexMeta:
    version: str
    built_at: float
    source_last_modified: str | None
    pages: int
    chunks: int


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    section_number: str
    breadcrumb: str
    heading: str | None  # matching sub-section or reference entry, None if the page intro
    snippet: str


def _anchor_identifiers(anchor: str) -> list[str]:
    for prefix, _kind in _ANCHOR_PREFIXES:
        if anchor.startswith(prefix):
            name = anchor[len(prefix) :]
            if prefix == "option_":
                # option_mysqld_sql-mode -> "sql-mode", "--sql-mode"; only server options
                # also get the system-variable spelling "sql_mode".
                program, _, name = name.partition("_")
                names = [name, f"--{name}"]
                return [*names, name.replace("-", "_")] if program == "mysqld" else names
            return [name, name.replace("-", "_")]
    return []


def _assign_index_terms(node: Node) -> list[list[str]]:
    """Attach each index term to the chunk it names, else to the page intro.

    ``"sql_mode system variable"`` belongs to the ``sysvar_sql_mode`` entry, not to the
    intro of server-system-variables.html (which would otherwise collect ~300 terms).
    """
    by_identifier: dict[str, int] = {}
    for i, chunk in enumerate(node.chunks):
        for name in _anchor_identifiers(chunk.anchor) if chunk.anchor else []:
            by_identifier.setdefault(name.casefold(), i)
    terms: list[list[str]] = [[] for _ in node.chunks]
    if not terms:
        return terms
    for term in node.index_terms:
        first = term.split(" ", 1)[0].strip("'\"(),").casefold()
        terms[by_identifier.get(first, 0)].append(term)
    return terms


def _breadcrumbs(nodes: list[Node]) -> dict[str, str]:
    by_name = {n.name: n for n in nodes}
    result: dict[str, str] = {}
    for node in nodes:
        titles: list[str] = []
        parent = by_name.get(node.up) if node.up else None
        seen = set()
        while parent is not None and parent.name != "Top" and parent.name not in seen:
            seen.add(parent.name)
            titles.append(parent.title)
            parent = by_name.get(parent.up) if parent.up else None
        result[node.name] = " > ".join(reversed(titles))
    return result


def build_index(
    path: Path, version: str, info_data: bytes, source_last_modified: str | None
) -> IndexMeta:
    """Parse the Info manual and (re)write the index at ``path`` atomically."""
    nodes = parse_info(info_data)
    crumbs = _breadcrumbs(nodes)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE pages (
              id INTEGER PRIMARY KEY, node TEXT UNIQUE, url TEXT, title TEXT,
              section_number TEXT, breadcrumb TEXT, parent TEXT, toc_order INTEGER
            );
            CREATE TABLE anchors (name TEXT, kind TEXT, anchor TEXT, page_id INTEGER);
            CREATE VIRTUAL TABLE chunks USING fts5(
              page_id UNINDEXED, anchor UNINDEXED, title, heading, identifiers, body,
              tokenize = "porter unicode61 tokenchars '_'"
            );
            """
        )
        n_chunks = 0
        for node in nodes:
            title = node.title if node.name != "Top" else f"MySQL {version} Reference Manual"
            cur = conn.execute(
                "INSERT INTO pages (node, url, title, section_number, breadcrumb, parent,"
                " toc_order) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    node.name,
                    refman_url(version, node.page),
                    title,
                    node.section_number,
                    crumbs[node.name],
                    node.up,
                    node.order,
                ),
            )
            page_id = cur.lastrowid
            for anchor in node.anchors:
                kind = next((k for p, k in _ANCHOR_PREFIXES if anchor.startswith(p)), "section")
                for name in _anchor_identifiers(anchor) or [anchor]:
                    conn.execute(
                        "INSERT INTO anchors VALUES (?, ?, ?, ?)",
                        (name.casefold(), kind, anchor, page_id),
                    )
            chunk_terms = _assign_index_terms(node)
            for i, chunk in enumerate(node.chunks):
                identifiers = [node.name.replace("-", " ")]
                if chunk.anchor:
                    identifiers += _anchor_identifiers(chunk.anchor) or [
                        chunk.anchor.replace("-", " ")
                    ]
                identifiers += chunk_terms[i]
                row = (page_id, chunk.anchor, title, chunk.heading, " ".join(identifiers))
                conn.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)", (*row, chunk.text))
                n_chunks += 1
        meta = IndexMeta(
            version=version,
            built_at=time.time(),
            source_last_modified=source_last_modified,
            pages=len(nodes),
            chunks=n_chunks,
        )
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("schema", str(SCHEMA_VERSION)),
                ("version", version),
                ("built_at", str(meta.built_at)),
                ("source_last_modified", source_last_modified or ""),
                ("pages", str(meta.pages)),
                ("chunks", str(meta.chunks)),
            ],
        )
        conn.execute("INSERT INTO chunks(chunks) VALUES ('optimize')")
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    return meta


def read_meta(path: Path) -> IndexMeta | None:
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            values = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return None
    if values.get("schema") != str(SCHEMA_VERSION):
        return None
    return IndexMeta(
        version=values["version"],
        built_at=float(values["built_at"]),
        source_last_modified=values.get("source_last_modified") or None,
        pages=int(values["pages"]),
        chunks=int(values["chunks"]),
    )


def touch_meta(path: Path) -> None:
    """Record that the source was re-checked and is unchanged."""
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'built_at'", (str(time.time()),))


# --------------------------------------------------------------------------- queries


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def query_terms(query: str) -> list[str]:
    """Split a free-text query into FTS5 terms: quoted phrases and single words.

    Words keep ``_`` (``sql_mode``) and are matched as phrases, so FTS5 syntax
    characters in the query can never produce a syntax error.
    """
    terms: list[str] = []
    for phrase in _PHRASE_RE.findall(query):
        words = _TOKEN_RE.findall(phrase)
        if words:
            terms.append(_quote(" ".join(words)))
    rest = _PHRASE_RE.sub(" ", query)
    for word in _TOKEN_RE.findall(rest):
        if word.casefold() in _STOPWORDS:
            continue
        if "_" in word:
            # Match both the identifier and its words in running text.
            spaced = word.replace("_", " ").strip()
            terms.append(f"({_quote(word)} OR {_quote(spaced)})")
        else:
            terms.append(_quote(word))
    return list(dict.fromkeys(terms))


def search(path: Path, query: str, limit: int) -> list[SearchHit]:
    terms = query_terms(query)
    if not terms:
        return []
    hits: dict[int, SearchHit] = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        # All terms first; fill up with any-term matches when that finds too few pages.
        for expression in _expressions(terms):
            for hit_page, hit in _run(conn, expression, limit * 20):
                if hit_page not in hits:
                    hits[hit_page] = hit
            if len(hits) >= limit:
                break
    return list(hits.values())[:limit]


def _expressions(terms: list[str]) -> Iterable[str]:
    yield " AND ".join(terms)
    if len(terms) > 1:
        yield " OR ".join(terms)


def _run(conn: sqlite3.Connection, expression: str, max_rows: int) -> list[tuple[int, SearchHit]]:
    rows = conn.execute(
        f"""
        SELECT c.page_id, c.anchor, c.heading,
               snippet(chunks, 5, '**', '**', '...', 24) AS snip,
               bm25(chunks, {_BM25_WEIGHTS}) AS score,
               p.url, p.title, p.section_number, p.breadcrumb
        FROM chunks c JOIN pages p ON p.id = c.page_id
        WHERE chunks MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (expression, max_rows),
    ).fetchall()
    out: list[tuple[int, SearchHit]] = []
    for page_id, anchor, heading, snip, _score, url, title, number, crumb in rows:
        out.append(
            (
                page_id,
                SearchHit(
                    title=title,
                    url=f"{url}#{anchor}" if anchor else url,
                    section_number=number,
                    breadcrumb=crumb,
                    heading=heading if anchor else None,
                    snippet=re.sub(r"\s+", " ", snip).strip(),
                ),
            )
        )
    return out
