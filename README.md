# MySQL Documentation MCP Server

MySQL 公式ドキュメント（[dev.mysql.com/doc](https://dev.mysql.com/doc/)）を AI エージェントが検索・参照できるようにする
[Model Context Protocol](https://modelcontextprotocol.io/) サーバー。
[AWS Documentation MCP Server](https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server) の MySQL 版を目指す。

クエリ作成、設定値の確認、バージョンアップ時の変更点調査などで、エージェントが公式ドキュメントに基づいた（出典付きの）回答を出せるようにする。

> **ステータス: 設計段階**（実装はまだありません）

## ドキュメント

| ドキュメント | 内容 |
|---|---|
| [docs/01_requirements.md](docs/01_requirements.md) | 目的・ユースケース・機能/非機能要件・スコープ |
| [docs/02_design.md](docs/02_design.md) | アーキテクチャ・ツール仕様・検索方式・モジュール設計・設定 |
| [docs/03_implementation_plan.md](docs/03_implementation_plan.md) | マイルストーン・テスト方針・評価・未決事項 |

## 提供予定のツール

| ツール | 概要 | 時期 |
|---|---|---|
| `search_documentation` | バージョン指定でマニュアル/リリースノートを全文検索 | MVP |
| `read_documentation` | ドキュメントページを Markdown で取得（ページネーション対応） | MVP |
| `read_sections` | 巨大ページから指定見出し/アンカーのみ取得 | MVP |
| `get_release_notes` | バージョン範囲の変更点をカテゴリ・キーワードで抽出 | MVP |
| `list_versions` | 参照可能なバージョン一覧（LTS / Innovation） | MVP |
| `lookup_reference` | システム変数・関数・エラーコード等をピンポイント参照 | 拡張 |
| `get_upgrade_guide` / `browse_toc` / `search_table` | 移行ガイド・目次ブラウズ・大きな表の検索 | 拡張 |
