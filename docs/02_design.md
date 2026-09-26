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
- 大きな表の省略（AWS 版は 20 行超を省略して `search_table` へ誘導）は、`search_table`（M4）を実装するまで行わない。
  それまでは表も省略せず返し、ページネーションで読む（省略すると行を読む手段がなくなるため）。

### 3.3 `read_sections`（MVP）

```python
read_sections(
    url: str,
    sections: list[str],   # 見出しテキスト or アンカー ID（例: "sysvar_sql_mode", "Window Function Frame Specification"）
) -> list[{"section": str, "anchor": str, "content": str}]
```

- MySQL の巨大ページ（例: `server-system-variables.html` は数十万文字）で必須。
- マッチング順: 見出しアンカー → 見出しテキスト完全一致（大小無視・節番号の有無は問わない）→ 見出しの部分一致（候補が 1 つの場合のみ）→ 本文中のアンカー（リファレンス項目）。
  アンカーは大小文字と `_`/`-` の違いを無視して照合する（関数のアンカーは `function_json-extract` のようにハイフン区切りだが、エージェントは `function_json_extract` と推測しがちなため）。
  見つからない場合は候補見出しと、名前が近いリファレンス項目のアンカーを返す。
- 範囲は「該当見出し（または該当アンカーを持つ要素）から、同レベル以上の次見出しまで」。
  システム変数一覧のような `<ul><li><a id="sysvar_...">` 構造は「次の同種アンカーまで」を 1 セクションとして扱う。

### 3.4 `get_release_notes`（MVP）

```python
get_release_notes(
    from_version: str,                  # 例: "8.0.36"（この版は含まない）
    to_version: str,                    # 例: "8.4.6"（この版を含む）
    categories: list[str] | None = None,# 既定: "bug" 以外の全カテゴリ（例: ["deprecation-removal", "feature", "security"]）
    important_only: bool = False,       # True なら "Important Change:" / "Incompatible Change:" の項目のみ
    keyword: str | None = None,         # 例: "replication", "mysql_native_password"
    max_length: int = 10000,
    start_index: int = 0,
) -> str
```

- 範囲内の各リリースのリリースノートページ（`news-X-Y-Z.html`）を取得し、カテゴリ単位で抽出する。
  Innovation リリースも範囲に含む（例: 8.0.36 → 8.4.6 なら 8.1.0, 8.2.0, 8.3.0 も対象。§5.5 の系列対応表で解決）。
- カテゴリはページ内の各カテゴリ節のアンカー `mysqld-{X}-{Y}-{Z}-{slug}` の `slug` をそのまま使う（M0 で確認済み）。主なもの:

  | slug | 見出し |
  |---|---|
  | `deprecation-removal` | Deprecation and Removal Notes |
  | `feature` | Functionality Added or Changed |
  | `security` | Security Notes |
  | `bug` | Bugs Fixed（量が多いため既定では除外） |
  | `innodb`, `optimizer`, `replication`, `performance-schema`, `configuration`, `authentication`, `json` 等 | 機能領域別の Notes |

- 項目冒頭の太字接頭辞（`Important Change:`, `Incompatible Change:`, `InnoDB:`, `Replication:` 等）は `flags` として抽出し、
  `important_only` と `keyword` の絞り込みに使う。

- 系列をまたぐ場合（8.0 → 8.4 など）は、各系列の該当範囲を連結し、
  **系列の境界では `get_upgrade_guide` を併用するよう出力内でヒントを出す**（大規模変更は新系列の GA 版ノートより「What Is New」ページのほうが網羅的なため）。
- 出力は `## 8.4.1 (2024-07-01)` → `### Deprecation and Removal Notes` → 箇条書き、の Markdown。各項目に元 URL＋アンカーを付ける。

### 3.5 `list_versions`（MVP）

```python
list_versions() -> list[{"version": "8.4", "track": "LTS" | "Innovation" | "Early Access" | "unknown",
                         "latest_release": "8.4.12", "has_refman": true,
                         "refman_url": "...", "relnotes_url": "..."}]
```
- `has_refman=false` の例: 26.10.0（Early Access）はリリースノートのみ存在し、マニュアルは無い（M0 時点）。

### 3.6 `lookup_reference`（MVP 後）

```python
lookup_reference(
    name: str,         # 例: "innodb_buffer_pool_size", "Threads_connected", "--port", "JSON_EXTRACT"
    kind: str | None = None,  # "system_variable" | "status_variable" | "option" | "function" | "operator" | None(自動)
    version: str | None = None,
) -> str
```

- インデックス構築時に Info アーカイブのタグテーブル（§4.3）からアンカー ID を取り込み、命名規則で種別を判定して
  「名前 → URL#アンカー」の辞書テーブルを作っておき、該当セクションだけを返す。M0 で確認した規則（8.4 の件数）:
  `option_*`（1,942。`option_mysqld_port` のように `option_{プログラム}_{名前}`）、`sysvar_*`（985）、`statvar_*`（549）、
  `function_*`（507）、`operator_*`（46）、`priv_*`（81）、`optflag_*`（26）。
- エラーコードは refman ではなく別ドキュメント「MySQL Error Message Reference」（`/doc/mysql-errors/{v}/en/`）にあるため対象外（doc_set 追加時に検討）。
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
| A. サイト内検索のスクレイピング | `https://dev.mysql.com/doc/search/?q=...` の結果 HTML を解析 | 事前準備不要、常に最新 | 公開 API ではなく仕様変更で壊れる。M0 で確認したところ結果は最新版（9.7）のページのみで、バージョン指定の方法が見当たらない |
| B. ローカル全文検索（SQLite FTS5） | マニュアルを取得してローカルでインデックス化 | 高速・オフライン可・バージョン毎に正確・追加依存なし（SQLite は標準ライブラリ） | 初回構築が必要（バージョン毎に約 4 MB の取得）、マニュアル更新の追従が必要 |
| C. ベクトル検索 | 埋め込みモデルで意味検索 | 言い換え・日本語クエリに強い | 埋め込みモデル依存が重い、配布が複雑、MVP には過剰 |
| D. 汎用 Web 検索 API | Google/Bing 等を site: 指定で利用 | 実装容易 | API キー・課金が必要、バージョン絞り込み不可 |

### 4.2 採用: **B（ローカル FTS5）**。A はバージョン指定ができないため採用しない

- 識別子（変数名・関数名）での検索が中心となる MySQL の用途では BM25 全文検索で十分な精度が見込める。
- 日本語での自然文クエリは、エージェントが英訳してから呼ぶよう `instructions` とツール説明で誘導する。
- 将来 C を追加する場合も、同じ SQLite に埋め込み列を足すだけで拡張できる構造にしておく。

### 4.3 インデックス構築方法

**refman: 公式 Info 形式アーカイブ `https://downloads.mysql.com/docs/mysql-{version}.info.zip` から構築する**（M0 で確定）。

- HTML 版の一括アーカイブは提供されていない（`refman-*-en.html-chapter.zip` 等は 404）。提供形式は PDF / man / Info のみ。
- Info は zip 約 4 MB（展開後約 20 MB）で、1 リクエストで全文が取得できる。8.0 / 8.4 / 9.7 / 26.7 で提供あり。
- Info を採用できる根拠（8.4 で確認）:
  - ノード名が HTML のページ名と一致する（ノード `select` → `select.html`）。約 2,400 ノード。
  - ノードヘッダ `File: ..., Node: select, Next: ..., Prev: ..., Up: sql-data-manipulation-statements` から目次の親子関係が作れる（`browse_toc` にも使える）。
  - 各ノード冒頭に節番号付きタイトル（`15.2.13 SELECT Statement`）がある。
  - 末尾のタグテーブル（`Ref: sysvar_sql_mode2676725`、約 6,700 件）からアンカー ID と所属ノードが得られる。
  - 索引エントリ（`* sql_mode system variable: server-system-variables.`）も検索語の補強に使える。
- 注意: Info では構文中のプレースホルダが大文字化される（HTML の `select_expr` → Info の `SELECT_EXPR`）。
  そのため **Info は検索インデックス専用** とし、エージェントに返す本文は必ず HTML から取得・変換する（§3.2）。
- Info が存在しないバージョンのみ、目次 HTML をたどるクロールにフォールバックする
  （同時接続 2・間隔 ≥ 0.5 秒。`robots.txt` は `/doc/` を禁止していない）。

**relnotes: 系列目次ページから各 `news-X-Y-Z.html` を取得して構築する**（1 系列あたり数十ページ。§5.5）。

構築の起点:
- CLI: `mysql-documentation-mcp index --version 8.4 [--doc-set refman|relnotes|all] [--refresh]`
- MCP サーバー起動時: `MYSQL_DOC_PRELOAD_VERSIONS=8.4,9.7` が指定されていればバックグラウンド構築
- 検索時: 未構築なら Info アーカイブから同期構築し（数秒〜十数秒を想定）、`ctx.report_progress` で進捗を通知する

更新: Info アーカイブの `Last-Modified`（例: 2026-09-25）と構築日時を `meta` に保存する。
`MYSQL_DOC_INDEX_TTL_DAYS`（既定 7 日）を超えたら条件付き GET（`If-Modified-Since`）で確認し、更新があれば再構築する。

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
- **バージョンのすり替わり検知**: 存在しないバージョンの URL は最新 LTS にリダイレクトされる
  （例: `/doc/refman/5.7/en/` や `/doc/refman/26.10/en/` → `/doc/refman/9.7/en/index.html`）。
  最終 URL のバージョンが要求と異なる場合は、内容を返さずに「このバージョンのマニュアルは公開されていない」旨のエラーにする。
  取り違えたバージョンの内容を返すと誤回答に直結するため。
- **許可 URL**: `^https://dev\.mysql\.com/doc/(refman|relnotes/mysql)/[^/]+/en/` と `^https://downloads\.mysql\.com/docs/` のみ（対象外の doc_set・英語以外の言語は拒否し、その旨をエラーで返す）。リダイレクト先も response hook で再検証（AWS 版 `enforce_redirect_allowlist` と同方式）。
- User-Agent: `Python-urllib/<Python の版> mysql-documentation-mcp/<version> (+<repo URL>)`。`MYSQL_DOC_USER_AGENT` で上書き可。
  M1 で確認したところ、dev.mysql.com と downloads.mysql.com は多くの User-Agent に 403（"Technical Difficulties" ページ）を返す。
  httpx の既定値、独自の製品トークンのみのもの、`Mozilla/5.0 ...`（ブラウザ風）は拒否され、`Python-urllib/*`, `python-requests/*`, `curl/*`, `Wget/*` などの標準的なクライアントは受け付けられる。
  そのため先頭に標準の `Python-urllib` トークンを置き（実際に Python の HTTP クライアントである）、続けて本サーバーの製品トークンとリポジトリ URL を付けて識別可能にしている。ブラウザは装わない。
  403 を受けた場合のエラーメッセージでは `MYSQL_DOC_USER_AGENT` での回避を案内する。
- プロキシ: httpx 標準の `HTTPS_PROXY` 等の環境変数に従う。
- レート制限: ホスト毎トークンバケット（既定 2 req/s）。429/503 は指数バックオフで最大 3 回リトライ。

### 5.2 キャッシュ

- 取得した **生の HTML（gzip）** とメタデータ（URL・リダイレクト後の URL・取得日時）を保存する。変換済み Markdown ではなく HTML を持つのは、
  `read_documentation` と `read_sections` の両方で使え、変換処理を直したときに古い出力が残らないため。
- TTL 既定 7 日。ドキュメントページは `ETag` / `Last-Modified` を返さない（`Cache-Control: no-cache` のみ。M1 で確認）ため、期限切れは単純に再取得する。
  条件付き GET（`If-Modified-Since`）は `Last-Modified` を返す Info アーカイブ（§4.3）で使う。
- 解析・変換済みのページはメモリ上に直近 8 ページ保持する（約 1 MB の `server-system-variables.html` は解析と変換に 2〜3 秒かかるため、ページ送りのたびにやり直さない）。
- バージョンのすり替わり（§5.1）で拒否したページはキャッシュしない。
- ネットワーク不通時は期限切れキャッシュでも返し、出力に「キャッシュ（取得日: …）」と明記する。
- `MYSQL_DOC_OFFLINE=1` でネットワークアクセスを完全に禁止（キャッシュ/インデックスのみで応答）。

### 5.3 HTML → Markdown 変換（converter）

- 本文コンテナは `#docs-body`（M0 で確認。refman・relnotes 共通）。その外側のパンくず（`#docs-breadcrumbs`）、
  バージョン切替（`#docs-version-nav`）、`footer` は自動的に除外される。本文内の `script`/`style`、`a.indexterm` は除去。
- ページタイトルは `<title>`（`MySQL :: MySQL 8.4 Reference Manual :: 15.2.13 SELECT Statement`）から節番号とタイトルを取る。
- `markdownify`（ATX 見出し）で変換。以下は MySQL 特有の調整:
  - `<pre class="programlisting language-sql">` → 言語付きコードブロック（`language-*` クラスから言語を取る）
  - 構文定義（`SELECT [ALL | DISTINCT] ...`）はそのままコードブロックで保持（書式崩れは正確性に直結するため）
  - システム変数等は `<li class="listitem">` 内の `<a name="sysvar_...">` と属性表（`div.informaltable`: Command-Line Format / System Variable / Scope / Dynamic / SET_VAR Hint Applies / Type / Default Value / Valid Values）で構成される。
    属性表は Markdown 表に変換し、`read_sections` ではこの `li` 1 つを 1 セクションとして扱う
    （`server-system-variables.html` は本文だけで約 37 万文字あるため、全文読みは現実的でない）
  - 相対リンクは絶対 URL に変換（エージェントが続けて `read_documentation` できるように）
  - `div.note` / `div.important` / `div.warning` は `> **Note:**` 形式の引用に変換

### 5.4 バージョン管理（versions）

- `Version` 型: `major.minor[.patch]` をタプル比較。連番（〜9.7）と暦年（26.7〜、`YY.M.P`）は数値比較でそのまま順序が保たれる（9.x < 26.x）。
- 公開中の refman（M0 時点）: **8.0 / 8.4 / 9.7 / 26.7**。5.7 と Innovation 系列（8.1〜8.3, 9.0〜9.6）のマニュアルは公開終了しており、
  それらの URL は後継バージョンへリダイレクトされる（5.7 → 9.7、8.3 → 8.4）。
- バージョン一覧は `https://dev.mysql.com/doc/` のリンク（`/doc/refman/{v}/en/`, `/doc/relnotes/mysql/{s}/en/`）と各系列のリリースノート目次から取得し
  `versions.json` に 1 日キャッシュ。取得失敗時はパッケージ同梱のフォールバック一覧を使う。
- **LTS / Innovation の判定は機械的に取得できない**（M0 で確認）。`mysql-releases.html` は文章のみで表が無く、
  「暦年バージョンからは LTS か Innovation か判別できない」と明記されている。リリースノートのタイトルにも記載が無い。
  そのため track はパッケージ同梱の対応表（8.4 = LTS, 9.7 = LTS, 26.7 = Innovation 等）で持ち、リリースごとに更新する。
  対応表に無いバージョンは `unknown` とする。
- **最新 LTS の判定**: バージョン無しの `/doc/refman/en/` が最新 LTS（現在 9.7）にリダイレクトされるので、その転送先を使う。
  取得できない場合は同梱の対応表で最も新しい LTS。
- `"8.4.6"` のようなパッチ指定は refman では系列 `8.4` に丸め、relnotes ではパッチ版のページを指す。

### 5.5 リリースノート解析（relnotes）

- 系列目次（`/doc/relnotes/mysql/{series}/en/`）から `news-X-Y-Z.html` の一覧とリリース日を取得。
  リンクテキストが `Changes in MySQL 8.4.12 (2026-08-18)` の形式なので、バージョンと日付をここから取る。
- **Innovation リリースのノートは、次の LTS 系列のディレクトリにある**（M0 で確認）:

  | 系列ディレクトリ | 含まれるリリース |
  |---|---|
  | `8.0/` | 8.0.0 〜 8.0.x |
  | `8.4/` | 8.1.0, 8.2.0, 8.3.0, 8.4.0 〜 8.4.x |
  | `9.7/` | 9.0.0 〜 9.6.0, 9.7.0 〜 9.7.x |
  | `26.7/` | 26.7.0 〜 |
  | `26.10/` | 26.10.0（Early Access） |

  そのため「バージョン → ページ URL」はパターンから推測せず、全系列の目次を読んで作った対応表で引く（旧系列 URL の `8.3/` 等はリダイレクトされる）。
- 各ページは `div.simplesect`（アンカー `mysqld-{X}-{Y}-{Z}-{slug}`）ごとにカテゴリ分割し、その中の `li.listitem` を 1 項目として
  `{version, date, category, text, anchor, flags}` に正規化する（ページ先頭の目次の `li` は除外）。
- `flags` には項目冒頭の `<strong>` 接頭辞（`Important Change:`, `InnoDB:`, `Replication:` 等）を入れる。
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
| `MYSQL_DOC_INDEX_TTL_DAYS` | インデックスの更新確認までの日数 | `7` |
| `MYSQL_DOC_CACHE_TTL_DAYS` | ページキャッシュの有効日数 | `7` |
| `MYSQL_DOC_OFFLINE` | `1` でネットワークアクセス禁止 | `0` |
| `MYSQL_DOC_USER_AGENT` | HTTP User-Agent の上書き | `Python-urllib/<ver> mysql-documentation-mcp/<ver> (+<repo>)`（§5.1） |
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
│   ├── server.py               # MCPServer 定義・instructions・ツール登録・main()
│   ├── cli.py                  # `index` などのサブコマンド
│   ├── config.py               # 環境変数
│   ├── tools/                  # 各ツールの実装と入出力モデル（pydantic）
│   │   ├── search.py
│   │   ├── read.py             # read_documentation / read_sections / search_table
│   │   ├── relnotes.py         # get_release_notes / get_upgrade_guide
│   │   └── versions.py         # list_versions
│   └── core/
│       ├── urls.py             # URL の許可リスト・正規化・バージョンすり替わり検知
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
- **著作権**: MySQL ドキュメントは Oracle の著作物。マニュアルの Legal Notices（"Use of This Documentation"）では、
  公開・再配布は禁止される一方、「内容を改変しない限り他形式への変換は許可」とされている。
  利用者環境での取得・Markdown 変換・キャッシュはこの範囲に収まるようにする（要約や書き換えをサーバー側で行わない理由でもある）。
  リポジトリ・パッケージ・テストフィクスチャに本文を含めず、利用者環境でのみ取得・キャッシュする。
- **サイト負荷**: キャッシュ・レート制限・アーカイブ一括取得優先で、リクエスト数を最小化する。
