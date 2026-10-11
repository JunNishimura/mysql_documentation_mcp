"""Command line: run the MCP server (default) or prepare search indexes."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from mysql_documentation_mcp import __version__
from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.core import indexer
from mysql_documentation_mcp.core.fetcher import Fetcher
from mysql_documentation_mcp.core.relnotes_index import read_relnotes_meta
from mysql_documentation_mcp.tools.read import DocumentationError, DocumentService
from mysql_documentation_mcp.tools.relnotes import ReleaseNotesService
from mysql_documentation_mcp.tools.search import SearchService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mysql-documentation-mcp",
        description="MCP server for the official MySQL documentation. "
        "Without a command, runs the server on stdio.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the MCP server on stdio (default)")
    index = commands.add_parser(
        "index", help="download the documentation and build the local search indexes"
    )
    index.add_argument(
        "versions",
        nargs="*",
        help='versions, e.g. 8.4 9.7 (default: the latest LTS; "all" for every version)',
    )
    index.add_argument(
        "--doc-set",
        choices=("refman", "relnotes", "all"),
        default="refman",
        help="Reference Manual (default), release notes, or both",
    )
    index.add_argument("--force", action="store_true", help="rebuild even if up to date")
    return parser


async def _index_refman(search: SearchService, versions: list[str], force: bool) -> int:
    status = 0
    if versions == ["all"]:
        versions = (await search.catalog.get()).refman_versions
    for spec in versions or ["lts"]:
        try:
            version = await search.resolve_version(spec)
            state = await search.indexes.ensure(version, force=force)
        except DocumentationError as e:
            print(f"{spec}: {e}", file=sys.stderr)
            status = 1
            continue
        meta = indexer.read_meta(state.path)
        pages = meta.pages if meta else "?"
        print(f"MySQL {version}: {pages} pages indexed at {state.path}")
        if state.note:
            print(f"  note: {state.note}", file=sys.stderr)
    return status


async def _index_relnotes(relnotes: ReleaseNotesService, versions: list[str], force: bool) -> int:
    status = 0
    if versions == ["all"]:
        directories = list((await relnotes.catalog.get()).relnotes)
    else:
        directories = []
        for spec in versions or ["lts"]:
            try:
                directories.append((await relnotes.resolve_scope(spec)).directory)
            except DocumentationError as e:
                print(f"{spec}: {e}", file=sys.stderr)
                status = 1
    for directory in dict.fromkeys(directories):
        try:
            path, note = await relnotes.ensure_index(directory, force=force)
        except DocumentationError as e:
            print(f"{directory} release notes: {e}", file=sys.stderr)
            status = 1
            continue
        meta = read_relnotes_meta(path)
        counts = f"{len(meta.releases)} releases, {meta.items} items" if meta else "?"
        print(f"MySQL {directory} release notes: {counts} indexed at {path}")
        if note:
            print(f"  note: {note}", file=sys.stderr)
    return status


async def _index(versions: list[str], doc_set: str, force: bool) -> int:
    settings = Settings.from_env()
    fetcher = Fetcher(settings)
    search = SearchService(settings, fetcher)
    relnotes = ReleaseNotesService(settings, DocumentService(settings, fetcher), search.catalog)
    status = 0
    try:
        if doc_set in ("refman", "all"):
            status |= await _index_refman(search, versions, force)
        if doc_set in ("relnotes", "all"):
            status |= await _index_relnotes(relnotes, versions, force)
    finally:
        await fetcher.aclose()
    return status


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "index":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        sys.exit(asyncio.run(_index(args.versions, args.doc_set, args.force)))

    from mysql_documentation_mcp.server import create_server, log_level

    logging.basicConfig(level=log_level())
    create_server().run()
