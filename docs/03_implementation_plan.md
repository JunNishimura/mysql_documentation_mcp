# 03. 実装計画

## 1. マイルストーン

### M0: 事前調査 ✅ 完了（2026-09-26）

設計中に「要検証」とした点を実サイトで確認し、`docs/02_design.md` に反映済み。

| 確認項目 | 結果 | 反映先 |
|---|---|---|
| 本文コンテナ・ナビ要素のセレクタ | `#docs-body`。パンくず・バージョン切替・フッターはその外側 | 02 §5.3 |
| HTML 一括アーカイブ | **無し**（PDF / man / Info のみ）。代わりに Info（`mysql-{v}.info.zip`, 約 4 MB）を検索インデックス用に採用 | 02 §4.3 |
| アンカー ID の命名規則 | `option_*`, `sysvar_*`, `statvar_*`, `function_*`, `operator_*` 等。エラーコードは別ドキュメント（mysql-errors） | 02 §3.6 |
| リリースノートの構成 | カテゴリ節のアンカー `mysqld-X-Y-Z-{slug}`、項目は `li.listitem`、接頭辞は `<strong>`。Innovation のノートは次の LTS 系列ディレクトリにある | 02 §3.4, §5.5 |
| バージョン一覧・LTS 区分 | 一覧は `/doc/` から取得可能。LTS / Innovation の区分は機械的に取得できない → 同梱の対応表で持つ | 02 §5.4 |
| 存在しないバージョンの扱い | 最新 LTS（9.7）へリダイレクトされる → すり替わり検知が必要 | 02 §5.1 |
| `robots.txt` / 利用条件 | `/doc/` は禁止されていない。マニュアルの Legal Notices で再配布は禁止、内容を改変しない形式変換は許可 | 02 §10 |
| サイト内検索（案 A） | `/doc/search/?q=` は動作するが結果は最新版のみ → 採用しない | 02 §4.2 |

### M1: 骨格と読み取り（MVP-1） ✅ 完了

- [x] `pyproject.toml`（uv, エントリポイント `mysql-documentation-mcp`）、ruff/pyright/pytest 設定、CI（GitHub Actions）
- [x] `core/urls.py`（URL の許可リスト・正規化・バージョンすり替わり検知）
- [x] `core/fetcher.py`（許可リスト・リダイレクト検証・UA・レート制限・リトライ）
- [x] `core/converter.py`（本文抽出・Markdown 変換・ページ内目次・セクション抽出）
- [x] `core/cache.py`
- [x] ツール: `read_documentation`, `read_sections`
- [x] MCP クライアント（SDK の `Client`、stdio サブプロセス起動を含む）で動作確認。実サイトでの確認は `pytest --run-live`

M1 で分かったこと（02_design.md に反映済み）:
- サイトは多くの User-Agent に 403 を返す → 既定 UA を変更（§5.1）
- ドキュメントページは ETag / Last-Modified を返さない → ページキャッシュは TTL で再取得（§5.2）
- MCP Python SDK 2.x では `ToolError` 以外の例外はクライアントに「Error executing tool」としか伝わらない → 想定内のエラーは `ToolError` に変換して理由を返す

### M2: 検索（MVP-2）

- [ ] `core/versions.py` とツール `list_versions`
- [ ] `core/indexer.py`（アーカイブ取得 or クロール → FTS5）
- [ ] CLI `mysql-documentation-mcp index`
- [ ] ツール: `search_documentation`
- [ ] 検索品質の評価セット（§3）で確認

### M3: リリースノート（MVP-3）

- [ ] `core/relnotes_parser.py`（正規化・カテゴリ分類）
- [ ] ツール: `get_release_notes`（系列またぎ対応）
- [ ] `search_documentation(doc_set="relnotes")`

→ ここまでで v0.1.0 として PyPI 公開

### M4: 拡張

- [ ] `lookup_reference`（システム変数の属性表の構造化を含む）
- [ ] `get_upgrade_guide`, `browse_toc`, `search_table`
- [ ] （検討）Streamable HTTP トランスポート、Docker イメージ、ベクトル検索

現時点で対象外と決めたもの（需要が出たら再検討）: MySQL Shell / Router / Connector 等の doc_set 追加、日本語版マニュアル対応。

## 2. テスト方針

| 種別 | 内容 |
|---|---|
| ユニット | バージョンのパース/比較（`8.0.36` < `8.3.0` < `8.4` < `9.7.1` < `26.7` < `26.10.0`）、URL 許可リスト、ページネーション境界、セクション抽出、リリースノート分類 |
| HTTP モック | `respx` で fetcher のリトライ・リダイレクト拒否・キャッシュ再検証を検証 |
| フィクスチャ | 構造を再現した**自作の最小 HTML**を使う（公式ページのコピーはライセンス上置かない） |
| ライブ結合 | `pytest --run-live` のみで実行。実サイトの構造変化を検知する（CI では週 1 回のスケジュール実行） |
| MCP 結合 | MCP SDK のクライアントでサーバーを起動し、ツール一覧・スキーマ・呼び出しを確認 |

## 3. 評価（検索・回答品質）

代表的な質問と「期待する出典ページ」の組を 30 件程度用意し、検索の上位 3 件に含まれる割合（Recall@3）を計測する。例:

| 質問 | 期待する出典 |
|---|---|
| 8.4 で `mysql_native_password` はどうなった？ | 8.4 リリースノートの Deprecation and Removal / 認証プラグインのページ |
| `innodb_buffer_pool_size` はオンラインで変更できるか | `innodb-parameters.html#sysvar_innodb_buffer_pool_size`, バッファプールのリサイズのページ |
| ウィンドウ関数のフレーム指定の構文 | `window-functions-frames.html` |
| `ALGORITHM=INSTANT` で可能な ALTER の種類 | `innodb-online-ddl-operations.html` |
| 8.0 → 8.4 で削除されたサーバーオプション | 8.4 の「What Is New」ページ |

## 4. 決定事項

| 項目 | 決定 |
|---|---|
| 対象ドキュメント | リファレンスマニュアル＋サーバーのリリースノートのみ。MySQL Shell / Router / Connector 等は対象外（必要最低限から始める） |
| 言語 | 英語版マニュアルのみ。日本語版は対象外 |
| `version` 省略時の既定値 | 最新 LTS（`MYSQL_DOC_DEFAULT_VERSION` で上書き可） |
| 配布形態 | PyPI 公開、`uvx mysql-documentation-mcp` で起動。Docker イメージは当面作らない |
| 実装言語 | Python 3.10+ |
| ライセンス | Apache-2.0（参考実装の AWS 版と同じ） |
