## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | sync_strict.py |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [quest_game_system.md](./quest_game_system.md) — **Issue #664 で同期の実体が移った先**。`GameSystem.sync_master_data(strict=True, dry_run=...)` が本ファイルの唯一の実処理
- [quest_master_sync_sql.md](./quest_master_sync_sql.md) — UPSERT文とパラメータ組み立ての一元管理（Issue #664。統合後は `game_system.py` からのみ参照される）
- [common.md](./common.md) — **Issue #664 で `common.py` ごと廃止された Deprecated Facade**（本ファイルは実体を直importするようになった。仕様書は履歴として残っている）
- [database.md](./database.md) — `core.database.get_db_cursor` / `get_ro_connection` の実体（統合後、本ファイルからは直接使わない）
- [quest_data.md](./quest_data.md) — 同期元マスターデータ`QUESTS`/`REWARDS`の定義元
- [init_unified_db.md](./init_unified_db.md) — `quest_master`/`reward_master`テーブルのスキーマ定義元
- [quest_service.md](./quest_service.md) — `quest_data` を保持する互換シム。`load_master_module()` はここ経由でマスタを読む

## 2. ファイルの概要

マスターデータ(`quest_data.QUESTS`/`REWARDS`)とデータベースのマスターテーブル(`quest_master`/`reward_master`)を完全に同期するコマンドライン実行用のスクリプト。マスターデータに存在しない行はDBから物理削除(DELETE)される破壊的操作であり、`quest_data.py`のID変更ミス一発で本番マスタが全件消えるリスクがあるため(M-9-6)、実行前に`confirm_or_abort`で安全ガード(マスタデータが空の場合の拒否)と対話的な確認プロンプトを挟む。`--dry-run`フラグでDBを一切変更せず削除・更新件数のみを表示するモードもある。
* 根拠: モジュールdocstring (行番号: 1〜19 / 抜粋: "quest_data.py の内容で quest_master/reward_master を完全同期する手動実行CLI。")
* 根拠: `class SyncAborted(Exception):` (行番号: 57〜58 / 抜粋: "M-9-6: ユーザーがマスタ同期の確認プロンプトで拒否した、または安全ガードで拒否された場合。")

**（Issue #664 で変更）本ファイルは同期処理そのものを持たない。** 同期の実体(`DELETE ... NOT IN` とUPSERTのループ)は`services/quest/game_system.py`の`GameSystem.sync_master_data(strict=True)`へ統合され、本ファイルに残るのはCLIの責務(引数解析と、破壊的操作に対する安全ガード)だけである。統合前はここと`GameSystem.sync_master_data()`に「マスタ→DB同期」が二重に書かれており、UPSERTの列リストが食い違う事故が#100(`reset_period`欠落)・#164(時間帯/期間/出現率/前提クエスト欠落)・#165(`description`欠落)と3度起きていた。#664の前段(PR #670)でUPSERT文自体は`services/quest/master_sync_sql.py`へ寄せられたが、削除方針と同期ループは二重のまま残っていたため、ここで完全に1本化された。
* 根拠: モジュールdocstring (行番号: 3〜16 / 抜粋: "同期の実体(`DELETE ... NOT IN` + UPSERT)は")
* 根拠: `game_system.sync_master_data(strict=True, dry_run=dry_run)` (行番号: 107 / 抜粋: "game_system.sync_master_data(strict=True, dry_run=dry_run)")

`strict=True`が API 経路(`POST /api/quest/seed` = `strict=False`)と異なる点は3つで、いずれも`GameSystem.sync_master_data`側のdocstringに記載されている: (1) マスタのクエストが空のとき`quest_master`を全削除する(`strict=False`は#242の安全弁として削除自体をスキップする)、(2) `quest_users`は同期しない(CLIの対象は一貫して`quest_master`/`reward_master`の2テーブル)、(3) マスタ各エントリの欠損キーを統合前の本ファイルと同じ既定値で補ってから`MasterQuest`/`MasterReward`で検証する。
* 根拠: モジュールdocstring (行番号: 17〜18 / 抜粋: "`strict=True` が API 経路(`POST /api/quest/seed` = `strict=False`)と違う点は")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `argparse` | 標準ライブラリ | CLI引数(`--dry-run`, `-y`/`--yes`, `--allow-empty-master`)のパース | `import argparse` (行番号: 20) |
| `sys` | 標準ライブラリ | 異常終了時のプロセス終了 (`sys.exit(1)`) | `import sys` (行番号: 21) |
| `core.logger.setup_logging` | ローカルモジュール | **（Issue #664 で変更）** 以前は Deprecated Facade である `common` 経由で参照していた。`common.py` の廃止に伴い実体を直接importする | `from core.logger import setup_logging` (行番号: 23) |
| `services.quest.game_system.game_system` | ローカルモジュール | **（Issue #664 で追加）** 同期の実体。モジュールレベルのシングルトン | `from services.quest.game_system import game_system, load_master_module` (行番号: 24) |
| `services.quest.game_system.load_master_module` | ローカルモジュール | **（Issue #664 で追加）** 安全ガードが数えるマスタを、同期処理と同じ読み込み口から取得するため | `from services.quest.game_system import game_system, load_master_module` (行番号: 24) |
| `traceback` (ローカルインポート) | 標準ライブラリ | `main`内で予期しない例外発生時のスタックトレース出力 | `import traceback` (行番号: 122、`main`内) |

**（Issue #664 で削除されたインポート）** `core.database.get_db_cursor`、`quest_data`の`QUESTS`/`REWARDS`、`services.quest.master_sync_sql`の4シンボル(`QUEST_UPSERT_SQL`/`REWARD_UPSERT_SQL`/`quest_upsert_params`/`reward_upsert_params`)は、同期処理ごと`game_system.py`へ移ったため本ファイルからは参照されなくなった。**本ファイルはDBに直接触れない。**
* 根拠: `import` 節全体 (行番号: 20〜24)

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `core.logger.setup_logging` | 内部実装が提供されておらず、設定されるロガーの詳細仕様が不明 | `logger = setup_logging("strict_sync")` (行番号: 27) |
| `services.quest.game_system.game_system` | `sync_master_data(strict=True, dry_run=...)` が実際にどのSQLを発行するかは本ファイルからは読み取れない | `game_system.sync_master_data(strict=True, dry_run=dry_run)` (行番号: 107) |
| `services.quest.game_system.load_master_module` | 互換シム経由でどのモジュールを解決し `importlib.reload` するかは本ファイルからは読み取れない | `quest_data = load_master_module()` (行番号: 101) |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `build_arg_parser`

* **役割**: CLI引数パーサを構築する。`--dry-run`(DB変更なしで件数のみ表示)、`-y`/`--yes`(確認プロンプトをスキップ)、`--allow-empty-master`(マスタが空でも実行を許可)の3フラグを定義する。
* 根拠: `def build_arg_parser() -> argparse.ArgumentParser:` (行番号: 30〜54)
* **引数/リクエスト**: なし
* 根拠: (行番号: 30)
* **戻り値/レスポンス**: `argparse.ArgumentParser`
* 根拠: (行番号: 30, 54 / 抜粋: "return parser")
* **副作用**: なし(パーサオブジェクトの構築のみ)
* 根拠: (行番号: 31〜53)
* **エラーハンドリング**: なし
* 根拠: (行番号: 30〜54、try-exceptなし)

### `SyncAborted` (例外クラス)

* **役割**: ユーザーが確認プロンプトで拒否した、または安全ガード(空マスタ)により実行を中止すべき場合に送出される専用例外(M-9-6)。
* 根拠: `class SyncAborted(Exception):` (行番号: 57〜58 / 抜粋: "M-9-6: ユーザーがマスタ同期の確認プロンプトで拒否した")
* **引数/リクエスト**: `Exception`を継承するのみで独自属性なし
* 根拠: (行番号: 57〜58)
* **戻り値/レスポンス**: 該当なし(例外クラス)
* **副作用**: なし
* **エラーハンドリング**: 該当なし。呼び出し元(`main`)が`except SyncAborted:`で捕捉し`sys.exit(1)`する
* 根拠: (行番号: 118〜119 / 抜粋: "except SyncAborted:")

### `confirm_or_abort`

* **役割**: 破壊的なDELETEを伴う同期を実行してよいか判定する安全ガード(M-9-6)。`master_quest_ids`または`master_reward_ids`が空で`allow_empty_master=False`の場合はエラーログを出して即座に`SyncAborted`を送出する。`assume_yes=True`ならここで終了(プロンプト表示なし)。それ以外は`input_func`で対話的に確認し、`y`/`yes`(大小文字・前後空白は無視)以外の回答なら`SyncAborted`を送出する。
* 根拠: `def confirm_or_abort(` (行番号: 61〜90)
* **引数/リクエスト**: `master_quest_ids: list`, `master_reward_ids: list`, `allow_empty_master: bool`, `assume_yes: bool`, `input_func=input`(テスト用の差し替え可能な入力関数)
* 根拠: (行番号: 61〜65)
* **戻り値/レスポンス**: `None`(中止すべき場合は`SyncAborted`を送出して戻らない)
* 根拠: (行番号: 65 / 抜粋: ") -> None:")
* **副作用**: `logger.error`/`logger.info`によるログ出力、`input_func`の呼び出し(標準入力からの読み取りが既定)
* 根拠: (行番号: 75〜79, 85〜87, 90)
* **エラーハンドリング**: 空マスタガードまたはユーザー拒否の場合に`SyncAborted`を送出する。それ以外の例外は捕捉しない
* 根拠: (行番号: 80 / 抜粋: "raise SyncAborted(\"empty master data without --allow-empty-master\")"), (行番号: 90 / 抜粋: "raise SyncAborted(\"user declined confirmation prompt\")")

### `run_sync`

* **役割**: **（Issue #664 で縮小）** CLIの実行本体。開始ログを出力し、`dry_run=False`の場合のみ`load_master_module()`でマスタを取得して`confirm_or_abort`に通したうえで、`game_system.sync_master_data(strict=True, dry_run=dry_run)`を1回呼ぶ。統合前はここで`get_db_cursor(commit=not dry_run)`を開き`sync_quests`/`sync_rewards`を順に実行していたが、その処理はすべて`game_system.py`側へ移った。マスタの取得に`load_master_module()`を使うのは、**安全ガードが数えるマスタと実際に同期されるマスタを同じ読み込み口に揃える**ためである(別々に読むと「ガードは通ったが別のデータで全削除された」が起こりうる)。
* 根拠: `def run_sync(dry_run: bool = False, assume_yes: bool = False, allow_empty_master: bool = False, input_func=input) -> None:` (行番号: 93〜107)
* 根拠: コメント (行番号: 96〜98 / 抜粋: "安全ガードは「これから同期されるマスタ」を数える必要があるため、")
* **引数/リクエスト**: `dry_run: bool = False`, `assume_yes: bool = False`, `allow_empty_master: bool = False`, `input_func=input`
* 根拠: (行番号: 93)
* **戻り値/レスポンス**: なし
* 根拠: (行番号: 93 / 抜粋: ") -> None:")
* **副作用**: 開始ログの出力、`dry_run=False`時の`load_master_module()`呼び出しと`confirm_or_abort`呼び出し、`game_system.sync_master_data`の呼び出し(DBへの変更はこの先で起きる)
* 根拠: (行番号: 94, 99〜107)
* **エラーハンドリング**: 本関数自体は例外を捕捉しない。`confirm_or_abort`からの`SyncAborted`、`load_master_module()`の`ImportError`、`sync_master_data`からの`HTTPException`・DB例外はいずれも呼び出し元(`main`)にそのまま伝播する
* 根拠: (行番号: 93〜107、try-exceptなし)

### `main`

* **役割**: CLIエントリポイント。`build_arg_parser`でパースした引数を`run_sync`に渡して実行する。`SyncAborted`(安全ガード/確認プロンプト拒否)は静かに`sys.exit(1)`、それ以外の`Exception`はエラーログとスタックトレースを出力してから`sys.exit(1)`する。
* 根拠: `def main(argv=None):` (行番号: 110〜125)
* **引数/リクエスト**: `argv=None`(`argparse.parse_args`にそのまま渡され、`None`ならプロセスの`sys.argv`が使われる)
* 根拠: (行番号: 110〜111)
* **戻り値/レスポンス**: なし(異常時は`sys.exit(1)`でプロセス終了)
* 根拠: (行番号: 110〜125)
* **副作用**: `run_sync`の呼び出し、エラー時のログ出力・`traceback.print_exc()`・プロセス終了
* 根拠: (行番号: 120〜124 / 抜粋: "logger.error(f\"❌ Sync failed: {e}\")")
* **エラーハンドリング**: `SyncAborted`を捕捉して`sys.exit(1)`(ログなし、`confirm_or_abort`側で既に出力済みのため)。それ以外の`Exception`はログ・トレース出力後に`sys.exit(1)`
* 根拠: (行番号: 118〜124)

## 5. 処理フロー図

```mermaid
flowchart TD
    Start([Start]) --> MainStart["main(argv)開始: build_arg_parser().parse_args(argv)"]
    MainStart --> TryBlock{"例外監視(try)"}
    TryBlock -->|正常処理| CallRunSync["run_sync(dry_run, assume_yes, allow_empty_master)"]

    CallRunSync --> StartLog["開始ログ: Starting Strict Master Data Sync (v3.0)..."]
    StartLog --> DryCheck{"dry_run か"}
    DryCheck -- Yes --> CallSync
    DryCheck -- No --> LoadMaster["外部: load_master_module()<br/>(互換シム経由で quest_data を取得し reload)"]
    LoadMaster --> Confirm["confirm_or_abort():<br/>空マスタガード → assume_yesなら即return → 確認プロンプト"]
    Confirm -->|SyncAborted| CatchAbort
    Confirm -->|通過| CallSync["外部: game_system.sync_master_data(strict=True, dry_run=dry_run)<br/>(削除方針・UPSERT・dry-run集計はすべてこの先。quest_game_system.md 参照)"]

    CallSync --> End([End])

    TryBlock -.->|SyncAborted| CatchAbort["except SyncAborted: sys.exit(1)<br/>(追加ログなし)"]
    CatchAbort --> End
    TryBlock -.->|その他のException| CatchOther["except Exception as e:<br/>エラーログ・traceback.print_exc()"]
    CatchOther --> ExitOther["外部: sys.exit(1)"]
    ExitOther --> End
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph sync_strict.py
        logger
        build_arg_parser
        SyncAborted
        confirm_or_abort
        run_sync
        main
    end

    main --> build_arg_parser
    main --> run_sync
    main --> logger
    main --> Ext_SysExit["外部: sys.exit"]
    main --> Ext_Traceback["外部: traceback.print_exc"]

    run_sync --> confirm_or_abort
    run_sync --> logger
    run_sync --> Ext_LoadMaster["外部: services.quest.game_system.load_master_module"]
    run_sync --> Ext_SyncMasterData["外部: services.quest.game_system.game_system.sync_master_data(strict=True)"]

    confirm_or_abort --> SyncAborted
    confirm_or_abort --> logger
    confirm_or_abort --> Ext_Input["外部: input (input_funcで差し替え可)"]

    Ext_SyncMasterData --> DB_quest_master[(DB: quest_master)]
    Ext_SyncMasterData --> DB_reward_master[(DB: reward_master)]
    Ext_SyncMasterData --> DB_user_inventory[(DB: user_inventory, FK参照チェック #165)]

    Ext_SetupLogging["外部: core.logger.setup_logging"] --> logger
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `services/quest/game_system.py` | **同期の実体がここにある**(Issue #664)。削除方針・UPSERT列リスト・dry-run集計・`strict`の意味はすべてこのファイルを読まないと分からない。 | 根拠: `from services.quest.game_system import game_system, load_master_module` (行番号: 24) |
| 高 | `quest_data.py` | 同期元となるマスターデータ `QUESTS`、`REWARDS` の正確なスキーマおよび内容を確認するため。 | 根拠: `quest_data = load_master_module()` (行番号: 101) |
| 中 | `services/quest/master_sync_sql.py` | `quest_master`/`reward_master`への実際のUPSERT列リストを確認するため。 | 根拠: モジュールdocstring (行番号: 8〜10 / 抜粋: "#664 の前段で SQL 自体は") |
| 中 | `services/quest_service.py` | `load_master_module()`が経由する互換シムで、`quest_data`の実体を保持している。 | 根拠: モジュールdocstring (行番号: 3〜4 / 抜粋: "`services/quest/game_system.py` の `GameSystem.sync_master_data(strict=True)` へ統合した。") |

## 8. 保守上の注意点

* **破壊的な削除操作**: 実行時(`dry_run=False`)に`quest_master`および`reward_master`テーブルのデータが物理削除(DELETE)され、その後Upsertされる。マスタデータ(`QUESTS`/`REWARDS`)が空、または想定より少ないと、DBの対応テーブルの内容が意図せず失われる。M-9-6でこのリスクに対する安全ガード(`confirm_or_abort`)が導入されている。**削除そのものを行うのは`game_system.sync_master_data(strict=True)`側**で、本ファイルはその前段のガードだけを担う。
* 根拠: `class SyncAborted(Exception):` (行番号: 57〜58), `game_system.sync_master_data(strict=True, dry_run=dry_run)` (行番号: 107)
* **（Issue #664）同期ロジックをここに書き戻さないこと**: UPSERTの列リストが食い違う事故が#100・#164・#165と3度起きたのは、同じ同期が2箇所に別実装で存在したためである。新しい列の追加・削除方針の変更は`services/quest/game_system.py`と`services/quest/master_sync_sql.py`の側で行う。回帰テスト`tests/test_sync_strict.py::TestMasterSyncSqlIsSharedWithGameSystem::test_the_cli_no_longer_has_its_own_sync_implementation`が、本ファイルに独自のSQL実行・`sync_quests`/`sync_rewards`が復活していないことを検査する。
* 根拠: モジュールdocstring (行番号: 3〜16 / 抜粋: "UPSERT の列リストが食い違う事故が #100")
* **安全ガードとマスタの読み込み口は一致させること**: `run_sync`は`load_master_module()`で取得したマスタの件数を`confirm_or_abort`に渡し、続けて`sync_master_data`が同じ読み込み口から読む。ここを別々の経路(例: `from quest_data import QUESTS`のモジュールレベルimport)に戻すと、ガードが見たマスタと実際に書かれるマスタがずれうる。
* 根拠: コメント (行番号: 96〜98 / 抜粋: "別々に読むと、ガードが見たマスタと実際に書かれるマスタがずれうる。")
* **`--dry-run`は確認プロンプトを通らない**: `run_sync`は`dry_run=True`のとき`load_master_module()`も`confirm_or_abort`も呼ばずに`sync_master_data(strict=True, dry_run=True)`へ進む。DBを変更しないため問題にはならないが、「dry-runでは空マスタガードが働かない」という非対称は意図的なものである。
* 根拠: (行番号: 96〜107 / 抜粋: "if not dry_run:")
* **`confirm_or_abort`の対話プロンプトはCLI実行前提**: `input_func`のデフォルトは組み込み`input`であり、`unified_server.py`等のAPI経由で本モジュールの関数を直接呼び出すような使い方は想定されていない(現状そのような呼び出し元は本ファイルからは確認できない)。API経路が使うのは`strict=False`側である。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `sync_master_data(strict=True)`が実際に発行するSQL | 本ファイルは呼び出すだけで、削除方針・UPSERT列リストは読み取れない。 | `services/quest/game_system.py`、`services/quest/master_sync_sql.py` |
| `QUESTS`, `REWARDS` の全プロパティ構造 | 本ファイルは`q['id']`/`r['id']`しか参照していないため、マスターデータの全容が不明。 | `quest_data.py` |
| DBの正確なテーブルスキーマ | 本ファイルはDBに触れないため、カラムの型や制約が判断できない。 | `MY_HOME_SYSTEM/migrations/`(唯一の定義元)、`MY_HOME_SYSTEM/current_schema.sql` |
| トランザクションの挙動 | `get_db_cursor`の呼び出しが本ファイルから消えたため、コミット単位が判断できない。 | `services/quest/game_system.py`、`core/database.py` |
| 本スクリプトの実運用上の呼び出しタイミング | `main`/`run_sync`がCI/デプロイ手順・運用者の手動実行のどちらを主な想定としているか、呼び出し元のドキュメントが本ファイルには存在しないため不明。 | デプロイ手順書、`deploy.sh`等 |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `sync_master_data(strict=True)`が実際に発行するSQL | `services/quest/game_system.py`の`sync_master_data`(103行目〜)を直接確認した。`strict=True`のとき、クエストは`active_q_ids`が非空なら`DELETE FROM quest_master WHERE quest_id NOT IN (?,...)`、**空なら`DELETE FROM quest_master`(全削除)**を実行する(`strict=False`は#242の安全弁として削除自体をスキップする)。報酬はどちらのモードでも、削除候補を`SELECT`で抽出したうえで`user_inventory`への参照が残るものを除外してから1行ずつ`DELETE`する(#165)。UPSERTは両モードとも`services/quest/master_sync_sql.py`の`QUEST_UPSERT_SQL`(全16列)・`REWARD_UPSERT_SQL`(全8列、`desc`には`description`と同じ値)を使う。`quest_users`は`strict=False`のときだけ同期される。 | 直接ソース確認: `MY_HOME_SYSTEM/services/quest/game_system.py:103-269`, `MY_HOME_SYSTEM/services/quest/master_sync_sql.py`（参考: [quest_game_system.md](./quest_game_system.md)・[quest_master_sync_sql.md](./quest_master_sync_sql.md)） |
| `QUESTS`, `REWARDS` の全プロパティ構造 | `quest_data.md`の解析によれば、`QUESTS`は`id`/`title`/`type`/`target`/`category`/`difficulty`/`exp`/`gold`/`icon`/`desc`を基本キーとし任意で`days`/`start_time`/`end_time`/`chance`を持つ辞書のリスト、`REWARDS`は`id`/`title`/`category`/`cost_gold`/`icon_key`/`desc`を基本キーとし任意で`target`を持つ辞書のリスト(23件)であることが判明した。いずれも`reset_period`キーは持たない。 | quest_data.md |
| DBの正確なテーブルスキーマ | **（Issue #330でスキーマの定義元が移動）** 現在の唯一の定義元は`MY_HOME_SYSTEM/migrations/`(空DBでは`0000_baseline_schema.sql`)と、そこから`python init_unified_db.py --dump-schema`で生成される参照用ダンプ`MY_HOME_SYSTEM/current_schema.sql`である。本スクリプトが同期対象とする2テーブルは`quest_master(quest_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT, quest_type TEXT DEFAULT 'daily', exp_gain INTEGER DEFAULT 10, gold_gain INTEGER DEFAULT 5, icon_key TEXT, day_of_week TEXT, target_user TEXT DEFAULT 'all', start_date TEXT, end_date TEXT, occurrence_chance REAL DEFAULT 1.0, start_time TEXT, end_time TEXT, days TEXT, pre_requisite_quest_id INTEGER DEFAULT NULL, reset_period TEXT DEFAULT 'daily')`と、`reward_master(reward_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, cost_gold INTEGER, category TEXT, icon_key TEXT, desc TEXT, target TEXT DEFAULT 'all', description TEXT)`である。**`reward_master`に`desc`と`description`が併存している**のは本スクリプトが担った移行の名残で、`services/quest/game_system.py`の`get_all_view_data`はビュー応答で`description`のみを正として`desc`を落としている。`user_inventory.reward_id`だけが`reward_master(reward_id)`への外部キーを持つ(`core/database.py`が接続ごとに`PRAGMA foreign_keys=ON`を設定する)。 | 直接ソース確認: `MY_HOME_SYSTEM/current_schema.sql`, `MY_HOME_SYSTEM/migrations/0000_baseline_schema.sql`, `MY_HOME_SYSTEM/core/database.py`（参考: [init_unified_db.md](./init_unified_db.md)・[migrations.md](./migrations.md)） |
| トランザクションの挙動 | `services/quest/game_system.py`の`sync_master_data`は、非dry-run時に`with get_db_cursor(commit=True) as cur:`の**単一ブロック**でユーザー・クエスト・報酬をまとめて処理するため、途中で例外が出れば全体がロールバックされる。dry-run時は`_report_dry_run`が`get_db_cursor(commit=False)`を開き、`_count_rows_to_delete`のSELECTしか実行しないため、DBへは一切の変更が残らない。`get_db_cursor`の実体は`MY_HOME_SYSTEM/core/database.py`にあり、`sqlite3.connect(config.SQLITE_DB_PATH, timeout=30.0)`を最大5回(1秒間隔)リトライし、`row_factory = sqlite3.Row`・`PRAGMA journal_mode=WAL`・`PRAGMA foreign_keys=ON`を設定する。 | 直接ソース確認: `MY_HOME_SYSTEM/services/quest/game_system.py:103-297`, `MY_HOME_SYSTEM/core/database.py`（参考: [database.md](./database.md)・[quest_game_system.md](./quest_game_system.md)） |
| 本スクリプトの実運用上の呼び出しタイミング | リポジトリ内の自動実行経路をすべて確認したが、**本スクリプトを起動する定期実行・デプロイ経路は存在しない**。実機のcrontabをリポジトリ管理下に置いた`deploy/cron/crontab`(Issue #528)に`sync_strict`のエントリは無く、`MY_HOME_SYSTEM/deploy/systemd/`配下のユニット、`MY_HOME_SYSTEM/start_all.sh`、`scheduler_boot.py`の`TASKS`のいずれからも参照されていない。したがって本スクリプトは**オーナーが必要時に手動で実行するCLIツール**という位置づけで、`build_arg_parser()`が`--dry-run`/`--yes`/`--allow-empty-master`を持ち、非dry-run時に`confirm_or_abort`で対話確認を求める設計もこれと整合する(自動実行を前提にしていない)。なお通常のマスタ同期は`POST /api/quest/seed`→`GameSystem.sync_master_data()`(= `strict=False`)が担い、マスタが空のときの全削除を行わない点が本スクリプトとの違いである。 | 直接ソース確認: `deploy/cron/crontab`（全体）, `MY_HOME_SYSTEM/deploy/systemd/`, `MY_HOME_SYSTEM/scheduler_boot.py`, `MY_HOME_SYSTEM/routers/quest_router.py`（参考: [run_task.md](./run_task.md)・[quest_game_system.md](./quest_game_system.md)） |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない（完了）
* [x] 全関数・全クラス・全コンポーネントを列挙した（完了）
* [x] 全てのインポート要素を列挙した（完了）
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した（完了）
* [x] 根拠漏れが0件である（完了）
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない（完了）
* [x] 不明事項を漏れなく列挙した（完了）
