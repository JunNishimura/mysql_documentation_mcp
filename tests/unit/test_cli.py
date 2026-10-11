import pytest
import respx

from mysql_documentation_mcp.cli import main
from tests.info_builder import SAMPLE_NODES, make_info, zip_info
from tests.unit.test_versions import mock_site


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSQL_DOC_CACHE_DIR", str(tmp_path))


@respx.mock
def test_index_command(tmp_path, capsys):
    mock_site()
    respx.get("https://downloads.mysql.com/docs/mysql-8.4.info.zip").respond(
        200, content=zip_info(make_info(SAMPLE_NODES))
    )
    with pytest.raises(SystemExit) as exit_info:
        main(["index", "8.4", "5.7"])
    assert exit_info.value.code == 1  # 5.7 has no manual
    out, err = capsys.readouterr()
    assert "MySQL 8.4: 4 pages indexed" in out
    assert "5.7: No MySQL 5.7 Reference Manual is published" in err
    assert (tmp_path / "index" / "refman-8.4-en.sqlite").exists()


def test_version_flag(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("mysql-documentation-mcp ")
