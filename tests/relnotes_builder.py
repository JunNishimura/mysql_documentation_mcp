"""Small synthetic release notes site for tests (no MySQL documentation text).

The markup follows the real pages: a table of contents list, then one
``div.simplesect`` per category with ``li.listitem`` items (see core/relnotes_parser.py).
"""

from __future__ import annotations

import respx

BASE = "https://dev.mysql.com/doc/relnotes/mysql"


def item(text: str, flag: str | None = None, extra: str = "") -> str:
    prefix = f'<span class="bold"><strong>{flag}:</strong></span> ' if flag else ""
    return f'<li class="listitem"><p>{prefix}{text}</p>{extra}</li>'


def release_page(version: str, info: str, sections: list[tuple[str, str, list[str]]]) -> str:
    """``sections``: (slug, title, items)."""
    dashed = version.replace(".", "-")
    toc = "".join(
        f'<li class="listitem"><p><a class="xref" href="news-{dashed}.html#mysqld-{dashed}-{slug}">'
        f"{title}</a></p></li>"
        for slug, title, _ in sections
    )
    body = "".join(
        f'<div class="simplesect"><div class="titlepage"><div><div><h3 class="title">'
        f'<a name="mysqld-{dashed}-{slug}"></a>{title}</h3></div></div></div>'
        f'<div class="itemizedlist"><ul class="itemizedlist">{"".join(items)}</ul></div></div>'
        for slug, title, items in sections
    )
    title = f"Changes in MySQL {version} ({info})"
    return (
        f"<html><head><title>MySQL :: MySQL Release Notes :: {title}</title></head><body>"
        f'<div id="docs-body"><div class="section"><div class="titlepage"><div><div>'
        f'<h2 class="title"><a name="news-{dashed}"></a>{title}</h2></div></div></div>'
        f'<a class="indexterm" name="id1"></a>'
        f'<div class="itemizedlist"><ul class="itemizedlist">{toc}</ul></div>'
        f"{body}</div></div></body></html>"
    )


def series_index(*releases: tuple[str, str]) -> str:
    links = "".join(
        f'<li><a href="news-{v.replace(".", "-")}.html">Changes in MySQL {v} ({info})</a></li>'
        for v, info in releases
    )
    return f'<html><body><div id="docs-body"><ul>{links}</ul></div></body></html>'


FEATURE = ("feature", "Functionality Added or Changed")
DEPRECATION = ("deprecation-removal", "Deprecation and Removal Notes")
SECURITY = ("security", "Security Notes")
BUG = ("bug", "Bugs Fixed")

# directory -> [(version, info, sections)]
SITE: dict[str, list[tuple[str, str, list[tuple[str, str, list[str]]]]]] = {
    "8.0": [
        (
            "8.0.1",
            "2020-01-01, General Availability",
            [
                (*FEATURE, [item("Added the <code>widget_mode</code> system variable.")]),
                (*BUG, [item("Fixed a crash when sorting widgets. (Bug #1)")]),
            ],
        ),
        (
            "8.0.2",
            "2020-02-01",
            [
                (
                    *DEPRECATION,
                    [
                        item(
                            'The <a href="/doc/refman/8.0/en/sprocket.html">sprocket</a> '
                            "plugin is deprecated.",
                            flag="Important Change",
                        )
                    ],
                ),
                (*BUG, [item("Sprockets leaked memory. (Bug #2)", flag="InnoDB")]),
            ],
        ),
    ],
    "8.4": [
        (
            "8.1.0",
            "2023-07-18",
            [
                (
                    *DEPRECATION,
                    [
                        item(
                            "The <code>--legacy-widget</code> option is removed.",
                            "Incompatible Change",
                        ),
                        item(
                            "<b>Gadget syntax.</b> The gadget statement changed:",
                            extra='<ul class="itemizedlist"><li class="listitem"><p>'
                            "Old gadget form removed.</p></li></ul>",
                        ),
                    ],
                ),
                (*FEATURE, [item("Gizmo support for widgets.")]),
            ],
        ),
        (
            "8.4.0",
            "2024-04-30",
            [
                (*FEATURE, [item("Widgets are faster.", flag="Important Change; InnoDB")]),
                (*SECURITY, [item("Sprocket connections now require TLS.")]),
                (*BUG, [item("Fixed gizmo rounding. (Bug #3)")]),
            ],
        ),
        ("8.4.1", "2024-07-01", [(*BUG, [item("Fixed widget_mode reporting. (Bug #4)")])]),
    ],
}


def page_url(directory: str, version: str) -> str:
    return f"{BASE}/{directory}/en/news-{version.replace('.', '-')}.html"


DOC_INDEX = """<html><body>
<a href="/doc/refman/8.4/en/">MySQL 8.4 Reference Manual</a>
<a href="/doc/refman/8.0/en/">MySQL 8.0 Reference Manual</a>
<a href="/doc/relnotes/mysql/8.4/en/">8.4 notes</a>
<a href="/doc/relnotes/mysql/8.0/en/">8.0 notes</a>
</body></html>"""


def mock_relnotes_site(site=SITE) -> dict[str, respx.Route]:
    """Mock the version discovery pages and every release page; returns page routes."""
    respx.get("https://dev.mysql.com/doc/").respond(200, text=DOC_INDEX)
    respx.get("https://dev.mysql.com/doc/refman/en/").respond(
        302, headers={"Location": "/doc/refman/8.4/en/"}
    )
    respx.get("https://dev.mysql.com/doc/refman/8.4/en/").respond(200, text="ok")
    routes = {}
    for directory, releases in site.items():
        respx.get(f"{BASE}/{directory}/en/").respond(
            200, text=series_index(*[(v, info) for v, info, _ in releases])
        )
        for version, info, sections in releases:
            routes[version] = respx.get(page_url(directory, version)).respond(
                200, text=release_page(version, info, sections)
            )
    return routes
