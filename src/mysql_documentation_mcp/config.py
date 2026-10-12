"""Settings read from environment variables (see README.md, Configuration)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir

from mysql_documentation_mcp import __version__

APP_NAME = "mysql-documentation-mcp"
REPOSITORY_URL = "https://github.com/JunNishimura/mysql_documentation_mcp"
# dev.mysql.com / downloads.mysql.com answer 403 to unknown User-Agents (including
# httpx's default and the bare product token below), while standard library clients
# are accepted. The standard "Python-urllib/X.Y" token comes first, followed by this
# server's own product token and repository URL so requests stay identifiable.
DEFAULT_USER_AGENT = (
    f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor} "
    f"{APP_NAME}/{__version__} (+{REPOSITORY_URL})"
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as e:
        raise ValueError(f"{name} must be a number, got {value!r}") from e


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    cache_ttl_days: float = 7.0
    # "lts", "latest" or a version such as "8.4"; used when a tool gets no version.
    default_version: str = "lts"
    index_ttl_days: float = 7.0
    preload_versions: tuple[str, ...] = ()
    offline: bool = False
    user_agent: str = DEFAULT_USER_AGENT
    # Minimum interval between requests to the same host (2 req/s by default).
    min_request_interval: float = 0.5
    request_timeout: float = 30.0
    max_retries: int = 3
    retry_backoff_base: float = 1.0

    @classmethod
    def from_env(cls) -> Settings:
        cache_dir = os.environ.get("MYSQL_DOC_CACHE_DIR") or user_cache_dir(APP_NAME)
        return cls(
            cache_dir=Path(cache_dir).expanduser(),
            cache_ttl_days=_env_float("MYSQL_DOC_CACHE_TTL_DAYS", 7.0),
            default_version=(os.environ.get("MYSQL_DOC_DEFAULT_VERSION") or "lts").strip(),
            index_ttl_days=_env_float("MYSQL_DOC_INDEX_TTL_DAYS", 7.0),
            preload_versions=tuple(
                v.strip()
                for v in os.environ.get("MYSQL_DOC_PRELOAD_VERSIONS", "").split(",")
                if v.strip()
            ),
            offline=_env_bool("MYSQL_DOC_OFFLINE"),
            user_agent=os.environ.get("MYSQL_DOC_USER_AGENT") or DEFAULT_USER_AGENT,
        )
