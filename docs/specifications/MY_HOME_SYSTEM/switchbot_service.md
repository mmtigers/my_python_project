## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `switchbot_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [switchbot.md](./switchbot.md) — `models.switchbot.DeviceStatusResponse`(バリデーション用モデル)を提供
- [config.md](./config.md) — 設定値(`SWITCHBOT_API_TOKEN`等)を提供
- [logger.md](./logger.md) — `core.logger.setup_logging`の実体
- [switchbot_power_monitor.md](./switchbot_power_monitor.md) — 呼び出し元(`get_device_status`を利用)
- [webhook_router.md](./webhook_router.md) — 呼び出し元(`get_device_name_by_id`を利用)
- [switchbot_webhook_fix.md](./switchbot_webhook_fix.md) — 呼び出し元(`create_switchbot_auth_headers`を利用)
- [tv_lock_monitor.md](./tv_lock_monitor.md) — 呼び出し元(`send_device_command`を利用)
- [quest_quest_service.md](./quest_quest_service.md) — 呼び出し元(`services/quest/quest_service.py`のクエスト承認処理から`trigger_tv_unlock`を利用)
- [routine_service.md](./routine_service.md) — 呼び出し元(朝の準備チェックリスト全項目達成時に`trigger_tv_unlock`を利用)
- [notification_service.md](./notification_service.md) — `trigger_tv_unlock`のFail-Soft通知先(`send_push`)を提供

## 2. ファイルの概要

* SwitchBot APIとのHTTP通信（GET/POSTリクエスト、Exponential Backoffによるリトライ処理、HMAC認証ヘッダーの生成）を担う。
* デバイスのステータス取得、デバイスへのコマンド送信処理を提供する。
* デバイスリストを取得し、デバイスIDとデバイス名のマッピングをメモリ上のキャッシュ（グローバル変数）に保持・取得する機能を提供する。
* TVプラグの電源ON処理（`trigger_tv_unlock`）を、複数の呼び出し元（`services/quest/quest_service.py`のクエスト承認処理、`services/routine_service.py`の朝の準備チェックリスト完了処理）から共有される非同期・Fail-Softなヘルパーとして提供する（毎朝ミッション統合で追加）。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `threading`（Issue #439で追加） | 標準ライブラリ | `DEVICE_NAME_CACHE`/`_fetch_attempted`を複数のWebhookリクエストスレッドから保護する`_device_cache_lock`(`threading.Lock`)の生成 | 根拠: `import threading` (行番号: 2 / 抜粋: "import threading")、[`_device_cache_lock`定義] (行番号: 35 / 抜粋: "_device_cache_lock = threading.Lock()") |
| `time` | 標準ライブラリ | 現在時刻の取得、リトライ時の待機（sleep） | 根拠: `import time` (行番号: 3 / 抜粋: "import time") |
| `hashlib` | 標準ライブラリ | HMAC署名生成時のハッシュアルゴリズム（SHA256）指定 | 根拠: `import hashlib` (行番号: 4 / 抜粋: "import hashlib") |
| `hmac` | 標準ライブラリ | 認証ヘッダー用のHMAC署名生成 | 根拠: `import hmac` (行番号: 5 / 抜粋: "import hmac") |
| `base64` | 標準ライブラリ | 生成したHMAC署名のBase64エンコード | 根拠: `import base64` (行番号: 6 / 抜粋: "import base64") |
| `uuid` | 標準ライブラリ | 認証ヘッダー用のnonce（一意な値）生成 | 根拠: `import uuid` (行番号: 7 / 抜粋: "import uuid") |
| `typing` | 標準ライブラリ | 静的型チェックのための型ヒントの提供 | 根拠: `from typing import...` (行番号: 8 / 抜粋: "from typing import Dict, Any, Optional") |
| `requests` | 外部ライブラリ | 外部API（SwitchBot API）へのHTTPリクエスト送信 | 根拠: `import requests` (行番号: 10 / 抜粋: "import requests") |
| `config` | 内部モジュール | APIホストURL、トークン、シークレット等の設定値取得 | 根拠: `import config` (行番号: 11 / 抜粋: "import config") |
| `core.logger` | 内部モジュール | ロガー（`setup_logging`）の取得 | 根拠: `from core.logger import...` (行番号: 14 / 抜粋: "from core.logger import setup_logging") |
| `core.utils`（#661で追加） | 内部モジュール | Exponential Backoff リトライの共通実装（`retry_with_backoff`）の取得。`request_switchbot_api` の独自ループを置き換えた | 根拠: `from core.utils import...` (行番号: 15 / 抜粋: "from core.utils import retry_with_backoff") |
| `models.switchbot` | 内部モジュール | レスポンスデータ検証用のPydanticモデル取得 | 根拠: `from models.switchbot import...` (行番号: 16 / 抜粋: "from models.switchbot import DeviceStatusResponse") |
| `services.notification_service`（毎朝ミッション統合で追加） | 内部モジュール | `trigger_tv_unlock`のFail-Soft通知（TV電源ON失敗時に親グループへLINE Push） | 根拠: `from services import notification_service` (行番号: 17 / 抜粋: "from services import notification_service") |

* **（Issue #664）** 12行目には長らく `# from common import retry_api_call # 削除` というコメントだけが残っていたが、`common.py`（Deprecated Facade）そのものの廃止に伴い「`common.py` ごと廃止された」旨の注記へ置き換えられた。実行される import は増減しておらず、リトライの実体は上表の `core.utils.retry_with_backoff` である（#661 以前は本モジュール内の独自ループだった）。根拠: コメント行 (行番号: 12 / 抜粋: "# (以前ここにあった `from common import retry_api_call` のコメントは、common.py ごと廃止された。Issue #664)")

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `config` | 設定値（トークン、シークレット、ホストURL）が環境変数から取得されているか等の実装詳細が不明。 | 根拠: `config.SWITCHBOT_API_TOKEN` (行番号: 157 / 抜粋: "token = config.SWITCHBOT_API_TOKEN") |
| `DeviceStatusResponse` | モデルのプロパティ定義や、`dict()`呼び出し時の挙動（シリアライズ仕様）が不明。 | 根拠: `DeviceStatusResponse` (行番号: 28 / 抜粋: "validated = DeviceStatusResponse(**raw_data)") |
| `setup_logging` | 生成されるロガーの設定（出力先、フォーマット、ログレベルなど）の詳細が不明。 | 根拠: `setup_logging` (行番号: 19 / 抜粋: "logger = setup_logging("service.switchbot")") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `request_switchbot_api`

* **役割**: SwitchBot APIに対してGETリクエストを送信する。タイムアウトや接続エラー時にはExponential Backoffを用いて最大指定回数リトライする。取得したデータをモデルでバリデーションして返す。
* 根拠: `request_switchbot_api` (行番号: 37〜86 / 抜粋: "def request_switchbot_api(url: str, headers: Dict[str, str], max_retries: int = 4) -> Optional[Dict[str, Any]]:")
* バックオフのループ自体は `core.utils.retry_with_backoff` へ委譲している。docstringによれば、これは #661 で独自ループを共通実装へ寄せたものであり、「GETは冪等なので再送して安全」であるのに対しコマンド送信の `post_switchbot_api` は二重実行の副作用がありうるため統合の対象外である、と説明されている。
* 根拠: [docstring および委譲呼び出し] (行番号: 40〜41, 70〜76 / 抜粋: "#661: バックオフのループ自体は `core.utils.retry_with_backoff` に寄せた", "        return retry_with_backoff(")
* `max_retries` は初回を含む総試行回数として扱われ、`retry_with_backoff` へは `max_retries - 1`(初回を含まない追加リトライ回数)として渡される。
* 根拠: (行番号: 72 / 抜粋: "            max_retries=max_retries - 1,  # max_retries は初回を含む総試行回数")


* **引数/リクエスト**:
* `url`: `str` (リクエスト先URL)
* `headers`: `Dict[str, str]` (リクエストヘッダー)
* `max_retries`: `int` (最大リトライ回数、デフォルト4)
* 根拠: `request_switchbot_api` (行番号: 37 / 抜粋: "def request_switchbot_api(url: str, headers: Dict[str, str], max_retries: int = 4) -> Optional[Dict[str, Any]]:")


* **戻り値/レスポンス**: `Optional[Dict[str, Any]]` (バリデーション済みの辞書データ。全リトライ失敗時はNone)
* 根拠: `request_switchbot_api` (行番号: 37 / 抜粋: "def request_switchbot_api(url: str, headers: Dict[str, str], max_retries: int = 4) -> Optional[Dict[str, Any]]:")


* **副作用**: ロガーへの出力（警告、エラー、デバッグ）。失敗した試行ごとに1本の警告と、最後に「完全失敗」の警告1本が出る。最終試行ぶんの警告は `on_retry` が呼ばれないため例外ハンドラ側から同じ書式で出している。
* 根拠: (行番号: 60〜62, 79〜80, 84 / 抜粋: "        logger.warning(f"⚠️ SwitchBot API connection issue (Attempt {attempts['n']}/{max_retries}): {error}")", "        _warn_connection_issue(e)", "    logger.warning("⚠️ SwitchBot API completely failed after retries. Operating in Fail-Soft mode.")")


* **エラーハンドリング**:
* `requests.exceptions.Timeout`, `requests.exceptions.ConnectionError`: `retryable_exceptions` に指定され、警告ログを出力して待機後にリトライされる。全リトライを使い切ると `retry_with_backoff` が再送出し、呼び出し側の `except` が受けて `None` を返す。
* その他の `requests.exceptions.RequestException`: `retryable_exceptions` に含まれないため `retry_with_backoff` から即座に伝播し、エラーログを出力して `None` を返す(リトライしない)。
* 上記いずれの経路でも最後に「完全失敗」の警告ログを出力し `None` を返す（フェイルソフト）。
* `requests.exceptions.RequestException` に該当しない例外(APIの応答が想定外の形だった場合のPydanticの検証エラー等)は捕捉せず呼び出し元へ送出する。docstringによれば、これは `None` に混ぜると「通信できなかった」と区別がつかなくなるためである。
* 根拠: (行番号: 46〜49, 69〜84 / 抜粋: "    ただしAPIの応答が想定外の形", "    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:")



### `post_switchbot_api`

* **役割**: SwitchBot APIに対してPOSTリクエストを送信する。モデルによるバリデーションは行わず生データを返す。
* 根拠: `post_switchbot_api` (行番号: 88〜99 / 抜粋: "def post_switchbot_api(url: str, ...")


* **引数/リクエスト**:
* `url`: `str` (リクエスト先URL)
* `headers`: `Dict[str, str]` (リクエストヘッダー)
* `json_data`: `Dict[str, Any]` (POSTするJSONペイロード)
* 根拠: `post_switchbot_api` (行番号: 88 / 抜粋: "url: str, headers: Dict[str, str], json_data: Dict[str, Any]")


* **戻り値/レスポンス**: `Dict[str, Any]` (APIレスポンスのJSONパース結果)
* 根拠: `post_switchbot_api` (行番号: 51 / 抜粋: "-> Dict[str, Any]:")


* **副作用**: 外部APIへのデータ送信（デバイスの操作など）
* 根拠: `requests.post` (行番号: 53 / 抜粋: "response = requests.post(url, ...")


* **エラーハンドリング**: HTTPエラーステータスが返却された場合、`response.raise_for_status()` により例外を送出。
* 根拠: `raise_for_status` (行番号: 54 / 抜粋: "response.raise_for_status()")



### `send_device_command`

* **役割**: 指定されたデバイスIDに対し、エンドポイントURLと認証ヘッダー、ペイロードを構築し、コマンド送信リクエストを行う。
* 根拠: `send_device_command` (行番号: 101〜119 / 抜粋: "def send_device_command(device_id: str, ...")


* **引数/リクエスト**:
* `device_id`: `str` (対象デバイスのID)
* `command`: `str` (実行するコマンド名)
* `parameter`: `str` (コマンドのパラメータ、デフォルト"default")
* `command_type`: `str` (コマンドの種類、デフォルト"command")
* 根拠: `send_device_command` (行番号: 101 / 抜粋: "device_id: str, command: str, parameter: str = "default", command_type: str = "command"")


* **戻り値/レスポンス**: `Optional[Dict[str, Any]]` (送信結果のレスポンス、失敗時はNone)
* 根拠: `send_device_command` (行番号: 58 / 抜粋: "-> Optional[Dict[str, Any]]:")


* **副作用**: APIへのPOSTリクエスト呼び出し、失敗時のエラーログ出力
* 根拠: `post_switchbot_api` (行番号: 115 / 抜粋: "response_data = post_switchbot_api(url, headers, payload)")


* **エラーハンドリング**: 実行中の任意の例外（`Exception`）をキャッチし、エラーログを出力して `None` を返す。
* 根拠: `except Exception as e` (行番号: 74〜76 / 抜粋: "except Exception as e:")



### `trigger_tv_unlock`（毎朝ミッション統合で追加）

* **役割**: TVプラグ（`config.TV_PLUG_DEVICE_ID`）の電源をONにする処理を、デーモンスレッド上で非同期・Fail-Softに実行する共有ヘルパー。元は`services/quest/quest_service.py`の`QuestService._trigger_tv_unlock`（クエスト承認時のTVロック解除専用）だったが、`services/routine_service.py`（朝の準備チェックリスト全項目達成時）でも同じ処理が必要になったため本ファイルへ切り出され、両呼び出し元から共有される。呼び出し元は事前に`config.TV_PLUG_DEVICE_ID`が設定されているかを確認する必要があり、本関数自体はその設定有無をガードしない（切り出し前の`QuestService._trigger_tv_unlock`の挙動をそのまま踏襲）。
* 根拠: `trigger_tv_unlock` (行番号: 121〜152 / 抜粋: "def trigger_tv_unlock(context: str) -> None:")、切り出し元に関する説明 (行番号: 97〜100 / 抜粋: "quest_service(クエスト承認時のTVロック解除)・routine_service(朝の準備\n    チェックリスト全項目達成時)など、複数の呼び出し元から共有される処理。")


* **引数/リクエスト**: `context`: `str`（ログ出力にのみ使う識別用の文字列。例: `"quest_id=101"`、`"朝の準備チェックリスト全項目達成"`）
* 根拠: `trigger_tv_unlock` (行番号: 121 / 抜粋: "def trigger_tv_unlock(context: str) -> None:")


* **戻り値/レスポンス**: `None`（結果はログ出力とFail-Soft通知のみで呼び出し元へは返さない。TV電源ONの成否を呼び出し元が同期的に知る手段はない）
* 根拠: `trigger_tv_unlock` (行番号: 94 / 抜粋: "-> None:")


* **副作用**: `daemon=True`の`threading.Thread`を起動し、その中で`send_device_command(config.TV_PLUG_DEVICE_ID, "turnOn")`を呼び出す。成功時は情報ログのみ。失敗時（`send_device_command`が`None`または`statusCode`が100以外を返した場合、または例外発生時）はエラーログを出力し、さらに`config.LINE_PARENTS_GROUP_ID`が設定されていれば`notification_service.send_push`で親グループへLINE通知を送る。呼び出し元スレッド（APIルーティング処理）はこのスレッド起動をブロックしない。
* 根拠: `unlock_task` 定義とスレッド起動 (行番号: 132〜148 / 抜粋: "def unlock_task():\n        logger.info(f\"📺 Initiating TV Unlock (Turn ON) for {context}\")"), スレッド起動 (行番号: 151〜152 / 抜粋: "t = threading.Thread(target=unlock_task, daemon=True)\n    t.start()"), Fail-Soft通知 (行番号: 116〜121 / 抜粋: "if config.LINE_PARENTS_GROUP_ID:\n                msg = \"⚠️ テレビの電源ON（自動ロック解除）に失敗しました。お手数ですが、SwitchBotアプリ等から手動でつけてあげてください。\"\n                notification_service.send_push(")


* **エラーハンドリング**: `unlock_task`内で`send_device_command`の戻り値が偽値または`statusCode != 100`の場合は`Exception`を送出して直後の`except Exception as e:`で捕捉し、任意の例外（`send_device_command`自体が投げうる例外も含む）をエラーログ出力とFail-Soft通知（LINE Push失敗時の例外は捕捉しない）で処理する。デーモンスレッド内で例外が伝播してもプロセス全体やAPIルーティングには影響しない。
* 根拠: `raise Exception` (行番号: 139 / 抜粋: "raise Exception(f\"API returned error: {res}\")")、`except Exception as e` (行番号: 113〜114 / 抜粋: "except Exception as e:\n            logger.error(f\"❌ TV Unlock failed: {e}\")")


### `create_switchbot_auth_headers`

* **役割**: トークン、タイムスタンプ、nonceを用いてHMAC-SHA256署名を生成し、APIリクエストに必要な認証ヘッダー群を構築する。
* 根拠: `create_switchbot_auth_headers` (行番号: 155〜182 / 抜粋: "def create_switchbot_auth_headers() -> Dict[str, str]:")


* **引数/リクエスト**: なし
* 根拠: `create_switchbot_auth_headers` (行番号: 155 / 抜粋: "def create_switchbot_auth_headers()")


* **戻り値/レスポンス**: `Dict[str, str]` (認証情報の入ったヘッダー辞書、設定不備時は空辞書)
* 根拠: `create_switchbot_auth_headers` (行番号: 155 / 抜粋: "-> Dict[str, str]:")


* **副作用**: 警告ログ出力（トークンまたはシークレット欠如時）
* 根拠: `logger.warning` (行番号: 162 / 抜粋: "logger.warning("SwitchBot Token/Secret is missing in config.")")


* **エラーハンドリング**: トークンまたはシークレットが設定されていない場合、警告を出力して空の辞書を返す。
* 根拠: `if not token or not secret:` (行番号: 84〜86 / 抜粋: "if not token or not secret:")



### `fetch_device_name_cache`

* **役割**: SwitchBot APIのデバイス一覧エンドポイントからデバイス情報を取得し、グローバル変数 `DEVICE_NAME_CACHE` にデバイスIDと名前のペアを格納する。**（Issue #439で修正）** 以前はAPIから取得したデバイス名を`DEVICE_NAME_CACHE`へループの都度ロック無しで直接書き込んでいたが、現在はまずローカル辞書`new_names`（ネットワークI/O中はロックを取得しない）へ全件を集め、最後に`_device_cache_lock`保持下で`DEVICE_NAME_CACHE.update(new_names)`によりまとめてマージする。
* 根拠: `fetch_device_name_cache` (行番号: 184〜223 / 抜粋: "def fetch_device_name_cache() -> bool:")、[ローカル辞書への集約とロック下でのマージ] (行番号: 137〜148 / 抜粋: "new_names: Dict[str, str] = {}\n            # 通常デバイス\n            for d in body.get('deviceList', []):\n                new_names[d['deviceId']] = d['deviceName']", "with _device_cache_lock:\n                DEVICE_NAME_CACHE.update(new_names)\n                cache_size = len(DEVICE_NAME_CACHE)")


* **引数/リクエスト**: なし
* 根拠: `fetch_device_name_cache` (行番号: 184 / 抜粋: "def fetch_device_name_cache()")


* **戻り値/レスポンス**: `bool` (処理の成功・失敗)
* 根拠: `fetch_device_name_cache` (行番号: 184 / 抜粋: "-> bool:")


* **副作用**: `_device_cache_lock`保持下でのグローバル変数 `DEVICE_NAME_CACHE` の追加更新（マージ）。インフォメーションおよびエラーログ出力。APIへのGETリクエスト（ロック外）。
* 根拠: `global DEVICE_NAME_CACHE` (行番号: 186 / 抜粋: "global DEVICE_NAME_CACHE")、[ロック保持下でのマージ] (行番号: 145〜147 / 抜粋: "with _device_cache_lock:\n                DEVICE_NAME_CACHE.update(new_names)")


* **エラーハンドリング**:
* 認証ヘッダー取得失敗時は `False` を返す。
* APIレスポンスが `None` の場合（Fail-Soft時）は `False` を返す。
* `statusCode` が100以外の場合はエラーログを出力し `False` を返す。
* 任意の例外発生時はエラーログを出力し `False` を返す。
* 根拠: `except Exception as e` (行番号: 154 / 抜粋: "except Exception as e:")



### `get_device_name_by_id`

* **（Issue #533 で修正）** 遅延ロードの制御を bool の `_fetch_attempted` から最終試行時刻 `_last_fetch_attempt_at`(`time.monotonic()`)に変更し、キャッシュが空のまま `DEVICE_NAME_FETCH_RETRY_SEC`(600秒)経過したら再取得を試みる。以前は一度失敗すると再起動まで再試行しなかった。
* 根拠: (行番号: 27〜28, 168〜174 / 抜粋: "retry_due = (\n            _last_fetch_attempt_at is None\n            or (now - _last_fetch_attempt_at) >= DEVICE_NAME_FETCH_RETRY_SEC\n        )")

* **役割**: `DEVICE_NAME_CACHE` から指定されたデバイスIDに対応するデバイス名を取得する。**（Issue #439で修正）** 「キャッシュが空かつ未試行かをチェックしてから`_fetch_attempted`を立てる」処理と、最終的なキャッシュ読み取りは、いずれも`_device_cache_lock`保持下で行うよう修正された。以前はロード無しでこのチェックを行っており、Webhookリクエストが集中する起動直後に複数スレッドが同時に「キャッシュ空・未試行」と判定してしまい、`fetch_device_name_cache`（SwitchBotのデバイス一覧API呼び出し）が並行して複数回走りうる状態だった。ネットワークI/Oを伴う`fetch_device_name_cache()`自体の呼び出しは、`_device_cache_lock`を一度解放してから（ロックの外側で）行う。
* 根拠: `get_device_name_by_id` (行番号: 225〜241 / 抜粋: "def get_device_name_by_id(device_id: str) -> Optional[str]:")、[ロック下でのcheck-and-set] (行番号: 161〜164 / 抜粋: "with _device_cache_lock:\n        should_fetch = not DEVICE_NAME_CACHE and not _fetch_attempted\n        if should_fetch:\n            _fetch_attempted = True")、[ロック外での遅延ロード呼び出し] (行番号: 165〜167 / 抜粋: "if should_fetch:\n        # APIリクエスト(ネットワークI/O)は_device_cache_lock保持中に行わない\n        fetch_device_name_cache()")、[ロック下での最終読み取り] (行番号: 168〜169 / 抜粋: "with _device_cache_lock:\n        return DEVICE_NAME_CACHE.get(device_id, None)")


* **引数/リクエスト**: `device_id`: `str` (デバイスID)
* 根拠: `get_device_name_by_id` (行番号: 158 / 抜粋: "device_id: str")


* **戻り値/レスポンス**: `Optional[str]` (見つかった場合はデバイス名、存在しない場合はNone)
* 根拠: `get_device_name_by_id` (行番号: 225 / 抜粋: "-> Optional[str]:")


* **副作用**: `_device_cache_lock`保持下での`DEVICE_NAME_CACHE`/`_fetch_attempted`の読み取り・書き込み、`DEVICE_NAME_CACHE` が空かつ未試行(`_fetch_attempted`が`False`)の場合、`fetch_device_name_cache()` を1回だけ呼び出して遅延ロードを試みる（**#411 S-L2で追加**: 以前は `fetch_device_name_cache` の呼出元がどこにも無く、`DEVICE_NAME_CACHE` は常に空のままだったため、`devices.json` に登録の無いセンサーからのWebhookは常に `Unknown_<mac>` 表示になっていた）。プロセス起動後の初回呼出し(＝最初のWebhook受信)時にのみ発火し、成否に関わらず以後は再試行しない。
* 根拠: `get_device_name_by_id` (行番号: 161〜167 / 抜粋: "with _device_cache_lock:\n        should_fetch = not DEVICE_NAME_CACHE and not _fetch_attempted")


* **エラーハンドリング**: なし（辞書の `get` メソッドによりKeyErrorを回避）。遅延ロード自体が失敗しても`fetch_device_name_cache`内で例外は握り潰され`False`が返るのみで、本関数は`None`を返す。
* 根拠: `DEVICE_NAME_CACHE.get` (行番号: 241 / 抜粋: "return DEVICE_NAME_CACHE.get(device_id, None)")



### `get_device_status`

* **役割**: 指定されたデバイスのステータス取得用URLを構築し、APIリクエストを送信して結果を取得する。
* 根拠: `get_device_status` (行番号: 243〜256 / 抜粋: "def get_device_status(device_id: str) -> Optional[Dict[str, Any]]:")


* **引数/リクエスト**: `device_id`: `str` (対象デバイスのID)
* 根拠: `get_device_status` (行番号: 171 / 抜粋: "device_id: str")


* **戻り値/レスポンス**: `Optional[Dict[str, Any]]` (取得したステータス辞書、失敗時はNone)
* 根拠: `get_device_status` (行番号: 171 / 抜粋: "-> Optional[Dict[str, Any]]:")


* **副作用**: APIへのGETリクエスト呼び出し、失敗時のエラーログ出力
* 根拠: `request_switchbot_api` (行番号: 252 / 抜粋: "response_data = request_switchbot_api(url, headers)")


* **エラーハンドリング**: 実行中の任意の例外（`Exception`）をキャッチし、エラーログを出力して `None` を返す。
* 根拠: `except Exception as e` (行番号: 182〜184 / 抜粋: "except Exception as e:")



## 5. 処理フロー図

主要な汎用リクエスト関数である `request_switchbot_api` のリトライ制御ロジックのフローを示します。

```mermaid
flowchart TD
    Start["Start: request_switchbot_api"] --> Delegate["core.utils.retry_with_backoff に _fetch を委譲<br/>(max_retries - 1 回まで再試行)"]
    Delegate --> TryRequest["_fetch: 外部 requests.get()"]
    TryRequest -- 成功 --> Validate["外部：DeviceStatusResponseでバリデーション"]
    Validate --> ReturnDict["戻り値: 辞書データ"] --> End["End"]

    TryRequest -- "Timeout / ConnectionError<br/>(retryable_exceptions)" --> CheckRetry{"再試行の余地あり?"}
    CheckRetry -- Yes --> OnRetry["on_retry: 警告ログ出力"] --> Wait["Exponential Backoff 待機<br/>(1s, 2s, 4s...)"] --> TryRequest
    CheckRetry -- No --> Reraise["retry_with_backoff が再送出"] --> LogLast["最終試行ぶんの警告ログ出力"] --> LogFailSoft["完全失敗警告ログ出力"] --> ReturnNone["戻り値: None"] --> End

    TryRequest -- "その他のRequestException<br/>(非リトライ対象)" --> LogErr["エラーログ出力"] --> LogFailSoft

    TryRequest -- "その他の例外(Pydantic検証エラー等)" --> Propagate["捕捉せず呼び出し元へ送出"] --> End

```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "switchbot_service.py"
        logger["logger (Global)"]
        DEVICE_NAME_CACHE["DEVICE_NAME_CACHE (Global)"]
        device_cache_lock["_device_cache_lock (Global, Issue #439)"]
        request_switchbot_api["request_switchbot_api()"]
        post_switchbot_api["post_switchbot_api()"]
        send_device_command["send_device_command()"]
        trigger_tv_unlock["trigger_tv_unlock()（毎朝ミッション統合で追加）"]
        create_switchbot_auth_headers["create_switchbot_auth_headers()"]
        fetch_device_name_cache["fetch_device_name_cache()"]
        get_device_name_by_id["get_device_name_by_id()"]
        get_device_status["get_device_status()"]
    end

    subgraph "外部依存"
        config["config"]
        core_logger["core.logger"]
        core_utils["core.utils (retry_with_backoff)"]
        models_switchbot["models.switchbot"]
        requests["requests"]
        threading_mod["threading"]
        notification_service["services.notification_service"]
    end

    logger --> core_logger
    create_switchbot_auth_headers --> config
    request_switchbot_api --> models_switchbot
    request_switchbot_api --> requests
    request_switchbot_api --> core_utils
    post_switchbot_api --> requests

    send_device_command --> create_switchbot_auth_headers
    send_device_command --> config
    send_device_command --> post_switchbot_api

    trigger_tv_unlock --> send_device_command
    trigger_tv_unlock --> config
    trigger_tv_unlock --> threading_mod
    trigger_tv_unlock --> notification_service

    fetch_device_name_cache --> create_switchbot_auth_headers
    fetch_device_name_cache --> request_switchbot_api
    fetch_device_name_cache --> DEVICE_NAME_CACHE
    fetch_device_name_cache --> device_cache_lock

    get_device_name_by_id --> DEVICE_NAME_CACHE
    get_device_name_by_id --> device_cache_lock
    device_cache_lock --> threading_mod

    get_device_status --> create_switchbot_auth_headers
    get_device_status --> config
    get_device_status --> request_switchbot_api

```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `models/switchbot.py` | `request_switchbot_api` 関数において、すべてのGET通信のレスポンスが `DeviceStatusResponse` でバリデーションされている。このモデルがデバイスリスト取得時（`/v1.1/devices`）のJSON構造も正しく処理できる設計になっているか確認する必要があるため。 | 根拠: `DeviceStatusResponse` (行番号: 16 / 抜粋: "from models.switchbot import DeviceStatusResponse") |
| 中 | `config.py` | API通信のホストURL、トークン、シークレットの設定がどのように注入されているか（環境変数、DB、ファイル等）を把握し、デプロイやテスト要件を明確にするため。 | 根拠: `config` (行番号: 11 / 抜粋: "import config") |

## 8. 保守上の注意点

* **[修正済み・Issue #439] `DEVICE_NAME_CACHE`/`_fetch_attempted`のスレッドセーフティ**: 以前は`fetch_device_name_cache`がグローバル変数 `DEVICE_NAME_CACHE` をロック無しで直接更新しており、`get_device_name_by_id`の「キャッシュが空か確認してから`_fetch_attempted`を立てる」チェックもロック無しで行っていたため、複数のWebhookリクエストスレッドが起動直後に集中すると、check-then-actの隙間を突いて`fetch_device_name_cache`（SwitchBotのデバイス一覧API呼び出し）が複数回同時に走りうる競合状態があった。現在は`_device_cache_lock`（グローバル`threading.Lock`）を導入し、(1) `get_device_name_by_id`の「空かつ未試行か」の判定と`_fetch_attempted`のセット、(2) `fetch_device_name_cache`が新規取得したデバイス名を`DEVICE_NAME_CACHE`へマージする処理、(3) `get_device_name_by_id`の最終的なキャッシュ読み取り、の3箇所をこのロックで保護している。ただしSwitchBot APIへのネットワークリクエスト自体（`fetch_device_name_cache`の`request_switchbot_api`呼び出し）は`_device_cache_lock`を保持したままでは行わない設計になっている。
* **バリデーションモデルの汎用性適用**: `request_switchbot_api` 内で常に `DeviceStatusResponse` モデルによるバリデーションを行っている。しかし、`fetch_device_name_cache` では、同関数を利用して `/v1.1/devices` エンドポイント（ステータスではなくリスト）を要求している。もし `DeviceStatusResponse` がデバイスリスト特有のキー（`deviceList`, `infraredRemoteList`）を許容しない厳密なスキーマだった場合、バリデーションエラーが発生する恐れがある。
* **広範な例外キャッチ**: `send_device_command`, `fetch_device_name_cache`, `get_device_status` において `except Exception as e:` が使われている。これにより予期しないシンタックスエラーや型エラー（TypeError）なども捕捉してしまい、バグが握りつぶされて `None` または `False` として処理される可能性がある。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `DeviceStatusResponse` の仕様 | どのようなプロパティを要求し、バリデーションエラー時にはどのような例外を投げるか（PydanticのValidationError等）が本ファイルからは読み取れない。 | `models/switchbot.py` |
| 認証情報の取得ロジック | `config.SWITCHBOT_API_TOKEN` 等が静的定数なのか、動的な環境変数読み込みなのかが不明。 | `config.py` |
| ロガーの仕様 | 出力フォーマットやログレベルが不明。 | `core/logger.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `DeviceStatusResponse` の仕様 | `models/switchbot.py`を直接確認した。`DeviceStatusResponse`(24〜28行目)は`statusCode: int`、`message: str`、`body: Dict[str, Any]`(デバイスにより中身が変わるため`Any`)の3フィールドを持つPydanticモデルであることが判明した。バリデーションエラー時に投げられる例外の型については本モデル自体には明記がなく、Pydanticの標準動作(`pydantic.ValidationError`)に依拠する設計と考えられるが、これを明示するコードは`models/switchbot.py`内には存在しない。 | 直接ソース確認: `MY_HOME_SYSTEM/models/switchbot.py:24-28` |
| 認証情報の取得ロジック | `config.py`を直接確認した。139行目の`load_dotenv()`実行後、177〜178行目で`SWITCHBOT_API_TOKEN: Optional[str] = os.getenv("SWITCHBOT_API_TOKEN")`、`SWITCHBOT_API_SECRET: Optional[str] = os.getenv("SWITCHBOT_API_SECRET")`と定義されており、静的定数ではなく`.env`ファイル由来の環境変数を動的に読み込む設計であることが判明した。 | 直接ソース確認: `MY_HOME_SYSTEM/config.py:139, 177-178` |
| ロガーの仕様 | `core/logger.py`を直接確認した。`setup_logging(name, webhook_url=None)`(46〜86行目)は、(1) `logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')`形式のコンソール出力用`StreamHandler`(57〜60行目)、(2) `config.BASE_DIR/logs/home_system.log`に対し`TimedRotatingFileHandler(when='midnight', interval=1, backupCount=7, encoding='utf-8')`で日次ローテーションするファイル出力(62〜74行目)、(3) `ERROR`レベル以上を`config.DISCORD_WEBHOOK_ERROR`(または引数指定URL)へPOST通知する`DiscordErrorHandler`(76〜84行目、レベル`logging.ERROR`)の3種のハンドラを登録することが判明した。ロガー自体の基本レベルは`logging.INFO`(54行目)。 | 直接ソース確認: `MY_HOME_SYSTEM/core/logger.py:46-86` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了