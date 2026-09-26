import pytest

from mysql_documentation_mcp.core.converter import (
    UnreadablePageError,
    contents_summary,
    extract_sections,
    page_to_markdown,
    parse_page,
)

URL = "https://dev.mysql.com/doc/refman/8.4/en/widget-statement.html"


@pytest.fixture
def page(refman_html):
    return parse_page(refman_html, URL)


def test_metadata(page):
    assert page.manual == "MySQL 8.4 Reference Manual"
    assert page.title == "15.9.1 WIDGET Statement"
    assert [(h.level, h.text, h.anchor) for h in page.headings] == [
        (3, "15.9.1 WIDGET Statement", "widget-statement"),
        (4, "Widget Names", "widget-names"),
        (4, "Widget Options", "widget-options"),
        (4, "Widget Limits", "widget-limits"),
    ]
    assert page.headings[0].title == "WIDGET Statement"
    assert page.reference_anchors == ["sysvar_widget_limit", "function_widget-count"]


def test_rejects_pages_without_docs_body():
    with pytest.raises(UnreadablePageError):
        parse_page("<html><title>Technical Difficulties</title><body>x</body></html>", URL)


def test_markdown_content(page):
    md = page_to_markdown(page)
    assert md.startswith("### 15.9.1 WIDGET Statement\n")
    # Syntax blocks are kept verbatim with the language from the class attribute.
    assert "```sql\nWIDGET [FAST | SLOW] widget_name\n    [WITH option_list]\n```" in md
    assert "```terminal\n# not a heading, just a shell comment\n$> widget --list\n```" in md
    # Relative links become absolute; link titles are dropped.
    assert "(https://dev.mysql.com/doc/refman/8.4/en/gadgets.html#gadget-intro)" in md
    assert 'WIDGET Statement")' not in md
    assert "> **Note:**\n>\n> Widgets are fictional." in md
    # The property table sits inside a list item, so it is indented.
    assert "  | Property | Value |\n  | --- | --- |\n  | System Variable | `widget_limit` |" in md
    for unwanted in ("Breadcrumb", "Version switcher", "Footer text", "tracking", "id1001"):
        assert unwanted not in md


def test_contents_summary(page):
    summary = contents_summary(page)
    assert "- 15.9.1 WIDGET Statement (#widget-statement)" in summary
    assert "  - Widget Options (#widget-options)" in summary
    assert "Reference entries on this page: 2" in summary


def test_heading_section_stops_at_next_sibling_heading(page):
    [section] = extract_sections(page, ["widget-names"])
    assert section.found
    assert section.title == "Widget Names"
    assert section.content.startswith("#### Widget Names")
    # The shell comment inside a code block does not end the section.
    assert "Names text beta." in section.content
    assert "Options text gamma." not in section.content


def test_heading_section_includes_reference_entries(page):
    [section] = extract_sections(page, ["Widget Options"])
    assert "Limit text delta." in section.content
    assert "Count text epsilon." in section.content
    assert "Limits text zeta." not in section.content


def test_top_heading_section_spans_subsections(page):
    [section] = extract_sections(page, ["WIDGET Statement"])  # title without number
    assert section.anchor == "widget-statement"
    assert "Limits text zeta." in section.content


def test_last_section_runs_to_end(page):
    [section] = extract_sections(page, ["#widget-limits"])
    assert section.content.strip().endswith("Limits text zeta.")


def test_partial_title_match(page):
    [section] = extract_sections(page, ["limits"])
    assert section.anchor == "widget-limits"


def test_reference_entry(page):
    [section] = extract_sections(page, ["sysvar_widget_limit"])
    assert section.found
    assert section.title == "widget_limit"
    assert section.anchor == "sysvar_widget_limit"
    assert "| Default Value | `42` |" in section.content
    assert "Limit text delta." in section.content
    assert "Count text epsilon." not in section.content


def test_reference_entry_tolerates_separator_and_case(page):
    [section] = extract_sections(page, ["FUNCTION_WIDGET_COUNT"])
    assert section.found
    assert section.anchor == "function_widget-count"
    assert section.title == "WIDGET_COUNT(expr)"


def test_not_found_lists_candidates(page):
    [section] = extract_sections(page, ["no such thing"])
    assert not section.found
    assert "was not found" in section.content
    assert "- Widget Options (#widget-options)" in section.content


def test_ambiguous_partial_match_is_not_guessed(page):
    [section] = extract_sections(page, ["widget"])
    assert not section.found
    assert "Did you mean one of these?" in section.content
