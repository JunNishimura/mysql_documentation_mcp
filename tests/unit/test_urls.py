import pytest

from mysql_documentation_mcp.core.urls import (
    InvalidDocumentationUrlError,
    is_allowed_url,
    parse_doc_url,
    version_mismatch_error,
)

REFMAN = "https://dev.mysql.com/doc/refman/8.4/en/select.html"


def test_parse_refman_url():
    doc = parse_doc_url(REFMAN + "#select-into")
    assert doc.url == REFMAN
    assert (doc.doc_set, doc.version, doc.page, doc.fragment) == (
        "refman",
        "8.4",
        "select.html",
        "select-into",
    )
    assert doc.manual_name == "MySQL 8.4 Reference Manual"


def test_parse_relnotes_url():
    doc = parse_doc_url("https://dev.mysql.com/doc/relnotes/mysql/9.7/en/news-9-6-0.html")
    assert (doc.doc_set, doc.version, doc.page) == ("relnotes", "9.7", "news-9-6-0.html")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://dev.mysql.com/doc/refman/8.4/en/select.html", REFMAN),
        (REFMAN + "?foo=bar", REFMAN),
        ("  " + REFMAN + " ", REFMAN),
        ("https://dev.mysql.com/doc/refman/8.4/en", "https://dev.mysql.com/doc/refman/8.4/en/"),
        (
            "https://dev.mysql.com/doc/refman/8.4/en/index.html",
            "https://dev.mysql.com/doc/refman/8.4/en/",
        ),
        ("https://dev.mysql.com/doc/refman/26.7/en/", "https://dev.mysql.com/doc/refman/26.7/en/"),
    ],
)
def test_normalization(raw, expected):
    assert parse_doc_url(raw).url == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucketnamingrules.html",
        "https://dev.mysql.com/doc/refman/8.0/ja/select.html",  # Japanese manual: out of scope
        "https://dev.mysql.com/doc/mysql-shell/8.4/en/",  # other doc sets: out of scope
        "https://dev.mysql.com/doc/mysql-errors/8.4/en/server-error-reference.html",
        "https://dev.mysql.com/doc/refman/8.4/en/../../../etc/passwd",
        "https://dev.mysql.com/doc/refman/8.4/en/select.txt",
        "https://dev.mysql.com.evil.example/doc/refman/8.4/en/select.html",
        "https://dev.mysql.com/doc/refman/latest/en/select.html",
        "file:///etc/passwd",
    ],
)
def test_rejects_out_of_scope_urls(raw):
    with pytest.raises(InvalidDocumentationUrlError):
        parse_doc_url(raw)


def test_is_allowed_url():
    assert is_allowed_url(REFMAN)
    assert is_allowed_url("https://downloads.mysql.com/docs/mysql-8.4.info.zip")
    assert not is_allowed_url("https://example.com/")
    assert not is_allowed_url("https://downloads.mysql.com/archives/")


def test_retired_manual_redirect_is_an_error():
    requested = parse_doc_url("https://dev.mysql.com/doc/refman/5.7/en/select.html")
    message = version_mismatch_error(
        requested, "https://dev.mysql.com/doc/refman/9.7/en/select.html"
    )
    assert message is not None
    assert "5.7" in message and "9.7" in message


def test_same_version_redirect_is_fine():
    requested = parse_doc_url("https://dev.mysql.com/doc/refman/8.4/en/index.html")
    assert version_mismatch_error(requested, "https://dev.mysql.com/doc/refman/8.4/en/") is None


def test_relnotes_series_redirect_is_fine():
    # Innovation release notes live under the following LTS series directory.
    requested = parse_doc_url("https://dev.mysql.com/doc/relnotes/mysql/8.3/en/news-8-3-0.html")
    served = "https://dev.mysql.com/doc/relnotes/mysql/8.4/en/news-8-3-0.html"
    assert version_mismatch_error(requested, served) is None


def test_relnotes_redirect_to_other_page_is_an_error():
    requested = parse_doc_url("https://dev.mysql.com/doc/relnotes/mysql/8.3/en/news-8-3-0.html")
    served = "https://dev.mysql.com/doc/relnotes/mysql/8.4/en/"
    assert version_mismatch_error(requested, served) is not None


def test_redirect_out_of_scope_is_an_error():
    requested = parse_doc_url(REFMAN)
    assert version_mismatch_error(requested, "https://dev.mysql.com/doc/") is not None
