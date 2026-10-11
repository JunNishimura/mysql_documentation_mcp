"""Tool behavior through a real MCP client session (HTTP mocked)."""

import dataclasses

import httpx
import pytest
import respx
from mcp import Client

from mysql_documentation_mcp.core.cache import CachedPage, PageCache
from mysql_documentation_mcp.server import create_server

URL = "https://dev.mysql.com/doc/refman/8.4/en/widget-statement.html"


@pytest.fixture
def client(settings):
    return Client(create_server(settings))


def text(result):
    return result.content[0].text


async def test_lists_read_only_tools(client):
    async with client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "search_documentation",
        "list_versions",
        "read_documentation",
        "read_sections",
        "get_release_notes",
    }
    for tool in tools.values():
        assert tool.annotations is not None and tool.annotations.read_only_hint


@respx.mock
async def test_read_documentation_paginates(client, refman_html):
    route = respx.get(URL).respond(200, text=refman_html)
    async with client:
        first = await client.call_tool("read_documentation", {"url": URL, "max_length": 200})
        body = text(first)
        assert not first.is_error
        assert body.startswith(
            "MySQL Documentation: 15.9.1 WIDGET Statement\n"
            f"Source: {URL}\n"
            "Manual: MySQL 8.4 Reference Manual\n"
        )
        assert "Sections on this page" in body
        assert "start_index=200" in body

        second = await client.call_tool(
            "read_documentation", {"url": URL, "max_length": 100_000, "start_index": 200}
        )
        assert "Limits text zeta." in text(second)
        assert "truncated" not in text(second)

        past_end = await client.call_tool(
            "read_documentation", {"url": URL, "start_index": 900_000}
        )
        assert "No more content available" in text(past_end)
    assert route.call_count == 1  # later calls are served from the cache


@respx.mock
async def test_fragment_hint(client, refman_html):
    respx.get(URL).respond(200, text=refman_html)
    async with client:
        result = await client.call_tool("read_documentation", {"url": URL + "#sysvar_widget_limit"})
    assert 'read_sections with ["sysvar_widget_limit"]' in text(result)


@respx.mock
async def test_version_redirect_is_reported_not_served(client, refman_html):
    requested = "https://dev.mysql.com/doc/refman/5.7/en/widget-statement.html"
    respx.get(requested).respond(
        301, headers={"Location": "/doc/refman/9.7/en/widget-statement.html"}
    )
    respx.get("https://dev.mysql.com/doc/refman/9.7/en/widget-statement.html").respond(
        200, text=refman_html
    )
    async with client:
        result = await client.call_tool("read_documentation", {"url": requested})
    assert result.is_error
    assert "MySQL 5.7 Reference Manual is not published" in text(result)
    assert "Limits text zeta." not in text(result)


async def test_invalid_url_error_reaches_the_agent(client):
    async with client:
        result = await client.call_tool("read_documentation", {"url": "https://example.com/a.html"})
    assert result.is_error
    assert "Unsupported URL" in text(result)


@respx.mock
async def test_stale_cache_is_used_when_the_site_is_unreachable(settings, refman_html):
    PageCache(settings.cache_dir).put(
        CachedPage(url=URL, final_url=URL, html=refman_html, fetched_at=0.0)
    )
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("read_documentation", {"url": URL})
    assert not result.is_error
    assert "Note: Could not refresh this page" in text(result)
    assert "1970-01-01" in text(result)


async def test_offline_without_cache_is_an_error(settings):
    async with Client(create_server(dataclasses.replace(settings, offline=True))) as client:
        result = await client.call_tool("read_documentation", {"url": URL})
    assert result.is_error
    assert "Offline mode" in text(result)


@respx.mock
async def test_read_sections(client, refman_html):
    respx.get(URL).respond(200, text=refman_html)
    async with client:
        result = await client.call_tool(
            "read_sections",
            {"url": URL, "sections": ["sysvar_widget_limit", "Widget Names", "nope"]},
        )
    assert not result.is_error
    data = result.structured_content
    assert data["manual"] == "MySQL 8.4 Reference Manual"
    assert data["page_title"] == "15.9.1 WIDGET Statement"
    limit, names, missing = data["sections"]
    assert limit["found"] and limit["url"] == URL + "#sysvar_widget_limit"
    assert "| Default Value | `42` |" in limit["content"]
    assert names["found"] and names["anchor"] == "widget-names"
    assert not missing["found"] and "was not found" in missing["content"]


@respx.mock
async def test_read_sections_truncates_long_sections(client, refman_html):
    respx.get(URL).respond(200, text=refman_html)
    async with client:
        result = await client.call_tool(
            "read_sections", {"url": URL, "sections": ["widget-statement"], "max_length": 50}
        )
    content = result.structured_content["sections"][0]["content"]
    assert "Section truncated at 50" in content


async def test_read_sections_requires_names(client):
    async with client:
        result = await client.call_tool("read_sections", {"url": URL, "sections": []})
    assert result.is_error
