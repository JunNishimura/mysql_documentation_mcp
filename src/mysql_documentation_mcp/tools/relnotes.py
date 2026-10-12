"""get_release_notes and release-notes search (see docs/how-it-works.md)."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.core.relnotes_index import (
    build_relnotes_index,
    read_relnotes_meta,
    search_relnotes,
)
from mysql_documentation_mcp.core.relnotes_parser import (
    Category,
    NoteItem,
    ReleasePage,
    parse_release_page,
    slugify,
)
from mysql_documentation_mcp.core.urls import refman_url
from mysql_documentation_mcp.core.versions import (
    Catalog,
    Release,
    Version,
    VersionCatalog,
    VersionError,
    newest_known_lts,
)
from mysql_documentation_mcp.tools.read import DocumentationError, DocumentService
from mysql_documentation_mcp.tools.search import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

# Each release is one page request; beyond this a call takes too long for a tool call.
MAX_RELEASES = 50
DEFAULT_EXCLUDED_CATEGORIES = frozenset({"bug"})


@dataclass(frozen=True)
class _Scope:
    directory: str  # release-notes series directory, e.g. "8.4"
    series: str | None  # restrict to this series (8.2 inside 8.4/), None for all


def _bound(spec: str, releases: list[Release], name: str) -> Version:
    """``8.4.6`` as given; a series such as ``8.0`` means its latest listed release."""
    try:
        version = Version.parse(spec)
    except VersionError as e:
        raise DocumentationError(f"{name}: {e}") from e
    if len(version.parts) >= 3:
        return version
    in_series = [r for r in releases if Version.parse(r.version).series == version.series]
    if not in_series:
        raise DocumentationError(
            f"{name}: no MySQL {version.series} releases are listed in the release notes. "
            "Series with release notes: " + ", ".join(_series_list(releases))
        )
    return Version.parse(in_series[-1].version)


def _series_list(releases: list[Release]) -> list[str]:
    return list(dict.fromkeys(Version.parse(r.version).series for r in releases))


def _category_keys(category: Category) -> set[str]:
    title = slugify(category.title)
    return {category.slug, title, title.removesuffix("-notes")}


def _category_matcher(
    categories: list[str] | None,
) -> tuple[Callable[[Category], bool], str, set[str]]:
    """(matcher, description, requested keys) for the ``categories`` argument."""
    if not categories:
        return (
            lambda c: c.slug not in DEFAULT_EXCLUDED_CATEGORIES,
            'all except "bug" (Bugs Fixed)',
            set(),
        )
    keys = {slugify(c) for c in categories if slugify(c)}
    if "all" in keys:
        return (lambda c: True, "all", set())
    return (lambda c: bool(_category_keys(c) & keys), ", ".join(sorted(keys)), keys)


def _bullet(markdown: str) -> str:
    lines = markdown.splitlines() or [""]
    return "\n".join(["- " + lines[0], *[("  " + line) if line else "" for line in lines[1:]]])


def _paginate(text: str, start_index: int, max_length: int) -> str:
    total = len(text)
    if start_index >= total:
        return f"<e>No more content available (total length: {total}).</e>"
    end = min(start_index + max_length, total)
    out = text[start_index:end]
    if end < total:
        out += (
            f"\n\n<e>Content truncated ({end} of {total} characters). Call get_release_notes "
            f"with the same arguments and start_index={end} to get more content.</e>"
        )
    return out


class ReleaseNotesService:
    def __init__(self, settings: Settings, docs: DocumentService, catalog: VersionCatalog) -> None:
        self._settings = settings
        self._docs = docs
        self.catalog = catalog
        # Parsed pages for paginated calls over the same range.
        self._pages: OrderedDict[str, ReleasePage] = OrderedDict()
        self._pages_max = 64
        self._locks: dict[str, asyncio.Lock] = {}

    async def _catalog(self) -> Catalog:
        catalog = await self.catalog.get()
        if not catalog.releases():
            raise DocumentationError(
                "The list of MySQL releases could not be read from https://dev.mysql.com/doc/."
            )
        return catalog

    async def page(self, release: Release) -> tuple[ReleasePage, str | None]:
        doc, cached, note = await self._docs.load_html(release.url)
        key = f"{doc.url}@{cached.fetched_at}"
        page = self._pages.get(key)
        if page is None:
            try:
                page = parse_release_page(cached.html, doc.url)
            except ValueError as e:
                raise DocumentationError(f"{doc.url} could not be read: {e}") from e
            self._pages[key] = page
            while len(self._pages) > self._pages_max:
                self._pages.popitem(last=False)
        else:
            self._pages.move_to_end(key)
        return page, note

    # ------------------------------------------------------------ get_release_notes

    async def get_release_notes(
        self,
        from_version: str,
        to_version: str,
        categories: list[str] | None = None,
        important_only: bool = False,
        keyword: str | None = None,
        max_length: int = 10_000,
        start_index: int = 0,
    ) -> str:
        catalog = await self._catalog()
        releases = catalog.releases()
        lower = _bound(from_version, releases, "from_version")
        upper = _bound(to_version, releases, "to_version")
        if lower >= upper:
            raise DocumentationError(
                f"from_version ({lower}) must be older than to_version ({upper}). "
                "Changes are listed for releases after from_version up to to_version."
            )
        selected = [r for r in releases if lower < Version.parse(r.version) <= upper]
        if not selected:
            raise DocumentationError(
                f"No MySQL releases after {lower} up to {upper} are listed. "
                f"Latest listed release: {releases[-1].version}."
            )
        if len(selected) > MAX_RELEASES:
            raise DocumentationError(
                f"The range covers {len(selected)} releases ({selected[0].version} to "
                f"{selected[-1].version}); at most {MAX_RELEASES} can be read in one call. "
                "Split it into smaller ranges, e.g. one series at a time."
            )

        matches_category, category_desc, requested = _category_matcher(categories)
        word = keyword.strip().casefold() if keyword and keyword.strip() else None

        def wanted(item: NoteItem) -> bool:
            if not matches_category(item.category):
                return False
            if important_only and not item.important:
                return False
            return word is None or word in item.text.casefold()

        notes: list[str] = []
        pages: list[ReleasePage] = []
        for release in selected:
            try:
                page, note = await self.page(release)
            except DocumentationError as e:
                notes.append(f"Release {release.version} could not be read: {e}")
                continue
            if note:
                notes.append(f"Release {release.version}: {note}")
            pages.append(page)
        if not pages:
            raise DocumentationError(
                "None of the release notes pages could be read. " + " ".join(notes)
            )

        body: list[str] = []
        empty: list[str] = []
        uncategorized: list[str] = []
        total = 0
        seen_keys: set[str] = set()
        for page in pages:
            for category in page.categories:
                seen_keys |= _category_keys(category)
            if not page.items:
                summary = f": {page.summary}" if page.summary else ""
                uncategorized.append(f"{page.version} ({page.url}){summary}")
                continue
            items = [i for i in page.items if wanted(i)]
            if not items:
                empty.append(page.version)
                continue
            total += len(items)
            heading = f"## MySQL {page.version}"
            details = ", ".join(x for x in (page.date, page.status) if x)
            body.append(f"{heading} ({details})" if details else heading)
            body.append(f"Source: {page.url}\n")
            for category in page.categories:
                in_category = [i for i in items if i.category is category]
                if not in_category:
                    continue
                body.append(f"### {category.title}")
                body.append(f"Source: {in_category[0].url}\n")
                body.extend(_bullet(i.markdown()) + "\n" for i in in_category)

        header = [
            f"# MySQL Release Notes: changes after {lower} up to {upper}",
            f"Releases: {len(selected)} ({selected[0].version} to {selected[-1].version})",
            f"Categories: {category_desc}",
        ]
        if important_only:
            header.append(
                "Only items marked Important Change, Incompatible Change or Important Note"
            )
        if word:
            header.append(f'Keyword: "{keyword}"')
        header.append(f"Matching items: {total}")
        if empty and total:
            header.append("Releases with no matching items: " + ", ".join(empty))
        header += [f"Release without a list of changes: {u}" for u in uncategorized]
        unknown = requested - seen_keys
        if unknown:
            header.append(
                "Note: no category matches "
                + ", ".join(sorted(unknown))
                + ". Categories in these releases: "
                + ", ".join(sorted({c.slug for p in pages for c in p.categories}))
            )
        header += [f"Note: {n}" for n in notes]
        hint = self._series_hint(catalog, lower, upper, pages)
        if hint:
            header.append(f"Note: {hint}")
        if not total:
            header.append(
                "No items matched. Try without important_only or keyword, or with more "
                'categories (categories=["all"] includes Bugs Fixed).'
            )
        text = "\n".join(header) + "\n\n" + "\n".join(body)
        return _paginate(text.rstrip() + "\n", start_index, max_length)

    @staticmethod
    def _series_hint(
        catalog: Catalog, lower: Version, upper: Version, pages: list[ReleasePage]
    ) -> str | None:
        series = {lower.series, *(Version.parse(p.version).series for p in pages)}
        if len(series) < 2:
            return None
        target = upper.series
        if target not in catalog.refman_versions:
            target = catalog.directory_of(upper.series) or target
        text = (
            f"This range crosses release series ({lower.series} to {upper.series}). Release "
            "notes list changes release by release; for an overview of what changed between "
            "series and what to check when upgrading, also read"
        )
        if target in catalog.refman_versions:
            new = refman_url(target, "mysql-nutshell.html")
            upgrading = refman_url(target, "upgrading.html")
            return (
                f"{text} What Is New in MySQL {target} ({new}) and Upgrading MySQL ({upgrading})."
            )
        return f"{text} the What Is New and Upgrading chapters of the MySQL Reference Manual."

    # ------------------------------------------------------------ search

    def index_path(self, directory: str) -> Path:
        return self._settings.cache_dir / "index" / f"relnotes-{directory}-en.sqlite"

    async def resolve_scope(self, version: str | None) -> _Scope:
        catalog = await self._catalog()
        releases = catalog.releases()
        text = (version or self._settings.default_version).strip().lower()
        if text in ("lts", "latest-lts"):
            series = catalog.latest_lts or newest_known_lts(list(catalog.relnotes))
            if series is None:
                raise DocumentationError("Could not determine the latest LTS version")
        elif text == "latest":
            ga = [r for r in releases if not r.early_access] or releases
            series = Version.parse(ga[-1].version).series
        else:
            try:
                series = Version.parse(text).series
            except VersionError as e:
                raise DocumentationError(str(e)) from e
        directory = catalog.directory_of(series)
        if directory is None:
            raise DocumentationError(
                f"No release notes for MySQL {series} are listed. Series with release notes: "
                + ", ".join(_series_list(releases))
            )
        return _Scope(directory, series if series != directory else None)

    async def ensure_index(self, directory: str, *, force: bool = False) -> tuple[Path, str | None]:
        """Build or update the search index of one release-notes directory.

        Only releases that are new since the last build are fetched; the index is
        otherwise reused (published notes rarely change).
        """
        catalog = await self._catalog()
        wanted = tuple(r.version for r in catalog.releases_in(directory))
        if not wanted:
            raise DocumentationError(f"No releases are listed for the {directory} release notes.")
        path = self.index_path(directory)
        meta = read_relnotes_meta(path)
        if meta and not force and meta.releases == wanted:
            return path, None
        async with self._locks.setdefault(directory, asyncio.Lock()):
            meta = read_relnotes_meta(path)
            if meta and not force and meta.releases == wanted:
                return path, None
            reuse = [v for v in wanted if meta and not force and v in meta.releases]
            pages: list[ReleasePage] = []
            failed: list[str] = []
            for release in catalog.releases_in(directory):
                if release.version in reuse:
                    continue
                try:
                    pages.append((await self.page(release))[0])
                except DocumentationError as e:
                    failed.append(f"{release.version} ({e})")
            if not pages and not reuse:
                if meta is not None:
                    return path, "The release notes index could not be updated; results may be old."
                raise DocumentationError(
                    f"The {directory} release notes could not be read: " + "; ".join(failed)
                )
            logger.info("Indexing %d release notes pages for %s", len(pages), directory)
            await asyncio.to_thread(build_relnotes_index, path, directory, pages, reuse)
        note = None
        if failed:
            note = (
                f"{len(failed)} release notes pages could not be read and are missing from the "
                "results: " + "; ".join(failed)
            )
        return path, note

    async def search(self, query: str, version: str | None, limit: int) -> SearchResponse:
        scope = await self.resolve_scope(version)
        path, note = await self.ensure_index(scope.directory)
        hits = await asyncio.to_thread(search_relnotes, path, query, limit, scope.series)
        if not hits:
            note = (note + " " if note else "") + (
                "No results. Use English technical terms (identifiers such as variable, "
                "option or statement names work best), or try fewer words."
            )
        label = scope.series or scope.directory
        return SearchResponse(
            query=query,
            version=label,
            manual=f"MySQL {scope.directory} Release Notes"
            + (f" (MySQL {scope.series} releases only)" if scope.series else ""),
            note=note,
            results=[
                SearchResult(
                    rank=i,
                    title=f"Changes in MySQL {h.version}" + (f" ({h.date})" if h.date else ""),
                    url=h.url,
                    section_number="",
                    breadcrumb=f"MySQL {scope.directory} Release Notes",
                    matched_section=h.category_title,
                    snippet=h.snippet,
                    release=h.version,
                )
                for i, h in enumerate(hits, start=1)
            ],
        )
