"""MCP server entry point."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal, cast

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from mysql_documentation_mcp import __version__
from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.tools.read import DocumentationError, DocumentService, SectionsResult

INSTRUCTIONS = """
# MySQL Documentation MCP Server

Tools to read the official MySQL documentation (dev.mysql.com): the English MySQL Reference
Manual and the MySQL Server release notes.

## Best practices

- Before answering questions about MySQL syntax, configuration, defaults or behavior, check
  the official documentation with these tools, and cite the documentation URL.
- Documentation differs between versions. Use the manual for the user's MySQL version by
  putting it in the URL: https://dev.mysql.com/doc/refman/{version}/en/{page}.html
  (published manuals: 8.0, 8.4, 9.7, 26.7). If the version is unknown, ask or say which
  version you assumed.
- To compare versions, read the same page with a different {version} in the URL.
- Large pages: the first read_documentation call lists the page's sections and reference
  entries. Use read_sections with a section title or anchor (for example
  "sysvar_sql_mode" on server-system-variables.html) instead of paging through everything.
- For long pages, call read_documentation again with start_index to continue, and stop once
  you have found the information you need.
- Release notes: https://dev.mysql.com/doc/relnotes/mysql/{series}/en/news-X-Y-Z.html.
  Innovation releases are listed under the following LTS series (8.1-8.3 under 8.4,
  9.0-9.6 under 9.7).
""".strip()

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _log_level() -> Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
    level = os.environ.get("FASTMCP_LOG_LEVEL", "WARNING").upper()
    return cast(
        Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        level if level in _LOG_LEVELS else "WARNING",
    )


def create_server(service: DocumentService | None = None) -> MCPServer:
    docs = service or DocumentService(Settings.from_env())

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await docs.aclose()

    mcp = MCPServer(
        "mysql-documentation-mcp",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level=_log_level(),
        lifespan=lifespan,
    )

    @mcp.tool(annotations=_READ_ONLY)
    async def read_documentation(
        url: Annotated[
            str,
            Field(
                description="URL of a MySQL documentation page, e.g. "
                "https://dev.mysql.com/doc/refman/8.4/en/select.html"
            ),
        ],
        max_length: Annotated[
            int, Field(description="Maximum number of characters to return.", gt=0, lt=1_000_000)
        ] = 10_000,
        start_index: Annotated[
            int,
            Field(
                description="Return output starting at this character index; use the value "
                "given in a truncated response to continue reading.",
                ge=0,
            ),
        ] = 0,
    ) -> str:
        """Fetch a MySQL documentation page and return it as Markdown.

        Supported URLs: the English MySQL Reference Manual
        (https://dev.mysql.com/doc/refman/{version}/en/...) and MySQL Server release notes
        (https://dev.mysql.com/doc/relnotes/mysql/{series}/en/...).

        The output starts with the source URL and manual version, then (for pages with several
        sections or reference entries) a table of contents with anchors usable in
        read_sections, then the page content. Syntax blocks and examples are kept verbatim as
        code blocks. Long pages are truncated; call again with the start_index given at the end
        of the response to continue.

        If the requested manual version is not published (e.g. 5.7), an error is returned
        instead of silently showing another version's page.
        """
        try:
            return await docs.read_documentation(url, max_length, start_index)
        except DocumentationError as e:
            raise ToolError(str(e)) from e

    @mcp.tool(annotations=_READ_ONLY)
    async def read_sections(
        url: Annotated[str, Field(description="URL of a MySQL documentation page")],
        sections: Annotated[
            list[str],
            Field(
                description="Section titles or anchors to extract, e.g. "
                '["sysvar_sql_mode", "Table Options"]. Titles may omit the section number.',
                min_length=1,
                max_length=20,
            ),
        ],
        max_length: Annotated[
            int,
            Field(description="Maximum characters per section.", gt=0, lt=1_000_000),
        ] = 20_000,
    ) -> SectionsResult:
        """Return only the requested sections of a MySQL documentation page as Markdown.

        Use this for large pages (for example server-system-variables.html, over 300,000
        characters) when you know which part you need. A section can be named by:
        - an anchor: a heading anchor ("create-table-options") or a reference entry anchor
          such as a system variable ("sysvar_max_connections"), status variable
          ("statvar_Threads_connected"), server option ("option_mysqld_port") or function
          ("function_json-extract"). A reference entry returns its property table (type,
          scope, dynamic, default value, ...) and description.
        - a heading title, with or without its section number ("Table Options").
        A heading section runs until the next heading of the same or a higher level.
        Unknown names return the list of available sections.
        """
        try:
            return await docs.read_sections(url, sections, max_length)
        except DocumentationError as e:
            raise ToolError(str(e)) from e

    return mcp


def main() -> None:
    logging.basicConfig(level=_log_level())
    create_server().run()


if __name__ == "__main__":
    main()
