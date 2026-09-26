# 02. 設計

## 1. 全体方針

- 参考実装は [awslabs/mcp の aws-documentation-mcp-server](https://github.com/awslabs/mcp/tree/main/src/aws-documentation-mcp-server)。
  ツールの粒度（search / read / read_sections / search_table）、`start_index` + `max_length` による文字数ページネーション、
  URL 許可リスト、HTML→Markdown 変換の方式は踏襲する。
- AWS との最大の違いは **MySQL には公開された検索 API がない** こと。そのため検索は
  **利用者のマシン上に構築するローカル全文検索インデックス（SQLite FTS5）** で実現する（§4 で比較検討）。
- MySQL 固有の価値である **バージョン差分調査** のために、リリースノート専用ツールを設ける。

## 2. アーキテクチャ

```
┌──────────────────────┐   stdio (JSON-RPC)   ┌──────────────────────────────────────────────┐
│ MCP クライアント       │ ◀──────────────────▶ │ mysql-documentation-mcp (FastMCP / Python)    │
│ (Claude Code 等)      │                      │                                              │
└──────────────────────┘                      │  tools/  ── search / read / relnotes / ...    │
                                              │     │                                        │
                                              │     ▼                                        │
                                              │  core/                                       │
                                              │   ├ versions.py   バージョン解決・比較          │
                                              │   ├ fetcher.py    HTTP取得・許可リスト・レート制限 │
                                              │   ├ cache.py      ページキャッシュ(ディスク)      │
                                              │   ├ converter.py  HTML→Markdown / セクション抽出 │
                                              │   ├ indexer.py    FTS5 インデックス構築          │
                                              │   └ relnotes.py   リリースノート解析             │
                                              └───────────────┬──────────────────────────────┘
                                                              │ HTTPS (許可ドメインのみ)
                                              ┌───────────────▼──────────────┐
                                              │ dev.mysql.com / downloads.mysql.com │
                                              └──────────────────────────────┘
   ローカルデータ: ~/.cache/mysql-documentation-mcp/
     ├ index/refman-8.4-en.sqlite     (FTS5 インデックス、バージョン毎)
     ├ pages/<sha1(url)>.json         (取得済みページの Markdown + メタデータ)
     └ versions.json                  (バージョン一覧キャッシュ)
```

## 3. ツール仕様

全ツール共通:
- 出力の先頭に `Source: <URL>` と `Version: <version>` を付け、エージェントが引用しやすくする。
- 失敗時は `ctx.error()` でログを出しつつ `ValueError` を送出（AWS 版と同じ）。
- `version` 引数を省略した場合は環境変数 `MYSQL_DOC_DEFAULT_VERSION`、未設定なら **最新 LTS** を使う。
  `"latest"`, `"lts"`, `"8.4"`, `"8.4.6"`（パッチ版は系列 `8.4` に丸める）を受け付ける。

### 3.1 `search_documentation`（MVP）

```python
search_documentation(
    query: str,                 # 検索語（英語推奨。日本語はエージェント側で英訳するよう instructions で誘導）
    version: str | None = None, # 例: "8.4", "9.7", "26.7", "lts", "latest"
    doc_set: str = "refman",    # "refman" | "relnotes"
    limit: int = 10,            # 1..50
) -> list[SearchResult]
```

`SearchResult`:
```json
{ "rank": 1, "title": "15.2.13 SELECT Statement", "url": "https://dev.mysql.com/doc/refman/8.4/en/select.html",
  "section_number": "15.2.13", "breadcrumb": "SQL Statements > Data Manipulation Statements",
  "snippet": "... SELECT ... [WINDOW window_name AS (window_spec) ...", "version": "8.4" }
```

- 実装: FTS5 の `bm25()` でスコアリング。重み付けは `title > headings > identifiers > body`。
- 識別子対策: `sql_mode` のようなアンダースコアを含む語を 1 トークンとして扱うため、
  トークナイザは `unicode61 tokenchars '_'` を使う。加えて body 列とは別に「識別子列」（アンカー ID・`<code>` 内の語）を持つ。
- インデックス未構築のバージョンが指定された場合は §4.3 の方法で自動構築（進捗は `ctx.report_progress` で通知）。

### 3.2 `read_documentation`（MVP）

```python
read_documentation(
    url: str,               # https://dev.mysql.com/doc/... のみ許可
    max_length: int = 10000,# 1..1_000_000
    start_index: int = 0,
) -> str
```

- ページを取得（キャッシュ優先）→ 本文抽出 → Markdown 変換 → `start_index` から `max_length` 文字を返す。
- 続きがある場合は末尾に `<e>Content truncated. Call read_documentation with start_index=N to get more content.</e>` を付与（AWS 版と同形式）。
- 先頭に **ページ内目次（見出し一覧とアンカー）** を短く付け、次に `read_sections` を使えるようにする。
- 20 行を超える表は先頭数行＋`search_table` 利用のヒントに省略（AWS 版と同様）。

### 3.3 `read_sections`（MVP）

```python
read_sections(
    url: str,
    sections: list[str],   # 見出しテキスト or アンカー ID（例: "sysvar_sql_mode", "Window Function Frame Specification"）
) -> list[{"section": str, "anchor": str, "content": str}]
```

- MySQL の巨大ページ（例: `server-system-variables.html` は数十万文字）で必須。
- マッチング順: アンカー ID 完全一致 → 見出し完全一致（大小無視）→ 部分一致。見つからない場合は候補見出しを返す。
- 範囲は「該当見出し（または該当アンカーを持つ要素）から、同レベル以上の次見出しまで」。
  システム変数一覧のような `<ul><li><a id="sysvar_...">` 構造は「次の同種アンカーまで」を 1 セクションとして扱う。

### 3.4 `get_release_notes`（MVP）

```python
get_release_notes(
    from_version: str,                  # 例: "8.0.36"（この版は含まない）
    to_version: str,                    # 例: "8.4.6"（この版を含む）
    categories: list[str] | None = None,# 既定: ["deprecation_removal", "functionality_changed", "security", "incompatible"]
    keyword: str | None = None,         # 例: "replication", "mysql_native_password"
    max_length: int = 10000,
    start_index: int = 0,
) -> str
```

- 範囲内の各パッチ版のリリースノートページ（`news-X-Y-Z.html`）を取得し、見出し単位で分類して抽出する。
- カテゴリ正規化（リリースノートの見出し → 内部カテゴリ）:

  | 見出し（例） | category |
  |---|---|
  | Deprecation and Removal Notes | `deprecation_removal` |
  | Functionality Added or Changed | `functionality_changed` |
  | Security Notes | `security` |
  | 各項目内の "Important Change" / "Incompatible Change" 表記 | `incompatible`（横断フラグ） |
  | Bugs Fixed | `bugs_fixed`（量が多いため既定では除外） |

- 系列をまたぐ場合（8.0 → 8.4 など）は、各系列の該当範囲を連結し、
  **系列の境界では `get_upgrade_guide` を併用するよう出力内でヒントを出す**（大規模変更は新系列の GA 版ノートより「What Is New」ページのほうが網羅的なため）。
- 出力は `## 8.4.1 (2024-07-01)` → `### Deprecation and Removal Notes` → 箇条書き、の Markdown。各項目に元 URL＋アンカーを付ける。

### 3.5 `list_versions`（MVP）

```python
list_versions() -> list[{"version": "8.4", "type": "LTS" | "Innovation" | "GA" | "EOL", "latest_release": "8.4.6",
                         "refman_url": "...", "relnotes_url": "..."}]
```

### 3.6 `lookup_reference`（MVP 後）

```python
lookup_reference(
    name: str,         # 例: "innodb_buffer_pool_size", "JSON_TABLE", "ER_LOCK_DEADLOCK", "1213"
    kind: str | None = None,  # "system_variable" | "status_variable" | "option" | "function" | "statement" | "error" | None(自動)
    version: str | None = None,
) -> str
```

- インデックス構築時にアンカー ID の命名規則（`sysvar_*`, `statvar_*`, `option_mysqld_*`, `function_*`, `error_er_*` 等 ※要検証）から
  「名前 → URL#アンカー」の辞書テーブルを作っておき、該当セクションだけを返す。
- システム変数の場合は属性表（Command-Line Format / System Variable / Scope / Dynamic / Type / Default Value / Valid Values）を構造化して先頭に出す。

### 3.7 `get_upgrade_guide`（MVP 後）

```python
get_upgrade_guide(from_version: str, to_version: str) -> list[{"title": str, "url": str}]
```
- 対象系列の refman から「What Is New in MySQL X」「Changes in MySQL X」「Upgrading MySQL」配下のページを TOC から特定して一覧で返す。本文は `read_documentation` で読む。

### 3.8 `browse_toc`（MVP 後）

```python
browse_toc(version: str | None = None, path: str | None = None, depth: int = 1) -> str
```
- 章立てを段階的にたどる（例: `path="15"` で「15 SQL Statements」直下を表示）。検索が当たらない時のフォールバック。

### 3.9 `search_table`（MVP 後）

AWS 版と同等。`url`, `section_title`, `query`, `max_rows=20` を受け、巨大な表から一致行を JSON で返す。
対象例: 「Server Option, System Variable, and Status Variable Reference」表（導入/非推奨/削除バージョン列を持ち、差分調査に有用）。

## 4. 検索方式の検討

### 4.1 選択肢

| 案 | 概要 | 長所 | 短所 |
|---|---|---|---|
| A. サイト内検索のスクレイピング | dev.mysql.com の検索ページ結果 HTML を解析 | 事前準備不要、常に最新 | 公開 API ではなく仕様変更で壊れる、バージョン絞り込み精度が不明、サイト負荷・規約面の懸念 |
| B. ローカル全文検索（SQLite FTS5） | マニュアルを取得してローカルでインデックス化 | 高速・オフライン可・バージョン毎に正確・追加依存なし（SQLite は標準ライブラリ） | 初回構築が必要（数十 MB の取得）、マニュアル更新の追従が必要 |
| C. ベクトル検索 | 埋め込みモデルで意味検索 | 言い換え・日本語クエリに強い | 埋め込みモデル依存が重い、配布が複雑、MVP には過剰 |
| D. 汎用 Web 検索 API | Google/Bing 等を site: 指定で利用 | 実装容易 | API キー・課金が必要、バージョン絞り込み不可 |

### 4.2 採用: **B（ローカル FTS5）を主、A を任意のフォールバック**

- 識別子（変数名・関数名）での検索が中心となる MySQL の用途では BM25 全文検索で十分な精度が見込める。
- 日本語での自然文クエリは、エージェントが英訳してから呼ぶよう `instructions` とツール説明で誘導する。
- 将来 C を追加する場合も、同じ SQLite に埋め込み列を足すだけで拡張できる構造にしておく。

### 4.3 インデックス構築方法

優先順に試す:

1. **公式オフライン HTML アーカイブを一括取得**
   dev.mysql.com/doc のダウンロード欄にある HTML 版アーカイブ（例: `refman-8.4-en.html-chapter.zip` 等 ※ファイル名・提供有無は要検証）を
   1 リクエストで取得して展開・解析する。最速かつサイト負荷が最小。
2. **TOC からのクロール**
   アーカイブが無いバージョン向け。`https://dev.mysql.com/doc/refman/{v}/en/` の目次をたどり、
   同時接続 2・リクエスト間隔 ≥ 0.5 秒で全ページを取得（`robots.txt` を尊重）。数千ページ規模のため数十分かかる想定で、
   MCP ツール呼び出しの中ではなく CLI (`mysql-documentation-mcp index --version 8.4`) での事前実行を推奨する。

構築の起点:
- CLI: `mysql-documentation-mcp index --version 8.4 [--doc-set refman|relnotes|all] [--refresh]`
- MCP サーバー起動時: `MYSQL_DOC_PRELOAD_VERSIONS=8.4,9.7` が指定されていればバックグラウンド構築
- 検索時: 未構築なら方法 1 で同期構築を試み、失敗したら「CLI で構築してください」というエラーメッセージを返す

更新: インデックスにマニュアルの生成日（各ページのフッターにある "Generated on" 等 ※要検証）と構築日時を保存し、
`MYSQL_DOC_INDEX_TTL_DAYS`（既定 30 日）を超えたら次回起動時にバックグラウンド再構築する。

### 4.4 インデックススキーマ（SQLite）

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);            -- doc_set, version, built_at, source 等
CREATE TABLE pages (
  id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, section_number TEXT,
  breadcrumb TEXT, parent_url TEXT, toc_order INTEGER
);
CREATE TABLE anchors (                                            -- lookup_reference / read_sections 用
  name TEXT, kind TEXT, anchor TEXT, page_id INTEGER REFERENCES pages(id)
);
CREATE INDEX anchors_name ON anchors(name);
CREATE VIRTUAL TABLE chunks USING fts5(                           -- 見出し単位で分割した本文
  page_id UNINDEXED, anchor UNINDEXED, title, headings, identifiers, body,
  tokenize = "unicode61 tokenchars '_'"
);
```

- 本文は見出し（h2/h3/h4）単位のチャンクに分割して登録する。検索結果はページ単位に集約し、最良チャンクの `snippet()` を抜粋に使う。

## 5. コアモジュール設計

### 5.1 取得（fetcher）

- `httpx.AsyncClient`、タイムアウト 30 秒、`follow_redirects=True`。
- **許可 URL**: `^https://dev\.mysql\.com/doc/(refman|relnotes/mysql)/[^/]+/en/` と `^https://downloads\.mysql\.com/docs/` のみ（対象外の doc_set・英語以外の言語は拒否し、その旨をエラーで返す）。リダイレクト先も response hook で再検証（AWS 版 `enforce_redirect_allowlist` と同方式）。
- User-Agent: `mysql-documentation-mcp/<version> (+<repo URL>)`。`MYSQL_DOC_USER_AGENT` で上書き可（社内プロキシ対策）。
- プロキシ: httpx 標準の `HTTPS_PROXY` 等の環境変数に従う。
- レート制限: ホスト毎トークンバケット（既定 2 req/s）。429/503 は指数バックオフで最大 3 回リトライ。

### 5.2 キャッシュ

- 取得した HTML から変換済み Markdown＋メタデータ（URL・タイトル・取得日時・ETag/Last-Modified）を JSON で保存。
- TTL 既定 7 日。期限切れは条件付き GET（`If-None-Match` / `If-Modified-Since`）で再検証。
- ネットワーク不通時は期限切れキャッシュでも返し、出力に「キャッシュ（取得日: …）」と明記する。
- `MYSQL_DOC_OFFLINE=1` でネットワークアクセスを完全に禁止（キャッシュ/インデックスのみで応答）。

### 5.3 HTML → Markdown 変換（converter）

- BeautifulSoup で本文コンテナを抽出（dev.mysql.com の本文要素のセレクタは実装時に実ページで確定する。候補: `#docs-body`, `div.section`, `div.chapter` ※要検証）。
- 除去: ヘッダー、左ナビ、パンくず、前後ページリンク、フッター、`script`/`style`、"PREV/HOME/UP/NEXT" 等。
- `markdownify`（ATX 見出し）で変換。以下は MySQL 特有の調整:
  - `<pre class="programlisting">` → 言語付きコードブロック（`sql` / `ini` / `terminal` を class やプロンプトから推定）
  - 構文定義（`SELECT [ALL | DISTINCT] ...`）はそのままコードブロックで保持（書式崩れは正確性に直結するため）
  - システム変数の属性表は Markdown 表に変換
  - 相対リンクは絶対 URL に変換（エージェントが続けて `read_documentation` できるように）
  - Note / Important / Warning ブロックは `> **Note:**` 形式の引用に変換

### 5.4 バージョン管理（versions）

- `Version` 型: `major.minor[.patch]` をタプル比較。連番（5.7〜9.7）と暦年（26.7〜）は数値比較でそのまま順序が保たれる（9.x < 26.x）。
- バージョン一覧は `https://dev.mysql.com/doc/`（マニュアル一覧）と各系列のリリースノート目次から取得し `versions.json` に 1 日キャッシュ。
  取得失敗時はパッケージ同梱の最小限のフォールバック一覧を使う。
- LTS / Innovation の判定は refman の「MySQL Releases: Innovation and LTS」ページ（`mysql-releases.html`）から取得する（要検証: 機械可読に抽出できるか）。
- `"8.4.6"` のようなパッチ指定は refman では系列 `8.4` に丸め、relnotes ではパッチ版のページを指す。

### 5.5 リリースノート解析（relnotes）

- 系列目次（`/doc/relnotes/mysql/{series}/en/`）から `news-X-Y-Z.html` の一覧とリリース日を取得。
- 各ページを見出しでカテゴリ分割し、箇条書き項目単位で `{version, date, category, text, anchor, flags}` に正規化。
- `flags` には項目冒頭の "Important Change:" / "Incompatible Change:" / "InnoDB:" / "Replication:" 等の接頭辞を抽出（keyword 絞り込みにも利用）。
- 正規化済み項目は `index/relnotes-{series}.sqlite` に保存し、`search_documentation(doc_set="relnotes")` の対象にもする。

## 6. サーバー instructions（エージェント向けガイド・骨子）

AWS 版にならい、FastMCP の `instructions` に以下を記載する:

- MySQL の構文・設定・挙動について答える前に、必ずこのサーバーで公式ドキュメントを確認し、出典 URL を示すこと
- 利用者の MySQL バージョンが分かっている場合は必ず `version` を指定すること（不明なら確認するか LTS を仮定し、その旨を明示）
- 検索語は英語の技術用語で（例: 「デッドロック 調査」→ `"deadlock detection innodb"`）
- 巨大ページは `read_documentation` で目次を見てから `read_sections` を使うこと
- バージョンアップ調査は `get_release_notes` → 系列境界では `get_upgrade_guide` の順で行うこと
- バージョン間比較は、URL の `/refman/{version}/` 部分を差し替えて同一ページを読み比べること

## 7. 設定（環境変数）

| 変数 | 説明 | 既定値 |
|---|---|---|
| `MYSQL_DOC_DEFAULT_VERSION` | `version` 省略時のバージョン | `lts`（最新 LTS） |
| `MYSQL_DOC_CACHE_DIR` | キャッシュ・インデックスの保存先 | OS 標準のキャッシュディレクトリ（`platformdirs`） |
| `MYSQL_DOC_PRELOAD_VERSIONS` | 起動時にインデックスを準備するバージョン（カンマ区切り） | なし |
| `MYSQL_DOC_INDEX_TTL_DAYS` | インデックス再構築までの日数 | `30` |
| `MYSQL_DOC_CACHE_TTL_DAYS` | ページキャッシュの有効日数 | `7` |
| `MYSQL_DOC_OFFLINE` | `1` でネットワークアクセス禁止 | `0` |
| `MYSQL_DOC_USER_AGENT` | HTTP User-Agent の上書き | パッケージ名/バージョン |
| `FASTMCP_LOG_LEVEL` | ログレベル | `WARNING` |

クライアント設定例:

```json
{
  "mcpServers": {
    "mysql-documentation": {
      "command": "uvx",
      "args": ["mysql-documentation-mcp@latest"],
      "env": { "MYSQL_DOC_DEFAULT_VERSION": "8.4", "FASTMCP_LOG_LEVEL": "ERROR" }
    }
  }
}
```

## 8. 技術スタック

| 用途 | 採用 | 理由 |
|---|---|---|
| 言語 | Python 3.10+ | 参考実装と同じ。HTML 解析・MCP SDK が充実（決定済み） |
| MCP | `mcp`（公式 Python SDK の FastMCP） | stdio トランスポート。将来 Streamable HTTP も同 SDK で対応可 |
| HTTP | `httpx` | async 対応・プロキシ対応 |
| HTML 解析 | `beautifulsoup4` + `lxml` | 大きなページ（数 MB）でも高速 |
| Markdown 変換 | `markdownify` | 参考実装と同じ |
| 検索 | `sqlite3`（標準ライブラリ, FTS5） | 追加依存なし |
| パス | `platformdirs` | OS 毎のキャッシュ場所 |
| 型/検証 | `pydantic` | ツール引数・戻り値スキーマ |
| パッケージ管理 | `uv` / PyPI 配布（`uvx` 起動） | 参考実装と同じ（決定済み） |
| ライセンス | Apache-2.0 | 参考実装と同じ。特許条項を含む（決定済み） |
| テスト | `pytest`, `pytest-asyncio`, `respx`（HTTP モック） | |
| Lint/型 | `ruff`, `pyright` | |

## 9. ディレクトリ構成（予定）

```
mysql_documentation_mcp/
├── pyproject.toml
├── LICENSE                     # Apache-2.0
├── README.md
├── docs/                       # 本設計ドキュメント
├── src/mysql_documentation_mcp/
│   ├── __init__.py
│   ├── server.py               # FastMCP 定義・instructions・ツール登録・main()
│   ├── cli.py                  # `index` などのサブコマンド
│   ├── config.py               # 環境変数
│   ├── models.py               # pydantic モデル (SearchResult 等)
│   ├── tools/
│   │   ├── search.py
│   │   ├── read.py             # read_documentation / read_sections / search_table
│   │   ├── relnotes.py         # get_release_notes / get_upgrade_guide
│   │   └── versions.py         # list_versions
│   └── core/
│       ├── fetcher.py
│       ├── cache.py
│       ├── converter.py
│       ├── indexer.py
│       ├── relnotes_parser.py
│       └── versions.py
└── tests/
    ├── fixtures/               # テスト用の最小 HTML（公式ページの丸ごとコピーは置かない）
    ├── unit/
    └── live/                   # --run-live 指定時のみ実行する実サイト結合テスト
```

## 10. セキュリティ・運用上の考慮

- **SSRF 防止**: 許可ドメイン以外の URL は取得前に拒否。リダイレクト先も検証。
- **プロンプトインジェクション**: 取得コンテンツはドキュメント本文としてそのまま返し、サーバー側で解釈・実行しない。
- **著作権**: MySQL ドキュメントは Oracle の著作物で再配布に制限がある（各マニュアルの Legal Notices 参照）。
  リポジトリ・パッケージ・テストフィクスチャに本文を含めず、利用者環境でのみ取得・キャッシュする。
- **サイト負荷**: キャッシュ・レート制限・アーカイブ一括取得優先で、リクエスト数を最小化する。
