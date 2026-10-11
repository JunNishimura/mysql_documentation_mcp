import pytest

from mysql_documentation_mcp.core.indexer import build_index, query_terms, read_meta, search
from tests.info_builder import SAMPLE_INDEX, SAMPLE_NODES, make_info

BASE = "https://dev.mysql.com/doc/refman/8.4/en/"


@pytest.fixture
def index(tmp_path):
    path = tmp_path / "refman-8.4-en.sqlite"
    build_index(path, "8.4", make_info(SAMPLE_NODES, SAMPLE_INDEX), "Fri, 25 Sep 2026 16:25:27 GMT")
    return path


def urls(hits):
    return [h.url for h in hits]


def test_meta(index):
    meta = read_meta(index)
    assert meta is not None
    assert (meta.version, meta.pages, meta.chunks) == ("8.4", 4, 7)
    assert meta.source_last_modified == "Fri, 25 Sep 2026 16:25:27 GMT"


def test_read_meta_of_missing_or_broken_index(tmp_path):
    assert read_meta(tmp_path / "missing.sqlite") is None
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not sqlite")
    assert read_meta(broken) is None


def test_reference_entry_is_found_by_identifier(index):
    [hit, *_] = search(index, "widget_limit", 5)
    assert hit.url == BASE + "widget-statement.html#sysvar_widget_limit"
    assert hit.heading == "widget_limit"
    assert hit.title == "1.1 WIDGET Statement"
    assert hit.section_number == "1.1"
    assert hit.breadcrumb == "1 Widgets"
    assert "**widget_limit**" in hit.snippet


def test_function_anchor_matches_underscore_spelling(index):
    expected = BASE + "widget-statement.html#function_widget-count"
    assert urls(search(index, "widget_count", 5))[0] == expected


def test_one_result_per_page(index):
    pages = [u.split("#")[0] for u in urls(search(index, "widget", 10))]
    assert len(pages) == len(set(pages))


def test_all_terms_preferred_then_any_term(index):
    # The page with both words comes first; pages with either word fill up the rest.
    hits = urls(search(index, "sprocket overload", 5))
    assert hits[0] == BASE + "widget-limits.html"
    assert BASE + "widget-statement.html#widget-statement-options" in hits[1:]
    assert urls(search(index, "sprocket overload", 1)) == [BASE + "widget-limits.html"]
    # No page has both words: falls back to pages with either.
    hits = urls(search(index, "sprocket frobnicated", 5))
    assert BASE + "widget-limits.html" in hits
    assert BASE + "widget-statement.html#function_widget-count" in hits


def test_index_terms_are_searchable(index):
    assert BASE + "widget-limits.html" in urls(search(index, "sprocket", 5))


def test_stemming(index):
    assert urls(search(index, "sprockets overloading", 1)) == [BASE + "widget-limits.html"]


def test_no_results(index):
    assert search(index, "nonexistentword", 5) == []
    assert search(index, "the of and", 5) == []


def test_top_node_gets_manual_title(index):
    hits = search(index, "Widget Manual", 10)
    assert any(h.url == BASE and h.title == "MySQL 8.4 Reference Manual" for h in hits)


@pytest.mark.parametrize(
    "query",
    ['AND OR NOT "unbalanced', "col:value", "NEAR(a b)", "a*", "-x ^y", "()", "'quoted'"],
)
def test_fts_syntax_in_queries_is_harmless(index, query):
    search(index, query, 5)  # must not raise


def test_query_terms():
    assert query_terms("How is the sql_mode set?") == ['("sql_mode" OR "sql mode")', '"set"']
    assert query_terms('"window frame" ROWS') == ['"window frame"', '"ROWS"']
    assert query_terms("ALGORITHM=INSTANT") == ['"ALGORITHM"', '"INSTANT"']
    # Quoted phrases come first; a stray quote is ignored.
    assert query_terms('say "hi""') == ['"hi"', '"say"']
