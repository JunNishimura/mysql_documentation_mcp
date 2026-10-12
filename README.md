# MySQL Documentation MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets AI agents
search and read the official MySQL documentation at [dev.mysql.com/doc](https://dev.mysql.com/doc/):
the English **MySQL Reference Manual** and the **MySQL Server release notes**.

It is modeled on the [AWS Documentation MCP Server](https://github.com/awslabs/mcp/tree/main/src/aws-documentation-mcp-server).
Agents can answer questions about SQL syntax, configuration, defaults and upgrades from the
documentation of a specific MySQL version, and cite the exact page and anchor.

> [!IMPORTANT]
> **This is an unofficial project.** It is not affiliated with, endorsed by or supported by
> Oracle Corporation or the MySQL project. MySQL is a trademark of Oracle and/or its
> affiliates. The documentation itself is © Oracle. This project does not bundle it; it
> fetches pages from dev.mysql.com when you use it and keeps them in a local cache.

## Features

- **Version-aware search** of the Reference Manual (8.0, 8.4, 9.7, 26.7, ...). Results link to
  the matching sub-section or reference entry, e.g. `server-system-variables.html#sysvar_sql_mode`.
- **Markdown pages** with code blocks, property tables of system variables and options, and
  absolute links. Large pages can be read one section or one reference entry at a time.
- **Release notes between two versions**, across release series and Innovation releases
  (8.0.36 → 8.4.6 includes 8.1.0-8.3.0), filtered by category, importance or keyword.
- **"When did this change?" search** over release notes items.
- **No wrong-version answers**: retired manuals (such as 5.7), which the site silently
  redirects to another version, are reported as unavailable instead.

## Requirements

- Python 3.10 or later and [uv](https://docs.astral.sh/uv/) (for `uvx`)
- Internet access to `dev.mysql.com` and `downloads.mysql.com`

The package is not published on PyPI; run it directly from this repository.

## Installation

### Claude Code

```bash
claude mcp add mysql-documentation -- uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp
```

### Claude Desktop and other MCP clients

Add the server to the client's MCP configuration (for Claude Desktop, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "mysql-documentation": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/JunNishimura/mysql_documentation_mcp",
        "mysql-documentation-mcp"
      ],
      "env": {
        "MYSQL_DOC_DEFAULT_VERSION": "8.4",
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

To pin a version of this server, append a tag or commit to the URL, e.g.
`git+https://github.com/JunNishimura/mysql_documentation_mcp@<commit>`.

## Example prompts

- "In MySQL 8.4, what is the default value of `sql_mode`? Cite the documentation."
- "Show the syntax of `ALTER TABLE ... ALGORITHM=INSTANT` in MySQL 8.0 and which operations it supports."
- "We are upgrading from 8.0.36 to 8.4.6. List the incompatible and important changes from the release notes."
- "In which release was `mysql_native_password` deprecated, and when was it disabled by default?"

Tip: tell the agent which MySQL version you use. Without one, the latest LTS manual is used.

## Tools

| Tool | Description |
|---|---|
| `search_documentation` | Full-text search of the Reference Manual of one version (`query`, `version`, `limit`). With `doc_set="relnotes"`, searches release notes items of a series instead. |
| `read_documentation` | Fetches a documentation page as Markdown, starting with a table of contents. Long pages are paginated with `start_index` / `max_length`. |
| `read_sections` | Returns only the requested sections of a page, by heading title or anchor (e.g. `sysvar_max_connections`). |
| `get_release_notes` | Changes in releases after `from_version` up to `to_version`, grouped by release and category. Bug fixes are excluded unless requested; `important_only`, `categories` and `keyword` narrow the output. |
| `list_versions` | Published versions with their track (LTS / Innovation / ...), latest release and documentation URLs. |

All tools are read-only and only access the official documentation URLs.

## Search indexes and first use

Search runs on local SQLite full-text indexes, built automatically the first time a version is
searched:

| Index | Built from | First build | Size |
|---|---|---|---|
| Reference Manual, per version | the official Info archive (about 4 MB download) | a few seconds | about 33 MB |
| Release notes, per series | every release page of the series | up to about 20 s (8.0) | under 5 MB |

Later release notes updates fetch only new releases. To build indexes in advance:

```bash
uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp index 8.4 9.7
uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp index --doc-set relnotes 8.0 8.4
# every published version and series:
uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp index --doc-set all all
```

Indexes and fetched pages are stored in the OS cache directory (`~/.cache/mysql-documentation-mcp/`
on Linux). They can be deleted at any time and are rebuilt when needed.

## Configuration

| Environment variable | Description | Default |
|---|---|---|
| `MYSQL_DOC_DEFAULT_VERSION` | Version used when a tool gets none: `lts`, `latest` or a version such as `8.4` | `lts` (latest LTS) |
| `MYSQL_DOC_CACHE_DIR` | Directory for the page cache and search indexes | OS cache directory |
| `MYSQL_DOC_CACHE_TTL_DAYS` | Days before a cached page is fetched again | `7` |
| `MYSQL_DOC_INDEX_TTL_DAYS` | Days between checks for a new Reference Manual archive | `7` |
| `MYSQL_DOC_PRELOAD_VERSIONS` | Comma-separated versions whose index is prepared at startup | none |
| `MYSQL_DOC_OFFLINE` | `1` to use only the cache and existing indexes, without network access | `0` |
| `MYSQL_DOC_USER_AGENT` | HTTP User-Agent override (see [Network behavior](docs/how-it-works.md#network-behavior)) | `Python-urllib/<ver> mysql-documentation-mcp/<ver> (+<repo URL>)` |
| `FASTMCP_LOG_LEVEL` | Log level | `WARNING` |

## Limitations

- English Reference Manual and MySQL Server release notes only. MySQL Shell, Router,
  Connectors, NDB Cluster, the Error Message Reference and translated manuals are not covered.
- Only versions that dev.mysql.com still publishes are available. The 5.7 manual and the
  Innovation manuals (8.1-8.3, 9.0-9.6) have been retired; their release notes remain available.
- Search snippets come from the Info edition of the manual, where placeholders in syntax are
  upper case (`TBL_NAME`). The page text returned by `read_documentation` is exact.
- The server depends on the structure of dev.mysql.com pages. A weekly CI job runs tests
  against the live site to detect changes.

## How it works

See [docs/how-it-works.md](docs/how-it-works.md) for the architecture, how versions and
redirects are handled, how the search indexes are built, and the site behaviors the server
works around.

## Development

```bash
uv sync
uv run pytest                  # unit tests (HTTP is mocked)
uv run pytest --run-live       # tests against dev.mysql.com, including search quality
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Issues and pull requests are welcome. Test fixtures are synthetic; please do not add copies of
documentation pages to the repository.

## License

The code in this repository is licensed under the [Apache License 2.0](LICENSE).
The MySQL documentation is © Oracle and is not part of this repository or license.
