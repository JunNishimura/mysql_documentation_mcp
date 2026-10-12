"""get_release_notes and release notes search through an MCP client (HTTP mocked)."""

import dataclasses

import httpx
import pytest
import respx
from mcp import Client

from mysql_documentation_mcp.core.fetcher import Fetcher
from mysql_documentation_mcp.core.relnotes_index import read_relnotes_meta
from mysql_documentation_mcp.core.versions import VersionCatalog
from mysql_documentation_mcp.server import create_server
from mysql_documentation_mcp.tools import relnotes as relnotes_module
from mysql_documentation_mcp.tools.read import DocumentService
from mysql_documentation_mcp.tools.relnotes import ReleaseNotesService
from tests.relnotes_builder import (
    BASE,
    item,
    mock_relnotes_site,
    page_url,
    release_page,
    series_index,
)


def text(result):
    return result.content[0].text


async def notes(settings, **args):
    async with Client(create_server(settings)) as client:
        return await client.call_tool("get_release_notes", args)


@respx.mock
async def test_range_across_series(settings):
    mock_relnotes_site()
    result = await notes(settings, from_version="8.0.1", to_version="8.4.1")
    assert not result.is_error
    out = text(result)
    assert out.startswith("# MySQL Release Notes: changes after 8.0.1 up to 8.4.1\n")
    assert "Releases: 4 (8.0.2 to 8.4.1)" in out
    assert 'Categories: all except "bug" (Bugs Fixed)' in out
    # from_version itself is excluded; bug fixes are excluded by default.
    assert "widget_mode" not in out
    assert "leaked memory" not in out and "Bug #3" not in out
    assert "Releases with no matching items: 8.4.1" in out
    # Innovation release 8.1.0 is read from the 8.4 directory.
    assert f"## MySQL 8.1.0 (2023-07-18)\nSource: {page_url('8.4', '8.1.0')}" in out
    assert (
        "### Deprecation and Removal Notes\n"
        f"Source: {page_url('8.4', '8.1.0')}#mysqld-8-1-0-deprecation-removal"
    ) in out
    assert "- **Incompatible Change:** The `--legacy-widget` option is removed." in out
    # The nested list stays inside its item.
    assert "  - Old gadget form removed." in out
    # Crossing series points to the target manual's overview pages.
    assert "https://dev.mysql.com/doc/refman/8.4/en/mysql-nutshell.html" in out
    assert out.index("## MySQL 8.0.2") < out.index("## MySQL 8.1.0") < out.index("## MySQL 8.4.0")


@respx.mock
async def test_filters(settings):
    mock_relnotes_site()
    important = text(
        await notes(settings, from_version="8.0.1", to_version="8.4", important_only=True)
    )
    assert "Matching items: 3" in important
    assert "sprocket" in important and "legacy-widget" in important and "faster" in important
    assert "Gizmo support" not in important

    bugs = text(
        await notes(settings, from_version="8.0", to_version="8.4", categories=["Bugs Fixed"])
    )
    # "8.0" stands for the latest 8.0 release (8.0.2).
    assert "changes after 8.0.2 up to 8.4.1" in bugs
    assert "Bug #3" in bugs and "Bug #4" in bugs and "Bug #2" not in bugs

    keyword = text(
        await notes(
            settings,
            from_version="8.0.0",
            to_version="8.4.1",
            categories=["all"],
            keyword="WIDGET_MODE",
        )
    )
    assert "Matching items: 2" in keyword  # 8.0.1 feature and 8.4.1 bug fix

    unknown = text(
        await notes(settings, from_version="8.0.1", to_version="8.4.1", categories=["nonsense"])
    )
    assert "Matching items: 0" in unknown
    assert "no category matches nonsense" in unknown
    assert "Categories in these releases: bug, deprecation-removal, feature, security" in unknown


@respx.mock
async def test_pagination(settings):
    mock_relnotes_site()
    first = text(await notes(settings, from_version="8.0.1", to_version="8.4.1", max_length=300))
    assert "start_index=300" in first
    second = text(
        await notes(
            settings, from_version="8.0.1", to_version="8.4.1", max_length=100_000, start_index=300
        )
    )
    assert "truncated" not in second
    assert len(first.split("\n\n<e>")[0]) == 300


@respx.mock
async def test_errors(settings, monkeypatch):
    mock_relnotes_site()
    reversed_range = await notes(settings, from_version="8.4.0", to_version="8.0.2")
    assert reversed_range.is_error
    assert "must be older than to_version" in text(reversed_range)

    unknown = await notes(settings, from_version="5.7", to_version="8.4")
    assert unknown.is_error
    assert "no MySQL 5.7 releases are listed" in text(unknown)
    assert "8.0, 8.1, 8.4" in text(unknown)

    monkeypatch.setattr(relnotes_module, "MAX_RELEASES", 2)
    too_many = await notes(settings, from_version="8.0.1", to_version="8.4.1")
    assert too_many.is_error and "at most 2 can be read" in text(too_many)


@respx.mock
async def test_unreadable_release_is_reported(settings):
    mock_relnotes_site()
    respx.get(page_url("8.4", "8.4.0")).respond(404)
    out = text(await notes(settings, from_version="8.0.1", to_version="8.4.1"))
    assert "Note: Release 8.4.0 could not be read" in out
    assert "## MySQL 8.1.0" in out


@respx.mock
async def test_release_without_changes_list(settings):
    mock_relnotes_site()
    respx.get(page_url("8.4", "8.4.1")).respond(
        200,
        text='<html><body><div id="docs-body"><h2>Changes in MySQL 8.4.1 (2024-07-01)</h2>'
        "<p>Container image update only.</p></div></body></html>",
    )
    out = text(await notes(settings, from_version="8.4.0", to_version="8.4.1"))
    assert (
        f"Release without a list of changes: 8.4.1 ({page_url('8.4', '8.4.1')}): "
        "Container image update only."
    ) in out


# ------------------------------------------------------------------ search


async def search(settings, **args):
    async with Client(create_server(settings)) as client:
        return await client.call_tool("search_documentation", {"doc_set": "relnotes", **args})


@respx.mock
async def test_search_release_notes(settings):
    routes = mock_relnotes_site()
    result = await search(settings, query="legacy-widget", version="8.4")
    assert not result.is_error
    data = result.structured_content
    assert data["manual"] == "MySQL 8.4 Release Notes"
    first = data["results"][0]
    assert first["release"] == "8.1.0"
    assert first["title"] == "Changes in MySQL 8.1.0 (2023-07-18)"
    assert first["matched_section"] == "Deprecation and Removal Notes"
    assert first["url"] == page_url("8.4", "8.1.0") + "#mysqld-8-1-0-deprecation-removal"
    assert "**legacy**" in first["snippet"]
    assert routes["8.0.1"].call_count == 0  # only the 8.4 directory is read


@respx.mock
async def test_search_release_notes_scope(settings):
    mock_relnotes_site()
    # "8.1" is searched in the 8.4 directory, limited to 8.1.x releases.
    data = (await search(settings, query="gizmo", version="8.1")).structured_content
    assert data["manual"] == "MySQL 8.4 Release Notes (MySQL 8.1 releases only)"
    assert {r["release"] for r in data["results"]} == {"8.1.0"}
    # Default: the latest LTS series (8.4 here), including its Innovation releases.
    data = (await search(settings, query="gizmo")).structured_content
    assert data["version"] == "8.4"
    assert [r["release"] for r in data["results"]] == ["8.1.0", "8.4.0"]
    missing = await search(settings, query="x", version="5.7")
    assert missing.is_error and "No release notes for MySQL 5.7" in text(missing)


@respx.mock
async def test_search_ranks_bug_fixes_lower(settings):
    mock_relnotes_site()
    data = (await search(settings, query="widget_mode", version="8.0")).structured_content
    assert [r["matched_section"] for r in data["results"]] == ["Functionality Added or Changed"]
    data = (await search(settings, query="gizmo widgets", version="8.4")).structured_content
    assert data["results"][0]["matched_section"] == "Functionality Added or Changed"


@pytest.fixture
async def service(settings):
    # Catalog without caching and pages without caching, to observe what is fetched.
    settings = dataclasses.replace(settings, cache_ttl_days=0)
    fetcher = Fetcher(settings)
    catalog = VersionCatalog(fetcher, settings.cache_dir, ttl_seconds=0)
    yield ReleaseNotesService(settings, DocumentService(settings, fetcher), catalog)
    await fetcher.aclose()


@respx.mock
async def test_index_update_fetches_only_new_releases(service):
    routes = mock_relnotes_site()
    path, note = await service.ensure_index("8.4")
    assert note is None
    meta = read_relnotes_meta(path)
    assert meta is not None and meta.releases == ("8.1.0", "8.4.0", "8.4.1")
    assert meta.items == 7

    # Unchanged release list: nothing is fetched.
    await service.ensure_index("8.4")
    assert routes["8.4.0"].call_count == 1

    # A new release: only its page is read, the others are copied from the index.
    respx.get(f"{BASE}/8.4/en/").respond(
        200,
        text=series_index(
            ("8.1.0", "2023-07-18"),
            ("8.4.0", "2024-04-30"),
            ("8.4.1", "2024-07-01"),
            ("8.4.2", "2024-10-15"),
        ),
    )
    new = respx.get(page_url("8.4", "8.4.2")).respond(
        200,
        text=release_page(
            "8.4.2",
            "2024-10-15",
            [("feature", "Functionality Added or Changed", [item("Doohickeys.")])],
        ),
    )
    path, _ = await service.ensure_index("8.4")
    meta = read_relnotes_meta(path)
    assert meta is not None and meta.releases == ("8.1.0", "8.4.0", "8.4.1", "8.4.2")
    assert meta.items == 8
    assert new.call_count == 1 and routes["8.4.0"].call_count == 1
    hits = (await service.search("doohickeys gizmo", "8.4", 10)).results
    assert {h.release for h in hits} == {"8.4.2", "8.1.0", "8.4.0"}


@respx.mock
async def test_index_build_failure(service):
    mock_relnotes_site()
    respx.get(page_url("8.0", "8.0.1")).mock(side_effect=httpx.ConnectError("down"))
    path, note = await service.ensure_index("8.0")
    assert note is not None and "8.0.1" in note
    meta = read_relnotes_meta(path)
    assert meta is not None and meta.releases == ("8.0.2",)
