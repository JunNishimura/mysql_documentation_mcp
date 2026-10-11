"""MySQL versions: parsing, comparison and discovery (docs/02_design.md §5.4)."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from functools import total_ordering
from pathlib import Path

from bs4 import BeautifulSoup

from mysql_documentation_mcp.core.fetcher import Fetcher, FetchError
from mysql_documentation_mcp.core.urls import (
    DOC_INDEX_URL,
    LATEST_REFMAN_URL,
    parse_doc_url,
    refman_url,
    relnotes_url,
)

logger = logging.getLogger(__name__)

# Release track per series. It cannot be derived from the site: calendar versions do
# not tell LTS from Innovation (refman "MySQL Releases: Innovation and LTS"), and the
# release notes do not say it either. Update this table when a new series appears.
KNOWN_TRACKS: dict[str, str] = {
    "8.0": "GA",
    "8.4": "LTS",
    "9.7": "LTS",
    "26.7": "Innovation",
}
# Used only when https://dev.mysql.com/doc/ cannot be read.
FALLBACK_REFMAN_VERSIONS = ("8.0", "8.4", "9.7", "26.7")

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?\s*$")
_NEWS_RE = re.compile(r"news-(\d+)-(\d+)-(\d+)\.html$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CATALOG_FORMAT = 1
_RETRY_INCOMPLETE_SECONDS = 300


class VersionError(ValueError):
    pass


@total_ordering
@dataclass(frozen=True)
class Version:
    """``8.4`` or ``8.4.6``; also calendar versions such as ``26.7.1`` (9.x < 26.x)."""

    parts: tuple[int, ...]

    @classmethod
    def parse(cls, text: str) -> Version:
        match = _VERSION_RE.match(text)
        if not match:
            raise VersionError(f"Not a MySQL version: {text!r} (expected e.g. 8.4 or 8.4.6)")
        return cls(tuple(int(p) for p in match.groups() if p is not None))

    @property
    def series(self) -> str:
        return f"{self.parts[0]}.{self.parts[1]}"

    def __str__(self) -> str:
        return ".".join(str(p) for p in self.parts)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.parts < other.parts


@dataclass(frozen=True)
class Release:
    version: str  # "8.4.6"
    date: str  # "2025-07-22", "" if unknown
    url: str
    early_access: bool = False


@dataclass
class Catalog:
    refman_versions: list[str]
    # release-notes series directory -> releases in it (Innovation releases are listed
    # under the following LTS series: 8.1-8.3 under 8.4, 9.0-9.6 under 9.7).
    relnotes: dict[str, list[Release]] = field(default_factory=dict)
    latest_lts: str | None = None
    fetched_at: float = 0.0
    complete: bool = True  # False when some pages could not be read

    def releases(self) -> list[Release]:
        """Every release once, oldest first.

        A release can be listed in more than one directory (the 26.10 index also links
        26.7.x); the copy in its own series' directory wins.
        """
        chosen: dict[str, tuple[bool, Release]] = {}
        for directory, releases in self.relnotes.items():
            for release in releases:
                own = Version.parse(release.version).series == directory
                if release.version not in chosen or (own and not chosen[release.version][0]):
                    chosen[release.version] = (own, release)
        return sorted((r for _, r in chosen.values()), key=lambda r: Version.parse(r.version))

    def releases_in(self, directory: str) -> list[Release]:
        """Releases whose notes are read from the given series directory."""
        return [r for r in self.releases() if r in self.relnotes.get(directory, [])]

    def directory_of(self, series: str) -> str | None:
        """Release-notes directory holding a series' releases (8.2 -> 8.4)."""
        for release in reversed(self.releases()):
            if Version.parse(release.version).series == series:
                return next(d for d, rs in self.relnotes.items() if release in rs)
        return None


@dataclass(frozen=True)
class VersionInfo:
    version: str
    track: str
    has_refman: bool
    latest_release: str | None
    latest_release_date: str | None
    refman_url: str | None
    relnotes_url: str | None


def track_of(series: str, catalog: Catalog, relnotes_dir: str | None = None) -> str:
    if series in KNOWN_TRACKS:
        return KNOWN_TRACKS[series]
    if series == catalog.latest_lts:
        return "LTS"
    if relnotes_dir and relnotes_dir != series:
        # Innovation release notes are listed under the following LTS series.
        return "Innovation"
    releases = [r for r in catalog.releases() if Version.parse(r.version).series == series]
    if releases and all(r.early_access for r in releases):
        return "Early Access"
    return "unknown"


class VersionCatalog:
    """Discovers published versions from dev.mysql.com and caches them for a day."""

    def __init__(self, fetcher: Fetcher, cache_dir: Path, ttl_seconds: float = 86400) -> None:
        self._fetcher = fetcher
        self._path = cache_dir / "versions.json"
        self._ttl = ttl_seconds
        self._catalog: Catalog | None = None

    async def get(self) -> Catalog:
        now = time.time()
        if self._catalog is None:
            self._catalog = self._load()
        if self._catalog is not None and now - self._catalog.fetched_at < self._ttl:
            return self._catalog
        fresh = await self._discover()
        if fresh.complete:
            self._catalog = fresh
            self._save(fresh)
        elif self._catalog is None:
            # Partial data (site unreachable): use it, but only in memory and retry soon.
            fresh.fetched_at = now - self._ttl + _RETRY_INCOMPLETE_SECONDS
            self._catalog = fresh
        return self._catalog

    async def resolve_refman_version(self, spec: str | None, default: str) -> str:
        """Map ``None``/"lts"/"latest"/"8.4.6" to a published manual version such as "8.4"."""
        catalog = await self.get()
        text = (spec or default).strip().lower()
        available = sorted(catalog.refman_versions, key=Version.parse)
        if text in ("lts", "latest-lts"):
            lts = catalog.latest_lts or newest_known_lts(available)
            if lts is None:
                raise VersionError("Could not determine the latest LTS version")
            return lts
        if text == "latest":
            return available[-1]
        series = Version.parse(text).series
        if series not in available:
            raise VersionError(
                f"No MySQL {series} Reference Manual is published. Available manual versions: "
                + ", ".join(available)
            )
        return series

    async def versions(self) -> list[VersionInfo]:
        catalog = await self.get()
        series_set = set(catalog.refman_versions)
        for release in catalog.releases():
            series_set.add(Version.parse(release.version).series)
        infos = []
        for series in sorted(series_set, key=Version.parse, reverse=True):
            releases = [r for r in catalog.releases() if Version.parse(r.version).series == series]
            latest = releases[-1] if releases else None
            directory = next(
                (d for d, rs in catalog.relnotes.items() if any(r in rs for r in releases)), None
            )
            infos.append(
                VersionInfo(
                    version=series,
                    track=track_of(series, catalog, directory),
                    has_refman=series in catalog.refman_versions,
                    latest_release=latest.version if latest else None,
                    latest_release_date=(latest.date or None) if latest else None,
                    refman_url=refman_url(series) if series in catalog.refman_versions else None,
                    relnotes_url=relnotes_url(directory) if directory else None,
                )
            )
        return infos

    # ---------------------------------------------------------------- discovery

    async def _discover(self) -> Catalog:
        complete = True
        refman: list[str] = []
        series_dirs: list[str] = []
        try:
            page = await self._fetcher.fetch(DOC_INDEX_URL)
            refman, series_dirs = parse_doc_index(page.text)
        except FetchError as e:
            logger.warning("Could not read %s: %s", DOC_INDEX_URL, e)
            complete = False
        if not refman:
            refman = list(FALLBACK_REFMAN_VERSIONS)
            complete = False

        relnotes: dict[str, list[Release]] = {}
        for series in series_dirs or refman:
            try:
                page = await self._fetcher.fetch(relnotes_url(series))
            except FetchError as e:
                logger.warning("Could not read release notes index for %s: %s", series, e)
                complete = False
                continue
            try:
                served = parse_doc_url(page.final_url)
            except ValueError:
                continue
            if served.version != series:
                continue  # redirected to another series (e.g. retired Innovation series)
            relnotes[series] = parse_relnotes_index(page.text, relnotes_url(series))

        latest_lts = None
        try:
            page = await self._fetcher.fetch(LATEST_REFMAN_URL)
            latest_lts = parse_doc_url(page.final_url).version
        except (FetchError, ValueError) as e:
            logger.warning("Could not determine the latest LTS from %s: %s", LATEST_REFMAN_URL, e)
            complete = False
        return Catalog(
            refman_versions=refman,
            relnotes=relnotes,
            latest_lts=latest_lts,
            fetched_at=time.time(),
            complete=complete,
        )

    def _load(self) -> Catalog | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("format") != _CATALOG_FORMAT:
                return None
            return Catalog(
                refman_versions=list(data["refman_versions"]),
                relnotes={s: [Release(**r) for r in rs] for s, rs in data["relnotes"].items()},
                latest_lts=data.get("latest_lts"),
                fetched_at=float(data["fetched_at"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save(self, catalog: Catalog) -> None:
        data = {
            "format": _CATALOG_FORMAT,
            "refman_versions": catalog.refman_versions,
            "relnotes": {s: [asdict(r) for r in rs] for s, rs in catalog.relnotes.items()},
            "latest_lts": catalog.latest_lts,
            "fetched_at": catalog.fetched_at,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            logger.warning("Could not write %s: %s", self._path, e)


def newest_known_lts(available: list[str]) -> str | None:
    lts = [v for v in available if KNOWN_TRACKS.get(v) == "LTS"]
    return max(lts, key=Version.parse) if lts else None


def parse_doc_index(html: str) -> tuple[list[str], list[str]]:
    """Manual versions and release-notes series linked from https://dev.mysql.com/doc/."""
    refman: set[str] = set()
    relnotes: set[str] = set()
    for a in BeautifulSoup(html, "lxml").select("a[href]"):
        href = str(a["href"])
        m = re.search(r"/doc/refman/(\d+\.\d+)/en/", href)
        if m:
            refman.add(m.group(1))
        m = re.search(r"/doc/relnotes/mysql/(\d+\.\d+)/en/", href)
        if m:
            relnotes.add(m.group(1))
    return sorted(refman, key=Version.parse), sorted(relnotes, key=Version.parse)


def parse_relnotes_index(html: str, base_url: str) -> list[Release]:
    """Releases listed on a release-notes series index page, oldest first."""
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("#docs-body") or soup
    releases: dict[str, Release] = {}
    for a in body.select("a[href]"):
        href = str(a["href"])
        m = _NEWS_RE.search(href)
        if not m:
            continue
        version = ".".join(m.groups())
        text = a.get_text(" ", strip=True)
        date = _DATE_RE.search(text)
        releases[version] = Release(
            version=version,
            date=date.group(0) if date else "",
            url=base_url + m.group(0),
            early_access="early access" in text.lower(),
        )
    return sorted(releases.values(), key=lambda r: Version.parse(r.version))
