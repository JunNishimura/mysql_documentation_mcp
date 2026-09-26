"""Search quality on the real manual (docs/03_implementation_plan.md §3).

Each query lists acceptable pages; a query passes when one of them is in the top 3.
Run with ``pytest --run-live -s`` to see the misses.
"""

import pytest

from mysql_documentation_mcp.config import Settings
from mysql_documentation_mcp.core.fetcher import Fetcher
from mysql_documentation_mcp.tools.search import SearchService

pytestmark = pytest.mark.live

EVAL = [
    ("sql_mode default value", ["server-system-variables", "sql-mode"]),
    ("innodb_buffer_pool_size resize online", ["innodb-buffer-pool-resize", "innodb-parameters"]),
    ("window function frame specification", ["window-functions-frames"]),
    ("ALGORITHM=INSTANT add column", ["innodb-online-ddl-operations", "alter-table"]),
    ("mysql_native_password", ["native-pluggable-authentication"]),
    ("JSON_TABLE", ["json-table-functions"]),
    ("common table expression recursive", ["with"]),
    ("deadlock detection", ["innodb-deadlock-detection", "innodb-deadlocks"]),
    ("max_connections", ["server-system-variables", "too-many-connections"]),
    ("GROUP BY ONLY_FULL_GROUP_BY", ["group-by-handling"]),
    ("EXPLAIN ANALYZE", ["explain"]),
    ("invisible index", ["invisible-indexes"]),
    ("generated invisible primary key", ["create-table-gipks"]),
    ("character set utf8mb4 collation default", ["charset-server", "charset-unicode-utf8mb4"]),
    ("transaction isolation level repeatable read", ["innodb-transaction-isolation-levels"]),
    ("lock wait timeout", ["innodb-parameters", "server-system-variables"]),
    ("upgrade from 8.0 to 8.4", ["upgrading", "upgrade-prerequisites", "upgrade-paths"]),
    ("CREATE USER password expiration", ["create-user", "password-management"]),
    ("replication GTID", ["replication-gtids"]),
    ("slow query log", ["slow-query-log"]),
    (
        "binary log expire_logs_days",  # removed in 8.4: the removal notes are the answer
        ["replication-options-binary-log", "added-deprecated-removed", "mysql-nutshell"],
    ),
    ("partitioning by range", ["partitioning-range"]),
    ("foreign key ON DELETE CASCADE", ["create-table-foreign-keys"]),
    ("full-text search boolean mode", ["fulltext-boolean"]),
    ("LOAD DATA INFILE local", ["load-data", "load-data-local-security"]),
    ("performance_schema events_statements", ["performance-schema-statement-tables"]),
    ("optimizer hints", ["optimizer-hints"]),
    ("CHECK constraint", ["create-table-check-constraints"]),
    ("DATE_FORMAT", ["date-and-time-functions"]),
    ("--skip-grant-tables", ["server-options", "resetting-permissions"]),
]


def page(url: str) -> str:
    return url.split("/en/", 1)[1].split("#")[0].removesuffix(".html")


async def test_recall_at_3(settings: Settings):
    fetcher = Fetcher(settings)
    service = SearchService(settings, fetcher)
    misses = []
    try:
        for query, expected in EVAL:
            response = await service.search(query, "8.4", 3)
            pages = [page(r.url) for r in response.results]
            if not set(pages) & set(expected):
                misses.append((query, pages))
    finally:
        await fetcher.aclose()
    for query, pages in misses:
        print(f"MISS {query!r}: {pages}")
    recall = 1 - len(misses) / len(EVAL)
    print(f"Recall@3 = {recall:.2f}")
    assert recall >= 0.8
