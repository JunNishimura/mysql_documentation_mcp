"""read_documentation / read_sections (docs/02_design.md §3.2, §3.3)."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.core.cache import CachedPage, PageCache
from mysql_documentation_mcp.core.converter import (
    ParsedPage,
    contents_summary,
    extract_sections,
    page_to_markdown,
    parse_page,
)
from mysql_documentation_mcp.core.fetcher import Fetcher, FetchError
from mysql_documentation_mcp.core.urls import DocUrl, parse_doc_url, version_mismatch_error

logger = logging.getLogger(__name__)


class DocumentationError(ValueError):
    """An expected failure whose message is meant for the agent."""


class SectionResult(BaseModel):
    requested: str = Field(description="The section name or anchor that was requested")
    found: bool
    title: str = Field(description="Heading or entry title of the matched section")
    anchor: str | None = Field(description="Anchor of the section; append as #anchor to cite it")
    url: str = Field(description="URL of the section, for citation")
    content: str = Field(description="Section content as Markdown, or a not-found explanation")


class SectionsResult(BaseModel):
    source_url: str
    manual: str = Field(
        description='Manual name including the version, e.g. "MySQL 8.4 Reference Manual"'
    )
    page_title: str
    note: str | None = Field(default=None, description="Caveats such as content served from cache")
    sections: list[SectionResult]


@dataclass
class _LoadedPage:
    doc: DocUrl
    cached: CachedPage
    parsed: ParsedPage
    note: str | None
    markdown: str | None = None


def _format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class DocumentService:
    def __init__(self, settings: Settings, fetcher: Fetcher | None = None) -> None:
        self._settings = settings
        self._fetcher = fetcher or Fetcher(settings)
        self._cache = PageCache(settings.cache_dir)
        # Parsing and converting a large page takes seconds (server-system-variables.html
        # is ~1 MB), so recently used pages are kept in memory for paginated reads.
        self._recent: OrderedDict[str, _LoadedPage] = OrderedDict()
        self._recent_max = 8

    async def aclose(self) -> None:
        await self._fetcher.aclose()

    async def _load(self, raw_url: str) -> _LoadedPage:
        try:
            doc = parse_doc_url(raw_url)
        except ValueError as e:
            raise DocumentationError(str(e)) from e

        ttl = self._settings.cache_ttl_days * 86400
        cached = self._cache.get(doc.url)
        note = None
        if cached is None or not cached.is_fresh(ttl):
            try:
                result = await self._fetcher.fetch(doc.url)
            except FetchError as e:
                if cached is None:
                    raise DocumentationError(str(e)) from e
                logger.warning("Serving stale cache for %s: %s", doc.url, e)
                note = (
                    f"Could not refresh this page ({e}); showing the cached copy fetched "
                    f"{_format_time(cached.fetched_at)}."
                )
            else:
                mismatch = version_mismatch_error(doc, result.final_url)
                if mismatch:
                    raise DocumentationError(mismatch)
                cached = CachedPage(
                    url=doc.url,
                    final_url=result.final_url,
                    html=result.text,
                    fetched_at=time.time(),
                )
                self._cache.put(cached)

        key = f"{doc.url}@{cached.fetched_at}"
        loaded = self._recent.get(key)
        if loaded is None:
            try:
                parsed = parse_page(cached.html, cached.final_url)
            except ValueError as e:
                raise DocumentationError(f"{cached.final_url} could not be read: {e}") from e
            loaded = _LoadedPage(doc=doc, cached=cached, parsed=parsed, note=note)
            self._recent[key] = loaded
            while len(self._recent) > self._recent_max:
                self._recent.popitem(last=False)
        else:
            self._recent.move_to_end(key)
            loaded.note = note
        return loaded

    async def read_documentation(self, url: str, max_length: int, start_index: int) -> str:
        page = await self._load(url)
        if page.markdown is None:
            summary = contents_summary(page.parsed)
            body = page_to_markdown(page.parsed)
            page.markdown = f"{summary}\n\n---\n\n{body}" if summary else body
        return format_page(page, start_index, max_length)

    async def read_sections(self, url: str, sections: list[str], max_length: int) -> SectionsResult:
        page = await self._load(url)
        parsed = page.parsed
        results = []
        for section in extract_sections(parsed, sections):
            content = section.content
            if len(content) > max_length:
                content = (
                    content[:max_length]
                    + f"\n\n<e>Section truncated at {max_length} of {len(content)} characters. "
                    "Request a narrower sub-section, or raise max_length.</e>"
                )
            results.append(
                SectionResult(
                    requested=section.requested,
                    found=section.found,
                    title=section.title,
                    anchor=section.anchor,
                    url=f"{parsed.url}#{section.anchor}" if section.anchor else parsed.url,
                    content=content,
                )
            )
        return SectionsResult(
            source_url=parsed.url,
            manual=parsed.manual or page.doc.manual_name,
            page_title=parsed.title,
            note=page.note,
            sections=results,
        )


def format_page(page: _LoadedPage, start_index: int, max_length: int) -> str:
    parsed = page.parsed
    content = page.markdown or ""
    header = [
        f"MySQL Documentation: {parsed.title}",
        f"Source: {parsed.url}",
        f"Manual: {parsed.manual or page.doc.manual_name}",
    ]
    if page.note:
        header.append(f"Note: {page.note}")
    if page.doc.fragment and start_index == 0:
        header.append(
            f"The URL points to #{page.doc.fragment}; call read_sections with "
            f'["{page.doc.fragment}"] to read only that part.'
        )
    head = "\n".join(header)

    total = len(content)
    if start_index >= total:
        return f"{head}\n\n<e>No more content available (total length: {total}).</e>"
    end = min(start_index + max_length, total)
    result = f"{head}\n\n{content[start_index:end]}"
    if end < total:
        result += (
            f"\n\n<e>Content truncated ({end} of {total} characters). Call read_documentation "
            f"with start_index={end} to get more content.</e>"
        )
    return result
