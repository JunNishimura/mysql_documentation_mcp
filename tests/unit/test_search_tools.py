"""search_documentation / list_versions through an MCP client session (HTTP mocked)."""

import dataclasses
import time

import httpx
import pytest
import respx
from mcp import Client

from mysql_documentation_mcp.core import indexer
from mysql_documentation_mcp.server import create_server
from mysql_documentation_mcp.tools.search import IndexManager
from tests.info_builder import SAMPLE_INDEX, SAMPLE_NODES, make_info, zip_info
from tests.unit.test_versions import mock_site

ZIP_URL = "https://downloads.mysql.com/docs/mysql-9.7.info.zip"
LAST_MODIFIED = "Fri, 25 Sep 2026 16:25:21 GMT"


def text(result):
    return result.content[0].text


def mock_archive():
    return respx.get(ZIP_URL).respond(
        200,
        content=zip_info(make_info(SAMPLE_NODES, SAMPLE_INDEX)),
        headers={"Last-Modified": LAST_MODIFIED},
    )


@respx.mock
async def test_search_builds_the_index_on_first_use(settings):
    mock_site()
    archive = mock_archive()
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("search_documentation", {"query": "widget_limit"})
        again = await client.call_tool("search_documentation", {"query": "sprocket"})
    assert not result.is_error
    data = result.structured_content
    assert data["version"] == "9.7"  # latest LTS by default
    assert data["manual"] == "MySQL 9.7 Reference Manual"
    first = data["results"][0]
    assert first["rank"] == 1
    assert first["url"] == (
        "https://dev.mysql.com/doc/refman/9.7/en/widget-statement.html#sysvar_widget_limit"
    )
    assert first["matched_section"] == "widget_limit"
    assert not again.is_error
    assert archive.call_count == 1


@respx.mock
async def test_search_with_explicit_version(settings):
    mock_site()
    respx.get("https://downloads.mysql.com/docs/mysql-8.4.info.zip").respond(
        200, content=zip_info(make_info(SAMPLE_NODES))
    )
    async with Client(create_server(settings)) as client:
        result = await client.call_tool(
            "search_documentation", {"query": "sprocket", "version": "8.4.6", "limit": 1}
        )
    data = result.structured_content
    assert data["version"] == "8.4"
    assert len(data["results"]) == 1
    assert data["results"][0]["url"].startswith("https://dev.mysql.com/doc/refman/8.4/en/")


@respx.mock
async def test_unknown_version_is_a_tool_error(settings):
    mock_site()
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("search_documentation", {"query": "x", "version": "5.7"})
    assert result.is_error
    assert "No MySQL 5.7 Reference Manual is published" in text(result)


@respx.mock
async def test_no_results_note(settings):
    mock_site()
    mock_archive()
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("search_documentation", {"query": "nonexistentword"})
    data = result.structured_content
    assert data["results"] == []
    assert "English technical terms" in data["note"]


@respx.mock
async def test_index_download_failure_without_index(settings):
    mock_site()
    respx.get(ZIP_URL).respond(404)
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("search_documentation", {"query": "widget"})
    assert result.is_error
    assert "search index for MySQL 9.7 could not be built" in text(result)


@respx.mock
async def test_list_versions(settings):
    mock_site()
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("list_versions", {})
    data = result.structured_content
    assert data["default_version"] == "9.7"
    assert data["latest_lts"] == "9.7"
    versions = {v["version"]: v for v in data["versions"]}
    assert versions["9.7"]["track"] == "LTS"
    assert versions["9.7"]["refman_url"] == "https://dev.mysql.com/doc/refman/9.7/en/"
    assert versions["9.6"]["track"] == "Innovation"


# ------------------------------------------------------------------ index refresh


@pytest.fixture
async def manager(settings):
    from mysql_documentation_mcp.core.fetcher import Fetcher

    fetcher = Fetcher(settings)
    yield IndexManager(dataclasses.replace(settings, index_ttl_days=0), fetcher)
    await fetcher.aclose()


def age_index(path):
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'built_at'")


@respx.mock
async def test_unchanged_archive_is_not_rebuilt(manager):
    mock_archive()
    state = await manager.ensure("9.7")
    age_index(state.path)
    route = respx.get(ZIP_URL).respond(304)
    state = await manager.ensure("9.7")
    assert state.note is None
    assert route.calls.last.request.headers["if-modified-since"] == LAST_MODIFIED
    meta = indexer.read_meta(state.path)
    assert meta is not None and meta.built_at > time.time() - 60


@respx.mock
async def test_existing_index_is_used_when_refresh_fails(manager):
    mock_archive()
    state = await manager.ensure("9.7")
    respx.get(ZIP_URL).mock(side_effect=httpx.ConnectError("down"))
    state = await manager.ensure("9.7")
    assert state.note is not None and "could not be refreshed" in state.note
    assert indexer.search(state.path, "sprocket", 1)


@respx.mock
async def test_corrupt_archive_keeps_existing_index(manager):
    mock_archive()
    await manager.ensure("9.7")
    respx.get(ZIP_URL).respond(200, content=b"garbage")
    state = await manager.ensure("9.7")
    assert state.note is not None
    assert indexer.search(state.path, "sprocket", 1)


@respx.mock
async def test_force_rebuild_ignores_last_modified(manager):
    route = mock_archive()
    await manager.ensure("9.7")
    await manager.ensure("9.7", force=True)
    assert "if-modified-since" not in route.calls.last.request.headers
