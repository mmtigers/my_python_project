## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `services/ui_event_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [ui_log_router.md](./ui_log_router.md) - 本サービスの`record_batch`を呼ぶルーター
* [ui_log.md](./ui_log.md) - 引数`UiTapBatch`の定義元
* [db_retention_service.md](./db_retention_service.md) - 保存先`ui_tap_events`を保持期間削除の対象に登録している

## 2. ファイルの概要

画面タップログを`ui_tap_events`へ追記するサービス。クライアントのバッチを1トランザクションの`executemany`で`INSERT OR IGNORE`し、新規・重複・範囲外の件数を返す。docstringによれば`quest_users`には触れないため残高ロックとは無関係である。
根拠: [ファイル冒頭docstring] (行番号: 2-7)、[クラス定義] (行番号: 35 / 抜粋: "class UiEventService:")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `datetime` | 標準 | 時刻変換・許容範囲 | 根拠: [インポート宣言] (行番号: 8 / 抜粋: "import datetime") |
| `sqlite3` | 標準 | DB書き込み失敗(`OperationalError`)の捕捉 | 根拠: [インポート宣言] (行番号: 9 / 抜粋: "import sqlite3") |
| `config` | ローカルモジュール | `UI_TAP_LOG_ENABLED`の参照 | 根拠: [インポート宣言] (行番号: 11 / 抜粋: "import config") |
| `core.database.get_db_cursor` | ローカルモジュール | 書き込みトランザクション | 根拠: [インポート宣言] (行番号: 12 / 抜粋: "from core.database import get_db_cursor") |
| `core.logger.setup_logging` | ローカルモジュール | ロガー生成 | 根拠: [インポート宣言] (行番号: 13 / 抜粋: "from core.logger import setup_logging") |
| `core.utils.get_now_iso` | ローカルモジュール | 受信時刻の生成 | 根拠: [インポート宣言] (行番号: 14 / 抜粋: "from core.utils import get_now_iso") |
| `models.ui_log.UiTapBatch` | ローカルモジュール | 引数の型 | 根拠: [インポート宣言] (行番号: 15 / 抜粋: "from models.ui_log import UiTapBatch") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `ui_tap_events`テーブルの定義 | `migrations/0022_add_ui_tap_events.sql`側(仕様書対象外)。本ファイルはINSERT文のみ | [SQL定義] (行番号: 27-32) |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### 定数

* **役割**: `MAX_EVENT_AGE`(30日。これより古いタップは端末の時計の異常とみなして保存しない)、`MAX_FUTURE_SKEW`(10分。これより未来は保存しない)、`JST`。
* 根拠: [定数定義] (行番号: 23 / 抜粋: "MAX_EVENT_AGE = datetime.timedelta(days=30)")、(行番号: 25 / 抜粋: "MAX_FUTURE_SKEW = datetime.timedelta(minutes=10)")
* **引数/リクエスト**: 該当なし
* **戻り値/レスポンス**: 該当なし
* **副作用**: なし
* **エラーハンドリング**: なし

### `UiEventService.record_batch`

* **役割**: バッチを保存し`{accepted, duplicated, rejected}`を返す。`UI_TAP_LOG_ENABLED`がfalseなら何も保存せず全件をacceptedとして返す(クライアントに失敗と誤認させ再送を繰り返させないため)。有効なら各タップの`occurred_at_ms`をJSTのISO文字列へ変換し(`10**18`のように変換できない桁外れの値は`OverflowError`/`OSError`/`ValueError`を捕捉して当該イベントだけをrejectedにする。放置すると500になり、クライアントが5xxを再試行扱いにして同じキューを永久に再送するため)、`now-30日 ≦ 時刻 ≦ now+10分`の範囲外はrejectedとして除外、残りを1トランザクションの`executemany`で`INSERT OR IGNORE`する。`accepted`は`cur.rowcount`(実際に挿入された行数)、`duplicated`は`len(rows)-inserted`。書き込みが`sqlite3.OperationalError`(Webhook等と重なった`database is locked`等)で失敗した場合は例外を伝播させず、警告ログを出して`{"accepted": 0, "duplicated": 0, "rejected": 範囲外の件数 + 保存できなかった件数}`を返す(Issue #971。タップログはベストエフォートな記録で、以前は500とExceptionGroupによる二重トレースバックでjournalを汚していた。5xxだとクライアントが再試行して書き込み集中時に負荷を足すため2xxで返す)。
* 根拠: [メソッド定義] (行番号: 36 / 抜粋: "def record_batch(self, batch: UiTapBatch, now: datetime.datetime | None = None) -> dict[str, int]:")、[無効時] (行番号: 42-43)、[範囲判定] (行番号: 62-64)、[保存] (行番号: 73-78)、[ロック競合時のフェイルソフト] (行番号: 79-92 / 抜粋: "except sqlite3.OperationalError as e:")、[戻り値] (行番号: 96)
* **引数/リクエスト**: `batch: UiTapBatch`, `now: datetime.datetime | None`(テスト用の注入。省略時は現在のJST)
* 根拠: [メソッド定義] (行番号: 36)
* **戻り値/レスポンス**: `dict[str, int]`(`accepted` / `duplicated` / `rejected`)
* 根拠: [戻り値] (行番号: 92, 96)
* **副作用**: `ui_tap_events`への追記(`get_db_cursor(commit=True)`)。範囲外があれば、またDB書き込みに失敗すれば警告ログを出す。
* 根拠: [保存] (行番号: 74-75)、[警告ログ] (行番号: 88-91, 94-95)
* **エラーハンドリング**: 時刻変換の`OverflowError`/`OSError`/`ValueError`を捕捉して当該イベントをrejected扱いにする。DB書き込みの`sqlite3.OperationalError`を捕捉して保存予定の全件をrejected扱いにする(Issue #971)。それ以外のDB例外はそのまま伝播する。
* 根拠: [時刻変換の例外捕捉] (行番号: 55 / 抜粋: "except (OverflowError, OSError, ValueError):")、[DB例外の捕捉] (行番号: 79 / 抜粋: "except sqlite3.OperationalError as e:")

### `ui_event_service`

* **役割**: モジュールレベルのシングルトン(CLAUDE.mdのDI方針どおり)。
* 根拠: [変数宣言] (行番号: 99 / 抜粋: "ui_event_service = UiEventService()")
* **引数/リクエスト**: 該当なし
* **戻り値/レスポンス**: 該当なし
* **副作用**: なし
* **エラーハンドリング**: なし

## 5. 処理フロー図

```mermaid
flowchart TD
    Start(["record_batch"]) --> En{"UI_TAP_LOG_ENABLED"}
    En -- false --> Skip(["保存せず全件accepted"])
    En -- true --> Loop["各タップを時刻変換"]
    Loop --> Rng{"now-30日 以上 now+10分 以下"}
    Rng -- No --> Rej["rejected加算"]
    Rng -- Yes --> Rows["rowsへ追加"]
    Rows --> Ins["executemany INSERT OR IGNORE (1トランザクション)"]
    Ins --> OpErr{"sqlite3.OperationalError"}
    OpErr -- Yes --> Soft(["警告ログ。accepted=0, rejected=範囲外+保存予定の全件"])
    OpErr -- No --> Ret(["accepted=rowcount, duplicated=残り, rejected"])
```

## 6. 依存関係図

```mermaid
graph TD
    record_batch --> get_db_cursor["core.database.get_db_cursor"]
    record_batch --> get_now_iso["core.utils.get_now_iso"]
    record_batch --> config["config.UI_TAP_LOG_ENABLED"]
    ui_log_router["routers/ui_log_router.py"] --> record_batch
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 中 | `migrations/0022_add_ui_tap_events.sql` | 保存先テーブルの列定義を把握するため。 | [SQL定義] (行番号: 27-32) |

## 8. 保守上の注意点

* `executemany`の`rowcount`に依存して`accepted`を数えている。INSERT OR IGNOREで無視された重複は含まれない。
* DBロック競合時は保存を諦めて2xxで返す(Issue #971)。5xxへ戻すとクライアント(`tapLogger`)が再試行扱いにするため、書き込み集中時の負荷とjournalの汚染が復活する。
* 追記専用でUPDATEは行わない。保持期間削除は`db_retention_service`の`RETENTION_TARGETS`(`DB_ROW_RETENTION_EVENT_DAYS`)が担う。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| テーブル列の制約詳細 | マイグレーションSQLは仕様書の対象外 | `migrations/0022_add_ui_tap_events.sql` |

## 10. 自己検証結果

* [x] 完了: 推測・外部ファイルの仕様を一切含んでいない
* [x] 完了: 全関数・全クラス・全コンポーネントを列挙した
* [x] 完了: 全てのインポート要素を列挙した
* [x] 完了: すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 完了: 根拠漏れが0件である
* [x] 完了: Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 完了: 不明事項を漏れなく列挙した
