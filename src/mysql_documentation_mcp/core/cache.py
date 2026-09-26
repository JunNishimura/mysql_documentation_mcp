"""On-disk cache of fetched documentation pages (docs/02_design.md §5.2).

The raw HTML is stored (gzip) rather than converted Markdown, so both
``read_documentation`` and ``read_sections`` can use it and converter changes
never serve stale output. dev.mysql.com sends no ETag / Last-Modified for pages,
so an expired entry is simply fetched again.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_FORMAT_VERSION = 1


@dataclass(frozen=True)
class CachedPage:
    url: str
    final_url: str
    html: str
    fetched_at: float
    etag: str | None = None
    last_modified: str | None = None

    def is_fresh(self, ttl_seconds: float, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) - self.fetched_at < ttl_seconds


class PageCache:
    def __init__(self, directory: Path) -> None:
        self._dir = directory / "pages"

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha1(url.encode()).hexdigest()
        return self._dir / f"{key}.json", self._dir / f"{key}.html.gz"

    def get(self, url: str) -> CachedPage | None:
        meta_path, body_path = self._paths(url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("format") != _FORMAT_VERSION or meta.get("url") != url:
                return None
            html = gzip.decompress(body_path.read_bytes()).decode("utf-8")
        except (OSError, ValueError, EOFError):
            return None
        return CachedPage(
            url=url,
            final_url=meta["final_url"],
            html=html,
            fetched_at=meta["fetched_at"],
            etag=meta.get("etag"),
            last_modified=meta.get("last_modified"),
        )

    def put(self, page: CachedPage) -> None:
        meta_path, body_path = self._paths(page.url)
        meta = {
            "format": _FORMAT_VERSION,
            "url": page.url,
            "final_url": page.final_url,
            "fetched_at": page.fetched_at,
            "etag": page.etag,
            "last_modified": page.last_modified,
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            # Write the body first and the metadata last (atomically), so a reader
            # never sees metadata pointing at a partially written body.
            _atomic_write(body_path, gzip.compress(page.html.encode("utf-8")))
            _atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
        except OSError as e:
            # A read-only or full cache directory must not break the tool call.
            logger.warning("Could not write page cache for %s: %s", page.url, e)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
