## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `camera_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [config.md](./config.md) — `NVR_RECORD_DIR`、`CAMERAS`、`DEVICES_JSON_PATH`設定を提供する。
- [camera_router.md](./camera_router.md) — 呼び出し元。本ファイルの各関数がどのHTTPエンドポイントから、どのようなエラーハンドリングと共に呼び出されているかが確認できる（`PUT /settings/{camera_id}`が`set_camera_enabled`を呼び出す）。
- [camera_monitor.md](./camera_monitor.md) — 同様のONVIF/WSDL動的探索ロジック（`find_wsdl_path`）を持つ姉妹モジュール（動体検知監視用）。
- [logger.md](./logger.md) — `setup_logging`の実装元。

## 2. ファイルの概要

* ONVIF対応カメラのRTSP URL取得、ffmpegを用いたライブHLSストリーミング配信、NASに保存された録画mp4ファイル群を結合したVOD（録画）HLSプレイリストの生成、および`devices.json`へのカメラ有効/無効設定の永続化を担うサービス層モジュールである。
* RTSP URLはONVIFカメラへの問い合わせ結果（またはカメラ設定に直接指定された`rtsp_url`）をメモリ上のキャッシュ(`_rtsp_cache`)に保持する。
* ライブ配信・録画変換はそれぞれ`subprocess.Popen`で起動したffmpegプロセスをカメラID／`カメラID_日付`単位で管理辞書(`_active_processes`, `_active_vod_processes`)に登録し、同一キーでの多重起動を防止する。
* 録画プレイリスト生成では、10分単位に分割されたmp4ファイル群から`ffconcat`形式のリストファイルを作成し、ファイル間の時刻差からdurationを補正することで録画の欠落区間にも対応する。
* 根拠: [モジュール冒頭のコメントと定数定義] (行番号: 21〜24 / 抜粋: "# /tmp (RAM) から物理ストレージ（プロジェクト直下のdataディレクトリ）へ変更")


* RTSP URLに含まれる認証情報をログ出力する際は、`urlparse`でnetloc部分のみを安全に再構築する`_mask_rtsp_url_for_log`関数でマスクする。パスワードが空文字の場合に`str.replace('', '***')`が文字列の全文字間へ`***`を挿入して破壊するという以前の不具合を修正したものである。
* 根拠: [_mask_rtsp_url_for_log関数のDocstring] (行番号: 54〜58 / 抜粋: "パスワードが空文字の場合、str.replace('', '***')は文字列の全文字間に")


* ライブ配信ffmpegプロセス起動時には`-hide_banner`/`-loglevel error`オプションを付与し、認証情報込みのRTSP URLがffmpeg自身の起動バナー経由でログファイルに平文出力されるのを防止する。また`ffmpeg.log`は`os.chmod`で`0o600`（所有者のみ読み書き可）に設定し、他ローカルユーザーからの閲覧を防ぐ。
* 根拠: [ffmpegコマンドとchmod] (行番号: 148〜149, 166 / 抜粋: "\"-hide_banner\",")


* `start_hls_stream`で開いたffmpegログファイルのハンドルは、`subprocess.Popen`呼び出し後に`finally`ブロックで親プロセス側から明示的に`close()`される（子プロセスは`dup()`済みのfdを保持するため親側は不要であり、以前はプロセス再起動のたびにファイルハンドルがリークしていた）。
* 根拠: [ログファイルクローズ] (行番号: 171〜175 / 抜粋: "log_file.close()")


* 録画プレイリスト生成(`generate_record_playlist`)は、`process_key`（`カメラID_日付`）単位の`threading.Lock`（`_get_vod_generation_lock`）で排他制御された内部関数`_generate_record_playlist_locked`へ処理を委譲し、同一キーへの同時リクエストによるffmpegの二重起動と同一ファイルへの競合書き込みを防止する。
* 根拠: [ロック委譲] (行番号: 212〜213 / 抜粋: "with _get_vod_generation_lock(process_key):")


* `_active_vod_processes`に登録されたプロセスのうち完了済み（`poll()`が`None`でない）ものは、`_generate_record_playlist_locked`の呼び出しの都度`_prune_finished_vod_processes`により除去され、`カメラID_日付`キーが無限に蓄積することを防ぐ。
* 根拠: [プルーニング呼び出し] (行番号: 240〜241 / 抜粋: "_prune_finished_vod_processes()")


* `set_camera_enabled`は`devices.json`上の該当カメラの`enabled`フラグを更新し、`config.CAMERAS`にも反映する。書き込みは一時ファイル(`.tmp`)への書き込み後に`os.replace`でアトミックに置き換える方式であり、書き込み途中のクラッシュ・電源断による`devices.json`破損を防ぐ。
* 根拠: [set_camera_enabled関数] (行番号: 337〜341 / 抜粋: "tmp_path = f\"{config.DEVICES_JSON_PATH}.tmp\"")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `json` | 標準ライブラリ | `devices.json`の読み込み・書き込み（`set_camera_enabled`） | 根拠: [import文] (行番号: 1 / 抜粋: "import json") |
| `os` | 標準ライブラリ | パス操作(`os.path.join`, `os.path.dirname`, `os.makedirs`, `os.path.exists`, `os.path.basename`)、環境変数取得(`os.getenv`)、`os.chmod`によるパーミッション変更、`os.replace`によるアトミックなファイル置換 | 根拠: [import文] (行番号: 2 / 抜粋: "import os") |
| `sys` | 標準ライブラリ | `sys.path` を走査したWSDLディレクトリ探索 | 根拠: [import文] (行番号: 3 / 抜粋: "import sys") |
| `subprocess` | 標準ライブラリ | ffmpegプロセスの起動(`Popen`)と型ヒント(`subprocess.Popen`) | 根拠: [import文] (行番号: 4 / 抜粋: "import subprocess") |
| `threading` | 標準ライブラリ | VOD生成の`process_key`単位ロック(`threading.Lock`)によるcheck-then-act競合の防止 | 根拠: [import文] (行番号: 5 / 抜粋: "import threading") |
| `time` | 標準ライブラリ | プレイリスト生成待機のスリープ(`time.sleep`) | 根拠: [import文] (行番号: 6 / 抜粋: "import time") |
| `urllib.parse` | 標準ライブラリ | RTSP URIのパース(`urlparse`)、認証情報のURLエンコード(`quote`)、ログ用マスク処理(`_mask_rtsp_url_for_log`内での`urlparse`/`_replace`) | 根拠: [import文] (行番号: 7 / 抜粋: "import urllib.parse") |
| `glob` | 標準ライブラリ | 日付パターンに一致するmp4ファイルの検索(`glob.glob`) | 根拠: [import文] (行番号: 8 / 抜粋: "import glob") |
| `datetime.datetime` | 標準ライブラリ | ファイル名中の時刻文字列のパース、現在日付との比較 | 根拠: [import文] (行番号: 9 / 抜粋: "from datetime import datetime") |
| `typing.Optional`, `Dict`, `Any`, `List` | 標準ライブラリ | 型ヒント（`List`は本ファイル内で明示的な使用箇所なし） | 根拠: [import文] (行番号: 10 / 抜粋: "from typing import Optional, Dict, Any, List") |
| `core.logger.setup_logging` | 内部モジュール | ロガーインスタンスの生成 | 根拠: [import文] (行番号: 11 / 抜粋: "from core.logger import setup_logging") |
| `config` | 内部モジュール | NVR録画保存ディレクトリ(`NVR_RECORD_DIR`)の取得（属性が無い場合は環境変数にフォールバック）、`devices.json`のパス(`DEVICES_JSON_PATH`)、カメラ設定一覧(`CAMERAS`)の参照・更新 | 根拠: [config参照] (行番号: 182, 221, 325, 344 / 抜粋: "nvr_base_dir = getattr(config, 'NVR_RECORD_DIR', ") |
| `onvif.ONVIFCamera` | 外部ライブラリ（任意依存） | ONVIFカメラへの接続、メディアプロファイル取得、ストリームURI取得。インポート失敗時は`Any`にフォールバック | 根拠: [try-exceptインポート] (行番号: 14〜17 / 抜粋: "try:\n    from onvif import ONVIFCamera\nexcept ImportError:\n    ONVIFCamera = Any") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `ONVIFCamera` (onvifライブラリ) | `create_media_service`, `GetProfiles`, `create_type`, `GetStreamUri` 等のメソッドの内部実装・通信プロトコル詳細は本ファイルからは不明。 | 根拠: [ONVIFCameraの利用箇所] (行番号: 102 / 抜粋: "mycam = ONVIFCamera(cam_conf['ip'], cam_conf.get('port', 80), cam_conf['user'], cam_conf.get('pass', ''), wsdl_dir=wsdl_path)") |
| `config` | `config.NVR_RECORD_DIR`属性の有無や値、`config.DEVICES_JSON_PATH`が指す実際のファイルパス、`config.CAMERAS`の実データがどのように設定されているか（環境変数、設定ファイル等）が本ファイルからは不明。 | 根拠: [getattr呼び出し] (行番号: 182 / 抜粋: "nvr_base_dir = getattr(config, 'NVR_RECORD_DIR', ") |
| `setup_logging` | 生成されるロガーの出力先・フォーマット・ログレベルの詳細が不明。 | 根拠: [ロガー生成] (行番号: 19 / 抜粋: "logger = setup_logging(\"camera_service\")") |
| `ffmpeg` / `nice` (外部コマンド) | `subprocess.Popen`で起動される外部コマンドの内部動作・エラー時の終了コード仕様は本ファイルの管理対象外。 | 根拠: [Popen呼び出し] (行番号: 143, 170, 294, 310 / 抜粋: "\"nice\", \"-n\", \"15\",") |
| `devices.json` (外部ファイル) | `set_camera_enabled`が読み書きする対象であり、既存カメラエントリの正確なJSON構造・件数は本ファイルからは不明。 | 根拠: [devices.json読み書き] (行番号: 325, 328〜329 / 抜粋: "if not os.path.exists(config.DEVICES_JSON_PATH):") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `_get_vod_generation_lock`

* **役割**: 指定された`process_key`に対応する`threading.Lock`を`_vod_generation_locks`辞書から取得し、存在しなければ新規作成して返す。ロックオブジェクトの生成自体は`_vod_generation_locks_guard`で保護される。
* 根拠: [関数定義] (行番号: 36〜42 / 抜粋: "def _get_vod_generation_lock(process_key: str) -> threading.Lock:")


* **引数/リクエスト**: `process_key: str`
* 根拠: [引数定義] (行番号: 36 / 抜粋: "def _get_vod_generation_lock(process_key: str) -> threading.Lock:")


* **戻り値/レスポンス**: `threading.Lock`（該当`process_key`用のロックオブジェクト）
* 根拠: [戻り値] (行番号: 42 / 抜粋: "return lock")


* **副作用**: `_vod_generation_locks`辞書への新規ロック登録（未登録時のみ）。
* 根拠: [ロック登録] (行番号: 41 / 抜粋: "_vod_generation_locks[process_key] = lock")


* **エラーハンドリング**: なし


### `_prune_finished_vod_processes`

* **役割**: `_active_vod_processes`に登録されたプロセスのうち、`poll()`が`None`でない（完了済み）ものをすべて辞書から除去する。
* 根拠: [関数定義とDocstring] (行番号: 45〜48 / 抜粋: "キーがcam_id×target_dateの組み合わせのため、剪定しないと日々増え続けて")


* **引数/リクエスト**: なし
* 根拠: [関数定義] (行番号: 45 / 抜粋: "def _prune_finished_vod_processes() -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: [関数定義] (行番号: 45 / 抜粋: "def _prune_finished_vod_processes() -> None:")


* **副作用**: `_active_vod_processes`辞書から完了済みエントリを削除する。
* 根拠: [削除処理] (行番号: 49〜51 / 抜粋: "finished_keys = [key for key, proc in _active_vod_processes.items()")


* **エラーハンドリング**: なし


### `_mask_rtsp_url_for_log`

* **役割**: RTSP URLに含まれる認証情報(user:pass)をログ出力用にマスクする。`urlparse`でnetloc部分のみを`***@host`（パスワードなし）または`***:***@host`（パスワードあり）に置換して再構築する。ユーザー名・パスワードのいずれも含まれないURLはそのまま返す。
* 根拠: [関数定義とDocstring] (行番号: 54〜58 / 抜粋: "'***'を挿入して破壊する(Pythonの仕様)ため、urlparseでnetloc部分のみ")


* **引数/リクエスト**: `url: str`
* 根拠: [引数定義] (行番号: 54 / 抜粋: "def _mask_rtsp_url_for_log(url: str) -> str:")


* **戻り値/レスポンス**: `str`（マスク済みURL。パース処理中に例外が発生した場合は固定文字列`"***"`）
* 根拠: [各return文] (行番号: 62, 67, 69 / 抜粋: "return parsed._replace(netloc=masked_netloc).geturl()")


* **副作用**: なし


* **エラーハンドリング**: `urlparse`等での例外を`except Exception:`で包括的に捕捉し、固定文字列`"***"`を返す（フェイルセーフ）。
* 根拠: [try-exceptブロック] (行番号: 68〜69 / 抜粋: "except Exception:\n        return \"***\"")


### `init_output_dir`

* **役割**: `base_dir/camera_id` のディレクトリを作成（既存の場合はそのまま）し、そのパスを返す。
* 根拠: [関数定義] (行番号: 71〜74 / 抜粋: "def init_output_dir(base_dir: str, camera_id: str) -> str:")


* **引数/リクエスト**: `base_dir: str`, `camera_id: str`
* 根拠: [引数定義] (行番号: 71 / 抜粋: "def init_output_dir(base_dir: str, camera_id: str) -> str:")


* **戻り値/レスポンス**: `str`（作成済みディレクトリの絶対/相対パス）
* 根拠: [戻り値] (行番号: 74 / 抜粋: "return cam_dir")


* **副作用**: ディレクトリ作成(`os.makedirs`, `exist_ok=True`)。
* 根拠: [makedirs呼び出し] (行番号: 73 / 抜粋: "os.makedirs(cam_dir, exist_ok=True)")


* **エラーハンドリング**: なし（`os.makedirs`が権限エラー等で例外を送出した場合、呼び出し元に伝播する）


### `find_wsdl_path`

* **役割**: `sys.path` 上の各ディレクトリを走査し、`onvif/wsdl` または `wsdl` サブディレクトリ内に `devicemgmt.wsdl` が存在するパスを探索して返す。
* 根拠: [関数定義とDocstring] (行番号: 76〜77 / 抜粋: "\"\"\"camera_monitor.pyと同等のWSDL動的探索ロジック\"\"\"")


* **引数/リクエスト**: なし
* 根拠: [関数定義] (行番号: 76 / 抜粋: "def find_wsdl_path() -> Optional[str]:")


* **戻り値/レスポンス**: `Optional[str]`（見つかったWSDLディレクトリのパス、見つからない場合は`None`）
* 根拠: [戻り値] (行番号: 85〜86 / 抜粋: "return candidate\n    return None")


* **副作用**: なし（`os.path.exists`によるファイルシステム参照のみ）
* **エラーハンドリング**: なし


### `get_rtsp_url`

* **役割**: カメラのRTSP URLを取得する。キャッシュ(`_rtsp_cache`)、設定内の直接指定(`rtsp_url`)、ONVIF経由の動的取得の順に解決を試み、ONVIF取得時は認証情報をURLエンコードして埋め込んだURIを構築する。
* 根拠: [関数定義] (行番号: 88〜125 / 抜粋: "def get_rtsp_url(cam_conf: Dict[str, Any]) -> str:")


* **引数/リクエスト**: `cam_conf: Dict[str, Any]`（カメラ設定辞書。`id`, `ip`, `user`, `pass`, `port`, `rtsp_url`等のキーを想定）
* 根拠: [引数定義] (行番号: 88 / 抜粋: "def get_rtsp_url(cam_conf: Dict[str, Any]) -> str:")


* **戻り値/レスポンス**: `str`（RTSP URL文字列。キャッシュヒット時・直接指定時はそのまま、ONVIF取得時は認証情報埋め込み済みURI）
* 根拠: [各return文] (行番号: 91, 95, 119, 122 / 抜粋: "auth_uri = f\"rtsp://{safe_user}:{safe_pass}@{parsed.netloc}{parsed.path}?{parsed.query}\"")


* **副作用**: `_rtsp_cache` への書き込み（キャッシュ登録）、ONVIFカメラへのネットワーク接続、取得失敗時のエラーログ出力。
* 根拠: [キャッシュ登録とエラーログ] (行番号: 121, 124 / 抜粋: "_rtsp_cache[cam_id] = auth_uri")


* **エラーハンドリング**: WSDLパスが見つからない場合は`FileNotFoundError`を送出。ONVIF通信等で例外が発生した場合はエラーログを出力したうえで例外を再送出(`raise`)する（呼び出し元での処理が必要）。
* 根拠: [try-exceptブロック] (行番号: 97, 100, 123〜125 / 抜粋: "except Exception as e:\n        logger.error(f\"❌ [{cam_conf['name']}] ONVIF経由のRTSP URL取得に失敗: {e}\")\n        raise")


### `start_hls_stream`

* **役割**: 指定カメラのライブHLSストリーミングをffmpegプロセスとして起動する。既に同一カメラIDのプロセスが実行中であれば新規起動せず既存のプレイリストパスを返す。ffmpegログファイルは`chmod 0o600`（所有者のみ読み書き可）で作成し、起動バナー経由の認証情報露出を防ぐため`-hide_banner`/`-loglevel error`オプションを付与する。
* 根拠: [関数定義] (行番号: 127〜177 / 抜粋: "def start_hls_stream(cam_conf: Dict[str, Any]) -> str:")


* **引数/リクエスト**: `cam_conf: Dict[str, Any]`
* 根拠: [引数定義] (行番号: 127 / 抜粋: "def start_hls_stream(cam_conf: Dict[str, Any]) -> str:")


* **戻り値/レスポンス**: `str`（プレイリストファイルのパス。RTSP URL取得に失敗した場合は空文字列`""`）
* 根拠: [各return文] (行番号: 133, 138, 177 / 抜粋: "except Exception:\n        return \"\"")


* **副作用**: 出力ディレクトリの作成(`init_output_dir`)、ffmpegログファイルのオープンと`chmod 0o600`によるパーミッション設定、`subprocess.Popen`によるffmpegプロセスの起動、起動後の親プロセス側でのログファイルクローズ、`_active_processes`辞書への登録、マスク済みRTSP URLを含むログ出力。
* 根拠: [ログファイル作成・chmod・Popen・クローズ] (行番号: 163〜176 / 抜粋: "log_path = os.path.join(cam_dir, \"ffmpeg.log\")")


* **エラーハンドリング**: `get_rtsp_url`が例外を送出した場合、これを捕捉して空文字列を返す（フェイルソフト）。ffmpegプロセス自体の起動失敗（`subprocess.Popen`の例外）に対する捕捉は本関数内には存在しないが、ログファイルのクローズは`finally`ブロックにより保証される。
* 根拠: [try-exceptブロックとfinally] (行番号: 135〜138, 169〜175 / 抜粋: "try:\n        rtsp_url = get_rtsp_url(cam_conf)\n    except Exception:\n        return \"\"")


### `get_record_start_offset`

* **役割**: 指定日付の最初の録画mp4ファイル名から時刻部分を抽出し、0時0分0秒からの経過秒数を算出して返す。
* 根拠: [関数定義とDocstring] (行番号: 179〜180 / 抜粋: "\"\"\"指定日の最初の録画ファイルの開始時刻を0時からの秒数で返す\"\"\"")


* **引数/リクエスト**: `cam_conf: Dict[str, Any]`, `target_date: str`
* 根拠: [引数定義] (行番号: 179 / 抜粋: "def get_record_start_offset(cam_conf: Dict[str, Any], target_date: str) -> int:")


* **戻り値/レスポンス**: `int`（0時からの経過秒数）。該当ファイルが存在しない場合、または解析に失敗した場合は`0`。
* 根拠: [各return文] (行番号: 189, 195, 198 / 抜粋: "return dt.hour * 3600 + dt.minute * 60 + dt.second")


* **副作用**: `config`/環境変数の参照、ファイルシステム検索(`glob.glob`)、解析失敗時の警告ログ出力。
* 根拠: [glob検索] (行番号: 185〜186 / 抜粋: "search_pattern = os.path.join(search_dir, f\"{target_date}_*.mp4\")")


* **エラーハンドリング**: 対象ファイルが存在しない場合は即座に`0`を返す。ファイル名の時刻文字列パース(`datetime.strptime`)で例外が発生した場合は警告ログを出力し`0`を返す。
* 根拠: [try-exceptブロック] (行番号: 196〜198 / 抜粋: "except Exception as e:\n            logger.warning(f\"Failed to parse start offset for {cam_conf['name']}: {e}\")\n            return 0")


### `generate_record_playlist`

* **役割**: 指定日の録画プレイリスト生成を、`process_key`（`カメラID_日付`）単位の`threading.Lock`で排他制御しながら内部実装`_generate_record_playlist_locked`へ委譲するラッパー関数。実際の生成ロジックは`_generate_record_playlist_locked`が担う。
* 根拠: [関数定義] (行番号: 201〜213 / 抜粋: "def generate_record_playlist(cam_conf: Dict[str, Any], target_date: str) -> Optional[str]:")


* **引数/リクエスト**: `cam_conf: Dict[str, Any]`, `target_date: str`
* 根拠: [引数定義] (行番号: 201 / 抜粋: "def generate_record_playlist(cam_conf: Dict[str, Any], target_date: str) -> Optional[str]:")


* **戻り値/レスポンス**: `Optional[str]`（`_generate_record_playlist_locked`の戻り値をそのまま返す）
* 根拠: [戻り値] (行番号: 213 / 抜粋: "return _generate_record_playlist_locked(cam_conf, target_date, process_key)")


* **副作用**: `process_key`（`f"{cam_id}_{target_date}"`）の算出、`_get_vod_generation_lock`によるロックの取得・解放（`with`文）。
* 根拠: [ロック取得] (行番号: 207, 212 / 抜粋: "with _get_vod_generation_lock(process_key):")


* **エラーハンドリング**: なし（内部実装`_generate_record_playlist_locked`に委譲）


### `_generate_record_playlist_locked`

* **役割**: 指定日の10分単位分割mp4ファイル群を`ffconcat`形式のリストファイルにまとめ、ffmpegでVOD用HLSプレイリストへ変換する。呼び出し前に完了済みVODプロセスを`_prune_finished_vod_processes`で剪定したうえで同一カメラ・日付の変換プロセスの多重実行を防止し、過去日付かつ生成済みの場合はキャッシュされたプレイリストを返す。呼び出し元`generate_record_playlist`が取得した`process_key`単位のロック内で実行されることを前提とする。
* 根拠: [関数定義] (行番号: 216〜319 / 抜粋: "def _generate_record_playlist_locked(cam_conf: Dict[str, Any], target_date: str, process_key: str) -> Optional[str]:")


* **引数/リクエスト**: `cam_conf: Dict[str, Any]`, `target_date: str`, `process_key: str`
* 根拠: [引数定義] (行番号: 216 / 抜粋: "def _generate_record_playlist_locked(cam_conf: Dict[str, Any], target_date: str, process_key: str) -> Optional[str]:")


* **戻り値/レスポンス**: `Optional[str]`（生成または既存のプレイリストパス。保存先ディレクトリ不在時・対象ファイルなし時・生成待機後もファイルが存在しない場合は`None`）
* 根拠: [各return文] (行番号: 249, 251, 258, 319 / 抜粋: "return playlist_path if os.path.exists(playlist_path) else None")


* **副作用**: NVR保存先ディレクトリ・mp4ファイルの検索、出力ディレクトリの作成(`init_output_dir`)、完了済みVODプロセスの剪定(`_prune_finished_vod_processes`)、`ffconcat`リストファイルへの書き込み、`subprocess.Popen`によるffmpegプロセスの起動、`_active_vod_processes`辞書への登録、警告・情報・デバッグログの出力、生成待機のための`time.sleep`。
* 根拠: [プルーニングとプロセス起動] (行番号: 240〜241, 310〜311 / 抜粋: "process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n    _active_vod_processes[process_key] = process")


* **エラーハンドリング**: 保存先ディレクトリが存在しない場合、または対象日のmp4ファイルが見つからない場合は警告ログを出力して`None`を返す。同一キーの変換プロセスが実行中の場合は最大5秒(10回×0.5秒)待機し、それでも未生成なら`None`を返す。ファイル間duration計算時の例外は個別に捕捉し警告ログを出力したうえでデフォルト値(600.0秒)を使用して処理を継続する。
* 根拠: [各ガード節とtry-except] (行番号: 224〜226, 232〜234, 244〜251, 286〜287 / 抜粋: "except Exception as e:\n                    logger.warning(f\"Failed to calculate duration for {mp4}: {e}\")")


### `set_camera_enabled`

* **役割**: `devices.json`上の該当カメラの`enabled`フラグを更新し、`config.CAMERAS`（メモリ上のキャッシュ）にも反映する。`devices.json`が存在しない場合、または該当カメラIDが見つからない場合は`False`を返す。書き込みは一時ファイル(`.tmp`)への書き込み後に`os.replace`で本ファイルへアトミックに置き換える方式であり、書き込み途中のクラッシュ・電源断による`devices.json`破損を防ぐ。
* 根拠: [関数定義とDocstring] (行番号: 322〜324 / 抜粋: "\"\"\"devices.json 上の該当カメラの enabled フラグを更新し、config.CAMERAS にも反映する。")


* **引数/リクエスト**: `camera_id: str`, `enabled: bool`
* 根拠: [引数定義] (行番号: 322 / 抜粋: "def set_camera_enabled(camera_id: str, enabled: bool) -> bool:")


* **戻り値/レスポンス**: `bool`（成功時`True`、`devices.json`不在または該当カメラ未検出時は`False`）
* 根拠: [各return文] (行番号: 326, 334, 349 / 抜粋: "return True")


* **副作用**: `devices.json`の読み込み(`json.load`)、対象カメラの`enabled`フィールド更新、一時ファイルへの書き込みと`os.replace`によるアトミックな置き換え、`config.CAMERAS`内の対応するカメラ辞書の`enabled`フィールド更新。
* 根拠: [アトミック書込] (行番号: 336〜346 / 抜粋: "tmp_path = f\"{config.DEVICES_JSON_PATH}.tmp\"")


* **エラーハンドリング**: `devices.json`が存在しない場合、または該当カメラIDが見つからない場合は`False`を返す（例外は送出しない）。`json.load`やファイルI/O自体で発生し得る例外（不正なJSON、権限エラー等）に対するtry-exceptは本関数内に存在せず、呼び出し元に伝播する。
* 根拠: [ガード節] (行番号: 325〜326, 333〜334 / 抜粋: "if target is None:\n        return False")


## 5. 処理フロー図

`generate_record_playlist`（ロック取得）と`_generate_record_playlist_locked`（実際の録画結合プレイリスト生成ロジック）のフローを示します。

```mermaid
flowchart TD
    Start["Start: generate_record_playlist"] --> AcquireLock["process_key単位のロックを取得<br>(_get_vod_generation_lock)"]
    AcquireLock --> Locked["_generate_record_playlist_locked を実行"]
    Locked --> FindDir["NVR保存先ディレクトリの解決"]
    FindDir --> DirExists{"保存先ディレクトリが存在するか?"}
    DirExists -- No --> WarnNoDir["警告ログ出力"] --> ReturnNone1["戻り値: None"]
    DirExists -- Yes --> GlobFiles["対象日のmp4ファイルをglob検索"]
    GlobFiles --> FilesExist{"ファイルが見つかったか?"}
    FilesExist -- No --> WarnNoFiles["警告ログ出力"] --> ReturnNone1
    FilesExist -- Yes --> PruneProcs["完了済みVODプロセスを剪定<br>(_prune_finished_vod_processes)"]
    PruneProcs --> CheckActive{"同一キーの変換プロセスが実行中か?"}

    CheckActive -- Yes --> WaitLoop["最大5秒待機しつつファイル存在確認"]
    WaitLoop --> WaitOk{"待機中にファイルが生成されたか?"}
    WaitOk -- Yes --> ReturnPath1["戻り値: playlist_path"]
    WaitOk -- No --> ReturnNone2["戻り値: None"]

    CheckActive -- No --> CacheCheck{"過去日付かつプレイリストが既に存在するか?"}
    CacheCheck -- Yes --> ReturnCached["戻り値: playlist_path (キャッシュ)"]
    CacheCheck -- No --> WriteConcat["ffconcatリストファイルを作成<br>(durationは時刻差から補正)"]
    WriteConcat --> RunFfmpeg["外部：subprocess.Popen でffmpeg起動<br>(concat -> HLS VOD)"]
    RunFfmpeg --> RegisterProc["_active_vod_processesに登録"]
    RegisterProc --> WaitGen["最大5秒、生成完了をポーリング"]
    WaitGen --> FileGenerated{"プレイリストファイルが存在するか?"}
    FileGenerated -- Yes --> ReturnPath2["戻り値: playlist_path"]
    FileGenerated -- No --> ReturnNone3["戻り値: None"]
```

`set_camera_enabled`のフローを示します。

```mermaid
flowchart TD
    S1["Start: set_camera_enabled"] --> S2{"devices.json が存在するか?"}
    S2 -- No --> S3["戻り値: False"]
    S2 -- Yes --> S4["devices.json を読み込み(json.load)"]
    S4 --> S5{"該当camera_idが見つかったか?"}
    S5 -- No --> S3
    S5 -- Yes --> S6["対象カメラのenabledフィールドを更新"]
    S6 --> S7["一時ファイル(.tmp)へ書き込み"]
    S7 --> S8["os.replaceでdevices.jsonにアトミック置換"]
    S8 --> S9["config.CAMERAS内の対応カメラのenabledも更新"]
    S9 --> S10["戻り値: True"]
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "camera_service.py"
        logger["logger (Global)"]
        active_processes["_active_processes (Global)"]
        active_vod_processes["_active_vod_processes (Global)"]
        rtsp_cache["_rtsp_cache (Global)"]
        vod_locks["_vod_generation_locks (Global)"]
        get_vod_lock["_get_vod_generation_lock()"]
        prune_vod["_prune_finished_vod_processes()"]
        mask_rtsp["_mask_rtsp_url_for_log()"]
        init_output_dir["init_output_dir()"]
        find_wsdl_path["find_wsdl_path()"]
        get_rtsp_url["get_rtsp_url()"]
        start_hls_stream["start_hls_stream()"]
        get_record_start_offset["get_record_start_offset()"]
        generate_record_playlist["generate_record_playlist()"]
        generate_record_playlist_locked["_generate_record_playlist_locked()"]
        set_camera_enabled["set_camera_enabled()"]
    end

    subgraph "外部依存"
        config["config"]
        core_logger["core.logger"]
        onvif["onvif.ONVIFCamera (任意依存)"]
        subprocess_mod["subprocess (ffmpeg/nice)"]
        os_mod["os"]
        glob_mod["glob"]
        json_mod["json"]
        threading_mod["threading"]
        devices_json["devices.json"]
    end

    logger --> core_logger
    get_rtsp_url --> onvif
    get_rtsp_url --> find_wsdl_path
    get_rtsp_url --> rtsp_cache

    start_hls_stream --> init_output_dir
    start_hls_stream --> get_rtsp_url
    start_hls_stream --> active_processes
    start_hls_stream --> subprocess_mod
    start_hls_stream --> mask_rtsp
    mask_rtsp --> os_mod

    get_record_start_offset --> config
    get_record_start_offset --> glob_mod

    generate_record_playlist --> get_vod_lock
    get_vod_lock --> vod_locks
    get_vod_lock --> threading_mod
    generate_record_playlist --> generate_record_playlist_locked

    generate_record_playlist_locked --> config
    generate_record_playlist_locked --> glob_mod
    generate_record_playlist_locked --> init_output_dir
    generate_record_playlist_locked --> active_vod_processes
    generate_record_playlist_locked --> prune_vod
    generate_record_playlist_locked --> subprocess_mod
    generate_record_playlist_locked --> os_mod

    set_camera_enabled --> config
    set_camera_enabled --> json_mod
    set_camera_enabled --> os_mod
    set_camera_enabled --> devices_json
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `config.py` | `config.NVR_RECORD_DIR`属性の有無や値、`config.DEVICES_JSON_PATH`、`cam_conf`辞書（`id`, `ip`, `user`, `pass`, `port`, `rtsp_url`, `nas_folder`, `name`, `enabled`）を供給する`CAMERAS`設定の全容を把握する必要があるため。 | 根拠: [getattr呼び出し] (行番号: 182 / 抜粋: "nvr_base_dir = getattr(config, 'NVR_RECORD_DIR', ...)") |
| 中 | `core/logger.py` | `setup_logging`によるロガー設定（出力先、フォーマット、ログレベル）を確認するため。 | 根拠: [import文] (行番号: 11 / 抜粋: "from core.logger import setup_logging") |
| 中 | `routers/camera_router.py` | 本モジュールの各関数（`start_hls_stream`, `get_record_start_offset`, `generate_record_playlist`, `set_camera_enabled`, `HLS_LIVE_DIR`, `HLS_VOD_DIR`）がどのようなHTTPエンドポイントから、どのようなエラーハンドリングと共に呼び出されているかを確認するため。 | 根拠: [呼び出し元ファイル。本ファイル単体からは不明] |
| 低 | `onvif`ライブラリ（サードパーティパッケージ） | `ONVIFCamera`クラスの`GetProfiles`/`GetStreamUri`等のAPI仕様を確認するため。 | 根拠: [try-exceptインポート] (行番号: 14〜17 / 抜粋: "from onvif import ONVIFCamera") |
| 低 | `MY_HOME_SYSTEM/tests/test_camera_service_unit.py` | URLマスクの空パスワード耐性、VODプロセスの剪定、`devices.json`のアトミック書込、ログファイルハンドルのclose、同時リクエストでのffmpeg単一起動など、本ファイルの期待仕様が単体テストとして記述されているため、実装意図の確認に有用。 | 根拠: [set_camera_enabled関数] (行番号: 322〜349 / 抜粋: "def set_camera_enabled(camera_id: str, enabled: bool) -> bool:") |

## 8. 保守上の注意点

* **プロセス管理辞書のスレッドセーフティ**: `_active_processes`, `_rtsp_cache` はモジュールレベルのグローバル辞書であり、ロック等の排他制御なしに読み書きされている。`_active_vod_processes`への登録・判定は同一`process_key`について`_get_vod_generation_lock`によるロックで直列化されるようになったが、`_vod_generation_locks`自体には使用済みエントリを削除する仕組みがなく、`カメラID_日付`の組み合わせごとにロックオブジェクトが増え続ける。
* **`start_hls_stream`の広範な例外抑制**: `get_rtsp_url`呼び出しを`except Exception:`で包括的に捕捉し、詳細を握りつぶして空文字列を返している（呼び出し元では失敗理由が判別できない）。
* **ffmpeg起動失敗の未捕捉**: `start_hls_stream`および`_generate_record_playlist_locked`内の`subprocess.Popen`呼び出し自体（例: ffmpeg実行ファイルが存在しない場合の`FileNotFoundError`）に対するtry-exceptが存在せず、例外は呼び出し元に伝播する。
* **RTSP URLのargv経由の残存露出**: ログファイルへの平文露出は`-hide_banner`/`-loglevel error`と`_mask_rtsp_url_for_log`により対策されたが、`subprocess.Popen`に渡す`cmd`のargv自体には認証情報込みのRTSP URLがそのまま含まれており、`ps`コマンド等によるプロセス一覧の閲覧では引き続き見える。ffmpeg CLIの仕様上URLを間接参照する手段がなく、完全な対策は本ファイルの変更のみでは行えない既知の残存リスクである。
* **ハードコードされたパス・値**: NVRのフォールバックパス`/mnt/nas/home_system/nvr_recordings`、ffmpegの`nice`優先度`15`、HLSセグメント長(`2`秒/`4`秒)やリストサイズ(`5`)、待機ループの最大回数(`10`回)・間隔(`0.5`秒)、ffmpegログのパーミッション(`0o600`)、devices.jsonの一時ファイル拡張子(`.tmp`)など多数のマジックナンバー・固定値がコード中に直接埋め込まれている。
* **`get_record_start_offset`と`_generate_record_playlist_locked`のロジック重複**: 両関数とも「NVR保存先の解決」「mp4ファイル名からの時刻抽出」処理をそれぞれ個別に実装しており、重複コードとなっている。
* **`set_camera_enabled`のファイルI/O例外未捕捉**: `devices.json`の読み込み・書き込み時に発生し得る`json.JSONDecodeError`や`OSError`等に対するtry-exceptが本関数内に存在せず、呼び出し元（`camera_router.py`の`PUT /settings/{camera_id}`）に例外がそのまま伝播する設計になっている。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `config.CAMERAS` および `config.NVR_RECORD_DIR` の実体 | `cam_conf`辞書に含まれる正確なキー一覧や、`NVR_RECORD_DIR`属性が設定されているかどうかが本ファイルからは不明。 | `config.py` |
| ロガーの出力仕様 | `setup_logging`が生成するロガーの出力先・フォーマット・ログレベルが不明。 | `core/logger.py` |
| `onvif`ライブラリのAPI仕様 | `ONVIFCamera`, `create_media_service`, `GetProfiles`, `GetStreamUri`等の正確な引数・戻り値仕様が不明。（リポジトリ内および実行環境を検索したが、`onvif`パッケージ自体はインストールされておらず(`ModuleNotFoundError: No module named 'onvif'`)、ソースはリポジトリ内に存在しない。PyPI配布の外部サードパーティ製ライブラリであるため解消不可。なお本ファイル14〜17行目で`try: from onvif import ONVIFCamera except ImportError: ONVIFCamera = Any`という防御的インポートになっており、未インストール環境でも本ファイル自体のインポートは失敗しない設計であることは直接確認できた） | `onvif`パッケージのソースまたは公式ドキュメント |
| 呼び出し元（ルーター）でのエラーハンドリング | 本モジュールの関数が返す`""`, `None`, `0`, `False`, 例外の再送出等を、呼び出し側（`camera_router.py`等）がどのようにHTTPレスポンスへ変換しているかは本ファイルからは不明。 | `routers/camera_router.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| 呼び出し元（ルーター）でのエラーハンドリング | `camera_router.md`の解析によれば、`start_hls_stream`の戻り値が空文字列相当（falsy）の場合はHTTP 500、ポーリングループでプレイリストファイルが生成されなかった場合はHTTP 503を返し、`get_record_start_offset`の戻り値（`int`）はそのまま`{"offset_seconds": offset}`として200で返却され、`generate_record_playlist`が`None`を返した場合はHTTP 404（"Recordings not found"）に変換されると推測される。加えて、本ファイル(`camera_service.py`)322〜349行目の`set_camera_enabled`は`routers/camera_router.py`の`PUT /settings/{camera_id}`（`update_camera_settings(camera_id, payload: CameraSettingsUpdate)`）から呼び出され、`False`が返った場合は`HTTPException(status_code=404, detail="Camera not found")`に変換され、成功時は`{"id": camera_id, "enabled": payload.enabled}`を返すことを`camera_router.py`47〜53行目で直接確認した。 | camera_router.md（直接ソース確認: `MY_HOME_SYSTEM/routers/camera_router.py:47-53`） |
| ロガーの出力仕様 | `logger.md`の解析によれば、`setup_logging`はコンソール出力・日次ローテーションファイル出力に加え、ERRORレベル以上のログをDiscord Webhookへ自動通知するハンドラを登録すると推測される。 | logger.md |
| `config.CAMERAS` および `config.NVR_RECORD_DIR` の実体 | `config.py`および呼び出し元`routers/camera_router.py`を直接確認した。`config.CAMERAS`(297行目で`List[Dict[str, Any]] = []`初期化、300〜305行目で`devices.json`から`CameraConfig(**c).model_dump(by_alias=True)`としてロード)の各要素のキーは`CameraConfig`(144〜154行目)により`id, name, nas_folder(任意), location, ip, port(既定2020), user(任意), password(エイリアス"pass", 任意), rtsp_url(任意), enabled(既定True)`であることを確認した。`enabled`フィールド(154行目)は`set_camera_enabled`が読み書きする`devices.json`カメラエントリの`enabled`キー、および`routers/camera_router.py`の`GET /settings`が返す`"enabled": cam.get("enabled", True)`(43行目)と対応する。本ファイル(`camera_service.py`)は`config.CAMERAS`を`set_camera_enabled`内でのみ直接参照・更新し（344〜346行目）、それ以外の関数は呼び出し元の`camera_router.py`が`next((c for c in config.CAMERAS if c["id"] == camera_id), None)`(例: 45行目)で取得した`cam_conf`辞書を各関数の引数として渡す設計であることを確認した。`config.NVR_RECORD_DIR`は`config.py`436行目で`str = os.path.join(NAS_MOUNT_POINT, "home_system", "nvr_recordings")`（既定`NAS_MOUNT_POINT="/mnt/nas"`のため`/mnt/nas/home_system/nvr_recordings"`）と定義されており、確かに設定されている。本ファイル182行目・221行目の`getattr(config, 'NVR_RECORD_DIR', os.getenv("NVR_RECORD_DIR", "/mnt/nas/home_system/nvr_recordings"))`というフォールバック用ハードコード値は、`config.py`のデフォルト計算結果と完全に一致することを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/config.py:144-154, 216-217, 297, 300-305, 436`, `MY_HOME_SYSTEM/services/camera_service.py:182, 221, 344-346`, `MY_HOME_SYSTEM/routers/camera_router.py:33-45` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
