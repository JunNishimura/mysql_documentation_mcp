# How it works

This page explains how the server finds, reads and caches the MySQL documentation, and
the dev.mysql.com behaviors it has to work around. It is meant for users who want to know
where answers come from, and for contributors.

## Overview

```
MCP client (Claude Code, Claude Desktop, ...)
        │  stdio (JSON-RPC)
        ▼
mysql-documentation-mcp
  tools/   search_documentation, list_versions, read_documentation, read_sections,
           get_release_notes
  core/    urls        URL allowlist, normalization, version mismatch detection
           fetcher     HTTP client: allowlist, redirect checks, User-Agent, rate limit, retries
           cache       on-disk page cache (gzip HTML)
           converter   HTML -> Markdown, table of contents, section extraction
           versions    version parsing, discovery, LTS / Innovation tracks
           info        parser for the manual's GNU Info archive
           indexer     SQLite FTS5 index of the Reference Manual
           relnotes_*  release notes parser and SQLite FTS5 index
        │  HTTPS (allowlisted hosts only)
        ▼
dev.mysql.com, downloads.mysql.com
```

All documentation text is fetched from the official site at run time and stored only in the
local cache directory. Nothing from the documentation is bundled with this package.

## Scope

- **MySQL Reference Manual** (English): `https://dev.mysql.com/doc/refman/{version}/en/`.
  Published versions at the time of writing: 8.0, 8.4 (LTS), 9.7 (LTS) and 26.7.
- **MySQL Server release notes**: `https://dev.mysql.com/doc/relnotes/mysql/{series}/en/`.
- Not covered: MySQL Shell, Router, Connectors, NDB Cluster, the Error Message Reference,
  and translated manuals. URLs outside the two document sets above are rejected.

## Versions

- Tools accept `"8.4"`, `"8.4.6"` (rounded to the `8.4` manual), `"lts"` and `"latest"`.
  Without a version, `MYSQL_DOC_DEFAULT_VERSION` is used (default: the latest LTS).
- The list of published manuals and release notes series is read from
  `https://dev.mysql.com/doc/` and each series' release notes index, and cached for a day.
  If the site cannot be reached, a bundled list is used.
- The latest LTS is taken from where `https://dev.mysql.com/doc/refman/en/` redirects.
- Whether a series is LTS or Innovation cannot be read from the site (calendar versions do
  not tell, and the release notes do not say). Known series come from a bundled table; a
  series whose release notes live in another series' directory is Innovation; a series with
  only Early Access releases is reported as such.
- **Retired versions are refused, not substituted.** The site redirects retired manuals
  (5.7, the Innovation manuals 8.1-8.3 and 9.0-9.6) to another version: for example
  `/doc/refman/5.7/en/select.html` serves the 9.7 page. Returning that page as if it were
  5.7 would give wrong answers, so the server returns an error that names the version the
  site redirected to.
- **Innovation release notes live in the next LTS directory**: 8.1.0-8.3.0 are under `8.4/`,
  9.0.0-9.6.0 under `9.7/`. Release URLs are therefore looked up from the series indexes,
  not guessed. A release can be listed in two directories (the `26.10/` index also lists
  26.7.x); the copy in its own series' directory is used.

## Searching the Reference Manual

There is no public search API that can be limited to a version (the site search only returns
pages of the newest manual), so the server builds a local full-text index per version.

- **Source**: the official GNU Info archive, `https://downloads.mysql.com/docs/mysql-{version}.info.zip`
  (about 4 MB; there is no HTML archive). Info node names match the HTML page names
  (`select` -> `select.html`), node headers give the table of contents, and the tag table gives
  the byte position of every anchor (`sysvar_sql_mode`, `option_mysqld_port`,
  `function_json-extract`, ...).
- **Chunks**: each page is split at its anchors, so a sub-section or a reference entry such as
  a system variable is one chunk and results can link to `page.html#anchor`. The 8.4 manual
  has about 2,400 pages and 9,100 chunks.
- **Index**: SQLite FTS5 with the `porter unicode61 tokenchars '_'` tokenizer, so `sql_mode`
  is one token. Column weights: identifiers (10) > title (8) > heading (6) > body (1).
  Identifiers include names derived from anchors (`sysvar_sql_mode` -> `sql_mode`;
  `option_mysqld_sql-mode` -> `sql-mode`, `--sql-mode`) and the manual's index terms.
- **Queries**: words are quoted (FTS5 operators in a query can never cause a syntax error),
  common English words are dropped, and `"double quoted"` text is a phrase. Results that
  contain all terms come first; if there are too few, results containing any term are added.
  One result is returned per page.
- **Freshness**: the archive's `Last-Modified` is stored. After `MYSQL_DOC_INDEX_TTL_DAYS`
  (default 7) a conditional request checks for a new archive and rebuilds only if it changed.
- Snippets come from the Info text, where placeholders in syntax are upper case
  (`TBL_NAME`). Read the page itself for the exact syntax.

## Reading pages

- Pages are fetched as HTML and converted to Markdown. The content is in `#docs-body`
  (breadcrumbs, version switcher and footer are outside it).
- Code blocks keep their language; syntax blocks are kept verbatim. Relative links become
  absolute. Notes and warnings become `> **Note:**` quotes.
- Reference entries (system variables, options, status variables) are list items that start
  with an anchor and a property table (Command-Line Format, Scope, Dynamic, Type, Default
  Value, ...). The property table becomes a Markdown table.
- `read_documentation` starts with a short table of contents (headings and anchors) and
  paginates with `start_index` / `max_length`.
- `read_sections` returns one section by heading title or anchor. A heading section runs to
  the next heading of the same or a higher level (some pages have flat headings, so sections
  are cut in document order). An anchor of a reference entry returns that entry. Anchors are
  matched ignoring case and `_` / `-` differences, because function anchors use hyphens
  (`function_json-extract`) and are often guessed with underscores.

## Release notes

- Each release page (`news-X-Y-Z.html`) is split into categories, one per `div.simplesect`
  with an anchor `mysqld-X-Y-Z-{slug}` (`deprecation-removal`, `feature`, `security`, `bug`,
  `innodb`, `replication`, ...). Each top-level `li.listitem` is one item; nested lists stay
  inside their item. Items have no anchors of their own, so citations point to the category.
- A bold prefix at the start of an item (`Important Change:`, `Incompatible Change;
  Group Replication:`) is kept as flags; `important_only` keeps items flagged Important
  Change, Incompatible Change or Important Note.
- Some releases have no list of changes (8.4.12 only updated the Docker image); their note
  is shown instead.
- `get_release_notes` reads every release after `from_version` up to `to_version` (at most 50
  per call, one request each). When the range crosses release series, the output points to
  the target manual's "What Is New" and "Upgrading MySQL" pages, which summarize series
  changes better than the release notes do.
- **Release notes search** (`search_documentation(doc_set="relnotes")`): there is no archive of
  the release notes (only PDF), so the index of a series directory is built from its release
  pages (41 pages for 8.0, about 20 seconds the first time). Published notes rarely change, so
  later updates fetch only releases that are new in the series index. Columns: flags (4) >
  category title (2) > text (1); Bugs Fixed items are ranked a little lower, because "when did
  X change" questions are usually about the change, not its fixes.

## Network behavior

- **Allowlist**: only `https://dev.mysql.com/doc/refman/...`, `https://dev.mysql.com/doc/relnotes/mysql/...`
  (English) and `https://downloads.mysql.com/docs/...` are fetched. Redirect targets are
  checked against the same list.
- **User-Agent**: dev.mysql.com and downloads.mysql.com answer 403 to many User-Agents,
  including httpx's default, bare product tokens and browser-like strings, while standard
  clients (`Python-urllib`, `python-requests`, `curl`, `Wget`) are accepted. The default is
  `Python-urllib/<python version> mysql-documentation-mcp/<version> (+<repository URL>)`:
  a standard token first, then this server's own token so requests stay identifiable. It does
  not imitate a browser. Override it with `MYSQL_DOC_USER_AGENT` if you still get 403.
- **Politeness**: at most 2 requests per second per host; 429 / 502 / 503 / 504 and network
  errors are retried up to 3 times with backoff. `HTTPS_PROXY` and similar variables are
  honored.

## Security

The server only reads public documentation, but its output goes straight into an AI agent,
and it makes network requests on the user's machine. These measures limit what can go wrong:

**Untrusted content (prompt injection)**
- Text that is not visible documentation is removed before conversion: `script`, `style`,
  `noscript`, `template`, `iframe`, `object`, `embed`, `svg`, form elements, HTML comments, and
  elements hidden with `hidden`, `aria-hidden="true"`, `display: none` or `visibility: hidden`.
  Hidden text therefore cannot carry instructions to the agent.
- Links are kept only with `http`, `https` or `mailto` URLs; `javascript:`, `data:` and other
  schemes are reduced to their text.
- Images are never output as Markdown images, which many clients load automatically (a
  common way to leak data through an attacker-chosen URL). Images on dev.mysql.com become
  plain links with their alt text; images elsewhere are replaced by their alt text.
- The server instructions tell the agent to treat returned text as reference material and
  never to follow instructions that appear in it.
- Content is passed on without summarizing or rewriting; the server never interprets or
  executes it.

**Requests (SSRF and unwanted downloads)**
- Only allowlisted URLs are requested (see Network behavior), and every redirect target is
  checked against the same list. On downloads.mysql.com only `mysql-X.Y.info.zip` is allowed.
  Tool arguments cannot name a host, port, credentials or query string.
- Response bodies are streamed and refused beyond 32 MB, and the Info archive is refused if
  it would decompress to more than 200 MB (zip bomb protection).
- Tool arguments have length limits (URLs 500 characters, versions 20, section names 200,
  keywords 200).

**Load on dev.mysql.com**
- Requests to a host are spaced at least 0.5 seconds apart across all tool calls of a server
  process, retries are limited (3, with backoff, `Retry-After` capped at 30 seconds), and
  pages are cached for 7 days.
- One `get_release_notes` call reads at most 50 release pages; a release notes index is
  built once per series and later only fetches new releases; the version list is read once
  a day.

**Local data**
- Only the cache directory is written. File names are SHA-1 hashes of URLs or validated
  version numbers. All SQL uses parameters, and search terms are quoted before they reach
  FTS5, so queries cannot change the SQL or the FTS5 expression.

## Cache

The cache directory is the OS cache directory (`~/.cache/mysql-documentation-mcp/` on Linux)
or `MYSQL_DOC_CACHE_DIR`. Everything in it can be deleted; it is rebuilt on demand.

| Content | Location | Size |
|---|---|---|
| Reference Manual search index | `index/refman-{version}-en.sqlite` | 32-34 MB per version |
| Release notes search index | `index/relnotes-{series}-en.sqlite` | 4.3 MB for 8.0, under 1 MB for the others |
| Fetched pages (gzip HTML) | `pages/` | about 15 KB per page |
| Version list | `versions.json` | small |

- Pages are kept for `MYSQL_DOC_CACHE_TTL_DAYS` (default 7), because documentation pages send
  no `ETag` / `Last-Modified`. When a page cannot be refreshed, the cached copy is returned with a
  note saying when it was fetched.
- Parsed pages are also kept in memory (the last 8 manual pages, 64 release pages), so
  paging through a large page does not parse it again.
- `MYSQL_DOC_OFFLINE=1` disables network access; only cached pages and existing indexes are
  used.

## Search quality

`tests/live/test_search_quality.py` measures, against the live site, whether an expected page
is among the top 3 results:

- Reference Manual (8.4): 29 of 30 queries (0.97). The miss is "replication GTID", where pages
  below the GTID overview page rank higher.
- Release notes: 7 of 8 queries (0.88). The miss is "VECTOR data type", where small later
  changes in the "Vector Data Type" category rank above the 9.0.0 item that introduced the type.

## Documentation copyright

The MySQL documentation is copyrighted by Oracle. Its legal notice does not allow
republishing it, but allows conversion to other formats as long as the content is not
changed. This server fetches pages on the user's machine, converts them to Markdown without
summarizing or rewriting, and keeps them only in the local cache. The repository and package
contain no documentation text; test fixtures are synthetic.
