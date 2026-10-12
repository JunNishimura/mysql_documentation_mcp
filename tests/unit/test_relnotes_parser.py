import pytest

from mysql_documentation_mcp.core.converter import UnreadablePageError
from mysql_documentation_mcp.core.relnotes_parser import parse_release_page
from tests.relnotes_builder import SITE, item, page_url, release_page


def parse(directory: str, index: int):
    version, info, sections = SITE[directory][index]
    url = page_url(directory, version)
    return parse_release_page(release_page(version, info, sections), url)


def test_release_metadata():
    page = parse("8.0", 0)
    assert page.version == "8.0.1"
    assert page.date == "2020-01-01"
    assert page.status == "General Availability"
    assert page.title == "Changes in MySQL 8.0.1 (2020-01-01, General Availability)"
    assert parse("8.0", 1).status == ""


def test_early_access_status():
    html = release_page("26.10.0", "2026-09-18 (Early Access Release)", [])
    page = parse_release_page(html, page_url("26.10", "26.10.0"))
    assert (page.date, page.status) == ("2026-09-18", "Early Access Release")


def test_categories_and_items():
    page = parse("8.4", 0)
    assert [(c.slug, c.title) for c in page.categories] == [
        ("deprecation-removal", "Deprecation and Removal Notes"),
        ("feature", "Functionality Added or Changed"),
    ]
    # The table of contents and the nested list inside an item are not items.
    assert len(page.items) == 3
    removed, gadget, gizmo = page.items
    assert removed.category.slug == "deprecation-removal"
    assert removed.url.endswith("news-8-1-0.html#mysqld-8-1-0-deprecation-removal")
    assert "Old gadget form removed." in gadget.text
    assert gizmo.category.slug == "feature"


def test_flags():
    page = parse("8.4", 1)
    faster = page.items[0]
    assert faster.flags == ("Important Change", "InnoDB")
    assert faster.important
    assert parse("8.4", 0).items[0].flags == ("Incompatible Change",)
    assert parse("8.4", 0).items[0].important
    sprockets = parse("8.0", 1).items[1]
    assert sprockets.flags == ("InnoDB",) and not sprockets.important
    # A bold lead-in that is not a "Prefix:" label is not a flag.
    assert parse("8.4", 0).items[1].flags == ()


def test_item_markdown_has_absolute_links():
    md = parse("8.0", 1).items[0].markdown()
    assert md.startswith("**Important Change:**")
    assert "[sprocket](https://dev.mysql.com/doc/refman/8.0/en/sprocket.html)" in md


def test_unreadable_page():
    with pytest.raises(UnreadablePageError):
        parse_release_page("<html><body>nothing</body></html>", page_url("8.4", "8.4.0"))


def test_release_without_changes_list():
    html = (
        '<html><body><div id="docs-body"><h2>Changes in MySQL 8.4.12 (2026-08-18)</h2>'
        '<div class="note"><p>This release updates the container image only.</p></div>'
        "</div></body></html>"
    )
    page = parse_release_page(html, page_url("8.4", "8.4.12"))
    assert page.version == "8.4.12" and not page.items
    assert page.summary == "This release updates the container image only."


def test_hidden_item_text_is_dropped():
    html = release_page(
        "8.4.0",
        "2024-04-30",
        [
            (
                "feature",
                "Functionality Added or Changed",
                [item("Shown.<span hidden>Secret.</span>")],
            )
        ],
    )
    page = parse_release_page(html, page_url("8.4", "8.4.0"))
    assert page.items[0].text == "Shown." and "Secret" not in page.items[0].markdown()
