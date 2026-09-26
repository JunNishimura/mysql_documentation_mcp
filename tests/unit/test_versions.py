import dataclasses

import pytest
import respx

from mysql_documentation_mcp.core.fetcher import Fetcher
from mysql_documentation_mcp.core.versions import (
    Version,
    VersionCatalog,
    VersionError,
    parse_doc_index,
    parse_relnotes_index,
)

DOC_INDEX = """<html><body>
<a href="/doc/refman/9.7/en/">MySQL 9.7 Reference Manual</a>
<a href="/doc/refman/8.4/en/">MySQL 8.4 Reference Manual</a>
<a href="/doc/refman/8.4/en/mysql-cluster.html">NDB</a>
<a href="/doc/refman/8.0/ja/">Japanese</a>
<a href="https://dev.mysql.com/doc/refman/en/">Current</a>
<a href="/doc/relnotes/mysql/9.7/en/">9.7 notes</a>
<a href="/doc/relnotes/mysql/9.8/en/news-9-8-0.html">9.8.0 (Early Access)</a>
</body></html>"""


def relnotes_index(*links):
    items = "".join(f'<li><a href="{href}">{text}</a></li>' for href, text in links)
    return f'<html><body><div id="docs-body"><ul>{items}</ul></div></body></html>'


def test_version_ordering():
    ordered = ["8.0.36", "8.3.0", "8.4", "8.4.6", "9.7.1", "26.7", "26.10.0"]
    parsed = [Version.parse(v) for v in ordered]
    assert sorted(reversed(parsed)) == parsed
    assert Version.parse("8.4.6").series == "8.4"
    assert str(Version.parse(" v26.10 ")) == "26.10"
    with pytest.raises(VersionError):
        Version.parse("8")
    with pytest.raises(VersionError):
        Version.parse("latest")


def test_parse_doc_index():
    refman, relnotes = parse_doc_index(DOC_INDEX)
    assert refman == ["8.4", "9.7"]  # the Japanese manual is out of scope
    assert relnotes == ["9.7", "9.8"]


def test_parse_relnotes_index():
    html = relnotes_index(
        ("news-9-7-1.html", "Changes in MySQL 9.7.1 (2026-06-16)"),
        ("news-9-6-0.html", "Changes in MySQL 9.6.0 (2026-01-20)"),
        ("news-9-8-0.html", "Changes in MySQL 9.8.0 (2026-09-18 (Early Access Release))"),
        ("other.html", "Something else"),
    )
    releases = parse_relnotes_index(html, "https://x/")
    assert [(r.version, r.date, r.early_access) for r in releases] == [
        ("9.6.0", "2026-01-20", False),
        ("9.7.1", "2026-06-16", False),
        ("9.8.0", "2026-09-18", True),
    ]
    assert releases[0].url == "https://x/news-9-6-0.html"


def mock_site(latest="9.7"):
    respx.get("https://dev.mysql.com/doc/").respond(200, text=DOC_INDEX)
    respx.get("https://dev.mysql.com/doc/relnotes/mysql/9.7/en/").respond(
        200,
        text=relnotes_index(
            ("news-9-6-0.html", "Changes in MySQL 9.6.0 (2026-01-20)"),
            ("news-9-7-0.html", "Changes in MySQL 9.7.0 (2026-04-21)"),
            ("news-9-7-1.html", "Changes in MySQL 9.7.1 (2026-06-16)"),
        ),
    )
    respx.get("https://dev.mysql.com/doc/relnotes/mysql/9.8/en/").respond(
        200, text=relnotes_index(("news-9-8-0.html", "Changes in MySQL 9.8.0 (Early Access)"))
    )
    respx.get("https://dev.mysql.com/doc/refman/en/").respond(
        302, headers={"Location": f"/doc/refman/{latest}/en/"}
    )
    respx.get(f"https://dev.mysql.com/doc/refman/{latest}/en/").respond(200, text="ok")


@pytest.fixture
async def catalog(settings):
    fetcher = Fetcher(settings)
    yield VersionCatalog(fetcher, settings.cache_dir)
    await fetcher.aclose()


@respx.mock
async def test_resolve_versions(catalog):
    mock_site()
    assert await catalog.resolve_refman_version(None, "lts") == "9.7"
    assert await catalog.resolve_refman_version("8.4.6", "lts") == "8.4"
    assert await catalog.resolve_refman_version("latest", "lts") == "9.7"
    assert await catalog.resolve_refman_version(None, "8.4") == "8.4"
    with pytest.raises(VersionError, match=r"Available manual versions: 8\.4, 9\.7"):
        await catalog.resolve_refman_version("5.7", "lts")


@respx.mock
async def test_versions_listing(catalog):
    mock_site()
    infos = {v.version: v for v in await catalog.versions()}
    assert list(infos) == ["9.8", "9.7", "9.6", "8.4"]
    assert infos["9.7"].track == "LTS"
    assert infos["9.7"].latest_release == "9.7.1"
    assert infos["9.7"].latest_release_date == "2026-06-16"
    # 9.6's notes are listed under the 9.7 series: an Innovation release.
    assert infos["9.6"].track == "Innovation"
    assert not infos["9.6"].has_refman
    assert infos["9.6"].relnotes_url == "https://dev.mysql.com/doc/relnotes/mysql/9.7/en/"
    assert infos["9.8"].track == "Early Access"
    assert infos["8.4"].has_refman and infos["8.4"].latest_release is None


@respx.mock
async def test_catalog_is_cached_on_disk(settings, catalog):
    route = respx.get("https://dev.mysql.com/doc/")
    mock_site()
    await catalog.get()
    fetcher = Fetcher(settings)
    try:
        again = VersionCatalog(fetcher, settings.cache_dir)
        assert (await again.get()).refman_versions == ["8.4", "9.7"]
    finally:
        await fetcher.aclose()
    assert route.call_count == 1


@respx.mock
async def test_fallback_when_the_site_is_unreachable(settings):
    fetcher = Fetcher(dataclasses.replace(settings, offline=True))
    catalog = VersionCatalog(fetcher, settings.cache_dir)
    data = await catalog.get()
    assert data.refman_versions == ["8.0", "8.4", "9.7", "26.7"]
    assert not data.complete
    # The newest bundled LTS is used when the site cannot tell.
    assert await catalog.resolve_refman_version(None, "lts") == "9.7"
