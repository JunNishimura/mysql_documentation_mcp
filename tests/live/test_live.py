"""Checks against the real site, to notice markup changes. Run with ``pytest --run-live``."""

import pytest
from mcp import Client

from mysql_documentation_mcp.server import create_server
from mysql_documentation_mcp.tools.read import DocumentService

pytestmark = pytest.mark.live


@pytest.fixture
def client(settings):
    return Client(create_server(DocumentService(settings)))


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
