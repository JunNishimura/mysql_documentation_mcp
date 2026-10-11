"""Checks against the real site, to notice markup changes. Run with ``pytest --run-live``."""

import pytest
from mcp import Client

from mysql_documentation_mcp.server import create_server

pytestmark = pytest.mark.live


@pytest.fixture
def client(settings):
    return Client(create_server(settings))


async def test_select_page(client):
    async with client:
        result = await client.call_tool(
            "read_documentation",
            {"url": "https://dev.mysql.com/doc/refman/8.4/en/select.html", "max_length": 3000},
        )
    body = result.content[0].text
    assert not result.is_error, body
    assert "Manual: MySQL 8.4 Reference Manual" in body
    assert "```sql\nSELECT\n" in body


async def test_system_variable_entry(client):
    async with client:
        result = await client.call_tool(
            "read_sections",
            {
                "url": "https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html",
                "sections": ["sysvar_max_connections"],
            },
        )
    section = result.structured_content["sections"][0]
    assert section["found"], section
    assert "| Default Value |" in section["content"]


async def test_retired_version_is_refused(client):
    async with client:
        result = await client.call_tool(
            "read_documentation", {"url": "https://dev.mysql.com/doc/refman/5.7/en/select.html"}
        )
    assert result.is_error
    assert "not published" in result.content[0].text


async def test_innovation_release_notes(client):
    async with client:
        result = await client.call_tool(
            "read_sections",
            {
                "url": "https://dev.mysql.com/doc/relnotes/mysql/8.3/en/news-8-3-0.html",
                "sections": ["Deprecation and Removal Notes"],
            },
        )
    assert not result.is_error
    assert result.structured_content["sections"][0]["found"]


async def test_search_and_versions(client):
    async with client:
        versions = await client.call_tool("list_versions", {})
        search = await client.call_tool(
            "search_documentation", {"query": "sql_mode", "version": "8.4", "limit": 3}
        )
    assert not versions.is_error
    listed = {v["version"]: v for v in versions.structured_content["versions"]}
    assert listed["8.4"]["track"] == "LTS" and listed["8.4"]["has_refman"]
    assert not search.is_error
    urls = [r["url"] for r in search.structured_content["results"]]
    assert any("#sysvar_sql_mode" in u for u in urls), urls


async def test_release_notes_across_series(client):
    async with client:
        result = await client.call_tool(
            "get_release_notes",
            {
                "from_version": "8.0.36",
                "to_version": "8.4.6",
                "categories": ["deprecation-removal"],
                "keyword": "mysql_native_password",
            },
        )
    body = result.content[0].text
    assert not result.is_error, body
    assert "Releases: 20 (8.0.37 to 8.4.6)" in body
    # Innovation release notes are read from the 8.4 directory.
    assert "## MySQL 8.1.0 (2023-07-18)" in body
    assert "news-8-4-0.html#mysqld-8-4-0-deprecation-removal" in body
    assert "refman/8.4/en/mysql-nutshell.html" in body


async def test_release_notes_search(client):
    async with client:
        result = await client.call_tool(
            "search_documentation",
            {"query": "mysql_native_password", "version": "8.4", "doc_set": "relnotes"},
        )
    assert not result.is_error, result.content[0].text
    hits = {(r["release"], r["matched_section"]) for r in result.structured_content["results"]}
    assert ("8.4.0", "Deprecation and Removal Notes") in hits, hits


async def test_latest_release_pages_parse(settings):
    """The newest page of every release notes series still has categories and items."""
    from mysql_documentation_mcp.core.fetcher import Fetcher
    from mysql_documentation_mcp.tools.read import DocumentService
    from mysql_documentation_mcp.tools.relnotes import ReleaseNotesService
    from mysql_documentation_mcp.tools.search import SearchService

    fetcher = Fetcher(settings)
    try:
        catalog = SearchService(settings, fetcher).catalog
        service = ReleaseNotesService(settings, DocumentService(settings, fetcher), catalog)
        data = await catalog.get()
        assert data.complete
        for directory in data.relnotes:
            # Some releases have no list of changes (8.4.12 only updated the docker
            # image), so look at the last three.
            pages = [(await service.page(r))[0] for r in data.releases_in(directory)[-3:]]
            assert all(p.categories or p.summary for p in pages), directory
            assert any(p.items for p in pages), directory
            for page in pages:
                assert all(c.anchor.startswith("mysqld-") for c in page.categories), page.url
    finally:
        await fetcher.aclose()
