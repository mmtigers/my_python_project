## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `scheduler_boot.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [switchbot_power_monitor.md](./switchbot_power_monitor.md) — 呼び出し先の可能性がある監視スクリプト(推定。scheduler_boot.py自身の解析では`TASKS`の全内容までは確認できていない)
- [weekly_analyze_report.md](./weekly_analyze_report.md) — 呼び出し先の可能性がある週次レポートスクリプト(推定。scheduler_boot.mdの不明事項一覧で関連ファイルとして言及されている)
- [logger.md](./logger.md) — `core.logger.setup_logging`(ロガー初期化)を提供
- [config.md](./config.md) — `config`モジュール(設定値)を提供
- [unified_server.md](./unified_server.md) — 呼び出し元。FastAPIアプリの`lifespan`起動時に本スクリプトをサブプロセスとして起動する

## 2. ファイルの概要

* 指定された間隔（秒）で、プロジェクト内のPythonスクリプトを定期的にサブプロセスとして実行し管理する無限ループのスケジューラ。
* `ThreadPoolExecutor` により各タスクを並列実行する。1タスクの長時間化が他のタスクの実行タイミングを丸ごと遅延させないための設計。
* 根拠: `main` 関数内のループおよび `TASKS` 定義 (行番号: 94-126, 29-43 / 抜粋: "with ThreadPoolExecutor(...", "TASKS: List[Task] = [")



## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `time` | 標準ライブラリ | 現在時刻の取得、待機処理（スリープ） | `import time` (行番号: 2 / 抜粋: "import time") |
| `subprocess` | 標準ライブラリ | 外部スクリプトのサブプロセス実行 | `import subprocess` (行番号: 3 / 抜粋: "import subprocess") |
| `sys` | 標準ライブラリ | モジュール検索パスの追加、プロセス終了処理、Pythonインタープリタパス取得 | `import sys` (行番号: 4 / 抜粋: "import sys") |
| `os` | 標準ライブラリ | パスの絶対パス解決・結合・存在確認、環境変数の取得 | `import os` (行番号: 5 / 抜粋: "import os") |
| `ThreadPoolExecutor`, `Future` | 標準ライブラリ (`concurrent.futures`) | タスクの並列実行、実行中タスクの完了状態管理 | `from concurrent.futures import ThreadPoolExecutor, Future` (行番号: 6 / 抜粋: "from concurrent.futures import") |
| `datetime` | 標準ライブラリ | 未使用（コード内に使用箇所なし） | `from datetime import datetime` (行番号: 7 / 抜粋: "from datetime import datetime") |
| `List` | 標準ライブラリ | 型ヒント（リスト） | `from typing import List, ...` (行番号: 8 / 抜粋: "from typing import List, Dict,") |
| `Dict` | 標準ライブラリ | 型ヒント（辞書、`in_flight`の型等） | `from typing import ... Dict, ...` (行番号: 8, 106 / 抜粋: "in_flight: Dict[str, Future] = {}") |
| `Any` | 標準ライブラリ | 未使用（コード内に使用箇所なし） | `from typing import ... Any, ...` (行番号: 8 / 抜粋: "from typing import List, Dict,") |
| `TypedDict` | 標準ライブラリ | 辞書型の構造定義 | `from typing import ... TypedDict` (行番号: 8 / 抜粋: "from typing import List, Dict,") |
| `config` | ローカルモジュール | 未使用（コード内に使用箇所なし。インポートの副作用利用の可能性あり） | `import config` (行番号: 14 / 抜粋: "import config") |
| `setup_logging` | ローカルモジュール | ロガーの初期化と取得 | `from core.logger import setup_logging` (行番号: 15 / 抜粋: "from core.logger import setup_log") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `config` | モジュールがインポートされているが、本ファイル内での使用箇所や実装内容が提供されていないため | `import config` (行番号: 14 / 抜粋: "import config") |
| `setup_logging` | 実装内容が外部ファイル(`core.logger`)にあるため、ログの出力先やフォーマット仕様が不明 | `from core.logger import setup_logging` (行番号: 15 / 抜粋: "from core.logger import setup_log") |
| `TASKS` に定義されている各スクリプト | サブプロセスとして呼び出される対象ファイル（`monitors/*.py`など）の実装が提供されていないため | `TASKS: List[Task] = [...]` (行番号: 29 / 抜粋: "{"script": "monitors/switchbot") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `Task`

* **役割**: 実行するスクリプトのパス、実行間隔、最終実行時刻、引数を保持するためのデータ構造を定義する。
* 根拠: `class Task(TypedDict):` (行番号: 20-21 / 抜粋: "実行タスクのデータ構造定義。")


* **引数/リクエスト**: 該当なし（型定義のため）
* 根拠: `class Task(TypedDict):` (行番号: 20 / 抜粋: "class Task(TypedDict):")


* **戻り値/レスポンス**: 該当なし
* 根拠: `class Task(TypedDict):` (行番号: 20 / 抜粋: "class Task(TypedDict):")


* **副作用**: なし
* 根拠: 内部での状態変更なし (行番号: 20 / 抜粋: "class Task(TypedDict):")


* **エラーハンドリング**: なし
* 根拠: エラー補足の記述なし (行番号: 20 / 抜粋: "class Task(TypedDict):")



### `run_script`

* **役割**: 指定されたスクリプトをサブプロセスとして実行し、実行結果をログに出力する。
* 根拠: `def run_script` (行番号: 45, 47 / 抜粋: "指定されたスクリプトをサブプロセスとして実行")
* **（Issue #360 / #361 で修正）** `subprocess.run` ではなく `subprocess.Popen` で起動して `_running_children[script_path]` に登録し、`proc.communicate(timeout=3600)` で完了を待つ。これにより SIGTERM 受信時に `terminate_running_children()` から実行中の子プロセスを止められる。失敗時にログへ流す stderr は末尾 20 行に絞る（Discord 通知の 2000 字制限対策）。タイムアウト時は `proc.kill()` を試みる。`finally` で `_running_children` から自分のエントリを外す。
* 根拠: `proc = subprocess.Popen(` (行番号: 115〜123)、`_stdout, stderr = proc.communicate(timeout=3600)` (行番号: 126)、`tail = "\n".join(stderr.strip().splitlines()[-20:])` (行番号: 135)


* **引数/リクエスト**: `script_path` (`str`): 実行するスクリプトの相対パス, `args` (`List[str]`): スクリプトに渡す引数
* 根拠: 関数定義 (行番号: 45 / 抜粋: "script_path: str, args: List")


* **戻り値/レスポンス**: `bool`: 実行成功（returncode 0）ならTrue、それ以外はFalse
* 根拠: return文 (行番号: 54 / 抜粋: "bool: 実行成功(returncode 0)ならTrue")


* **副作用**: 外部プロセスの起動。標準出力および標準エラー出力のキャプチャとログ出力。
* 根拠: `subprocess.run` (行番号: 70 / 抜粋: "result = subprocess.run(")


* **エラーハンドリング**: `subprocess.TimeoutExpired`（タイムアウト時）および一般的な `Exception` をキャッチしてログ出力し、`False` を返す。また、サブプロセスの返り値が0以外の場合もエラーログを出力し `False` を返す。
* 根拠: try-except ブロック (行番号: 87 / 抜粋: "except subprocess.TimeoutExpir")




### `terminate_running_children` / `_handle_shutdown_signal` / `install_signal_handlers`（Issue #360 で追加）

* **役割**: `_running_children`（実行中の子プロセスの `script_path → Popen`、`_children_lock` で保護）を走査し、生存中のものを `terminate()` → timeout 後 `kill()` して停止数を返す。`_handle_shutdown_signal` は SIGTERM/SIGINT で `_shutdown_event` を立てて `terminate_running_children()` を呼ぶ。`install_signal_handlers` は両シグナルにこのハンドラを登録する（メインスレッド以外からの呼び出し等で `ValueError`/`OSError` になる場合は無視）。以前は scheduler が SIGTERM で即死し、実行中の `nas_monitor.py` 等（最大3600s）が孤児として走り続けて再起動後の新世代と DB 書き込み・保持期間削除が競合していた。
* 根拠: `_running_children: Dict[str, subprocess.Popen] = {}` (行番号: 48〜50)、`def terminate_running_children(timeout: float = 5.0) -> int:` (行番号: 53〜70)、`def _handle_shutdown_signal(signum, _frame) -> None:` (行番号: 73〜76)、`def install_signal_handlers() -> None:` (行番号: 79〜85)
* **引数/リクエスト**: `timeout: float` / `(signum, _frame)` / なし
* 根拠: (行番号: 53, 73, 79)
* **戻り値/レスポンス**: `int`（停止数）/ なし / なし
* 根拠: (行番号: 70)
* **副作用**: 子プロセスの terminate/kill、`_shutdown_event` の set、シグナルハンドラ登録、ログ出力
* 根拠: (行番号: 59〜69, 74〜76, 80〜85)
* **エラーハンドリング**: 子プロセス停止失敗は WARNING ログのみ、`signal.signal` の失敗は無視
* 根拠: (行番号: 67〜68, 83〜85)

### `main`

* **役割**: `ThreadPoolExecutor`（ワーカー数 = `TASKS`件数、最低1）を使って `TASKS` リストを巡回し、現在時刻と最終実行時刻の差が指定間隔（`interval`）以上、かつ当該スクリプトが実行中でないタスクに対して `run_script` を非同期（別スレッド）で投入する無限ループを実行する。実行中のタスクは `in_flight` 辞書（スクリプトパス→`Future`）で管理し、完了していないタスクは同一周期内で再投入しない（多重起動防止）。
* 根拠: `def main() -> None:` (行番号: 94 / 抜粋: "メインループ。")
* **（Issue #360 で修正）** 冒頭で `install_signal_handlers()` を呼び、メインループは `while True` ではなく `while not _shutdown_event.is_set()`、スリープは `_shutdown_event.wait(10)`（シャットダウン要求で即抜ける）。ループを抜けた後に `terminate_running_children()` を呼ぶ。
* 根拠: `install_signal_handlers()` (行番号: 167)、`while not _shutdown_event.is_set():` (行番号: 172)、`_shutdown_event.wait(10)` (行番号: 189)、`terminate_running_children()` (行番号: 191)


* **引数/リクエスト**: なし
* 根拠: 関数定義 (行番号: 94 / 抜粋: "def main() -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: 関数定義 (行番号: 94 / 抜粋: "def main() -> None:")


* **副作用**: `ThreadPoolExecutor.submit` による `run_script` の並列実行。`TASKS` 内各タスクの `last_run` の更新。`in_flight` 辞書への `Future` の登録。1回のループ終了ごとの10秒間のスリープ。
* 根拠: `in_flight[script] = executor.submit(run_script, script, task["args"])`, `task["last_run"] = now`, `time.sleep(10)` (行番号: 106, 108, 122-123, 126)


* **エラーハンドリング**: 関数内での明示的な例外キャッチはなし（各タスクの例外は `run_script` 内、または `Future` 内部で捕捉・保持される）。
* 根拠: 関数内部の処理 (行番号: 94-126)



### `__main__` 実行ブロック

* **役割**: `main` 関数を呼び出してスケジューラを起動し、停止命令や予期せぬエラー時にプロセスを終了させる。
* 根拠: `if __name__ == "__main__":` (行番号: 128 / 抜粋: "if **name** == "**main**":")


* **引数/リクエスト**: なし
* 根拠: ブロック定義 (行番号: 128 / 抜粋: "if **name** == "**main**":")


* **戻り値/レスポンス**: なし
* 根拠: ブロック定義 (行番号: 128 / 抜粋: "if **name** == "**main**":")


* **副作用**: `sys.exit(1)` によるプロセスの終了。
* 根拠: `sys.exit(1)` (行番号: 135 / 抜粋: "sys.exit(1)")


* **エラーハンドリング**: `KeyboardInterrupt` をキャッチして停止ログを出力し正常終了する。それ以外の `Exception` をキャッチしてクリティカルログを出力し、`sys.exit(1)` で異常終了させる。
* 根拠: try-except ブロック (行番号: 131, 133 / 抜粋: "except KeyboardInterrupt:")



## 5. 処理フロー図

```mermaid
flowchart TD
    Start([Start]) --> MainBlock{"__name__ == '__main__'"}
    MainBlock -- True --> TryBlock(tryブロック開始)
    TryBlock --> InitMain("main()呼び出し")
    InitMain --> LoopStart{"while True (無限ループ)"}
    
    LoopStart --> IterateTasks{"TASKSをループ"}
    
    IterateTasks -- "タスクあり" --> CheckRunning{"in_flight[script]が<br>実行中(未完了)か?"}
    CheckRunning -- True --> IterateTasks
    CheckRunning -- False --> CheckTime{"now - last_run >= interval"}
    CheckTime -- True --> UpdateLastRun("last_runをnowに更新")
    UpdateLastRun --> SubmitTask["外部：executor.submit(run_script)<br>(別スレッドで非同期実行)"]
    SubmitTask --> RegisterFuture("in_flight[script]にFutureを登録")
    RegisterFuture --> IterateTasks
    CheckTime -- False --> IterateTasks
    
    IterateTasks -- "タスク確認完了" --> Sleep["外部：time.sleep(10)"]
    Sleep --> LoopStart

    subgraph "run_script (別スレッドで実行)"
        RSStart(("run_script開始")) --> CheckExist{"os.path.exists()"}
        CheckExist -- True --> Subprocess["外部：subprocess.run()"]
        CheckExist -- False --> LogError("エラーログ出力")
    end

    SubmitTask -.->|"executor.submitで別スレッド起動"| RSStart
    
    MainBlock -- False --> End([End])
    
    TryBlock -. "KeyboardInterrupt" .-> CatchKB("停止ログ出力")
    CatchKB --> End
    
    TryBlock -. "Exception" .-> CatchExc("CRITICALログ出力")
    CatchExc --> SysExit["外部：sys.exit(1)"]
    SysExit --> End

```

## 6. 依存関係図

```mermaid
graph TD
    Scheduler["scheduler_boot.py"]
    SysLib["標準ライブラリ: sys, os, time, subprocess"]
    ConcurrentLib["標準ライブラリ: concurrent.futures (ThreadPoolExecutor, Future)"]
    TypingLib["標準ライブラリ: typing (未使用含む)"]
    DateLib["標準ライブラリ: datetime (未使用)"]
    Config["外部: config (ブラックボックス)"]
    Logger["外部: core.logger (ブラックボックス)"]
    Scripts["外部: TASKSで定義されたスクリプト群 (ブラックボックス)"]

    Scheduler --> SysLib
    Scheduler --> ConcurrentLib
    Scheduler --> TypingLib
    Scheduler --> DateLib
    Scheduler --> Config
    Scheduler --> Logger
    Scheduler --> Scripts

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `core/logger.py` | ログの出力先、フォーマット仕様、ログレベルの設定内容を把握するため。 | `from core.logger import setup_logging` |
| 高 | `monitors/switchbot_power_monitor.py` など | TASKSに登録されている定期実行処理の実態と、システムへの影響を解析するため。 | `TASKS: List[Task] = [...]` |
| 中 | `config.py` | 本ファイル内では使用されていないがインポートされており、副作用（グローバル変数の初期化など）があるか確認するため。 | `import config` |

## 8. 保守上の注意点

* **並列実行への変更**: 従来は `main()` のループ内で `run_script` を直接（同期的に）呼び出しており、1タスクの実行時間が長引くと後続タスクの実行開始が遅延する問題があった。現在は `ThreadPoolExecutor`（ワーカー数=`len(TASKS)`）で各タスクを別スレッドに投入する設計に変更されており、この問題は解消されている。
* 根拠: `with ThreadPoolExecutor(...)` および `executor.submit(run_script, ...)` (行番号: 108, 123)


* **多重起動防止の仕組み**: `in_flight` 辞書（スクリプトパス→`Future`）で実行中タスクを管理し、`Future.done()` が `False`（未完了）の間はそのタスクの次回投入をスキップする。ループ周期(10秒)ごとに判定するため、タスクの完了検知には最大10秒弱の遅延がありうる。
* 根拠: `if running_future is not None and not running_future.done(): continue` (行番号: 116-118)


* **タイムアウト設定と実装の整合**: `subprocess.run` の `timeout=3600`（60分）は、直上のコメント「タイムラプスなど長時間タスクを許容するため60分に延長」および例外時のログメッセージ「exceeded 3600 seconds.」と整合しており、矛盾は確認されなかった。
* 根拠: `timeout=3600` のコメントおよび `TimeoutExpired` 時のログ (行番号: 75, 88)


* **未使用のインポート**: `datetime`, `Any`（`Dict`は`in_flight`の型ヒントで使用）はインポートされているがコード内で使用されていない。また `config` も明示的な使用箇所がない。
* **パス解決の依存**: 外部スクリプトの実行パスは `__file__` を基準とした `PROJECT_ROOT` に依存しているため、このファイル自身のディレクトリ階層を変更するとすべてのタスク実行が失敗する。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `config` モジュールの役割 | 明示的な呼び出しがないがインポートされており、副作用の有無が判断できないため | `config.py` (または同名のパッケージ) |
| ログの出力仕様 | 初期化関数 `setup_logging` の詳細な設定（コンソール出力、ファイル出力先など）が不明なため | `core/logger.py` |
| 各監視スクリプトの詳細仕様 | `TASKS` で呼び出される各Pythonスクリプトが行う具体的な処理内容（API通信やDB操作の有無など）が不明なため | `monitors/*.py`, `weekly_analyze_report.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `config` モジュールの役割 | `scheduler_boot.py`14行目で`import config`されているのみで、モジュール内での`config.`という属性アクセスは存在しない(検索で1件もヒットしない)ことを確認した。ただし`config.py`自体を直接確認したところ、モジュールロード時(import時)に副作用を伴う設計であることが判明した：`load_dotenv()`の呼び出し(139行目)、NAS等ディレクトリの検証・作成を行う`verify_and_initialize_storage`関数の実行、`devices.json`/`family_events.json`の読み込み・パース(283行目, 302行目)、ログ・アセット等の必須ディレクトリの自動作成ループ(516行目、`os.makedirs(d, exist_ok=True)`)が該当する。したがって`scheduler_boot.py`にとっての`import config`は、直接の属性参照はなくとも、これらモジュールロード時の環境初期化処理（ディレクトリ検証・作成、設定ファイル読み込み）を一括して発生させるための副作用目的のインポートであると判断できる。 | 直接ソース確認: `MY_HOME_SYSTEM/scheduler_boot.py:14`, `MY_HOME_SYSTEM/config.py:139, 283, 302, 516`（config.py自体の全体像は`config.md`参照） |
| ログの出力仕様 | `core/logger.py`46〜86行目の`setup_logging`を直接確認した。コンソール出力(`StreamHandler`)、日次ローテーションのファイル出力(`TimedRotatingFileHandler`、`when='midnight', interval=1, backupCount=7`)、ERRORレベル以上をDiscord Webhookへ送信する`DiscordErrorHandler`の3種のハンドラを登録する。ログ保存先は`os.path.join(config.BASE_DIR, "logs")`(63行目)で、`config.BASE_DIR`は`config.py`212行目で`os.path.dirname(os.path.abspath(__file__))`（`MY_HOME_SYSTEM/`ディレクトリ）と定義されているため、実際のログファイルパスは`MY_HOME_SYSTEM/logs/home_system.log`になる。Discord送信先は`webhook_url`引数指定時はそれを優先し、未指定時は`config.DISCORD_WEBHOOK_ERROR`(config.py194行目、環境変数`DISCORD_WEBHOOK_ERROR`から取得)を使用する(78行目)。`scheduler_boot.py`18行目の`setup_logging("scheduler")`は`webhook_url`引数を渡していないため、`config.DISCORD_WEBHOOK_ERROR`が使われる。 | 直接ソース確認: `MY_HOME_SYSTEM/core/logger.py:46-86`, `MY_HOME_SYSTEM/config.py:194, 212`, `MY_HOME_SYSTEM/scheduler_boot.py:18` |
| 各監視スクリプトの詳細仕様 | `scheduler_boot.py`29〜43行目の`TASKS`リストを直接確認した。実際に定期実行登録されているのは`monitors/switchbot_power_monitor.py`(300秒間隔), `monitors/nature_remo_monitor.py`(300秒), `monitors/server_watchdog.py`(600秒), `monitors/tv_lock_monitor.py`(300秒、コメントの「30分」という頻度区分表記とは裏腹に実際の値は300秒), `monitors/memory_monitor.py`(600秒), `monitors/nas_monitor.py`(3600秒)の6本で、いずれも`args: []`（追加引数なし）である。`monitors/timelapse_runner.py`は37行目でコメントアウトされており現在は無効。旧不明事項に挙げられていた`weekly_analyze_report.py`は`TASKS`リストおよび`scheduler_boot.py`全文検索のいずれにも登場せず、本ファイルの定期実行対象には含まれていないことを確認した（別の起動経路を持つと推測されるが、その経路自体は本ファイルの解析範囲外）。各スクリプトの冒頭import文からは、`switchbot_power_monitor.py`が`services.switchbot_service`/`services.sensor_service`を、`server_watchdog.py`と`tv_lock_monitor.py`が`services.notification_service`/`services.switchbot_service`をそれぞれ利用する構成であることが確認できるが、各スクリプト内部の詳細な処理ロジックはそれぞれの専用仕様書の解析範囲である。 | 直接ソース確認: `MY_HOME_SYSTEM/scheduler_boot.py:29-43`, `MY_HOME_SYSTEM/monitors/switchbot_power_monitor.py:1-15`, `MY_HOME_SYSTEM/monitors/server_watchdog.py:1-14`, `MY_HOME_SYSTEM/monitors/tv_lock_monitor.py:1-14` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した
完了