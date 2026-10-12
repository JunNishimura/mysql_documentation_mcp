"""search_documentation / list_versions (see docs/how-it-works.md)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.core import indexer
from mysql_documentation_mcp.core.fetcher import Fetcher, FetchError
from mysql_documentation_mcp.core.info import InfoFormatError, read_info_archive
from mysql_documentation_mcp.core.urls import info_archive_url
from mysql_documentation_mcp.core.versions import VersionCatalog, VersionError
from mysql_documentation_mcp.tools.read import DocumentationError

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    rank: int
    title: str = Field(description="Page title with section number")
    url: str = Field(description="Page URL; includes #anchor when a sub-section matched")
    section_number: str
    breadcrumb: str = Field(description="Parent sections of the page")
    matched_section: str | None = Field(
        description="Sub-section or reference entry (e.g. a system variable) that matched; "
        "pass the URL's #anchor to read_sections to read just that part"
    )
    snippet: str = Field(description="Excerpt with matched terms in **bold**")
    release: str | None = Field(
        default=None, description='Release the item belongs to (release notes only, e.g. "8.4.0")'
    )


class SearchResponse(BaseModel):
    query: str
    version: str = Field(description="Manual version that was searched")
    manual: str
    note: str | None = None
    results: list[SearchResult]


class VersionEntry(BaseModel):
    version: str
    track: str = Field(description='"LTS", "Innovation", "GA", "Early Access" or "unknown"')
    has_refman: bool = Field(description="Whether a Reference Manual is published for it")
    latest_release: str | None
    latest_release_date: str | None
    refman_url: str | None
    relnotes_url: str | None


class VersionsResponse(BaseModel):
    default_version: str = Field(description="Manual version used when none is given")
    latest_lts: str | None
    versions: list[VersionEntry]


@dataclass(frozen=True)
class _IndexState:
    path: Path
    note: str | None


class IndexManager:
    """Builds and refreshes the per-version search index from the Info archive."""

    def __init__(self, settings: Settings, fetcher: Fetcher) -> None:
        self._settings = settings
        self._fetcher = fetcher
        self._locks: dict[str, asyncio.Lock] = {}

    def path(self, version: str) -> Path:
        return self._settings.cache_dir / "index" / f"refman-{version}-en.sqlite"

    async def ensure(self, version: str, *, force: bool = False) -> _IndexState:
        path = self.path(version)
        ttl = self._settings.index_ttl_days * 86400
        meta = indexer.read_meta(path)
        if meta and not force and time.time() - meta.built_at < ttl:
            return _IndexState(path, None)
        async with self._locks.setdefault(version, asyncio.Lock()):
            meta = indexer.read_meta(path)  # another call may have refreshed it
            if meta and not force and time.time() - meta.built_at < ttl:
                return _IndexState(path, None)
            try:
                result = await self._fetcher.fetch(
                    info_archive_url(version),
                    last_modified=meta.source_last_modified if meta and not force else None,
                )
                if result.not_modified and meta:
                    await asyncio.to_thread(indexer.touch_meta, path)
                    return _IndexState(path, None)
                logger.info("Building search index for MySQL %s", version)
                await asyncio.to_thread(_build, path, version, result.content, result.last_modified)
                return _IndexState(path, None)
            except (FetchError, InfoFormatError) as e:
                if meta is None:
                    raise DocumentationError(
                        f"The search index for MySQL {version} could not be built: {e}"
                    ) from e
                logger.warning("Using the existing index for %s: %s", version, e)
                return _IndexState(
                    path, f"The search index could not be refreshed ({e}); results may be old."
                )


def _build(path: Path, version: str, archive: bytes, last_modified: str | None) -> None:
    indexer.build_index(path, version, read_info_archive(archive), last_modified)


class SearchService:
    def __init__(self, settings: Settings, fetcher: Fetcher) -> None:
        self._settings = settings
        self.catalog = VersionCatalog(fetcher, settings.cache_dir)
        self.indexes = IndexManager(settings, fetcher)

    async def resolve_version(self, version: str | None) -> str:
        try:
            return await self.catalog.resolve_refman_version(
                version, self._settings.default_version
            )
        except VersionError as e:
            raise DocumentationError(str(e)) from e

    async def search(self, query: str, version: str | None, limit: int) -> SearchResponse:
        resolved = await self.resolve_version(version)
        state = await self.indexes.ensure(resolved)
        hits = await asyncio.to_thread(indexer.search, state.path, query, limit)
        note = state.note
        if not hits:
            note = (
                (note + " " if note else "")
                + "No results. Use English technical terms (identifiers such as variable, "
                "function or statement names work best), or try fewer words."
            )
        return SearchResponse(
            query=query,
            version=resolved,
            manual=f"MySQL {resolved} Reference Manual",
            note=note,
            results=[
                SearchResult(
                    rank=i,
                    title=h.title,
                    url=h.url,
                    section_number=h.section_number,
                    breadcrumb=h.breadcrumb,
                    matched_section=h.heading,
                    snippet=h.snippet,
                )
                for i, h in enumerate(hits, start=1)
            ],
        )

    async def list_versions(self) -> VersionsResponse:
        infos = await self.catalog.versions()
        catalog = await self.catalog.get()
        return VersionsResponse(
            default_version=await self.resolve_version(None),
            latest_lts=catalog.latest_lts,
            versions=[VersionEntry(**vars(v)) for v in infos],
        )
