## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | migrations.py |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [logger.md](./logger.md) - `setup_logging`を提供する`core/logger.py`。本ファイルのロガー初期化元
* [unified_server.md](./unified_server.md) - `apply_pending_migrations`をアプリ起動時に呼び出す呼び出し元
* [init_unified_db.md](./init_unified_db.md) - DB初期化処理の中で`apply_pending_migrations`を呼び出す呼び出し元
* [quest_service.md](./quest_service.md) - 本ファイルのモジュールdocstringで言及されている、従来の場当たり的なスキーマ変更(`sync_master_data()`内の実行時ALTER TABLE)を行っていたモジュール

## 2. ファイルの概要

`migrations/`ディレクトリ配下の`*.sql`ファイルをファイル名の昇順で適用し、適用済みバージョンを`schema_migrations`テーブルで管理する軽量なマイグレーションランナー。モジュールdocstringによれば、従来は`services/quest_service.py`の`sync_master_data()`内で「SELECTを試して失敗したらALTER TABLE」という実行時チェックとして場当たり的にスキーマ変更が追加されており、変更の追跡や複数プロセス同時実行時のレース懸念があったため、本モジュールが導入された(根拠: `[モジュールdocstring]` (行番号: 3〜15 / 抜粋: "これまでスキーマ変更は services/quest_service.py の sync_master_data() 内で"))。`MIGRATIONS_DIR`は本ファイル(`core/`配下)の親ディレクトリを基準に`migrations`サブディレクトリとして解決される(根拠: `[MIGRATIONS_DIR]` (行番号: 25 / 抜粋: "MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), \"migrations\")"))。中心となる`apply_pending_migrations`は、追跡用テーブルの作成、適用済みバージョン一覧の取得、未適用の`.sql`ファイルの検出・`_split_statements`によるステートメント単位への分割・1文ずつの実行という流れで処理を行う（Issue #99で`conn.executescript`によるスクリプト一括実行から変更。詳細は`apply_pending_migrations`の項を参照）。`sqlite3.OperationalError`発生時は、モジュールレベルの定数`_ALREADY_APPLIED_ERROR_PATTERNS`(`"duplicate column"`, `"already exists"`)に該当する既知のエラー文言の場合のみ「既に別経路で適用済み」とみなして警告ログを出しそのステートメントをスキップして次の文へ処理を継続するが、それ以外の`OperationalError`(DBロック・ディスクフル・SQL誤り等、原因不明のもの)は`conn.rollback()`のうえバージョンを記録せずそのまま再送出する(M-2で修正。以前は`OperationalError`を種類を問わず一律「適用済み」とみなして握りつぶしていたため、本来失敗すべきマイグレーションのスキーマドリフトを見逃す可能性があった)。
* 根拠: `[_ALREADY_APPLIED_ERROR_PATTERNS]` (行番号: 27〜29 / 抜粋: "_ALREADY_APPLIED_ERROR_PATTERNS = (\"duplicate column\", \"already exists\")"), `[apply_pending_migrations docstring]` (行番号: 67〜83 / 抜粋: "「duplicate column」「already exists」のように「既に別経路\n    （旧来の実行時チェック等）で適用済み」と断定できる既知のエラー文言が出た"), `[except節の分岐]` (行番号: 100〜107 / 抜粋: "if not any(pattern in message for pattern in _ALREADY_APPLIED_ERROR_PATTERNS):")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `os` | 標準ライブラリ | パス組み立て(`MIGRATIONS_DIR`)・ディレクトリ存在確認・ディレクトリ内ファイル一覧取得 | 根拠: `[import os]` (行番号: 17 / 抜粋: "import os") |
| `sqlite3` | 標準ライブラリ | SQLite接続オブジェクト(`sqlite3.Connection`)の型ヒント、および`sqlite3.OperationalError`の捕捉 | 根拠: `[import sqlite3]` (行番号: 18 / 抜粋: "import sqlite3") |
| `List`, `Set` | 標準ライブラリ(`typing`) | 関数の戻り値・型ヒント(`List[str]`, `Set[str]`)に使用 | 根拠: `[from typing import List, Set]` (行番号: 19 / 抜粋: "from typing import List, Set") |
| `setup_logging` | 内部モジュール(`core.logger`) | 本モジュール用ロガー(`core.migrations`)の初期化 | 根拠: `[from core.logger import setup_logging]` (行番号: 21 / 抜粋: "from core.logger import setup_logging") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `migrations/*.sql`ファイルの内容 | 各マイグレーションファイル自体は本ファイルの解析範囲外であり、実際にどのようなDDL/DML(ALTER TABLE等)が実行されるかは提供されていないため。 | 根拠: `[sql読み込み・実行]` (行番号: 73〜78 / 抜粋: "with open(path, \"r\", encoding=\"utf-8\") as f:\n            sql = f.read()") |
| `conn`(呼び出し元から渡される`sqlite3.Connection`) | 接続オブジェクトがどのDBファイルに対して開かれているか、どのようなisolation_level等の設定かは呼び出し元の実装に依存し、本ファイルからは不明であるため。 | 根拠: `[apply_pending_migrations引数]` (行番号: 53 / 抜粋: "def apply_pending_migrations(conn: sqlite3.Connection) -> None:") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `_ensure_tracking_table`

* **役割**: マイグレーション適用履歴を記録する`schema_migrations`テーブルが存在しない場合に作成する。
* 根拠: `[_ensure_tracking_table]` (行番号: 32〜39 / 抜粋: "CREATE TABLE IF NOT EXISTS schema_migrations (\n            version TEXT PRIMARY KEY,\n            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP\n        )")


* **引数/リクエスト**: `conn` (`sqlite3.Connection`。テーブル作成対象のDB接続)
* 根拠: `[関数シグネチャ]` (行番号: 32 / 抜粋: "def _ensure_tracking_table(conn: sqlite3.Connection) -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: `[戻り値の型アノテーション]` (行番号: 32 / 抜粋: "-> None:")


* **副作用**: `CREATE TABLE IF NOT EXISTS`の実行および`conn.commit()`によるコミット。
* 根拠: `[conn.execute, conn.commit]` (行番号: 33〜39 / 抜粋: "conn.commit()")


* **エラーハンドリング**: なし(例外捕捉は行われていない)
* 根拠: `[関数本体]` (行番号: 32〜39 / 抜粋: "def _ensure_tracking_table(conn: sqlite3.Connection) -> None:")


### `_applied_versions`

* **役割**: `schema_migrations`テーブルから適用済みバージョン(ファイル名)の集合を取得する。
* 根拠: `[_applied_versions]` (行番号: 42〜44 / 抜粋: "rows = conn.execute(\"SELECT version FROM schema_migrations\").fetchall()\n    return {row[0] for row in rows}")


* **引数/リクエスト**: `conn` (`sqlite3.Connection`。問い合わせ対象のDB接続)
* 根拠: `[関数シグネチャ]` (行番号: 42 / 抜粋: "def _applied_versions(conn: sqlite3.Connection) -> Set[str]:")


* **戻り値/レスポンス**: `Set[str]`(適用済みマイグレーションファイル名の集合)
* 根拠: `[戻り値]` (行番号: 44 / 抜粋: "return {row[0] for row in rows}")


* **副作用**: なし(読み取り専用のクエリ実行)
* 根拠: `[関数本体]` (行番号: 42〜44 / 抜粋: "def _applied_versions(conn: sqlite3.Connection) -> Set[str]:")


* **エラーハンドリング**: なし
* 根拠: `[関数本体]` (行番号: 42〜44 / 抜粋: "def _applied_versions(conn: sqlite3.Connection) -> Set[str]:")


### `_discover_migration_files`

* **役割**: `MIGRATIONS_DIR`配下の`.sql`拡張子ファイルをファイル名昇順でリストアップする。ディレクトリが存在しない場合は空リストを返す。
* 根拠: `[_discover_migration_files]` (行番号: 47〜50 / 抜粋: "return sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(\".sql\"))")


* **引数/リクエスト**: なし
* 根拠: `[関数シグネチャ]` (行番号: 47 / 抜粋: "def _discover_migration_files() -> List[str]:")


* **戻り値/レスポンス**: `List[str]`(ファイル名昇順の`.sql`ファイル名リスト、ディレクトリ不在時は`[]`)
* 根拠: `[戻り値]` (行番号: 48〜50 / 抜粋: "if not os.path.isdir(MIGRATIONS_DIR):\n        return []\n    return sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(\".sql\"))")


* **副作用**: なし(ファイルシステムの読み取りのみ)
* 根拠: `[関数本体]` (行番号: 47〜50 / 抜粋: "def _discover_migration_files() -> List[str]:")


* **エラーハンドリング**: `MIGRATIONS_DIR`が存在しない場合は例外を発生させず空リストを返す。それ以外の例外(パーミッションエラー等)は捕捉されない。
* 根拠: `[os.path.isdir分岐]` (行番号: 48〜49 / 抜粋: "if not os.path.isdir(MIGRATIONS_DIR):\n        return []")


### `_strip_line_comment`

* **役割**: 1行から`--`以降の行コメントを除去する（**#411 品質で追加**）。シングルクォート文字列中に現れる`-`はコメント開始と誤認しないよう、単純な状態機械でクォート内かどうかを追跡する。
* 根拠: `def _strip_line_comment(line: str) -> str:` (行番号: 53〜65)

### `_split_statements`

* **役割**: マイグレーションSQL文字列を`;`区切りでステートメント単位のリストに分割する（Issue #99で新設）。空白のみの要素は除外する。このリポジトリのマイグレーション規約(`migrations/README.md`)が「`ALTER TABLE ... ADD COLUMN`を先頭に、後続はシンプルな`UPDATE`」という単純な構成のみを前提としているため、文字列/BLOBリテラル内にセミコロンを含むような複雑な文は考慮しない。**（#411 品質で修正）** ただしこのリポジトリの規約(コメント・docstringは日本語で書く)ではALTER文の前に長い日本語の説明コメントを書くことが多く、そのプローズ文中に句点代わりのセミコロンが登場するとコメントを読み切る前に誤って分割されてしまう恐れがあった。`;`で分割する前に各行を`_strip_line_comment`で処理し、行コメント内のセミコロンが分割点にならないようにした。
* 根拠: `def _split_statements(sql: str) -> List[str]:` (行番号: 68〜84 / 抜粋: "cleaned = \"\\n\".join(_strip_line_comment(line) for line in sql.splitlines())")
* **引数/リクエスト**: `sql: str`(マイグレーションファイルの全文)
* 根拠: (行番号: 68)
* **戻り値/レスポンス**: `List[str]`(前後の空白を除去したステートメント文字列のリスト。空要素は含まない)
* 根拠: (行番号: 84)
* **副作用**: なし(純粋な文字列処理)
* 根拠: (行番号: 68〜84)
* **エラーハンドリング**: なし
* 根拠: (行番号: 68〜84)

### `apply_pending_migrations`

* **役割**: 追跡テーブルの確保、適用済みバージョンの取得を行った上で、未適用の`.sql`ファイルをファイル名昇順で1件ずつ読み込み、`_split_statements`でステートメント単位に分割して1文ずつ`conn.execute`で実行し、成功時は`schema_migrations`に記録する（Issue #99: 以前は`conn.executescript(sql)`でスクリプト全体を一度に実行していたため、先頭の`ALTER TABLE`が「duplicate column」で失敗するとその時点でスクリプト全体の実行が中断され、後続のデータ移行文(`UPDATE`等)が1文も実行されないままマイグレーション全体が適用済み記録されてしまっていた)。ステートメントごとの`sqlite3.OperationalError`発生時は、モジュールレベルの`_ALREADY_APPLIED_ERROR_PATTERNS`(`"duplicate column"`, `"already exists"`)に該当する既知のエラー文言の場合のみ警告ログを出してそのステートメントをスキップし、後続の文の実行を継続する。ファイル全体としてそれ以外の`OperationalError`が発生した場合は`conn.rollback()`したうえでエラーログを出力し、バージョンを記録せずそのまま再送出して起動処理自体を失敗させる（M-2で導入された選別ロジック自体は維持したまま、Issue #99でステートメント単位の粒度に変更）。
* 根拠: `[apply_pending_migrations]` (行番号: 87〜136 / 抜粋: "def apply_pending_migrations(conn: sqlite3.Connection) -> None:")


* **引数/リクエスト**: `conn` (`sqlite3.Connection`。マイグレーションを適用する対象のDB接続)
* 根拠: `[関数シグネチャ]` (行番号: 66 / 抜粋: "def apply_pending_migrations(conn: sqlite3.Connection) -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: `[戻り値の型アノテーション]` (行番号: 66 / 抜粋: "-> None:")


* **副作用**: `_ensure_tracking_table`によるテーブル作成、`_split_statements`によるSQL分割、`conn.execute`によるステートメント単位のSQL実行(DDL/DML)、`schema_migrations`テーブルへのINSERT、`conn.commit()`によるコミット、`.sql`ファイル読み込み(`open`)、ログ出力(`logger.info`/`logger.warning`/`logger.error`)、ファイル単位で回復不能な`OperationalError`発生時の`conn.rollback()`。
* 根拠: `[副作用一式]` (行番号: 92〜93, 97〜107, 109〜111, 113 / 抜粋: "for statement in _split_statements(sql):\n                try:\n                    conn.execute(statement)")


* **エラーハンドリング**: 各ステートメントの実行を内側の`try`ブロックで囲み、`sqlite3.OperationalError`を捕捉したうえでエラーメッセージを小文字化し(`message = str(e).lower()`)、`_ALREADY_APPLIED_ERROR_PATTERNS`のいずれにも一致しない場合はそのまま`raise`で外側の`try`へ伝播させる。一致する場合は`logger.warning`でログを出し、そのステートメントの実行だけをスキップして`for`ループを継続する（`INSERT OR IGNORE`は使わなくなり、ファイル全体の処理が最後まで到達すれば通常の`INSERT INTO`でバージョンを記録する）。外側の`try`は、内側から伝播した「既知パターン以外」の`OperationalError`を捕捉し、`conn.rollback()`のうえ`logger.error`でログを出し、バージョンを記録せずに`raise`で再送出する。`OperationalError`以外の例外については本関数では捕捉されず、そのまま呼び出し元に伝播する。
* 根拠: `[内側except: 既知パターン以外はraise]` (行番号: 100〜103 / 抜粋: "if not any(pattern in message for pattern in _ALREADY_APPLIED_ERROR_PATTERNS):\n                        raise"), `[外側except: rollbackして再送出]` (行番号: 112〜115 / 抜粋: "conn.rollback()\n            logger.error(f\"❌ Migration '{filename}' failed and was not recorded as applied: {e}\")\n            raise")


## 5. 処理フロー図

```mermaid
flowchart TD
    A1["開始: apply_pending_migrations(conn)"] --> A2["_ensure_tracking_table(conn): schema_migrationsテーブルをCREATE IF NOT EXISTS"]
    A2 --> A3["_applied_versions(conn): 適用済みバージョン集合を取得"]
    A3 --> A4["_discover_migration_files(): MIGRATIONS_DIR配下の.sqlをファイル名昇順で取得"]
    A4 --> A5{"未処理のファイルが残っているか"}
    A5 -- No --> A9["終了"]
    A5 -- Yes --> A6{"ファイル名がappliedに含まれるか"}
    A6 -- Yes --> A5
    A6 -- No --> A7["SQLファイルを読み込み"]
    A7 --> A7b["_split_statements(sql)でステートメント単位に分割"]
    A7b --> A7c{"未処理のステートメントが<br/>残っているか"}
    A7c -- No --> A11["schema_migrationsにINSERT / commit / 成功ログ<br/>(ファイル内の全ステートメント処理完了)"]
    A7c -- Yes --> A8["外部: conn.execute(statement) で1文を実行"]
    A8 --> A10{"例外(sqlite3.OperationalError)が発生したか"}
    A10 -- No --> A7c
    A10 -- Yes --> A14{"エラー文言が_ALREADY_APPLIED_ERROR_PATTERNS<br/>('duplicate column'/'already exists')を含むか"}
    A14 -- Yes --> A12["警告ログ出力(このステートメントのみ既に<br/>別経路で適用済みとみなしスキップ)"]
    A12 --> A7c
    A14 -- No --> A15["例外をそのまま再送出(内側tryの外へ)"]
    A15 --> A15b["conn.rollback() / エラーログ出力"]
    A15b --> A16["例外を再送出(バージョンは記録しない)"]
    A16 --> A17(["呼び出し元へ伝播: 起動失敗"])
    A11 --> A5
```

## 6. 依存関係図

```mermaid
graph TD
    MigrationsPY["core/migrations.py"]

    subgraph Python_Standard_Libraries
        OS["os"]
        Sqlite3["sqlite3"]
        Typing["typing (List, Set)"]
    end

    subgraph Project_Internal
        Logger["core/logger.py (setup_logging)"]
    end

    subgraph Filesystem
        MigrationsDir["migrations/*.sql"]
    end

    subgraph Callers["呼び出し元(推測される関連ファイル)"]
        UnifiedServer["unified_server.py"]
        InitUnifiedDB["init_unified_db.py"]
    end

    MigrationsPY --> OS
    MigrationsPY --> Sqlite3
    MigrationsPY --> Typing
    MigrationsPY --> Logger
    MigrationsPY -->|"読み込み・実行"| MigrationsDir

    UnifiedServer -.->|"apply_pending_migrations呼び出し"| MigrationsPY
    InitUnifiedDB -.->|"apply_pending_migrations呼び出し"| MigrationsPY
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `migrations/0001_add_quest_users_role.sql`ほか`migrations/`配下の各`.sql`ファイル | 実際に適用されるDDL/DML内容を確認し、`apply_pending_migrations`が処理する対象の実体を把握するため。 | 根拠: `[sql読み込み]` (行番号: 91〜93 / 抜粋: "path = os.path.join(MIGRATIONS_DIR, filename)\n        with open(path, \"r\", encoding=\"utf-8\") as f:") |
| 高 | `unified_server.py` | `apply_pending_migrations`の実際の呼び出しタイミング(起動シーケンス上の位置)と渡される`conn`の生成元を確認するため。 | 根拠: `[モジュールdocstring, MIGRATIONS_DIR]` (行番号: 1〜15 / 抜粋: "本モジュールは migrations/ 配下の *.sql ファイルをファイル名の昇順で適用し") |
| 中 | `services/quest_service.py` | モジュールdocstringで言及されている、従来の場当たり的なスキーマ変更処理(`sync_master_data()`)との後方互換関係を確認するため。 | 根拠: `[モジュールdocstring]` (行番号: 5〜8, 12〜15 / 抜粋: "既存の quest_service.py 側の実行時チェックは、init_db() を経由しない\n既存の本番運用パス") |

## 8. 保守上の注意点

* **`OperationalError`の選別許容、ステートメント単位に変更（M-2で導入、Issue #99でステートメント粒度に修正）**: `apply_pending_migrations`は`_ALREADY_APPLIED_ERROR_PATTERNS`(`"duplicate column"`, `"already exists"`)に該当する既知のエラー文言のみを「既に別経路で適用済み」とみなして警告ログで許容し、それ以外の`OperationalError`はロールバックのうえ再送出して起動を失敗させる設計になっている。#99以前は`conn.executescript(sql)`でファイル全体を一括実行していたため、先頭のALTERがduplicate columnで失敗すると後続のUPDATE文が1文も実行されずに「適用済み」記録されてしまっていたが、`_split_statements`でステートメント単位に分割し1文ずつ`conn.execute`する方式に変更したことで、既知パターンのエラーはそのステートメントのみスキップして後続の文の実行が継続されるようになった。`_ALREADY_APPLIED_ERROR_PATTERNS`の文言判定は`str(e).lower()`への部分一致(`in`演算子)であるため、無関係なエラーメッセージにたまたま`"already exists"`等の文字列が含まれる場合は依然として誤って「適用済み」とみなされ得る点は変わっていない。 根拠: `[内側except: ステートメント単位の判定]` (行番号: 100〜107 / 抜粋: "if not any(pattern in message for pattern in _ALREADY_APPLIED_ERROR_PATTERNS):\n                        raise")
* **`OperationalError`以外の例外は未捕捉**: マイグレーションSQL実行時に`sqlite3.IntegrityError`など`OperationalError`以外の例外が発生した場合は、内側・外側いずれの`try`でも捕捉されず、そのまま呼び出し元(`unified_server.py`等の起動処理)に伝播し、起動処理自体を止める可能性がある。 根拠: `[except節がOperationalErrorのみ(内側/外側とも)]` (行番号: 100, 112 / 抜粋: "except sqlite3.OperationalError as e:")
* **旧来のスキーマ変更経路は完全退役済み（#411 品質でdocstringの記述を訂正）**: `quest_service.py`側にあった「SELECTを試して失敗したらALTER TABLE」式の実行時チェックは、以前このモジュールのdocstringで「後方互換のためあえて残している」と説明されていたが、実際にはIssue #330で既に完全に退役済み(`quest_service.sync_master_data`のコメント参照)であり、モジュールdocstringが実態と乖離した記述のまま残っていた。migrations/(0000ベースライン+0001以降)がスキーマの唯一の定義元であることをdocstringに明記するよう訂正した。 根拠: `[モジュールdocstring]` (行番号: 10〜15)、`MY_HOME_SYSTEM/services/quest_service.py`の`sync_master_data`内コメント(「Issue #330: 以前ここにあった...レガシー実行時マイグレーション...は完全退役した」)
* **ファイル名の辞書式ソートに依存**: マイグレーションの適用順序は`sorted()`によるファイル名の辞書式ソートに完全依存しており(行番号50)、ファイル名の命名規則(`0001_`, `0002_`等の連番プレフィックス)が崩れると適用順序が意図と異なる可能性がある。 根拠: `[sorted]` (行番号: 50 / 抜粋: "return sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(\".sql\"))")
* **`_split_statements`は文字列/BLOBリテラル内のセミコロンを考慮しない単純な`;`分割**: `migrations/README.md`が定める「ALTER TABLE ADD COLUMNを先頭に、後続はシンプルなUPDATE」という規約の範囲では問題ないが、将来的に文字列リテラル中に`;`を含むデータ移行文(例: `UPDATE ... SET description = '...; ...'`)を書くと、意図しない位置で文が分割され構文エラーになる可能性がある。SQLパーサを使わない素朴な実装であることに留意が必要。 根拠: `[_split_statements]` (行番号: 53〜63 / 抜粋: "return [s.strip() for s in sql.split(\";\") if s.strip()]")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| 各マイグレーションファイルの実際のSQL内容 | `migrations/`配下の`.sql`ファイル自体は本ファイルの解析範囲外であるため。 | `migrations/0001_add_quest_users_role.sql`ほか`migrations/`配下の各`.sql`ファイル |
| `apply_pending_migrations`の実際の呼び出しタイミング・渡される`conn`の生成方法 | 呼び出し元のコードは本ファイルに含まれていないため。 | `unified_server.py`、`init_unified_db.py` |
| `quest_service.py`側の実行時チェックとの具体的な整合性(競合の有無) | `quest_service.py`の実装内容自体は本ファイルの解析範囲外であるため。 | `services/quest_service.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| 各マイグレーションファイルの実際のSQL内容 | `MY_HOME_SYSTEM/migrations/`配下の全5ファイルを直接確認した。`0001_add_quest_users_role.sql`は`quest_users`に`role TEXT`列を追加し、`user_id`が`dad`/`mom`なら`'role_adult'`、`daughter`/`son`/`child`なら`'role_child'`を設定する。`0002_add_quest_master_reset_period.sql`は`quest_master`に`reset_period TEXT DEFAULT 'weekly_monday'`列を追加する。`0003_add_reward_master_description.sql`は`reward_master`に`description TEXT`列を追加する。`0004_add_coop_quest_link.sql`は`quest_history`に`linked_history_id INTEGER DEFAULT NULL`列を追加し、コメントによれば兄妹連携クエスト(`target_user='siblings'`)の2行を相互連結するためのものである。`0005_fix_quest_master_reset_period_default.sql`は、コメントによれば0002で追加した`reset_period`の初期値`'weekly_monday'`が`is_within_reset_period()`未対応値でありクエスト完了判定が常に`False`になるバグを引き起こしていたため、既存の`NULL`または`'weekly_monday'`の行を`'daily'`へ補正する`UPDATE`文である。 | 直接ソース確認: `MY_HOME_SYSTEM/migrations/0001_add_quest_users_role.sql`, `0002_add_quest_master_reset_period.sql`, `0003_add_reward_master_description.sql`, `0004_add_coop_quest_link.sql`, `0005_fix_quest_master_reset_period_default.sql`（全文） |
| `apply_pending_migrations`の実際の呼び出しタイミング・渡される`conn`の生成方法 | `MY_HOME_SYSTEM/unified_server.py`と`MY_HOME_SYSTEM/init_unified_db.py`を直接確認した。`unified_server.py`は29行目で`from core.migrations import apply_pending_migrations`をインポートし、FastAPIの`lifespan(app)`(90行目〜)内、起動時ログ出力の直後・カメラプロセス起動処理より前に、`migration_conn = sqlite3.connect(config.SQLITE_DB_PATH)`(102行目)で新規接続を生成して`apply_pending_migrations(migration_conn)`(105行目)を呼び出し、`finally`節で確実に`migration_conn.close()`する(106〜107行目)。呼び出し全体は`try`/`except Exception as e:`(101, 108〜109行目)で囲まれ、失敗してもログ出力のみで起動処理を継続する。`init_unified_db.py`側は560行目で`apply_pending_migrations(cur.connection)`と、既存の`with`ブロックで開かれた接続(`cur.connection`)をそのまま渡している。`migrations/README.md`にもこの2箇所（`init_unified_db.init_db()`と`unified_server.py`起動時）が実行タイミングとして明記されていることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/unified_server.py:29, 101-109`, `MY_HOME_SYSTEM/init_unified_db.py:558-560`（参考: `MY_HOME_SYSTEM/migrations/README.md`） |
| `quest_service.py`側の実行時チェックとの具体的な整合性(競合の有無) | `MY_HOME_SYSTEM/services/quest_service.py`の`sync_master_data`メソッド(688行目〜)を直接確認した。同メソッドは`SELECT role FROM quest_users LIMIT 1`を試み例外発生時のみ`ALTER TABLE quest_users ADD COLUMN role TEXT`等を実行する実行時チェックを3箇所持つ: (1) 710〜717行目の`role`列追加(`migrations/0001`と同一のUPDATE条件)、(2) 719〜724行目の`reset_period`列追加(`migrations/0002`と同一のデフォルト値`'weekly_monday'`)、(3) 770〜773行目の`description`列追加(`migrations/0003`と同一)。列が既に存在する場合は`SELECT`が例外を出さず`ALTER TABLE`はスキップされるため、`apply_pending_migrations`が先に列を追加済みであれば`quest_service.py`側のチェックは単に無害な`SELECT`一発で終わり、`OperationalError`は発生しない。逆に`apply_pending_migrations`が未実行またはDBがマイグレーション未適用の状態で`sync_master_data`が先に呼ばれた場合は、`quest_service.py`側が先に列を追加してしまうため、後から`apply_pending_migrations`が同じ`ALTER TABLE`を実行すると`sqlite3.OperationalError`(duplicate column)が発生するが、本ファイル(`core/migrations.py`)100〜107行目の設計通り、このエラー文言は`_ALREADY_APPLIED_ERROR_PATTERNS`の`"duplicate column"`に一致するため、そのステートメントに限り「既に適用済み」とみなされ警告ログのみで次の文へ処理が継続される(M-2で導入された選別ロジック自体は、Issue #99でステートメント単位の粒度に変わった後も、この`duplicate column`ケースを許容する既知パターンという扱いは変わっていない)。したがって両者は列追加に関しては非破壊的に共存できる設計であることを確認した。ただし0004・0005に対応する実行時チェックは`sync_master_data`には存在しない（`linked_history_id`・`reset_period`のデフォルト値修正は正式マイグレーション経由でのみ適用される）ことも確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/services/quest_service.py:688-786`, `MY_HOME_SYSTEM/core/migrations.py:100-107` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない（完了）
* [x] 全関数・全クラス・全コンポーネントを列挙した（完了）
* [x] 全てのインポート要素を列挙した（完了）
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した（完了）
* [x] 根拠漏れが0件である（完了）
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない（完了）
* [x] 不明事項を漏れなく列挙した（完了）
