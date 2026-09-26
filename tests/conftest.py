from __future__ import annotations

from pathlib import Path

import pytest

from mysql_documentation_mcp.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests against dev.mysql.com"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="needs --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        cache_dir=tmp_path / "cache",
        min_request_interval=0.0,
        retry_backoff_base=0.0,
        request_timeout=5.0,
    )


@pytest.fixture
def refman_html() -> str:
    return (FIXTURES / "refman_page.html").read_text(encoding="utf-8")
