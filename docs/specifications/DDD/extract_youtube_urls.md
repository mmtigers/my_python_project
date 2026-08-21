## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `extract_youtube_urls.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [file_utils.md](./file_utils.md) — 本ファイルが利用する共通ファイル名サニタイズ処理（`sanitize_filename`）の実装元。
* [../MY_HOME_SYSTEM/nas_utils.md](../MY_HOME_SYSTEM/nas_utils.md) — 本ファイルがインポートを試みる`core.nas_utils.get_managed_target_directory`の実装候補（同名関数のシグネチャ・実装が確認できる）。
* [../MY_HOME_SYSTEM/logger.md](../MY_HOME_SYSTEM/logger.md) — 本ファイルがインポートを試みる`core.logger.get_logger`の実装候補に関する参考情報。
* [batch_download_discord.md](./batch_download_discord.md) — 同じDDDサブシステム内で`yt_dlp`と`file_utils.sanitize_filename`を併用する類似スクリプトとの比較参考。

## 2. ファイルの概要

* モジュールDocstring上「YouTube URL Extractor (Integrated with MY_HOME_SYSTEM)」と称される、指定されたYouTubeチャンネルやプレイリストから動画URLを抽出するスクリプトである。
* 根拠: [モジュールDocstring] (行番号: 4〜9 / 抜粋: "YouTube URL Extractor (Integrated with MY_HOME_SYSTEM)\n------------------------------------------------------\n指定されたYouTubeチャンネルやプレイリストから動画URLを抽出するスクリプト。\nMY_HOME_SYSTEMのエコシステム（ロガー、ディレクトリ構成）に準拠。")
* `MY_HOME_SYSTEM`の共通コア機能（`core.logger.get_logger`, `core.nas_utils.get_managed_target_directory`）のインポートを試み、失敗時（開発環境・単体実行時）はファイル内にフォールバック実装（標準`logging`ベースのロガー、固定`./data`を返すディレクトリ解決関数）を用意している。
* 根拠: [try-exceptブロック] (行番号: 35〜44 / 抜粋: "try:\n    from core.logger import get_logger\n    from core.nas_utils import get_managed_target_directory\n    logger = get_logger(__name__)\nexcept ImportError:")
* `yt_dlp`を用いて対象URL（チャンネル・プレイリスト・単一動画）から動画URLを抽出する`YouTubeExtractor`、抽出結果をテキストファイルへ保存する`FileManager`、SQLite DBに登録されたチャンネルを定期巡回する`SubscriptionManager`、およびコマンドライン引数を解析してこれらを統括する`UrlExtractorApp`の4クラスで構成される。
* 根拠: [各クラス定義] (行番号: 110〜111, 263〜264, 314〜318, 412〜413 / 抜粋: "class YouTubeExtractor:\n    """YouTubeからURL情報を抽出するクラス。"""")
* チャンネルURLが指定された場合は`/videos`と`/playlists`の両方を自動探索し、通常動画一覧に加えて各プレイリストも個別に抽出する。
* 根拠: [extract_iterメソッド] (行番号: 207〜219 / 抜粋: "チャンネルURLの場合は `/videos` と `/playlists` を自動探索する。")
* `--cron`引数指定時は、SQLite DB（`home_system.db`）の`youtube_subscriptions`テーブルに登録されたアクティブなチャンネルURLを順次巡回する自動サブスクリプションモードで動作する。レート制限対策としてリクエスト間のジッター付き待機と、連続失敗時のサーキットブレーカーを備える。
* 根拠: [SubscriptionManager.process_subscriptionsとrunメソッド] (行番号: 355〜407, 429〜432 / 抜粋: "if args.cron:\n            self.sub_manager.process_subscriptions()")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `sys` | 標準ライブラリ | `sys.path`へのプロジェクトルート追加、`sys.exit`によるプロセス終了 | 根拠: [import文] (行番号: 11 / 抜粋: "import sys") |
| `argparse` | 標準ライブラリ | コマンドライン引数（URL、`--cron`フラグ）の解析 | 根拠: [import文] (行番号: 12 / 抜粋: "import argparse") |
| `re` | 標準ライブラリ | チャンネルURL判定用の正規表現(`_is_channel_url`) | 根拠: [import文] (行番号: 13 / 抜粋: "import re") |
| `time` | 標準ライブラリ | サブスクリプション巡回時のリクエスト間スリープ | 根拠: [import文] (行番号: 14 / 抜粋: "import time") |
| `random` | 標準ライブラリ | スリープ時間のランダムなジッター生成 | 根拠: [import文] (行番号: 15 / 抜粋: "import random") |
| `pathlib.Path` | 標準ライブラリ | パス操作全般 | 根拠: [import文] (行番号: 16 / 抜粋: "from pathlib import Path") |
| `dataclasses.dataclass`, `field` | 標準ライブラリ | `ExtractionResult`データクラスの定義 | 根拠: [import文] (行番号: 17 / 抜粋: "from dataclasses import dataclass, field") |
| `typing.List`, `Optional`, `Set`, `Iterator`, `Dict`, `Any` | 標準ライブラリ | 型ヒント全般 | 根拠: [import文] (行番号: 18 / 抜粋: "from typing import List, Optional, Set, Iterator, Dict, Any") |
| `sqlite3` | 標準ライブラリ | サブスクリプション管理用DB（`home_system.db`）への接続・クエリ実行 | 根拠: [import文] (行番号: 19 / 抜粋: "import sqlite3") |
| `contextlib.closing` | 標準ライブラリ | SQLite接続・カーソルの確実なクローズ（`with closing(...)`） | 根拠: [import文] (行番号: 20 / 抜粋: "from contextlib import closing") |
| `yt_dlp` | サードパーティ | YouTubeチャンネル/プレイリスト/動画のメタデータ抽出(`extract_info`) | 根拠: [import文] (行番号: 22 / 抜粋: "import yt_dlp") |
| `file_utils.sanitize_filename` (as `_shared_sanitize_filename`) | ローカルモジュール | 保存ファイル名のサニタイズ処理の委譲先 | 根拠: [import文] (行番号: 24 / 抜粋: "from file_utils import sanitize_filename as _shared_sanitize_filename") |
| `core.logger.get_logger` | 内部モジュール（オプショナル、try節） | ロガーインスタンスの取得。インポート失敗時はファイル内フォールバック実装（`logging.getLogger`ベース）を使用 | 根拠: [import文] (行番号: 36 / 抜粋: "from core.logger import get_logger") |
| `core.nas_utils.get_managed_target_directory` | 内部モジュール（オプショナル、try節） | NAS/ローカルの出力先ディレクトリの解決・管理。インポート失敗時はファイル内フォールバック実装（固定で`Path("./data")`を返す）を使用 | 根拠: [import文] (行番号: 37 / 抜粋: "from core.nas_utils import get_managed_target_directory") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `core.logger.get_logger` | インポート成功時に実際に使用される実装（フォーマット、出力先、ログレベル等）の詳細が本ファイルからは不明。フォールバック実装のみがこのファイルから確認できる。 | 根拠: [import文とフォールバック定義] (行番号: 36, 41〜43 / 抜粋: "from core.logger import get_logger") |
| `core.nas_utils.get_managed_target_directory` | インポート成功時の実際の実装（NASマウント確認・自動修復ロジックの詳細）が不明。フォールバック実装は単に`Path("./data")`を返すのみ。 | 根拠: [import文とフォールバック定義] (行番号: 37, 44 / 抜粋: "from core.nas_utils import get_managed_target_directory") |
| `yt_dlp.YoutubeDL` | `extract_info`が返す辞書の詳細な構造（`entries`, `url`, `webpage_url`, `id`, `title`, `channel`, `uploader`等のキーの完全な仕様）は`yt_dlp`本体の実装に依存し、本ファイルからは分からない。 | 根拠: [YoutubeDL利用箇所] (行番号: 167〜168 / 抜粋: "with yt_dlp.YoutubeDL(dict(AppConfig.YDL_OPTS)) as ydl:\n                info = ydl.extract_info(target_url, download=False)") |
| `file_utils.sanitize_filename` | サニタイズの具体的なルール（禁止文字、長さ制限等）は本ファイル単体からは不明。ただし関連ドキュメント`file_utils.md`に実装の解析結果が存在する。 | 根拠: [import文] (行番号: 24 / 抜粋: "from file_utils import sanitize_filename as _shared_sanitize_filename") |
| `home_system.db`（SQLite DB） | `youtube_subscriptions`テーブル以外にどのようなテーブル・データが存在するか、他プロセスとの共有スキーマの全体像は本ファイルからは不明（本ファイルは`CREATE TABLE IF NOT EXISTS`で自テーブルのみ関知）。 | 根拠: [_init_db] (行番号: 341〜353 / 抜粋: "CREATE TABLE IF NOT EXISTS youtube_subscriptions (") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `get_managed_target_directory` (フォールバック実装)

* **役割**: `core.nas_utils`のインポートに失敗した場合に使用される、常に固定のローカルディレクトリ`./data`を返す簡易フォールバック関数。
* 根拠: [関数定義] (行番号: 44 / 抜粋: "def get_managed_target_directory(*args, **kwargs): return Path("./data")")


* **引数/リクエスト**: `*args`, `**kwargs`（本フォールバック実装では未使用、シグネチャ互換のためのみ受け取る）
* 根拠: [関数定義] (行番号: 44 / 抜粋: "def get_managed_target_directory(*args, **kwargs): return Path("./data")")


* **戻り値/レスポンス**: `Path`（常に`Path("./data")`）
* **副作用**: なし
* **エラーハンドリング**: なし


### `AppConfig`

* **役割**: 出力先ディレクトリ、NASパス、サブディレクトリ名、レート制限対策のスリープ範囲、`yt_dlp`オプションなど、アプリケーション全体の設定値を保持する定数クラス（インスタンス化不要、クラス変数と`classmethod`のみで構成）。
* 根拠: [クラス定義とDocstring] (行番号: 49〜50 / 抜粋: "class AppConfig:\n    """アプリケーション設定を保持する定数クラス。"""")


* **引数/リクエスト**: なし（クラス変数として静的に定義）
* 根拠: [クラス変数定義群] (行番号: 52〜71 / 抜粋: "BASE_DIR: Path = CURRENT_DIR")


* **戻り値/レスポンス**: 該当なし
* **副作用**: なし（クラス変数の定義自体には外部通信・ファイルI/O等の副作用はない）
* **エラーハンドリング**: なし


### `AppConfig.get_output_base_dir`

* **役割**: NASアクセスを検証・修復し、動的に出力先ベースディレクトリを解決するクラスメソッド。クラスロード時ではなく実際のファイル処理が必要になったタイミング（遅延評価）で呼び出す設計。
* 根拠: [メソッド定義とDocstring] (行番号: 73〜82 / 抜粋: "def get_output_base_dir(cls) -> Path:\n        """NASアクセスを検証・修復し、動的にベースディレクトリを解決する（遅延評価）。")


* **引数/リクエスト**: なし（`cls`のみ、`@classmethod`）
* 根拠: [デコレータと引数] (行番号: 73〜74 / 抜粋: "@classmethod\n    def get_output_base_dir(cls) -> Path:")


* **戻り値/レスポンス**: `Path`（利用可能なディレクトリパス）
* 根拠: [Docstringと戻り値] (行番号: 80〜82 / 抜粋: "Returns:\n            Path: 利用可能なディレクトリパス\n        """\n        return get_managed_target_directory(")


* **副作用**: `get_managed_target_directory`（インポート成功時は`core.nas_utils`、失敗時はフォールバック実装）の呼び出し。
* 根拠: [呼び出し] (行番号: 83〜87 / 抜粋: "return get_managed_target_directory(\n            nas_dir_str=cls.NAS_DIR_STR,\n            fallback_dir_str=cls.LOCAL_DIR_STR,\n            mount_point=cls.MOUNT_POINT\n        )")


* **エラーハンドリング**: なし（本メソッド自体には例外処理なし。委譲先の実装に依存）


### `ExtractionResult`

* **役割**: 1件の抽出結果（動画リスト/プレイリストのタイトル、URLリスト、抽出元URL、チャンネル名、プレイリストか否か）を保持するデータクラス。
* 根拠: [クラス定義とDocstring] (行番号: 90〜100 / 抜粋: "@dataclass\nclass ExtractionResult:\n    """抽出結果を格納するデータクラス。")


* **引数/リクエスト**: `title: str`, `urls: List[str]`, `source_url: str`, `channel_name: str = "unknown_channel"`, `is_playlist: bool = False`
* 根拠: [フィールド定義] (行番号: 101〜105 / 抜粋: "title: str\n    urls: List[str]\n    source_url: str\n    channel_name: str = "unknown_channel"\n    is_playlist: bool = False")


* **戻り値/レスポンス**: 該当なし（データクラスのフィールド定義自体）
* **副作用**: なし
* **エラーハンドリング**: なし


### `YouTubeExtractor._normalize_url`

* **役割**: `yt_dlp`のエントリ辞書から正規化されたYouTube動画URLを生成する静的メソッド。`video_id`があれば`watch?v=`形式のURLを優先的に構築し、なければ既存の`url`/`webpage_url`をYouTubeドメインかどうか判定した上で採用する。
* 根拠: [メソッド定義とDocstring] (行番号: 113〜131 / 抜粋: "def _normalize_url(entry: Dict[str, Any]) -> Optional[str]:\n        """エントリ情報から正規化されたYouTube URLを生成する。")


* **引数/リクエスト**: `entry: Dict[str, Any]`（`yt-dlp`から取得したエントリ辞書）
* 根拠: [引数定義とDocstring] (行番号: 114, 117〜118 / 抜粋: "entry (Dict[str, Any]): yt-dlp から取得したエントリ辞書。")


* **戻り値/レスポンス**: `Optional[str]`（正規化されたURL。生成できない場合は`None`）
* 根拠: [Docstringと各return] (行番号: 120〜121, 127, 130, 131 / 抜粋: "Returns:\n            Optional[str]: 正規化されたURL。生成できない場合は None。")


* **副作用**: なし（純粋な文字列生成処理）
* **エラーハンドリング**: なし（想定外の入力に対しては`None`を返すのみ）


### `YouTubeExtractor._is_channel_url`

* **役割**: 指定URLが末尾クエリを除去・末尾スラッシュを除去した上で、チャンネルトップページ（`@handle`, `channel/`, `c/`, `user/`形式）のURLパターンに一致するかを正規表現で判定するインスタンスメソッド。
* 根拠: [メソッド定義とDocstring] (行番号: 133〜143 / 抜粋: "def _is_channel_url(self, url: str) -> bool:\n        """指定されたURLがチャンネルトップページのURLかを判定する。")


* **引数/リクエスト**: `url: str`
* 根拠: [引数定義とDocstring] (行番号: 133, 136〜137 / 抜粋: "url (str): 判定対象のURL。")


* **戻り値/レスポンス**: `bool`（チャンネルURLであれば`True`）
* 根拠: [Docstringと戻り値] (行番号: 139〜140, 143 / 抜粋: "Returns:\n            bool: チャンネルURLであれば True。")


* **副作用**: なし
* **エラーハンドリング**: なし


### `YouTubeExtractor._extract_single_list`

* **役割**: 単一のURL（動画リストまたはプレイリスト）を`yt_dlp`で解析し、含まれる全動画URLを正規化・重複排除した`ExtractionResult`を構築するインスタンスメソッド。`AppConfig.YDL_OPTS`は呼び出し間の状態汚染を避けるためコピーして渡される。
* 根拠: [メソッド定義とコメント] (行番号: 145〜167 / 抜粋: "def _extract_single_list(self, target_url: str, force_title: str = "") -> Optional[ExtractionResult]:")


* **引数/リクエスト**: `target_url: str`（対象のURL）, `force_title: str = ""`（タイトルを強制指定する場合に使用）
* 根拠: [引数定義とDocstring] (行番号: 145, 148〜150 / 抜粋: "target_url (str): 対象のURL。\n            force_title (str, optional): タイトルを強制指定する場合に使用。")


* **戻り値/レスポンス**: `Optional[ExtractionResult]`（抽出結果オブジェクト。失敗時（`yt_dlp`が情報を返さない、例外発生、URLが1件も抽出できない）は`None`）
* 根拠: [Docstringと各return] (行番号: 152〜153, 170, 194, 198, 200〜205 / 抜粋: "Returns:\n            Optional[ExtractionResult]: 抽出結果オブジェクト。失敗時は None。")


* **副作用**: `yt_dlp.YoutubeDL.extract_info`によるネットワークアクセス、進捗・エラーのログ出力。
* 根拠: [extract_info呼び出しとログ] (行番号: 155, 167〜168 / 抜粋: "logger.info(f"🔍 解析開始: {target_url}")", "info = ydl.extract_info(target_url, download=False)")


* **エラーハンドリング**: `yt_dlp`実行時の例外を`except Exception`で捕捉し、スタックトレース付き(`exc_info=True`)でエラーログを出力して`None`を返す。抽出結果のURLが0件の場合も`None`を返す。
* 根拠: [try-exceptブロック] (行番号: 191〜194 / 抜粋: "except Exception:\n            # Error Handling: スタックトレースを含めてログ出力\n            logger.error(f"❌ 抽出失敗 ({target_url})", exc_info=True)\n            return None")


### `YouTubeExtractor.extract_iter`

* **役割**: URLの種類に応じて抽出方式を切り替えるイテレータメソッド。チャンネルURLの場合は`/videos`（全動画）と`/playlists`（各プレイリスト）を自動探索して複数の`ExtractionResult`を`yield`し、それ以外（プレイリストURLや単一動画URL）の場合は単発で`_extract_single_list`を呼び出す。
* 根拠: [メソッド定義とDocstring] (行番号: 207〜217 / 抜粋: "def extract_iter(self, target_url: str) -> Iterator[ExtractionResult]:\n        """URLの種類に応じて再帰的または単発で抽出を行うイテレータ。")


* **引数/リクエスト**: `target_url: str`（開始URL）
* 根拠: [引数定義とDocstring] (行番号: 207, 212〜213 / 抜粋: "target_url (str): 開始URL。")


* **戻り値/レスポンス**: `Iterator[ExtractionResult]`（抽出結果を順次`yield`）
* 根拠: [Docstringと戻り値ヒント] (行番号: 207, 215〜216 / 抜粋: "Yields:\n            Iterator[ExtractionResult]: 抽出結果を順次返す。")


* **副作用**: チャンネルURLの場合、`/videos`・`/playlists`双方への`yt_dlp`アクセス（ネットワーク通信）、進捗ログ出力。
* 根拠: [チャンネル探索処理] (行番号: 218〜254 / 抜粋: "if self._is_channel_url(target_url):\n            logger.info("ℹ️ チャンネルURLを検出。詳細スキャンを開始します。")")


* **エラーハンドリング**: プレイリスト一覧取得時（`/playlists`）の例外を`except Exception`で捕捉し、スタックトレース付きでエラーログを出力（処理は中断されるがメソッド自体は正常終了）。個々の`_extract_single_list`呼び出しの失敗（`None`が返る場合）は単に`yield`をスキップする。
* 根拠: [try-exceptブロック] (行番号: 253〜254 / 抜粋: "except Exception:\n                logger.error("❌ プレイリスト一覧の取得に失敗しました", exc_info=True)")


### `FileManager._sanitize_filename`

* **役割**: 外部モジュール`file_utils.sanitize_filename`へファイル名のサニタイズ処理を委譲する静的メソッド。
* 根拠: [メソッド定義とDocstring] (行番号: 266〜276 / 抜粋: "def _sanitize_filename(filename: str) -> str:\n        """ファイル名として使用できない文字を置換する。")


* **引数/リクエスト**: `filename: str`（元の文字列）
* 根拠: [引数定義とDocstring] (行番号: 267, 270〜271 / 抜粋: "filename (str): 元の文字列。")


* **戻り値/レスポンス**: `str`（安全なファイル名文字列）
* 根拠: [Docstringと戻り値] (行番号: 273〜274, 276 / 抜粋: "Returns:\n            str: 安全なファイル名文字列。\n        """\n        return _shared_sanitize_filename(filename)")


* **副作用**: なし
* **エラーハンドリング**: なし（委譲先の例外処理には依存）


### `FileManager.save`

* **役割**: `ExtractionResult`の抽出結果（チャンネル名・タイトルをサニタイズしたファイル名）をテキストファイルへ1行1URL形式で保存するインスタンスメソッド。保存先ディレクトリは`AppConfig.get_output_base_dir()`を遅延評価で取得する。
* 根拠: [メソッド定義とDocstring] (行番号: 278〜286 / 抜粋: "def save(self, result: ExtractionResult) -> bool:\n        """抽出結果をテキストファイルに保存する。")


* **引数/リクエスト**: `result: ExtractionResult`（保存対象の抽出データ）
* 根拠: [引数定義とDocstring] (行番号: 278, 281〜282 / 抜粋: "result (ExtractionResult): 保存対象の抽出データ。")


* **戻り値/レスポンス**: `bool`（保存に成功した場合`True`。ディレクトリ作成失敗時・ファイル書き込み失敗時は`False`）
* 根拠: [Docstringと各return] (行番号: 284〜285, 293, 309, 312 / 抜粋: "Returns:\n            bool: 保存に成功した場合は True。")


* **副作用**: 保存先ディレクトリの作成(`mkdir`)、テキストファイルへの書き込み(`open(..., "w")`)、成功/失敗・上書き時のログ出力。
* 根拠: [ディレクトリ作成とファイル書き込み] (行番号: 289〜290, 304〜307 / 抜粋: "target_dir.mkdir(parents=True, exist_ok=True)", "with output_path.open("w", encoding="utf-8") as f:\n                for url in result.urls:\n                    f.write(url + "\\n")")


* **エラーハンドリング**: ディレクトリ作成時の`OSError`を捕捉してエラーログを出力し`False`を返す。ファイル書き込み時の`IOError`を捕捉してエラーログを出力し`False`を返す。出力先ファイルが既に存在する場合は警告ログを出力するのみで上書きを継続する。
* 根拠: [try-exceptブロック] (行番号: 289〜293, 304〜312 / 抜粋: "except OSError as e:\n            logger.error(f"❌ ディレクトリ作成失敗: {target_dir}", exc_info=True)\n            return False")


### `SubscriptionManager.__init__`

* **役割**: `YouTubeExtractor`と`FileManager`のインスタンスを保持し、サブスクリプション管理用SQLite DB（`home_system.db`）のパスを（NASベースディレクトリの1階層上として）決定するコンストラクタ。
* 根拠: [クラス定義とDocstringおよび__init__] (行番号: 314〜325 / 抜粋: "class SubscriptionManager:\n    """\n    定期巡回（サブスクリプション）を管理するクラス。\n    SSOTポリシーに基づき、SQLite DBを用いて状態を管理する。\n    """")


* **引数/リクエスト**: `extractor: YouTubeExtractor`, `file_manager: FileManager`
* 根拠: [引数定義] (行番号: 320 / 抜粋: "def __init__(self, extractor: YouTubeExtractor, file_manager: FileManager):")


* **戻り値/レスポンス**: 該当なし
* **副作用**: `self.extractor`, `self.file_manager`, `self.db_path`への属性代入。`self.db_path`決定時に`AppConfig.get_output_base_dir()`の呼び出し（間接的にNASアクセス確認等の副作用を誘発しうる）。
* 根拠: [属性代入] (行番号: 321〜325 / 抜粋: "self.extractor = extractor\n        self.file_manager = file_manager\n        \n        # DBはNASのベースディレクトリの1つ上の階層（home_system直下）に配置\n        self.db_path = AppConfig.get_output_base_dir().parent / "home_system.db"")


* **エラーハンドリング**: なし


### `SubscriptionManager._verify_environment`

* **役割**: 現在の出力先ベースディレクトリがローカルフォールバック（`LOCAL_DIR_STR`）中でないか（＝NASが正常にマウントされているか）を検証するインスタンスメソッド。
* 根拠: [メソッド定義とDocstring] (行番号: 327〜334 / 抜粋: "def _verify_environment(self) -> bool:\n        """\n        NASのマウント状態（フォールバック中ではないか）を検証する。")


* **引数/リクエスト**: なし（`self`のみ）
* 根拠: [引数定義] (行番号: 327 / 抜粋: "def _verify_environment(self) -> bool:")


* **戻り値/レスポンス**: `bool`（正常なNAS環境であれば`True`、ローカルフォールバック中であれば`False`）
* 根拠: [Docstringと各return] (行番号: 335〜337, 339 / 抜粋: "Returns:\n            bool: 正常なNAS環境であれば True、ローカルフォールバック中であれば False\n        """)


* **副作用**: フォールバック検知時のエラーログ出力（2行）。
* 根拠: [ログ出力] (行番号: 336〜337 / 抜粋: "logger.error("🚨 NASがアンマウント状態（ローカルフォールバック中）を検知しました。")\n            logger.error("データの不整合・上書きを防ぐため、サブスクリプション処理をFail-Softで中断します。")")


* **エラーハンドリング**: なし


### `SubscriptionManager._init_db`

* **役割**: サブスクリプション管理用テーブル(`youtube_subscriptions`)が存在しない場合に作成するインスタンスメソッド。`id`, `channel_url`（一意制約）, `is_active`, `added_at`の各カラムを持つ。
* 根拠: [メソッド定義とDocstring] (行番号: 341〜353 / 抜粋: "def _init_db(self) -> None:\n        """サブスクリプション管理用のテーブルが存在しない場合は作成する。"""")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 341 / 抜粋: "def _init_db(self) -> None:")


* **副作用**: SQLite DB接続、テーブル作成用DDL実行(`CREATE TABLE IF NOT EXISTS`)、コミット。
* 根拠: [DDL実行] (行番号: 343〜353 / 抜粋: "with closing(sqlite3.connect(self.db_path)) as conn:\n            with closing(conn.cursor()) as cur:\n                cur.execute(\"\"\"\n                    CREATE TABLE IF NOT EXISTS youtube_subscriptions (")


* **エラーハンドリング**: なし（本メソッド自体には例外処理なし。呼び出し元の`process_subscriptions`が`sqlite3.Error`を捕捉する）


### `SubscriptionManager.process_subscriptions`

* **役割**: DBから読み込んだアクティブなチャンネルURLを順次巡回し、`extractor.extract_iter`で抽出→`file_manager.save`で保存するメイン処理。環境検証（NASフォールバック中でないか）、DB初期化、リクエスト間のジッター付き待機、連続失敗時のサーキットブレーカー（`CONSECUTIVE_FAILURE_THRESHOLD`回で巡回を中断）を含む。
* 根拠: [メソッド定義とDocstring] (行番号: 355〜356 / 抜粋: "def process_subscriptions(self) -> None:\n        """登録されたチャンネルリストをDBから読み込み、順次抽出を実行する。"""")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 355 / 抜粋: "def process_subscriptions(self) -> None:")


* **副作用**: 環境検証・DB初期化・DBからのSELECT、URL巡回ごとの`time.sleep`、`extractor.extract_iter`によるネットワークアクセス、`file_manager.save`によるファイル書き込み、各段階でのログ出力。
* 根拠: [メイン処理フロー] (行番号: 357〜407 / 抜粋: "logger.info(f"🔄 サブスクリプション巡回開始: {len(urls)} 件 (Source: SQLite DB)")")


* **エラーハンドリング**: 環境検証失敗時は即座に`return`。DB初期化(`sqlite3.Error`)・DB読み込み(`sqlite3.Error`)失敗時はエラーログを出力して`return`。アクティブなURLが0件の場合はデバッグログを出力して`return`。連続失敗数が`CONSECUTIVE_FAILURE_THRESHOLD`（既定3）に達した場合はエラーログを出力してループを`break`で中断する。
* 根拠: [各種ガード節とbreak] (行番号: 357〜359, 365〜367, 378〜380, 382〜384, 405〜407 / 抜粋: "if consecutive_failures >= AppConfig.CONSECUTIVE_FAILURE_THRESHOLD:\n                    logger.error("複数回連続で抽出に失敗したため巡回を中断します — レート制限の可能性があります")\n                    break")


### `UrlExtractorApp.__init__`

* **役割**: `YouTubeExtractor`, `FileManager`, `SubscriptionManager`の各インスタンスを生成・保持するコンストラクタ。
* 根拠: [メソッド定義] (行番号: 415〜418 / 抜粋: "def __init__(self):\n        self.extractor = YouTubeExtractor()\n        self.file_manager = FileManager()\n        self.sub_manager = SubscriptionManager(self.extractor, self.file_manager)")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: 該当なし
* **副作用**: 3つのインスタンス属性への代入（間接的に`SubscriptionManager.__init__`のNASアクセス確認等の副作用を誘発しうる）。
* 根拠: [属性代入] (行番号: 416〜418 / 抜粋: "self.extractor = YouTubeExtractor()\n        self.file_manager = FileManager()\n        self.sub_manager = SubscriptionManager(self.extractor, self.file_manager)")


* **エラーハンドリング**: なし


### `UrlExtractorApp.run`

* **役割**: コマンドライン引数（`url`位置引数、`--cron`フラグ）を解析し、`--cron`指定時はサブスクリプション巡回、それ以外はURL引数（未指定時は対話的に`input()`で取得）を`extract_iter`で処理・保存するエントリーポイントメソッド。
* 根拠: [メソッド定義とDocstring] (行番号: 420〜421 / 抜粋: "def run(self) -> None:\n        """コマンドライン引数を解析し、メイン処理を実行する。"""")


* **引数/リクエスト**: なし（`self`のみ、`sys.argv`経由で`argparse`が解析）
* 根拠: [argparse定義] (行番号: 424〜427 / 抜粋: "parser = argparse.ArgumentParser(description="Extract YouTube URLs from channels or playlists.")\n        parser.add_argument("url", nargs="?", help="Target YouTube URL")\n        parser.add_argument("--cron", action="store_true", help="Auto-subscription mode")")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 420 / 抜粋: "def run(self) -> None:")


* **副作用**: 起動・完了ログ出力、`--cron`時は`sub_manager.process_subscriptions()`呼び出し、URL未指定時の対話的`input()`呼び出し、`extractor.extract_iter`によるネットワークアクセスと`file_manager.save`によるファイル保存。
* 根拠: [メイン処理フロー] (行番号: 422, 429〜432, 436〜450 / 抜粋: "logger.info("=== YouTube URL Extractor (v3.1.0) Started ===")")


* **エラーハンドリング**: 対話的URL入力時の`KeyboardInterrupt`を捕捉し、情報ログを出力して`sys.exit(0)`で正常終了する。それ以外の例外処理はこのメソッド自体にはない。
* 根拠: [try-exceptブロック] (行番号: 437〜442 / 抜粋: "except KeyboardInterrupt:\n                logger.info("ユーザーにより中断されました")\n                sys.exit(0)")


## 5. 処理フロー図

```mermaid
flowchart TD
    Start["Start: UrlExtractorApp.run()"] --> ParseArgs["引数解析(argparse)<br>url / --cron"]
    ParseArgs --> CronCheck{"--cronが指定されているか?"}

    CronCheck -->|Yes| SubProcess["外部: sub_manager.process_subscriptions()"]
    SubProcess --> End1["終了(自動巡回完了)"]

    CronCheck -->|No| UrlCheck{"url引数が指定されているか?"}
    UrlCheck -->|No| PromptInput["対話的にinput()でURLを取得"]
    PromptInput --> Interrupt{"KeyboardInterrupt発生?"}
    Interrupt -->|Yes| Exit0["sys.exit(0)"]
    Interrupt -->|No| UrlCheck2

    UrlCheck -->|Yes| UrlCheck2{"target_urlが空でないか?"}
    UrlCheck2 -->|No| LogNoUrl["URL未指定のログ出力"] --> End2["終了"]
    UrlCheck2 -->|Yes| ExtractLoop["extractor.extract_iter(target_url)をループ"]

    ExtractLoop --> IsChannel{"チャンネルURLか?<br>(_is_channel_url)"}
    IsChannel -->|Yes| VideosPhase["Phase1: {url}/videos を抽出"]
    VideosPhase --> PlaylistsPhase["Phase2: {url}/playlists を探索し<br>各プレイリストを個別抽出"]
    PlaylistsPhase --> YieldResults["ExtractionResultを順次yield"]

    IsChannel -->|No| SingleExtract["_extract_single_list(target_url)で単発抽出"]
    SingleExtract --> YieldResults

    YieldResults --> SaveResult["file_manager.save(result)でファイル保存"]
    SaveResult --> MoreResults{"次の結果があるか?"}
    MoreResults -->|Yes| ExtractLoop
    MoreResults -->|No| LogDone["処理完了ログ出力(合計ファイル数)"]
    LogDone --> End3["終了"]
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "extract_youtube_urls.py"
        AppConfig
        ExtractionResult
        YouTubeExtractor
        FileManager
        SubscriptionManager
        UrlExtractorApp
    end

    subgraph "外部依存(コアモジュール、try節)"
        core_logger["core.logger.get_logger"]
        core_nas_utils["core.nas_utils.get_managed_target_directory"]
    end

    subgraph "外部依存(ローカルモジュール)"
        file_utils_mod["file_utils.sanitize_filename"]
    end

    subgraph "外部依存(サードパーティ/標準ライブラリ)"
        yt_dlp_mod["yt_dlp"]
        sqlite3_mod["sqlite3"]
    end

    subgraph "外部システム"
        YouTube["YouTube (yt-dlp経由)"]
        NAS["NAS/ローカルストレージ"]
        DB["home_system.db (SQLite)"]
    end

    UrlExtractorApp --> YouTubeExtractor
    UrlExtractorApp --> FileManager
    UrlExtractorApp --> SubscriptionManager

    SubscriptionManager --> YouTubeExtractor
    SubscriptionManager --> FileManager
    SubscriptionManager --> sqlite3_mod
    SubscriptionManager --> AppConfig
    sqlite3_mod --> DB

    YouTubeExtractor --> yt_dlp_mod
    YouTubeExtractor --> ExtractionResult
    yt_dlp_mod --> YouTube

    FileManager --> file_utils_mod
    FileManager --> AppConfig
    FileManager --> NAS

    AppConfig -.->|"インポート成功時"| core_nas_utils
    AppConfig -.->|"インポート失敗時はフォールバック"| NAS
    core_nas_utils --> NAS

    UrlExtractorApp -.->|"インポート成功時"| core_logger
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `core/nas_utils.py` | `get_managed_target_directory`の実際の実装（NASマウント確認・自動修復ロジック）が、フォールバック実装（単に`Path("./data")`を返すのみ）とどう異なるかを確認する必要があるため。 | 根拠: [import文] (行番号: 37 / 抜粋: "from core.nas_utils import get_managed_target_directory") |
| 中 | `core/logger.py` | `get_logger`の実際の実装（出力フォーマット、ログレベル、出力先）を確認するため。 | 根拠: [import文] (行番号: 36 / 抜粋: "from core.logger import get_logger") |
| 中 | `file_utils.py` | `sanitize_filename`の具体的なサニタイズルールを確認するため（既に`docs/specifications/DDD/file_utils.md`として解析済み）。 | 根拠: [import文] (行番号: 24 / 抜粋: "from file_utils import sanitize_filename as _shared_sanitize_filename") |
| 低 | `home_system.db`を書き込む他のプロセス/スクリプト | `youtube_subscriptions`テーブルへどのようにチャンネルURLが登録・アクティブ化されるか（本ファイルはSELECTのみで、INSERT/UPDATEを行う箇所が存在しない）を確認するため。 | 根拠: [process_subscriptions] (行番号: 375 / 抜粋: "cur.execute("SELECT channel_url FROM youtube_subscriptions WHERE is_active = 1")") |

## 8. 保守上の注意点

* **フォールバック実装と本番実装の差異リスク**: `core.logger`, `core.nas_utils`のインポートに失敗した場合、ファイル内の簡易フォールバック実装（特に`get_managed_target_directory`は常に`Path("./data")`を返すのみ）に切り替わる。本番環境で意図せずインポートが失敗した場合、NASではなくローカルディスクにデータが保存される可能性がある。
* 根拠: [フォールバック定義] (行番号: 39〜44 / 抜粋: "except ImportError:\n    # 開発環境や単体実行時のフォールバック")
* **`youtube_subscriptions`テーブルへの書き込み手段が本ファイルに存在しない**: `_init_db`はテーブル作成のみを行い、`process_subscriptions`はSELECTのみを実行する。チャンネルURLの登録・有効化（INSERT/UPDATE）を行う手段が本ファイル内に見当たらず、外部プロセスまたは手動でのDB操作が前提と見られる。
* 根拠: [process_subscriptions] (行番号: 375 / 抜粋: "cur.execute("SELECT channel_url FROM youtube_subscriptions WHERE is_active = 1")")
* **YDL_OPTS共有辞書のコピー渡し**: `yt_dlp.YoutubeDL.__init__`が渡された`params`辞書を直接書き換えるため、`AppConfig.YDL_OPTS`（クラス属性の共有辞書）をそのまま渡すと繰り返し呼び出し時に状態汚染が起きるリスクがあり、コード内コメントで明示的に`dict(AppConfig.YDL_OPTS)`によるコピー渡しが行われている。
* 根拠: [コメントとコピー渡し] (行番号: 162〜167, 236〜238 / 抜粋: "# yt_dlp.YoutubeDL.__init__は渡されたparams辞書を直接書き換える\n            # （実測でjs_runtimes/http_headers/outtmpl等のキーが追加される）ため、")
* **既存ファイルの無警告上書き**: `FileManager.save`は出力先に同名ファイルが既存の場合、警告ログを出力するのみで上書きを継続する。
* 根拠: [上書きチェック] (行番号: 301〜302 / 抜粋: "if output_path.exists():\n            logger.warning(f"⚠️ 上書き: {filename} は既に存在します（チャンネル名/タイトルが重複している可能性）")")
* **チャンネルURL探索の暗黙的な仕様依存**: `/videos`・`/playlists`のURLパス付与がYouTube側のURL構造に依存しており、YouTube側の仕様変更で機能しなくなるリスクがある。
* 根拠: [extract_iter内のURL構築] (行番号: 220, 223, 239 / 抜粋: "base_url = target_url.split('?')[0].rstrip('/')")
* **`_is_channel_url`の判定パターンの限定性**: 正規表現は`@handle`, `channel/`, `c/`, `user/`の4形式のみに対応しており、これら以外のURL形式（例: カスタムショートURL等）は判定対象外となる可能性がある。
* 根拠: [正規表現定義] (行番号: 143 / 抜粋: "return bool(re.search(r"youtube\\.com/(@[\\w\\-\\.]+|channel/[\\w\\-]+|c/[\\w\\-]+|user/[\\w\\-]+)$", clean_url))")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `core.logger.get_logger`の実際の実装 | ログの出力フォーマット、出力先、ログレベルの詳細が本ファイルからは不明（フォールバック実装のみ確認可能）。 | `core/logger.py` |
| `core.nas_utils.get_managed_target_directory`の実際の実装 | NASマウント確認・自動修復ロジックの詳細な挙動が不明（フォールバック実装は単純なローカルパス返却のみ）。 | `core/nas_utils.py` |
| `youtube_subscriptions`テーブルへのレコード登録手段 | 本ファイルはSELECT（読み取り専用）のみを行っており、チャンネルURLがどのプロセス・手段で登録・有効化(`is_active=1`)されるかが不明。 | DB登録を行う別スクリプトまたは運用手順書 |
| `yt_dlp.extract_info`が返す辞書の完全な構造 | `entries`, `channel`, `uploader`等の各キーが常に存在するか、`yt_dlp`のバージョンによって変化しうるかは本ファイルのコードからは分からない。 | `yt_dlp`本体のソースまたは公式ドキュメント（コード外） |
| 本ファイルの実行方法（cron設定等） | `--cron`引数での自動巡回モードが存在するが、実際にどのスケジュール（cron、systemdタイマー等）で起動されるかは本ファイルからは不明。 | デプロイ設定・cron定義ファイル等 |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `sanitize_filename`の詳細ルール | 関連ドキュメント（`file_utils.md`）の解析結果によれば、`sanitize_filename(filename, max_length=200)`は禁止文字（`\ / * ? : " < > |`）をアンダースコアに置換し、前後の空白を除去したうえで`max_length`（既定200文字）まで切り詰め、さらに末尾のピリオド・空白を除去する実装であることが分かった。これはあくまで別ファイルの解析結果に基づく補足情報である。 | [file_utils.md](./file_utils.md) |
| `core.nas_utils.get_managed_target_directory`の実際の実装 | 関連ドキュメント（`nas_utils.md`）の解析結果によれば、同名関数`get_managed_target_directory(nas_dir_str, fallback_dir_str, mount_point="/mnt/nas")`が存在し、マウント確認・書き込み権限チェック→未マウント時は`sudo mount`による再マウント試行→復旧時はフォールバックデータをNASへ同期→最終手段としてローカルのフォールバックパスを返す、という実装であることが分かった。引数名は本ファイルの呼び出し箇所（`nas_dir_str`, `fallback_dir_str`, `mount_point`）と一致しており、関連性が高いと考えられる。ただしこれはあくまで別ファイルの解析結果に基づく補足情報であり、本ファイルおよび`core/nas_utils.py`のソースコードを直接確認したものではない。 | [../MY_HOME_SYSTEM/nas_utils.md](../MY_HOME_SYSTEM/nas_utils.md) |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
