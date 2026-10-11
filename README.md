# MySQL Documentation MCP Server

MySQL 公式ドキュメント（[dev.mysql.com/doc](https://dev.mysql.com/doc/)）を AI エージェントが検索・参照できるようにする
[Model Context Protocol](https://modelcontextprotocol.io/) サーバー。
[AWS Documentation MCP Server](https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server) の MySQL 版を目指す。

対象は MySQL リファレンスマニュアルとサーバーのリリースノート（英語版）。
クエリ作成、設定値の確認、バージョンアップ時の変更点調査などで、エージェントが公式ドキュメントに基づいた（出典付きの）回答を出せるようにする。

> **ステータス: MVP 完成（M3）**。検索（`search_documentation`）・読み取り（`read_documentation` / `read_sections`）・`list_versions`・リリースノート（`get_release_notes`、リリースノート検索）が使える。PyPI 未公開。

## 使い方

Python 3.10 以上と [uv](https://docs.astral.sh/uv/) が必要。PyPI 公開前は GitHub から直接起動する。

```json
{
  "mcpServers": {
    "mysql-documentation": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/JunNishimura/mysql_documentation_mcp", "mysql-documentation-mcp"],
      "env": { "FASTMCP_LOG_LEVEL": "ERROR" }
    }
  }
}
```

Claude Code の場合:

```bash
claude mcp add mysql-documentation -- uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp
```

使用例:
- 「MySQL 8.4 で `sql_mode` のデフォルト値は？公式ドキュメントを引用して」
- 「8.0.36 から 8.4.6 にアップグレードするときの注意点を、リリースノートから洗い出して」
- 「`mysql_native_password` はどのバージョンで非推奨・無効化された？」

### 検索インデックス

検索は、公式の Info 形式アーカイブ（1 バージョン約 4 MB）をダウンロードして手元に作る全文検索インデックス（SQLite FTS5、1 バージョン約 30 MB）で行う。
初めてそのバージョンを検索したときに自動で作られる（2〜3 秒）。事前に作っておく場合:

```bash
uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp index 8.4 9.7
# 公開中の全バージョン: ... index all
```

リリースノートの検索（`search_documentation` の `doc_set="relnotes"`）は、系列（8.0 / 8.4 / 9.7 …）ごとに各リリースのページを取得して索引を作る
（リリースノートには一括アーカイブが無いため。初回は 8.0 で約 20 秒、以降は新しいリリースの分だけ取得）。事前に作っておく場合:

```bash
uvx --from git+https://github.com/JunNishimura/mysql_documentation_mcp mysql-documentation-mcp index --doc-set relnotes 8.0 8.4
```

### 環境変数

| 変数 | 説明 | 既定値 |
|---|---|---|
| `MYSQL_DOC_DEFAULT_VERSION` | バージョン未指定時に使うマニュアル（`lts` / `latest` / `8.4` 等） | `lts`（最新 LTS） |
| `MYSQL_DOC_CACHE_DIR` | ページキャッシュと検索インデックスの保存先 | OS 標準のキャッシュディレクトリ |
| `MYSQL_DOC_CACHE_TTL_DAYS` | キャッシュの有効日数 | `7` |
| `MYSQL_DOC_INDEX_TTL_DAYS` | 検索インデックスの更新確認の間隔（日） | `7` |
| `MYSQL_DOC_PRELOAD_VERSIONS` | 起動時にインデックスを用意するバージョン（カンマ区切り） | なし |
| `MYSQL_DOC_OFFLINE` | `1` でネットワークに出ず、キャッシュのみで応答 | `0` |
| `MYSQL_DOC_USER_AGENT` | HTTP User-Agent の上書き（403 が返る環境向け） | [設計書 §5.1](docs/02_design.md) 参照 |
| `FASTMCP_LOG_LEVEL` | ログレベル | `WARNING` |

## 開発

```bash
uv sync
uv run pytest                 # ユニットテスト（HTTP はモック）
uv run pytest --run-live      # dev.mysql.com に実際にアクセスするテスト
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/01_requirements.md](docs/01_requirements.md) | 目的・ユースケース・機能/非機能要件・スコープ |
| [docs/02_design.md](docs/02_design.md) | アーキテクチャ・ツール仕様・検索方式・モジュール設計・設定 |
| [docs/03_implementation_plan.md](docs/03_implementation_plan.md) | マイルストーン・テスト方針・評価・決定事項 |

## ツール

| ツール | 概要 | 時期 |
|---|---|---|
| `search_documentation` | バージョン指定でリファレンスマニュアルを全文検索。該当する小見出し・変数などの #アンカー付き URL を返す。`doc_set="relnotes"` でリリースノートの項目を検索（どのリリースで変わったか） | ✅ 実装済み |
| `read_documentation` | ドキュメントページを Markdown で取得（ページネーション対応） | ✅ 実装済み |
| `read_sections` | 巨大ページから指定見出し/アンカーのみ取得 | ✅ 実装済み |
| `list_versions` | 参照可能なバージョン一覧（LTS / Innovation、最新リリース） | ✅ 実装済み |
| `get_release_notes` | バージョン範囲（系列またぎ・Innovation 込み）の変更点を、カテゴリ・重要度・キーワードで絞って Markdown で返す | ✅ 実装済み |
| `lookup_reference` | システム変数・オプション・関数等をピンポイント参照 | 拡張 |
| `get_upgrade_guide` / `browse_toc` / `search_table` | 移行ガイド・目次ブラウズ・大きな表の検索 | 拡張 |

## ライセンス

本リポジトリのコードは [Apache License 2.0](LICENSE) で提供する。
MySQL ドキュメント本体は Oracle の著作物であり、本リポジトリおよび配布パッケージには含めない（利用者の環境で取得・キャッシュする）。
