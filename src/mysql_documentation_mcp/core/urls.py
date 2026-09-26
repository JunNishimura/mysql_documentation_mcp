"""Validation and parsing of MySQL documentation URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urlsplit

# Only the English reference manual and server release notes are in scope
# (docs/01_requirements.md §6).
_DOC_URL_RE = re.compile(
    r"^https://dev\.mysql\.com/doc/"
    r"(?P<doc_set>refman|relnotes/mysql)/(?P<version>[0-9]+\.[0-9]+)/en/"
    r"(?P<page>[A-Za-z0-9._-]*)$"
)
_DOWNLOADS_URL_RE = re.compile(r"^https://downloads\.mysql\.com/docs/[A-Za-z0-9._-]+$")

EXAMPLE_URLS = (
    "https://dev.mysql.com/doc/refman/8.4/en/select.html",
    "https://dev.mysql.com/doc/relnotes/mysql/8.4/en/news-8-4-0.html",
)


class InvalidDocumentationUrlError(ValueError):
    pass


@dataclass(frozen=True)
class DocUrl:
    """A normalized documentation page URL (without fragment)."""

    url: str
    doc_set: str  # "refman" | "relnotes"
    version: str  # manual version or release-notes series, e.g. "8.4"
    page: str  # e.g. "select.html"; "" for the manual's top page
    fragment: str  # anchor from the original URL, "" if none

    @property
    def manual_name(self) -> str:
        kind = "Reference Manual" if self.doc_set == "refman" else "Release Notes"
        return f"MySQL {self.version} {kind}"


def parse_doc_url(raw_url: str) -> DocUrl:
    """Validate a user-supplied URL and normalize it.

    ``http://`` is upgraded to ``https://`` and ``.../en/index.html`` is mapped to
    ``.../en/`` (where the site serves it). Anything outside the allowlist raises
    :class:`InvalidDocumentationUrlError`.
    """
    url = raw_url.strip()
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    url, fragment = urldefrag(url)
    if urlsplit(url).query:
        url = url.split("?", 1)[0]
    if url.endswith("/en"):
        url += "/"
    match = _DOC_URL_RE.match(url)
    if not match:
        raise InvalidDocumentationUrlError(
            f"Unsupported URL: {raw_url}. Only English MySQL Reference Manual and MySQL Server "
            "release notes pages are supported, for example: " + ", ".join(EXAMPLE_URLS)
        )
    page = match["page"]
    if page == "index.html":
        # The site serves the manual's top page at the directory URL.
        page = ""
        url = url[: -len("index.html")]
    elif page and not page.endswith(".html"):
        raise InvalidDocumentationUrlError(f"Unsupported URL: {raw_url}. URL must end with .html")
    doc_set = "refman" if match["doc_set"] == "refman" else "relnotes"
    return DocUrl(url=url, doc_set=doc_set, version=match["version"], page=page, fragment=fragment)


def is_allowed_url(url: str) -> bool:
    """Whether the fetcher may request (or be redirected to) ``url``."""
    url = urldefrag(url)[0].split("?", 1)[0]
    return bool(_DOC_URL_RE.match(url) or _DOWNLOADS_URL_RE.match(url))


def version_mismatch_error(requested: DocUrl, served_url: str) -> str | None:
    """Explain why the page served after redirects cannot stand in for the requested one.

    The site silently redirects retired manual versions to another version
    (``/doc/refman/5.7/en/select.html`` -> ``/doc/refman/9.7/en/select.html``).
    Returning that page as if it were the requested version would give wrong answers,
    so it is reported as an error instead. Release notes are exempt: their page name
    (``news-8-3-0.html``) identifies the release, only the series directory moves.
    """
    try:
        served = parse_doc_url(served_url)
    except InvalidDocumentationUrlError:
        return f"{requested.url} redirected to an unsupported URL: {served_url}"
    if served.doc_set != requested.doc_set:
        return f"{requested.url} redirected to a different document: {served.url}"
    if requested.doc_set == "relnotes":
        if served.page != requested.page:
            return f"{requested.url} redirected to a different page: {served.url}"
        return None
    if served.version != requested.version:
        return (
            f"The MySQL {requested.version} Reference Manual is not published; the site "
            f"redirected {requested.url} to the {served.version} manual ({served.url}). "
            f"Content was not returned because it may not apply to MySQL {requested.version}. "
            f"If MySQL {served.version} is acceptable, read {served.url} explicitly."
        )
    return None
