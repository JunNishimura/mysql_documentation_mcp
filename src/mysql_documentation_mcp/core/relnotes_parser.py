"""Parsing of MySQL Server release notes pages (see docs/how-it-works.md).

Structure of ``news-X-Y-Z.html``:

* The page title is ``Changes in MySQL 8.4.6 (2025-07-22)``; the parentheses may also
  carry a status (``2018-04-19, General Availability``, ``2026-09-18 (Early Access
  Release)``).
* Each category is a ``div.simplesect`` whose heading anchor is
  ``mysqld-{X}-{Y}-{Z}-{slug}`` (``mysqld-8-4-0-deprecation-removal``).
* Each change is a top-level ``li.listitem`` in that section. Items can contain nested
  lists, which belong to the item. Items have no anchors of their own.
* An item may start with a bold prefix such as ``Important Change:`` or
  ``Important Change; Group Replication:``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from mysql_documentation_mcp.core.converter import UnreadablePageError, fragment_to_markdown

_TITLE_RE = re.compile(r"Changes in MySQL (?P<version>\d+\.\d+\.\d+)\s*(?:\((?P<info>.*)\))?")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CATEGORY_ANCHOR_RE = re.compile(r"^mysqld-\d+-\d+-\d+-(?P<slug>[a-z0-9-]+)$")
_NEWS_PAGE_RE = re.compile(r"news-(\d+)-(\d+)-(\d+)\.html$")
# Item prefixes that mark changes needing attention when upgrading.
IMPORTANT_FLAGS = frozenset({"important change", "incompatible change", "important note"})
_MAX_SUMMARY = 500


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")


@dataclass(frozen=True)
class Category:
    slug: str  # e.g. "deprecation-removal"
    title: str  # e.g. "Deprecation and Removal Notes"
    anchor: str  # e.g. "mysqld-8-4-0-deprecation-removal"


@dataclass(eq=False)
class NoteItem:
    version: str
    date: str
    category: Category
    flags: tuple[str, ...]  # e.g. ("Important Change", "Group Replication")
    text: str  # plain text, for filtering and search
    page_url: str
    element: Tag = field(repr=False)
    _markdown: str | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"{self.page_url}#{self.category.anchor}" if self.category.anchor else self.page_url

    @property
    def important(self) -> bool:
        return any(f.casefold() in IMPORTANT_FLAGS for f in self.flags)

    def markdown(self) -> str:
        if self._markdown is None:
            self._markdown = fragment_to_markdown(self.element, self.page_url).strip()
        return self._markdown


@dataclass
class ReleasePage:
    version: str  # "8.4.6"
    date: str  # "2025-07-22", "" if not shown
    status: str  # "General Availability", "Early Access Release", ... ("" if none)
    title: str  # "Changes in MySQL 8.4.6 (2025-07-22)"
    url: str
    categories: list[Category]
    items: list[NoteItem]
    # Page text when the release has no categorized changes (e.g. a security update
    # of the docker image only, such as 8.4.12); "" otherwise.
    summary: str = ""


def _flags(item: Tag) -> tuple[str, ...]:
    first = item.find("p")
    if first is None:
        return ()
    lead = next((c for c in first.children if not (isinstance(c, str) and not c.strip())), None)
    if not isinstance(lead, Tag) or lead.name not in ("span", "strong", "b"):
        return ()
    text = _clean_text(lead.get_text())
    if not text.endswith(":"):
        return ()
    return tuple(f.strip() for f in text.rstrip(":").split(";") if f.strip())


def _category(section: Tag) -> Category:
    heading = section.find(["h2", "h3", "h4", "h5"])
    title = _clean_text(heading.get_text(" ")) if heading else ""
    anchor_tag = (heading or section).find("a", attrs={"name": True})
    anchor = str(anchor_tag["name"]) if anchor_tag else ""
    match = _CATEGORY_ANCHOR_RE.match(anchor)
    return Category(slug=match["slug"] if match else slugify(title), title=title, anchor=anchor)


def parse_release_page(html: str, url: str) -> ReleasePage:
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("#docs-body")
    if body is None or not body.get_text(strip=True):
        raise UnreadablePageError("the page has no documentation content (#docs-body)")
    for tag in body.select("script, style, a.indexterm"):
        tag.decompose()

    heading = body.find(["h1", "h2"])
    title = _clean_text(heading.get_text(" ")) if heading else ""
    match = _TITLE_RE.search(title)
    if match:
        version = match["version"]
        info = match["info"] or ""
    else:
        page = _NEWS_PAGE_RE.search(url)
        if page is None:
            raise UnreadablePageError(f"not a release notes page: {title or url}")
        version, info = ".".join(page.groups()), ""
    date_match = _DATE_RE.search(info)
    date = date_match.group(0) if date_match else ""
    status = _clean_text(_DATE_RE.sub("", info).replace("(", " ").replace(")", " ")).strip(" ,")

    categories: list[Category] = []
    items: list[NoteItem] = []
    for section in body.select("div.simplesect"):
        if section.find_parent("div", class_="simplesect") is not None:
            continue
        category = _category(section)
        categories.append(category)
        for li in section.select("li.listitem"):
            if li.find_parent("li") is not None:
                continue  # nested list inside an item
            items.append(
                NoteItem(
                    version=version,
                    date=date,
                    category=category,
                    flags=_flags(li),
                    text=_clean_text(li.get_text(" ")),
                    page_url=url,
                    element=li,
                )
            )
    summary = ""
    if not categories:
        summary = _clean_text(body.get_text(" "))
        if title and summary.startswith(title):
            summary = summary[len(title) :].strip()
        if len(summary) > _MAX_SUMMARY:
            summary = summary[: _MAX_SUMMARY - 3].rstrip() + "..."
    return ReleasePage(
        version=version,
        date=date,
        status=status,
        title=title,
        url=url,
        categories=categories,
        items=items,
        summary=summary,
    )
