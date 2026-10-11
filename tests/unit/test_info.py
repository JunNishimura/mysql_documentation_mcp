import pytest

from mysql_documentation_mcp.core.info import InfoFormatError, parse_info, read_info_archive
from tests.info_builder import SAMPLE_INDEX, SAMPLE_NODES, make_info, zip_info


@pytest.fixture
def nodes():
    return {n.name: n for n in parse_info(make_info(SAMPLE_NODES, SAMPLE_INDEX))}


def test_node_metadata(nodes):
    assert list(nodes) == ["Top", "widgets", "widget-statement", "widget-limits"]
    node = nodes["widget-statement"]
    assert node.title == "1.1 WIDGET Statement"
    assert node.section_number == "1.1"
    assert node.up == "widgets"
    assert node.page == "widget-statement.html"
    assert nodes["Top"].page == ""
    assert nodes["Top"].up is None


def test_chunks_are_split_at_anchors(nodes):
    chunks = nodes["widget-statement"].chunks
    assert [c.anchor for c in chunks] == [
        None,
        "widget-statement-options",
        "sysvar_widget_limit",
        "function_widget-count",
    ]
    headings = [c.heading for c in chunks[1:]]
    assert headings == ["Widget Options", "widget_limit", "WIDGET_COUNT(EXPR)"]
    assert "maximum number of gizmos" in chunks[2].text
    assert "frobnicated" not in chunks[2].text
    assert nodes["widget-statement"].anchors == [c.anchor for c in chunks[1:]]


def test_menus_and_cross_references_are_cleaned(nodes):
    intro = nodes["widget-statement"].chunks[0].text
    assert "Menu" not in intro and "widget-into::" not in intro
    # The syntax block right after the menu is kept.
    assert "WIDGET [FAST | SLOW] WIDGET_NAME" in intro
    assert "see widget-limits." in intro
    assert "*note" not in intro


def test_index_terms(nodes):
    assert nodes["widget-statement"].index_terms == ["widget_limit system variable"]
    assert nodes["widget-limits"].index_terms == ["sprocket"]
    # The index itself is not part of the last node's text.
    assert all("system variable" not in c.text for c in nodes["widget-limits"].chunks)


def test_bad_tag_table_offsets_are_rejected():
    data = make_info(SAMPLE_NODES)
    broken = data.replace(b"Node: widgets\x7f", b"Node: widgets\x7f1")  # shifts the offset
    with pytest.raises(InfoFormatError):
        parse_info(broken)


def test_missing_tag_table():
    with pytest.raises(InfoFormatError):
        parse_info(b"no tag table here")


def test_read_info_archive():
    data = make_info(SAMPLE_NODES)
    assert read_info_archive(zip_info(data)) == data
    with pytest.raises(InfoFormatError):
        read_info_archive(b"not a zip")
    with pytest.raises(InfoFormatError):
        read_info_archive(zip_info(data, name="readme.txt"))
