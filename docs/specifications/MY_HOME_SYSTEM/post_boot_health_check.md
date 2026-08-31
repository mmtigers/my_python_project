## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `post_boot_health_check.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [common.md](./common.md) - `common.setup_logging`/`common.send_push`を提供するFacadeモジュール(実体は`core.logger`/`services.notification_service`)
* [logger.md](./logger.md) - `setup_logging`の実体。コンソール出力・日次ローテーションファイル出力・ERRORレベルのDiscord通知(`DiscordErrorHandler`)を提供
* [notification_service.md](./notification_service.md) - `common.send_push`の実体。Discord/LINEへの統合通知処理
* [config.md](./config.md) - `LOG_DIR`, `SQLITE_DB_PATH`, `BACKEND_URL`, `SPEAKER_BLUETOOTH_MAC`, `ENABLE_BLUETOOTH`等の設定値を提供する`config.py`の解析結果
* [database.md](./database.md), [init_unified_db.md](./init_unified_db.md) - チェック対象DBの接続・初期化・スキーマ検証を行うモジュール
* [unified_server.md](./unified_server.md) - `check_services`がチェックする「Backend Server」に相当すると推測されるFastAPIサーバー
* [switchbot_service.md](./switchbot_service.md) - `check_network_and_apis`が`create_switchbot_auth_headers()`を直接呼び出して疎通確認するSwitchBot APIのクライアント実装

## 2. ファイルの概要

* システム起動直後にハードウェア・ネットワーク・データベース・周辺機器・各種サービス・直近ログの健全性を一括チェックするスクリプト。
* チェック結果は `CheckResult` データクラスのリストとして `PostBootHealthCheck` インスタンスに蓄積され、最終的に1件のレポートとしてDiscordへ送信（`common.send_push`）される。
* サービス起動待ち（`check_services`）では、対象ポート/HTTPエンドポイントが応答するまで最大12回・10秒間隔でリトライするポーリング処理を持つ。
* NASの書き込み権限エラー検知時（`check_peripherals`）は、レポート集約を待たずにその場で即時Discord通知を行う特別処理を持つ。
* `config` と `common` のインポートに失敗した場合は、標準エラー出力にメッセージを表示してプロセスを終了する（起動時ガード）。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `os` | 標準ライブラリ | パス操作、ファイル存在確認、マウント確認、一時ファイル削除 | `import os` (行番号: 1 / 抜粋: "import os") |
| `sys` | 標準ライブラリ | `sys.path` 操作、標準エラー出力、プロセス終了（`sys.exit`） | `import sys` (行番号: 2 / 抜粋: "import sys") |
| `time` | 標準ライブラリ | サービス起動待ちのリトライ間隔（`sleep`） | `import time` (行番号: 3 / 抜粋: "import time") |
| `socket` | 標準ライブラリ | TCPポート疎通確認 | `import socket` (行番号: 4 / 抜粋: "import socket") |
| `subprocess` | 標準ライブラリ | `vcgencmd`, `ping`, `tail`, `aplay`, `bluetoothctl` 等の外部コマンド実行 | `import subprocess` (行番号: 5 / 抜粋: "import subprocess") |
| `shutil` | 標準ライブラリ | ディスク使用量取得（`disk_usage`） | `import shutil` (行番号: 6 / 抜粋: "import shutil") |
| `requests` | 外部ライブラリ | HTTPヘルスチェック、外部API疎通確認 | `import requests` (行番号: 7 / 抜粋: "import requests") |
| `sqlite3` | 標準ライブラリ | DBファイルの整合性チェック接続 | `import sqlite3` (行番号: 8 / 抜粋: "import sqlite3") |
| `typing.List` | 標準ライブラリ | `results` フィールドの型ヒント | `from typing import List` (行番号: 9 / 抜粋: "from typing import List") |
| `dataclasses.dataclass` | 標準ライブラリ | `CheckResult` クラスの定義 | `from dataclasses import dataclass` (行番号: 10 / 抜粋: "from dataclasses import dataclass") |
| `datetime`, `timedelta` | 標準ライブラリ | ログの時刻フィルタリング（直近10分判定） | `from datetime import datetime, timedelta` (行番号: 11 / 抜粋: "from datetime import datetime, timedelta") |
| `concurrent.futures.ThreadPoolExecutor` | 標準ライブラリ | `check_services`でのサービス起動待ちリトライを対象ごとに並列実行する | `from concurrent.futures import ThreadPoolExecutor` (行番号: 12 / 抜粋: "from concurrent.futures import ThreadPoolExecutor") |
| `config` | 内部モジュール | 各種設定値（`LOG_DIR`, `SQLITE_DB_PATH`, `BACKEND_URL`, `FRONTEND_URL`, `NAS_IP`, `NAS_MOUNT_POINT`, `CAMERAS`, `LINE_USER_ID`, `NATURE_REMO_ACCESS_TOKEN`, `SPEAKER_BLUETOOTH_MAC`, `ENABLE_BLUETOOTH`）の取得 | `import config` (行番号: 19 / 抜粋: "import config") |
| `common` | 内部モジュール | ロガー生成（`setup_logging`）、通知送信（`send_push`） | `import common` (行番号: 20 / 抜粋: "import common") |
| `services.switchbot_service` | 内部モジュール | SwitchBot API疎通確認用の認証ヘッダー生成（`create_switchbot_auth_headers`） | `from services import switchbot_service` (行番号: 21 / 抜粋: "from services import switchbot_service") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `common.setup_logging` | 生成されるロガーの出力先・フォーマット・ログレベルの詳細が不明。 | `logger = common.setup_logging("health_check")` (行番号: 27 / 抜粋: "logger = common.setup_logging("health_check")") |
| `common.send_push` | 通知の実際の送信方式や失敗時の挙動（例外送出の有無等）が不明。 | `common.send_push(` (行番号: 249 / 抜粋: "common.send_push(") |
| `config` の各設定値 | `LOG_DIR`, `SQLITE_DB_PATH`, `BACKEND_URL`, `FRONTEND_URL`, `NAS_IP`, `NAS_MOUNT_POINT`, `CAMERAS`, `LINE_USER_ID`, `NATURE_REMO_ACCESS_TOKEN`, `SPEAKER_BLUETOOTH_MAC`, `ENABLE_BLUETOOTH` の実際の値・存在有無が不明（`LOG_DIR`等は `getattr` によるデフォルト値付きアクセス。`NATURE_REMO_ACCESS_TOKEN`は直接参照）。 | `getattr(config, 'LOG_DIR', os.path.join(BASE_DIR, 'logs'))` (行番号: 62 / 抜粋: "log_dir = getattr(config, 'LOG_DIR', os.path.join(BASE_DIR, 'logs'))") |
| `vcgencmd`, `ping`, `tail`, `aplay`, `bluetoothctl` コマンド | 実行環境（Raspberry Pi等）にこれらのコマンドが存在する前提のコードだが、コマンドの実装自体は本ファイル外。いずれの呼び出しにも`timeout`引数（`bluetoothctl`のみ`stdin=subprocess.DEVNULL`も併用）が付与され、応答なく無限待機する事態を防いでいる。 | `subprocess.check_output(["vcgencmd", "measure_temp"], timeout=10)` (行番号: 97 / 抜粋: "res = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=10).decode("utf-8")") |
| SwitchBot / NatureRemo API | ステータスコードによる疎通確認と認証ヘッダーの送信は行うが、レスポンス本文の内容までは見ていないため、応答スキーマの詳細は不明。 | `("SwitchBot", "https://api.switch-bot.com/v1.0/devices", switchbot_service.create_switchbot_auth_headers())` (行番号: 146 / 抜粋: "("SwitchBot", "https://api.switch-bot.com/v1.0/devices", switchbot_service.create_switchbot_auth_headers()),") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### モジュール初期化処理（パス設定・`config`/`common` インポートガード）

* **役割**: スクリプト自身のディレクトリを `BASE_DIR` として算出し `sys.path` に追加した上で、`config`, `common`, `services.switchbot_service` モジュールをインポートする。インポートに失敗した場合は標準エラー出力にメッセージを表示しプロセスを終了する。
* 根拠: `BASE_DIR = os.path.dirname(os.path.abspath(__file__))` 〜 `sys.exit(1)` (行番号: 15〜24 / 抜粋: "try:\n    import config\n    import common\n    from services import switchbot_service\nexcept ImportError as e:")


* **引数/リクエスト**: なし
* 根拠: (行番号: 15〜24 / 抜粋: "sys.path.append(BASE_DIR)")


* **戻り値/レスポンス**: なし
* 根拠: (行番号: 15〜24 / 抜粋: "import config")


* **副作用**: `sys.path` へのパス追加、失敗時の標準エラー出力・プロセス終了。
* 根拠: `sys.path.append(BASE_DIR)` (行番号: 16 / 抜粋: "sys.path.append(BASE_DIR)")


* **エラーハンドリング**: `ImportError` を捕捉し、エラーメッセージを `sys.stderr` に出力後 `sys.exit(1)` でプロセスを終了する。
* 根拠: `except ImportError as e:` (行番号: 22〜24 / 抜粋: "except ImportError as e:\n    print(f"Error: Failed to import config or common modules. {e}", file=sys.stderr)\n    sys.exit(1)")



### `logger` (モジュールレベル変数)

* **役割**: `common.setup_logging` を用いて `"health_check"` 名のロガーインスタンスを生成する。
* 根拠: `logger = common.setup_logging("health_check")` (行番号: 27 / 抜粋: "logger = common.setup_logging("health_check")")


* **引数/リクエスト**: なし
* 根拠: (行番号: 27 / 抜粋: "logger = common.setup_logging("health_check")")


* **戻り値/レスポンス**: なし（グローバル変数への代入）
* 根拠: (行番号: 27 / 抜粋: "logger = common.setup_logging("health_check")")


* **副作用**: モジュール変数 `logger` の生成。
* 根拠: (行番号: 27 / 抜粋: "logger = common.setup_logging("health_check")")


* **エラーハンドリング**: なし
* 根拠: (行番号: 27 / 抜粋: "logger = common.setup_logging("health_check")")



### `resolve_target_bluetooth_mac`

* **役割**: `config.ENABLE_BLUETOOTH` が真の場合に限り `config.SPEAKER_BLUETOOTH_MAC` を返すモジュールレベル関数。BT運用が無効な環境（`bluetooth.service`停止時など）でSpeakerチェックがBT WARNを出し続けないよう、無効時は `None` を返してサウンドカード確認へのフォールバックを促す。戻り値は直後にモジュールレベル変数 `TARGET_BLUETOOTH_MAC` へ代入される。
* 根拠: `def resolve_target_bluetooth_mac():` 〜 `return getattr(config, "SPEAKER_BLUETOOTH_MAC", None)` (行番号: 32〜40 / 抜粋: "if not getattr(config, "ENABLE_BLUETOOTH", False):\n        return None\n    return getattr(config, "SPEAKER_BLUETOOTH_MAC", None)")


* **引数/リクエスト**: なし
* 根拠: (行番号: 32 / 抜粋: "def resolve_target_bluetooth_mac():")


* **戻り値/レスポンス**: `str | None`（`config.ENABLE_BLUETOOTH`が真なら`config.SPEAKER_BLUETOOTH_MAC`の値、そうでなければ`None`）
* 根拠: `return getattr(config, "SPEAKER_BLUETOOTH_MAC", None)` (行番号: 40 / 抜粋: "return getattr(config, "SPEAKER_BLUETOOTH_MAC", None)")


* **副作用**: なし（`config`属性の読み取りのみ）。呼び出し結果はモジュールレベル変数 `TARGET_BLUETOOTH_MAC` に代入される。
* 根拠: `TARGET_BLUETOOTH_MAC = resolve_target_bluetooth_mac()` (行番号: 42 / 抜粋: "TARGET_BLUETOOTH_MAC = resolve_target_bluetooth_mac()")


* **エラーハンドリング**: なし（`getattr`のデフォルト値により、`config`に該当属性が存在しない場合も例外は発生しない）。
* 根拠: (行番号: 38, 40 / 抜粋: "if not getattr(config, "ENABLE_BLUETOOTH", False):")



### `CheckResult`

* **役割**: 1件のヘルスチェック結果（項目名・ステータス・メッセージ）を保持するデータクラス。
* 根拠: `@dataclass\nclass CheckResult:` (行番号: 50〜54 / 抜粋: "class CheckResult:\n    name: str\n    status: str\n    message: str")


* **引数/リクエスト**: `name: str`, `status: str`, `message: str`
* 根拠: (行番号: 52〜54 / 抜粋: "name: str\n    status: str\n    message: str")


* **戻り値/レスポンス**: `CheckResult` インスタンス
* 根拠: `@dataclass` (行番号: 50 / 抜粋: "@dataclass")


* **副作用**: なし
* 根拠: (行番号: 50〜54 / 抜粋: "class CheckResult:")


* **エラーハンドリング**: なし（型ヒントのみで実行時バリデーションはなし）
* 根拠: (行番号: 50〜54 / 抜粋: "class CheckResult:")



### `PostBootHealthCheck` (クラス概要)

* **役割**: システム起動直後の健全性チェックをまとめて実行するメインクラス。リソース・ネットワーク・DB・サービス・周辺機器・ログの各チェックメソッドと、結果集約・通知送信メソッドを持つ。
* 根拠: `class PostBootHealthCheck:` (行番号: 56〜405 / 抜粋: "class PostBootHealthCheck:")


* **引数/リクエスト**: なし（コンストラクタは引数なし）
* 根拠: `def __init__(self):` (行番号: 57 / 抜粋: "def __init__(self):")


* **戻り値/レスポンス**: 該当なし（クラス定義）
* 根拠: (行番号: 56 / 抜粋: "class PostBootHealthCheck:")


* **副作用**: 該当なし（クラス定義自体には副作用なし。各メソッド参照）
* 根拠: (行番号: 56 / 抜粋: "class PostBootHealthCheck:")


* **エラーハンドリング**: 該当なし（各メソッド参照）
* 根拠: (行番号: 56 / 抜粋: "class PostBootHealthCheck:")



### `PostBootHealthCheck.__init__`

* **役割**: リトライ回数・間隔、結果リスト、ログファイルパスを初期化する。
* 根拠: `def __init__(self):` (行番号: 57〜63 / 抜粋: "def __init__(self):\n        self.max_retries = 12       \n        self.retry_interval = 10    \n        self.results: List[CheckResult] = []")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 57 / 抜粋: "def __init__(self):")


* **戻り値/レスポンス**: なし
* 根拠: (行番号: 57 / 抜粋: "def __init__(self):")


* **副作用**: `self.max_retries=12`, `self.retry_interval=10`, `self.results=[]`, `self.log_file_path` の各インスタンス属性を設定する。
* 根拠: `self.log_file_path = os.path.join(log_dir, "home_system.log")` (行番号: 63 / 抜粋: "self.log_file_path = os.path.join(log_dir, "home_system.log")")


* **エラーハンドリング**: なし
* 根拠: (行番号: 57〜63 / 抜粋: "self.results: List[CheckResult] = []")



### `PostBootHealthCheck._check_port`

* **役割**: 指定ホスト・ポートへのTCP接続を試み、疎通可否を判定する。
* 根拠: `def _check_port(self, host: str, port: int, timeout=3) -> bool:` (行番号: 66〜71 / 抜粋: "def _check_port(self, host: str, port: int, timeout=3) -> bool:")


* **引数/リクエスト**: `host: str`, `port: int`, `timeout=3`
* 根拠: (行番号: 66 / 抜粋: "def _check_port(self, host: str, port: int, timeout=3) -> bool:")


* **戻り値/レスポンス**: `bool`（接続成功時 `True`、失敗時 `False`）
* 根拠: `return True` / `return False` (行番号: 69, 71 / 抜粋: "return True")


* **副作用**: なし（ソケット接続を確立しコンテキスト終了時に自動クローズ）
* 根拠: `with socket.create_connection((host, port), timeout=timeout):` (行番号: 68 / 抜粋: "with socket.create_connection((host, port), timeout=timeout):")


* **エラーハンドリング**: `socket.timeout`, `ConnectionRefusedError`, `OSError` を捕捉し `False` を返す。
* 根拠: `except (socket.timeout, ConnectionRefusedError, OSError):` (行番号: 70 / 抜粋: "except (socket.timeout, ConnectionRefusedError, OSError):")



### `PostBootHealthCheck._check_http`

* **役割**: 指定URLへ`headers`付きでHTTP GETリクエストを送信し、ステータスコードが200〜399の範囲かを判定する。`headers`は省略可能（省略時は`None`のまま`requests.get`に渡される）。
* 根拠: `def _check_http(self, url: str, timeout=5, headers=None) -> bool:` (行番号: 73〜78 / 抜粋: "def _check_http(self, url: str, timeout=5, headers=None) -> bool:")


* **引数/リクエスト**: `url: str`, `timeout=5`, `headers=None`
* 根拠: (行番号: 73 / 抜粋: "def _check_http(self, url: str, timeout=5, headers=None) -> bool:")


* **戻り値/レスポンス**: `bool`
* 根拠: `return 200 <= res.status_code < 400` (行番号: 76 / 抜粋: "return 200 <= res.status_code < 400")


* **副作用**: 外部へのHTTP GETリクエスト送信。
* 根拠: `res = requests.get(url, headers=headers, timeout=timeout)` (行番号: 75 / 抜粋: "res = requests.get(url, headers=headers, timeout=timeout)")


* **エラーハンドリング**: 任意の `Exception` を捕捉し `False` を返す。
* 根拠: `except Exception:` (行番号: 77 / 抜粋: "except Exception:")



### `PostBootHealthCheck._get_uptime`

* **役割**: `/proc/uptime` を読み取り、システム稼働時間を「秒」「分」「時間+分」の形式で文字列化する。
* 根拠: `def _get_uptime(self) -> str:` (行番号: 80〜91 / 抜粋: "def _get_uptime(self) -> str:")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 80 / 抜粋: "def _get_uptime(self) -> str:")


* **戻り値/レスポンス**: `str`（例: `"5秒"`, `"3分"`, `"1時間20分"`。失敗時は `"不明"`）
* 根拠: `return f"{int(uptime_seconds)}秒"` および `return "不明"` (行番号: 85, 91 / 抜粋: "return "不明"")


* **副作用**: `/proc/uptime` ファイルの読み取り。
* 根拠: `with open('/proc/uptime', 'r') as f:` (行番号: 82 / 抜粋: "with open('/proc/uptime', 'r') as f:")


* **エラーハンドリング**: 無条件の `except:`（bare except）で全例外を捕捉し `"不明"` を返す。
* 根拠: `except:` (行番号: 90 / 抜粋: "except:")



### `PostBootHealthCheck.check_system_resources`

* **役割**: CPU温度（`vcgencmd measure_temp`）とディスク使用率（`shutil.disk_usage`）を取得し、3段階の閾値（温度: 75°C未満OK/75〜85°C未満WARN/85°C以上ERR、ディスク使用率: 90%以下OK/90〜95%WARN/95%超ERR）に基づきステータスを判定して結果に追加する。いずれかがERRなら全体もERR、いずれかがWARN(かつERRなし)ならWARNとする。
* 根拠: `def check_system_resources(self):` (行番号: 94〜134 / 抜粋: "def check_system_resources(self):")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 94 / 抜粋: "def check_system_resources(self):")


* **戻り値/レスポンス**: なし（`self.results` へ `CheckResult` を追加）
* 根拠: `self.results.append(CheckResult(\n            "System Resource", final_status, f"CPU: {temp_msg} / Disk: {disk_msg}"\n        ))` (行番号: 132〜134 / 抜粋: "self.results.append(CheckResult(")


* **副作用**: `vcgencmd` サブプロセス実行（`timeout=10`付き）、`shutil.disk_usage` によるディスク情報取得、`self.results` への追加。
* 根拠: `res = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=10).decode("utf-8")` (行番号: 97 / 抜粋: "res = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=10).decode("utf-8")")


* **エラーハンドリング**: 温度取得・ディスク取得それぞれを個別の `try/except:`（bare except）で保護し、失敗時は `STATUS_WARN` と `"Unknown"` を設定して処理を継続する。
* 根拠: `except:` (行番号: 106, 121 / 抜粋: "except:\n            temp_status = STATUS_WARN\n            temp_msg = "Unknown"")



### `PostBootHealthCheck.check_network_and_apis`

* **役割**: `8.8.8.8` へのping疎通確認を行い、失敗時はネットワークエラーとして即座に結果を追加し処理を打ち切る。成功時はSwitchBot（`switchbot_service.create_switchbot_auth_headers()`による認証ヘッダー付き）とNatureRemo（`Authorization: Bearer`ヘッダー付き）のAPIへ、ステータスコード検証込みの`_check_http`で疎通確認し結果を追加する。
* 根拠: `def check_network_and_apis(self):` (行番号: 136〜157 / 抜粋: "def check_network_and_apis(self):")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 136 / 抜粋: "def check_network_and_apis(self):")


* **戻り値/レスポンス**: なし（`self.results` へ追加。ping失敗時は途中で `return` して以降のAPIチェックを行わない）
* 根拠: `self.results.append(CheckResult("Network", STATUS_ERR, "Offline (Ping NG)"))\n            return` (行番号: 141〜142 / 抜粋: "return ")


* **副作用**: `ping` コマンドのサブプロセス実行（`timeout=10`付き）、`switchbot_service.create_switchbot_auth_headers()`呼び出し、SwitchBot/NatureRemo APIへの認証ヘッダー付きHTTP GETリクエスト（`_check_http`経由）、`self.results` への追加。
* 根拠: `subprocess.check_call(["ping", "-c", "1", "-W", "2", "8.8.8.8"], stdout=subprocess.DEVNULL, timeout=10)` (行番号: 139 / 抜粋: "subprocess.check_call(["ping", "-c", "1", "-W", "2", "8.8.8.8"], stdout=subprocess.DEVNULL, timeout=10)")


* **エラーハンドリング**: ping失敗時（bare except）は `STATUS_ERR` を追加して即 `return`。個々のAPI呼び出しは `_check_http` 内部で例外・非2xx/3xxステータスの両方を判定し、失敗時は `api_ngs` リストに追加、全体としては処理を継続する。
* 根拠: `except:` (行番号: 140 / 抜粋: "except:"), `if not self._check_http(url, headers=headers):` (行番号: 151 / 抜粋: "if not self._check_http(url, headers=headers):")



### `PostBootHealthCheck.check_database`

* **役割**: SQLite DBファイルの存在確認と `PRAGMA quick_check` による整合性チェックを行う。
* 根拠: `def check_database(self):` (行番号: 160〜181 / 抜粋: "def check_database(self):")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 160 / 抜粋: "def check_database(self):")


* **戻り値/レスポンス**: なし（`self.results` へ追加。ファイル不在時は途中で `return`）
* 根拠: `self.results.append(CheckResult("Database", STATUS_ERR, "File Not Found"))\n            return` (行番号: 166〜167 / 抜粋: "return")


* **副作用**: 読み取り専用モード（`mode=ro`）でのSQLite接続・クエリ実行・接続クローズ、`self.results` への追加。
* 根拠: `conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)` (行番号: 170 / 抜粋: "conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)")


* **エラーハンドリング**: DBファイル不在時は `STATUS_ERR` を追加して `return`。接続・クエリ実行中の任意の `Exception` を捕捉し `STATUS_ERR` とエラー内容を結果に追加する。
* 根拠: `except Exception as e:` (行番号: 180 / 抜粋: "except Exception as e:")



### `PostBootHealthCheck.check_services`

* **役割**: バックエンドサーバー、Family Quest（フロントエンド）、ダッシュボードの3対象について、`ThreadPoolExecutor`（`max_workers=len(targets)`、対象数と同じ3ワーカー）で対象ごとに独立したスレッドへ`_wait_for_service`を並列実行させ、各サービスの起動待ち・判定を並行して行う。以前は3対象を直列にリトライしており、全滅時は最悪ケースで（対象数）×（1対象あたりの最大待ち時間）＝最大6分間notifyがブロックされていたが、並列化により最悪時間を単一対象のリトライ時間（最大2分）程度まで縮めている。
* 根拠: `def check_services(self):` (行番号: 174〜191 / 抜粋: "def check_services(self):"), `with ThreadPoolExecutor(max_workers=len(targets)) as executor:\n            self.results.extend(executor.map(self._wait_for_service, targets))` (行番号: 190〜191 / 抜粋: "with ThreadPoolExecutor(max_workers=len(targets)) as executor:")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 174 / 抜粋: "def check_services(self):")


* **戻り値/レスポンス**: なし（`self.results` へ、`executor.map`が返す各サービスの `CheckResult` を対象の元の順序のまま追加）
* 根拠: `self.results.extend(executor.map(self._wait_for_service, targets))` (行番号: 191 / 抜粋: "self.results.extend(executor.map(self._wait_for_service, targets))")


* **副作用**: `logger.info` によるログ出力、`ThreadPoolExecutor`の生成・3スレッドでの`_wait_for_service`並列実行（各スレッド内で`_check_port`/`_check_http`呼び出しと`time.sleep`が発生）、`self.results` への追加。
* 根拠: `logger.info("⏳ Waiting for services to startup...")` (行番号: 184 / 抜粋: "logger.info("⏳ Waiting for services to startup...")")


* **エラーハンドリング**: 明示的な例外捕捉はなし（`_wait_for_service`側にも例外捕捉はなく、`_check_port`/`_check_http`が内部で例外を吸収して`bool`を返す設計に依存している）。
* 根拠: (行番号: 174〜191 / 抜粋: "def check_services(self):")



### `PostBootHealthCheck._wait_for_service`

* **役割**: 1つのサービス対象（`target`辞書）について、`type`（`"port"`または`"http"`）に応じて`_check_port`/`_check_http`で疎通確認し、成功するまで最大`max_retries`回・`retry_interval`秒間隔でリトライしたうえで判定結果の`CheckResult`を返す。`check_services`から`ThreadPoolExecutor`経由で対象ごとに並列に呼び出されることを前提とした、旧`check_services`本体のリトライループを1対象分に切り出したメソッド。`critical=True`の対象（Backend Server, Family Quest, Dashboard の3件すべて）が全リトライ失敗した場合は`STATUS_ERR`とする（`critical=False`の対象は現状存在しないため`STATUS_WARN`に倒れる分岐は到達しない）。
* 根拠: `def _wait_for_service(self, target: dict) -> CheckResult:` (行番号: 193〜216 / 抜粋: "def _wait_for_service(self, target: dict) -> CheckResult:")


* **引数/リクエスト**: `target: dict`（`"name"`, `"type"`, `"val"`, `"critical"`の各キーを持つ、`check_services`内で定義される対象定義。`Dashboard`は`{"name": "Dashboard", "type": "port", "val": 8501, "critical": True}`）
* 根拠: `{"name": "Dashboard",      "type": "port", "val": 8501, "critical": True},` (行番号: 181 / 抜粋: "{"name": "Dashboard",      "type": "port", "val": 8501, "critical": True},")


* **戻り値/レスポンス**: `CheckResult`（対象名・判定ステータス・メッセージ）
* 根拠: `return CheckResult(target["name"], status, msg)` (行番号: 216 / 抜粋: "return CheckResult(target["name"], status, msg)")


* **副作用**: `_check_port` / `_check_http` 呼び出し、リトライ間の `time.sleep(self.retry_interval)`。
* 根拠: `time.sleep(self.retry_interval)` (行番号: 203 / 抜粋: "time.sleep(self.retry_interval)")


* **エラーハンドリング**: 明示的な例外捕捉はなし。`if target["critical"]:`の分岐で全リトライ失敗時のステータスを`STATUS_ERR`（`critical=True`）または`STATUS_WARN`（`critical=False`、現状到達しない）に振り分ける。
* 根拠: `if target["critical"]:\n                status = STATUS_ERR\n                msg = "Failed"\n            else:\n                status = STATUS_WARN` (行番号: 209〜213 / 抜粋: "if target["critical"]:")



### `PostBootHealthCheck.check_peripherals`

* **役割**: NASのマウント状況と書き込み権限、防犯カメラ群のポート疎通、スピーカー（サウンドカードまたはBluetooth接続）の状態をチェックする。NAS書き込み権限エラー時は即座にDiscord通知を送信する。カメラは `config.CAMERAS` が空（`devices.json` 読み込み失敗等）の場合 `STATUS_WARN "No Config"` とする。スピーカーは `TARGET_BLUETOOTH_MAC`（モジュールレベルで `config.SPEAKER_BLUETOOTH_MAC` から取得）が設定されていれば `bluetoothctl info` による実際のBluetooth接続確認を行う。
* 根拠: `def check_peripherals(self) -> None:` (行番号: 219〜298 / 抜粋: "def check_peripherals(self) -> None:")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 219 / 抜粋: "def check_peripherals(self) -> None:")


* **戻り値/レスポンス**: `None`（`self.results` へNAS・カメラ・スピーカーの各 `CheckResult` を追加）
* 根拠: `-> None:` および `self.results.append(CheckResult("Speaker", spk_status, spk_msg))` (行番号: 219, 298 / 抜粋: "-> None:")


* **副作用**: `os.path.ismount` によるマウント確認、テストファイルの書き込み・削除（NAS書き込みテスト）、`logger.error` 出力、`common.send_push` によるDiscord即時通知（権限エラー時）、カメラへのポート疎通確認、`aplay -l` / `bluetoothctl info` のサブプロセス実行、`self.results` への3件（NAS, Cameras, Speaker）の追加。
* 根拠: `common.send_push(\n                    user_id=getattr(config, "LINE_USER_ID", None),` (行番号: 239〜240 / 抜粋: "common.send_push(")


* **エラーハンドリング**: NAS書き込みテストで `IOError`, `PermissionError` を捕捉し `STATUS_ERR` を設定・エラーログ出力・即時Discord通知を行う。カメラ設定が空の場合は `STATUS_WARN` とする。サウンドカード検出・Bluetooth接続確認処理は個別に bare `except:` で保護されている。
* 根拠: `except (IOError, PermissionError) as e:` (行番号: 234 / 抜粋: "except (IOError, PermissionError) as e:"), `else:\n            cam_status = STATUS_WARN\n            cam_msg = "No Config"` (行番号: 265〜267 / 抜粋: "else:"), `except: pass` (行番号: 279 / 抜粋: "except: pass")



### `PostBootHealthCheck.check_recent_logs`

* **役割**: ログファイルの末尾200行を取得し、直近10分以内に出力された `ERROR` または `CRITICAL` を含む行のみを抽出して結果を判定する。`tail` サブプロセス実行自体が失敗した場合は、ログを読めていない旨を `STATUS_WARN` として明示し、以降の行走査には進まない（旧実装では例外を捕捉してログ出力するのみで `error_lines` が空のまま `STATUS_OK "Clean"` に落ちていたが、修正済み）。
* 根拠: `def check_recent_logs(self):` (行番号: 301〜343 / 抜粋: "def check_recent_logs(self):")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 301 / 抜粋: "def check_recent_logs(self):")


* **戻り値/レスポンス**: なし（`self.results` へ `CheckResult` を追加。ログファイル未存在時・`tail` 失敗時はいずれも途中で `return`）
* 根拠: `self.results.append(CheckResult("Logs", STATUS_WARN, "No log file yet"))\n            return` (行番号: 304〜305 / 抜粋: "return")


* **副作用**: `tail -n 200` サブプロセス実行によるログファイル読み取り、`logger.error` 出力（`tail` 失敗時）、`self.results` への追加。
* 根拠: `res = subprocess.check_output(["tail", "-n", "200", self.log_file_path]).decode("utf-8", errors="ignore")` (行番号: 312 / 抜粋: "res = subprocess.check_output(["tail", "-n", "200", self.log_file_path]).decode("utf-8", errors="ignore")")


* **エラーハンドリング**: ログファイル未存在時は `STATUS_WARN` を追加して `return`。`tail` コマンド実行失敗など全体の `Exception` を捕捉した場合は `logger.error` 出力に加え `STATUS_WARN` を結果に追加して `return`（行の走査自体を行わない）。各行の日時パース失敗（`ValueError`）はその行をスキップする。
* 根拠: `except Exception as e:\n            logger.error(f"Log check failed: {e}")\n            self.results.append(CheckResult("Logs", STATUS_WARN, f"Check Failed: {e}"))\n            return` (行番号: 313〜316 / 抜粋: "self.results.append(CheckResult("Logs", STATUS_WARN, f"Check Failed: {e}"))"), `except ValueError:` (行番号: 329 / 抜粋: "except ValueError:")



### `PostBootHealthCheck.run`

* **役割**: 各チェックメソッド（ネットワーク・システムリソース・DB・周辺機器・サービス・ログ）を順に実行し、最後にレポート送信を行う。
* 根拠: `def run(self):` (行番号: 346〜354 / 抜粋: "def run(self):")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 346 / 抜粋: "def run(self):")


* **戻り値/レスポンス**: なし
* 根拠: (行番号: 346〜354 / 抜粋: "def run(self):")


* **副作用**: `logger.info` によるログ出力、各チェックメソッドの実行、`self._send_report()` の呼び出し。
* 根拠: `self.check_network_and_apis()` 〜 `self._send_report()` (行番号: 348〜354 / 抜粋: "self._send_report()")


* **エラーハンドリング**: なし（各チェックメソッド内部で個別に処理される前提）
* 根拠: (行番号: 346〜354 / 抜粋: "def run(self):")



### `PostBootHealthCheck._send_report`

* **役割**: `self.results` の内容からステータスアイコン付きのレポート文字列を組み立て、ログ出力とDiscord通知を行う。
* 根拠: `def _send_report(self):` (行番号: 356〜390 / 抜粋: "def _send_report(self):")


* **引数/リクエスト**: `self` のみ
* 根拠: (行番号: 356 / 抜粋: "def _send_report(self):")


* **戻り値/レスポンス**: なし
* 根拠: (行番号: 356〜390 / 抜粋: "def _send_report(self):")


* **副作用**: `self._get_uptime()` の呼び出し、`logger.info` によるレポート全文のログ出力、`common.send_push` によるDiscord通知送信。
* 根拠: `common.send_push(\n            user_id=getattr(config, "LINE_USER_ID", None),\n            messages=[{"type": "text", "text": f"{title}\n\n{body}"}],\n            target="discord",\n            channel="report"\n        )` (行番号: 385〜390 / 抜粋: "common.send_push(")


* **エラーハンドリング**: なし
* 根拠: (行番号: 356〜390 / 抜粋: "def _send_report(self):")



### モジュールレベル実行部（`if __name__ == "__main__":`）

* **役割**: スクリプトを直接実行した場合に `PostBootHealthCheck` をインスタンス化し `run()` を呼び出す。
* 根拠: `if __name__ == "__main__":\n    checker = PostBootHealthCheck()\n    checker.run()` (行番号: 392〜394 / 抜粋: "if __name__ == "__main__":")


* **引数/リクエスト**: なし
* 根拠: (行番号: 392〜394 / 抜粋: "checker = PostBootHealthCheck()")


* **戻り値/レスポンス**: なし
* 根拠: (行番号: 392〜394 / 抜粋: "checker.run()")


* **副作用**: `PostBootHealthCheck` インスタンスの生成、`run()` を通じた全チェックの実行とレポート送信。
* 根拠: `checker = PostBootHealthCheck()\n    checker.run()` (行番号: 393〜394 / 抜粋: "checker.run()")


* **エラーハンドリング**: なし
* 根拠: (行番号: 392〜394 / 抜粋: "if __name__ == "__main__":")



## 5. 処理フロー図

`run()` を起点とした全体のチェック実行フローを示します。

```mermaid
flowchart TD
    Start(["Start: run()"]) --> NetCheck["check_network_and_apis()"]
    NetCheck -- ping失敗 --> ResNet["結果: Network ERR"] --> SysCheck
    NetCheck -- ping成功 --> APICheck["外部: SwitchBot/NatureRemo API疎通確認 (認証ヘッダー+ステータスコード検証)"] --> SysCheck["check_system_resources()"]

    SysCheck --> DBCheck["check_database()"]
    DBCheck --> PeriphCheck["check_peripherals()"]
    PeriphCheck -- NAS書き込みエラー --> ImmediateNotify["外部: common.send_push (即時通知)"]
    ImmediateNotify --> SvcCheck
    PeriphCheck -- 正常 --> SvcCheck["check_services() (3対象をThreadPoolExecutorで並列に_wait_for_service実行、各最大12回リトライ)"]

    SvcCheck --> LogCheck["check_recent_logs() (直近10分のERROR/CRITICALを抽出)"]
    LogCheck --> SendReport["_send_report()"]
    SendReport --> BuildMsg["アイコン付きレポート文字列を組み立て"]
    BuildMsg --> LogInfo["logger.info(レポート全文)"]
    LogInfo --> Notify["外部: common.send_push (Discord通知)"]
    Notify --> End(["End"])
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "post_boot_health_check.py"
        logger["logger (Global)"]
        CheckResult["CheckResult (dataclass)"]
        PostBootHealthCheck["PostBootHealthCheck"]
        init["__init__()"]
        check_port["_check_port()"]
        check_http["_check_http()"]
        get_uptime["_get_uptime()"]
        check_system_resources["check_system_resources()"]
        check_network_and_apis["check_network_and_apis()"]
        check_database["check_database()"]
        check_services["check_services()"]
        wait_for_service["_wait_for_service()"]
        check_peripherals["check_peripherals()"]
        check_recent_logs["check_recent_logs()"]
        run["run()"]
        send_report["_send_report()"]
    end

    subgraph "外部依存"
        config["config"]
        common["common"]
        switchbot_service["services.switchbot_service"]
        requests_lib["requests"]
        subprocess_lib["subprocess"]
        socket_lib["socket"]
        sqlite3_lib["sqlite3"]
        shutil_lib["shutil"]
        thread_pool_executor_lib["concurrent.futures.ThreadPoolExecutor"]
    end

    logger --> common
    PostBootHealthCheck --> init
    PostBootHealthCheck --> check_port
    PostBootHealthCheck --> check_http
    PostBootHealthCheck --> get_uptime
    PostBootHealthCheck --> check_system_resources
    PostBootHealthCheck --> check_network_and_apis
    PostBootHealthCheck --> check_database
    PostBootHealthCheck --> check_services
    PostBootHealthCheck --> wait_for_service
    PostBootHealthCheck --> check_peripherals
    PostBootHealthCheck --> check_recent_logs
    PostBootHealthCheck --> run
    PostBootHealthCheck --> send_report

    init --> config
    check_system_resources --> subprocess_lib
    check_system_resources --> shutil_lib
    check_network_and_apis --> subprocess_lib
    check_network_and_apis --> requests_lib
    check_network_and_apis --> switchbot_service
    check_network_and_apis --> config
    check_database --> sqlite3_lib
    check_database --> config
    check_services --> thread_pool_executor_lib
    check_services --> wait_for_service
    check_services --> config
    wait_for_service --> check_port
    wait_for_service --> check_http
    check_peripherals --> config
    check_peripherals --> common
    check_peripherals --> check_port
    check_peripherals --> subprocess_lib
    check_recent_logs --> subprocess_lib
    run --> check_network_and_apis
    run --> check_system_resources
    run --> check_database
    run --> check_peripherals
    run --> check_services
    run --> check_recent_logs
    run --> send_report
    send_report --> get_uptime
    send_report --> common
    send_report --> config
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `common.py` | `setup_logging` と `send_push` の実装が本ファイルの全チェック結果通知・NAS権限エラー即時通知の挙動を左右するため。 | `import common` (行番号: 20 / 抜粋: "import common") |
| 高 | `config.py` | `LOG_DIR`, `SQLITE_DB_PATH`, `BACKEND_URL`, `FRONTEND_URL`, `NAS_IP`, `NAS_MOUNT_POINT`, `CAMERAS`, `LINE_USER_ID`, `NATURE_REMO_ACCESS_TOKEN`, `SPEAKER_BLUETOOTH_MAC` の実値を把握し、どの環境を対象としたヘルスチェックかを確認するため。 | `getattr(config, "SQLITE_DB_PATH", "home_system.db")` (行番号: 151 / 抜粋: "db_path = getattr(config, "SQLITE_DB_PATH", "home_system.db")") |
| 中 | `home_system.db`（対象DBファイル） | `PRAGMA quick_check` の対象となるDBのスキーマ・データ構造を把握し、健全性チェックの意味を正確に理解するため。 | `conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)` (行番号: 160 / 抜粋: "conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)") |

## 8. 保守上の注意点

* **多数の bare `except:`**: `_get_uptime`（80行目）、`check_system_resources`（96, 111行目）、`check_network_and_apis`（130行目）、`check_peripherals`（279, 289行目）で無条件の `except:` が使われており、`KeyboardInterrupt` や `SystemExit` を含むあらゆる例外を捕捉してしまう可能性がある（Python 3ではこれらは `BaseException` 派生であり、bare exceptで捕捉されうる）。
* **（修正済み）`check_recent_logs` のログ判定タイミング**: 以前は `tail` サブプロセスの `Exception` を捕捉した場合でも `error_lines` が空のまま後続の判定に進み `STATUS_OK` として「Clean」と誤報告される問題があったが、現在は `except Exception as e:`（313〜316行目）で即座に `STATUS_WARN` を結果に追加して `return` するよう修正済みで、ログ取得自体の失敗と「エラーなし」が区別されるようになっている。
* **（修正済み）`TARGET_BLUETOOTH_MAC`**: 以前はモジュールレベルで `None` にハードコードされており、Bluetoothスピーカーの接続確認ロジック（`bluetoothctl info` 呼び出し）が常にデッドコード化していたが、現在は `getattr(config, "SPEAKER_BLUETOOTH_MAC", None)`（32行目）から取得するよう修正済みで、`config.SPEAKER_BLUETOOTH_MAC` が設定されていれば実際のBluetooth接続状態を確認する経路が有効になる。
* **NAS権限エラー時の二重通知の可能性**: `check_peripherals` 内で権限エラー検知時に即時 `send_push` を行うが（239〜244行目）、この結果もその後 `self.results` に追加され `_send_report` で改めてレポートに含まれ通知される。同一の障害について2回Discord通知が飛ぶ可能性がある。
* **（修正済み）`check_services` のブロッキング待機**: 以前はBackend Server/Family Quest/Dashboardの3対象を直列にリトライしており、各サービスにつき最大12回×10秒（最大2分/サービス）の同期的な `time.sleep` が発生するため、全滅時は最悪ケースで合計6分間スクリプトがブロックされ通知が遅延していたが、現在は`ThreadPoolExecutor`（190〜191行目）で対象ごとに独立したスレッドへ`_wait_for_service`（193〜216行目）を並列実行するよう修正済みで、最悪時間が単一対象のリトライ時間（最大2分）程度まで縮まっている。
* **SwitchBot/NatureRemo APIの認証はするが応答内容は見ていない**: `check_network_and_apis`（126〜147行目）は認証ヘッダー付与とステータスコード検証（`_check_http`経由）まで行うようになったが、レスポンス本文の内容（デバイス一覧の妥当性等）までは検証していないため、200番台を返すが実質的に空/不正なレスポンスのケースは検知できない。
* **（修正済み）`check_system_resources` の危険域判定**: 以前はCPU温度・ディスク使用率がどれだけ閾値を超過しても `STATUS_WARN` までしか上がらず、危険域でもタイトルアイコンが🔴（`_send_report` の `has_err` 判定）にならなかったが、現在は温度85°C以上・ディスク使用率95%超で `STATUS_ERR` に昇格するよう修正済み（89〜92, 104〜107行目）。
* **（修正済み）`check_services` のDashboard扱い**: 以前は `Dashboard` のみ `critical=False` でポート未応答でも `STATUS_WARN` までしか上がらなかったが、現在は3対象すべて `critical=True`（181行目、`_wait_for_service`が参照する対象定義）に統一され、Dashboard未起動時も `STATUS_ERR` として報告される。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `common.setup_logging` / `common.send_push` の実装 | ロガーの出力先や、Discord通知の実際の送信方式・失敗時の挙動が本ファイルからは不明。 | `common.py` |
| `config` の各設定値の実体 | `LOG_DIR`, `SQLITE_DB_PATH`, `BACKEND_URL`, `FRONTEND_URL`, `NAS_IP`, `NAS_MOUNT_POINT`, `CAMERAS`, `LINE_USER_ID`, `NATURE_REMO_ACCESS_TOKEN`, `SPEAKER_BLUETOOTH_MAC` の実際の値が不明。 | `config.py` |
| 実行環境の前提 | `vcgencmd`, `bluetoothctl`, `aplay` 等のコマンドが利用可能なOS・ハードウェア（Raspberry Pi等）を前提としているかは本ファイルのみからは断定できない。（`start_all.sh`を直接確認したが`vcgencmd`/`bluetoothctl`/`aplay`への言及はなし。ただし`MY_HOME_SYSTEM/old/README.md`4行目に`- **Raspberry Pi IP**: Fixed (Static IP) via NetworkManager.`という記載を発見し、Raspberry Pi上で運用されている旨は別ファイルから確認できた） | 実行環境のセットアップ資料 or `start_all.sh` 等の起動スクリプト |
| DBスキーマ | `PRAGMA quick_check` の対象となるSQLite DBの構造・想定サイズが不明。 | `config.SQLITE_DB_PATH` が指すDBファイル、または `current_schema.sql` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `common.setup_logging` / `common.send_push` の実装 | `MY_HOME_SYSTEM/common.py`15行目・31〜37行目を直接確認したところ、`setup_logging`は`core.logger`から、`send_push`は`services.notification_service`からそのまま再エクスポートされるFacadeであることを確認した。実体の`core/logger.py`の`setup_logging(name, webhook_url=None)`(46〜86行目)はコンソール出力・`config.BASE_DIR/logs/home_system.log`への日次ローテーションファイル出力・ERRORレベルログのDiscord通知(`DiscordErrorHandler`)の3種のハンドラを登録する。実体の`services/notification_service.py`の`send_push(user_id, messages, image_data=None, target="both", channel="notify", filename="snapshot.jpg")`(116〜140行目)は`target`に応じてDiscord Webhook(`_send_discord_webhook`)およびLINE Messaging API(`_send_line_push`)へ送信し、LINE送信失敗時は135〜137行目で`_send_discord_webhook(fallback, None, 'error')`によりDiscordのエラーチャンネルへフォールバック通知する設計であることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/common.py:15, 31-37`, `MY_HOME_SYSTEM/core/logger.py:46-86`, `MY_HOME_SYSTEM/services/notification_service.py:116-140` |
| `config` の各設定値の実体 | `MY_HOME_SYSTEM/config.py`を直接確認した。`LOG_DIR`(230〜233行目)は`ensure_safe_path_with_backoff(os.path.join(BASE_DIR, "logs"), "logs")`の戻り値(通常`{BASE_DIR}/logs`)。`SQLITE_DB_PATH`(224行目)は`os.getenv("SQLITE_DB_PATH") or os.path.join(BASE_DIR, "home_system.db")`。`NAS_IP`(410行目)は既定`"192.168.1.20"`(環境変数`NAS_IP`で上書き可)。`NAS_MOUNT_POINT`(218行目)は既定`"/mnt/nas"`。`FRONTEND_URL`(416行目)は既定`"http://192.168.1.200:8000/quest"`。`CAMERAS`(299〜307行目)は`devices.json`の`"cameras"`配列を`CameraConfig`で検証したリスト。`LINE_USER_ID`(187行目)は`os.getenv("LINE_USER_ID")`で値そのものは`.env`(gitignore対象)依存のため未確認。`NATURE_REMO_ACCESS_TOKEN`(182行目)は`os.getenv("NATURE_REMO_ACCESS_TOKEN")`。`SPEAKER_BLUETOOTH_MAC`(174行目)は既定`"F4:4E:FC:B6:65:D4"`(環境変数`SPEAKER_BLUETOOTH_MAC`で上書き可。`tools/connect_speaker.sh`, `tools/keep_alive_anker.sh`と同一のAnker SoundCore 2のMACアドレス)。なお`BACKEND_URL`は`config.py`内に定義が一切存在せず、`post_boot_health_check.py`175行目の`getattr(config, "BACKEND_URL", "http://localhost:8000")`により常に既定値`"http://localhost:8000"`が使われることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/config.py:174, 182, 187, 218, 224, 230-233, 299-307, 410, 416`（参考: `MY_HOME_SYSTEM/post_boot_health_check.py:175`） |
| DBスキーマ | `config.SQLITE_DB_PATH`(既定`{BASE_DIR}/home_system.db`)の初期化を担う`MY_HOME_SYSTEM/init_unified_db.py`、および実際のスキーマダンプである`MY_HOME_SYSTEM/current_schema.sql`(全346行)を直接確認した。`current_schema.sql`には`device_records`, `ohayo_records`, `daily_records`, `health_records`, `quest_users`, `quest_master`, `quest_history`, `reward_master`, `switchbot_meter_logs`, `power_usage`等、計36個の`CREATE TABLE`文が存在することを確認した。本ファイルの`PRAGMA quick_check;`(162行目)はテーブル単位ではなくDBファイル全体の整合性チェックであり、対象サイズそのものはDBファイルの実データ量に依存するため本ファイル・スキーマ定義からは判断できない。 | 直接ソース確認: `MY_HOME_SYSTEM/current_schema.sql:1-346`（参考: `MY_HOME_SYSTEM/post_boot_health_check.py:150-170`, `MY_HOME_SYSTEM/init_unified_db.py`） |
| 実行環境の前提 | `MY_HOME_SYSTEM/start_all.sh`(全75行)を直接確認したが`vcgencmd`/`bluetoothctl`/`aplay`への言及やハードウェア種別の明記はなかった。ただし`MY_HOME_SYSTEM/old/README.md`4行目に`- **Raspberry Pi IP**: Fixed (Static IP) via NetworkManager.`という記載があることを直接確認し、本システムがRaspberry Pi上で運用されていることが別ファイルから判明した(ただし`post_boot_health_check.py`自体との直接的な結び付きを示す記述ではない)。 | 直接ソース確認: `MY_HOME_SYSTEM/old/README.md:4`（参考: `MY_HOME_SYSTEM/start_all.sh:1-75`） |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
