from mysql_documentation_mcp.core.cache import CachedPage, PageCache

URL = "https://dev.mysql.com/doc/refman/8.4/en/select.html"


def test_roundtrip(tmp_path):
    cache = PageCache(tmp_path)
    assert cache.get(URL) is None
    page = CachedPage(url=URL, final_url=URL, html="<p>ünïcode</p>", fetched_at=1000.0)
    cache.put(page)
    assert cache.get(URL) == page


def test_freshness():
    page = CachedPage(url=URL, final_url=URL, html="", fetched_at=1000.0)
    assert page.is_fresh(ttl_seconds=60, now=1059)
    assert not page.is_fresh(ttl_seconds=60, now=1060)


def test_corrupt_entry_is_a_miss(tmp_path):
    cache = PageCache(tmp_path)
    cache.put(CachedPage(url=URL, final_url=URL, html="x", fetched_at=1.0))
    for body in (tmp_path / "pages").glob("*.html.gz"):
        body.write_bytes(b"not gzip")
    assert cache.get(URL) is None


def test_unwritable_cache_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    cache = PageCache(blocker)  # "pages" cannot be created under a regular file
    cache.put(CachedPage(url=URL, final_url=URL, html="x", fetched_at=1.0))
    assert cache.get(URL) is None
