# 03. 実装計画

## 1. マイルストーン

### M0: 事前調査（実装前に必ず実施）

設計中に「要検証」とした点を、実サイトを見て確定する。結果は `docs/02_design.md` に反映する。

- [ ] dev.mysql.com の本文コンテナ・ナビ要素の HTML セレクタ
- [ ] オフライン HTML アーカイブ（`*.html-chapter.zip` 等）の提供有無・URL・サイズ（バージョン別）
- [ ] アンカー ID の命名規則（`sysvar_*`, `statvar_*`, `option_mysqld_*`, 関数、エラーコード）
- [ ] リリースノートの見出し構成（カテゴリ見出しの表記揺れ、"Important Change" 等の接頭辞）
- [ ] バージョン一覧と LTS/Innovation 区分を機械的に取得できるページ
- [ ] `robots.txt` と利用規約（Terms of Use）上のクロール・キャッシュの扱い
- [ ] サイト内検索（案 A）の URL 形式と安定性（フォールバックとして採用するか判断）

> 注: 本設計を作成した環境からは dev.mysql.com にネットワーク接続できなかったため、上記は未確認。

### M1: 骨格と読み取り（MVP-1）

- [ ] `pyproject.toml`（uv, エントリポイント `mysql-documentation-mcp`）、ruff/pyright/pytest 設定、CI（GitHub Actions）
- [ ] `core/fetcher.py`（許可リスト・リダイレクト検証・UA・リトライ）
- [ ] `core/converter.py`（本文抽出・Markdown 変換・ページ内目次・セクション抽出）
- [ ] `core/cache.py`
- [ ] ツール: `read_documentation`, `read_sections`
- [ ] MCP Inspector と Claude Code で動作確認

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
| ユニット | バージョンのパース/比較（`5.7` < `8.0.36` < `8.4` < `9.7.1` < `26.7`）、URL 許可リスト、ページネーション境界、セクション抽出、リリースノート分類 |
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
