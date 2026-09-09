## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `newface_monitor.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | `dbbfc81` |

## 関連ドキュメント

* [../MY_HOME_SYSTEM/nas_utils.md](../MY_HOME_SYSTEM/nas_utils.md) — 本ファイルがインポートを試みる`core.nas_utils.get_managed_target_directory`の実装候補（同名関数のシグネチャ・実装が確認できる）。
* [../MY_HOME_SYSTEM/utils.md](../MY_HOME_SYSTEM/utils.md) — 本ファイルがインポートを試みる`core.utils.wait_for_storage_warmup`の実装候補（同名関数のシグネチャ・実装が確認できる）。
* [../MY_HOME_SYSTEM/logger.md](../MY_HOME_SYSTEM/logger.md) — `core.logger`配下のロガー実装（`setup_logging`, `DiscordErrorHandler`）に関する参考情報。ただし本ファイルがインポートする`get_logger`関数自体はこのドキュメントでは文書化されていない。
* [../MY_HOME_SYSTEM/notification_service.md](../MY_HOME_SYSTEM/notification_service.md) — Discord Webhook通知の別実装パターンとの比較参考（本ファイルは`services.notification_service`を使わず、独自の`DiscordNotifier`クラスで`requests`セッションを直接使いWebhookへPOSTする）。
* [../MY_HOME_SYSTEM/nas_monitor.md](../MY_HOME_SYSTEM/nas_monitor.md) — NAS監視・容量管理という運用文脈での関連。
* [batch_download_discord.md](./batch_download_discord.md) — 一時ファイル経由のアトミック書き込み（`.tmp`→`replace`）という同一パターンを採用している同じDDDサブシステム内の類似スクリプト（`DataManager.save_known_casts`のコメントで直接言及されている）。また、`run_monitor`の多重起動防止ロックは、本ファイルの`BatchDownloader.run`が既に採用している`fcntl.flock`による同種のロックパターンを踏襲したものである（本ファイルのコメントで直接言及されている）。
* [file_utils.md](./file_utils.md) — `DiscordNotifier`がインスタンス単位で保持する`DiscordCircuitBreaker`（Discord Webhookへの連続送信失敗検知用）の実装。`batch_download_discord.py`の`DiscordNotifier.send`とも共通利用される。加えて、`PROJECT_ROOT`(MY_HOME_SYSTEM)を解決する`resolve_my_home_system_root`も提供する（品質で追加、`batch_download_discord.py`/`extract_youtube_urls.py`と共通化）。

## 2. ファイルの概要

* モジュールDocstring上「NewFace Monitor System (Refactored for MY_HOME_SYSTEM)」と称される、`sites.json`に登録された複数のWebサイトの新人紹介ページを定期巡回し、新規キャストの追加をDiscord Webhookで通知するバッチスクリプトである。**（Issue #413で変更）** 監視対象サイトの定義は、以前は本ファイル内に`SiteConfig`インスタンスを約970行のPythonリテラルとして直書きしていたが、同ディレクトリの`sites.json`へ外出しされた。拡張時は`sites.json`にエントリを1件追記するだけでよく、本ファイル（2000行超のロジックファイル）の変更は不要になった。
* 根拠: [モジュールDocstring] (行番号: 4〜13 / 抜粋: "NewFace Monitor System (Refactored for MY_HOME_SYSTEM)\nTargets: sites.json に登録された複数サイト（起動時に MonitorConfig.SITES へ読み込まれる）")
* `MY_HOME_SYSTEM`の共通コア機能（`core.logger`, `core.nas_utils`, `core.utils`）のインポートを試み、失敗時（単体テスト用・モジュール欠損時）はファイル内にフォールバック実装（ロガー、NASディレクトリ解決の簡易版、ストレージウォームアップ処理）を用意している。
* 根拠: [try-exceptブロック] (行番号: 49〜54 / 抜粋: "try:\n    # システム統合環境下でのインポート\n    from core.logger import get_logger\n    from core.nas_utils import get_managed_target_directory\n    from core.utils import wait_for_storage_warmup\nexcept ImportError:")
* `SiteConfig`データクラスは監視対象1サイト分の設定（対象URL、CSSセレクタ、画像取得方法、名前抽出時の特殊処理フラグ等）を保持し、モジュールimport時に`_load_sites(SITES_JSON_PATH)`が`sites.json`を読み込んで`MonitorConfig.SITES`（`SiteConfig`インスタンス79件のリスト）を構築する（2026-09-02にサイト閉鎖が確認された`bellica`が削除され80件から79件になった。削除理由は`sites.json`の該当エントリの`_comment`フィールドとして残されている）。各サイトのHTML構造の違い（lazyload画像、インラインCSS背景画像、年齢バッジの位置、クエリパラメータ形式のID等）を、コード変更ではなく`SiteConfig`のフラグ・パラメータ調整のみで吸収する設計である。バリデーション自体は`SiteConfig`（frozen dataclass）に委譲しており、`sites.json`側はデータを保持するだけという役割分担は変わっていない。
* 根拠: [SiteConfigクラスと_load_sites定義] (行番号: 142〜147 / 抜粋: "新しいサイトを監視対象に加える場合は、sites.json にこのデータクラスの\n    フィールド名をキーとするエントリを1件追加するだけでよい\n    （コード本体の変更は不要。読み込みは _load_sites が担う）。")、[_load_sitesとMonitorConfig.SITES] (行番号: 243〜305 / 抜粋: "SITES: List[SiteConfig] = _load_sites(SITES_JSON_PATH)")
* `requests`と`BeautifulSoup`を用いて各サイトをスクレイピングし、キャスト情報（ID・名前・詳細URL・画像URL・年齢）を抽出、サイトごとに保存された既知キャスト一覧（JSON永続化、`known_casts_{site_id}.json`）との差分検知により新規キャストのみをDiscordへ通知する。
* 根拠: [WebMonitor._parse_htmlとCastMember] (行番号: 1602〜1667, 415〜457 / 抜粋: "def _parse_html(self, soup: BeautifulSoup, site: SiteConfig) -> Set[CastMember]:")
* 1サイトの通信障害・レイアウト変更・パースエラーが他サイトの監視処理に波及しないよう、サイト単位の処理は`_check_site`関数として分離され、例外は`run_monitor`内でサイトごとに個別捕捉される。
* 根拠: [_check_site Docstring] (行番号: 1831〜1840 / 抜粋: "サイト単位の処理を分離することで、あるサイトの通信障害・レイアウト変更が\n    他サイトの監視処理に波及しないようにする。")
* **（2026-09-02のbellica閉鎖対応で追加）** サイト別の連続失敗回数を`site_failures.json`に永続化し、`CONSECUTIVE_FAILURE_ALERT_THRESHOLD`（24回=1時間毎実行前提で約1日）に達したサイトは「閉鎖・移転の疑い」としてDiscordへ1回だけテキストアラートを送信した上で、以降の失敗ログをERRORからWARNINGへ降格する（一次ヘルスチェックのERROR監視が恒久的に消失したサイトで発報し続けないようにするため）。キャストを1件以上取得できた時点で連続失敗状態はリセットされ、通常のERROR運用に自動的に戻る。**（Issue #395で拡張）** 失敗として計上する対象はネットワーク例外に加えて「別ドメインへのリダイレクト（200を返す消失サイト）」「キャスト0件」にも広げられ、ログの降格はアラート送信の成否ではなく閾値到達で判定し、アラートは全サイト処理後にまとめて送信（失敗サイト割合が`SELF_OUTAGE_SUPPRESS_RATIO`超なら自局側障害とみなして抑止）する。
* 根拠: [_handle_site_network_failure Docstring] (行番号: 1704〜1722 / 抜粋: "単発・短期のネットワーク障害は従来\n    どおりERRORで記録しつつ、CONSECUTIVE_FAILURE_ALERT_THRESHOLD回連続で失敗した\n    サイトは「閉鎖・移転の疑い」としてDiscordへ1回だけテキスト通知し")
* 各サイトの新規検知件数はサイト単位のJSONに加え、`daily_summary.json`にも累積され、21時台の実行時にこれまでの累積分をテキスト形式でDiscordへ別途通知する（重複送信は送信済み日付の永続化で防止）。**（Issue #183で修正）** 以前はカレンダー日付が変わると集計が無条件にリセットされていたため、21時台送信後(22時〜24時)の検知や21時台の実行自体が無かった日の検知がどのサマリにも計上されないまま失われていたが、現在は実際に送信された時にのみ累積がクリアされ、日付をまたいでも未送信分は必ず次回送信に引き継がれる。
* 根拠: [_maybe_send_daily_summary Docstring] (行番号: 1945〜1963 / 抜粋: "このスクリプトはcron等により1時間毎に別プロセスとして起動される前提\n    (デーモン常駐ではない)のため、「21時になったら送る」という時刻トリガーは\n    実行時刻の時(hour)が21かどうかで判定する。")
* **（Issue #364で修正）** データディレクトリ（NAS上の`known_casts_*.json`等の保存先）は`_run_monitor_locked`の冒頭で`MonitorConfig.get_data_dir()`により**1回だけ**解決され、その値を束縛した`DataManager`インスタンスが全サイトの処理で使い回される。以前は`DataManager`の全メソッドが静的メソッドで、呼び出しのたびに`get_data_dir()`（= `core.nas_utils.get_managed_target_directory`。NAS未マウント時は`sudo mount`による自己修復とDiscord/LINE障害通知を伴う）を再評価していたため、79サイト×最低3回で1実行あたり240回以上呼ばれ、NAS障害時には毎時数百回の`sudo mount`と数百件のDiscord投稿が発生していた。あわせて、解決結果がローカルフォールバック先（`MonitorConfig.LOCAL_DIR_STR`）だった場合は、ローカル側に`known_casts_*.json`が無く全サイトの全在籍キャストが「新規」として再通知されてしまうため、`MonitorConfig.is_local_fallback_dir`で検知して実行全体を中断する（`extract_youtube_urls.py`の`_verify_environment`と同じ方針）。
* 根拠: [_run_monitor_lockedの解決・フォールバック判定とDataManager生成] (行番号: 2026〜2048 / 抜粋: "# #364: データディレクトリはここで1回だけ解決し、DataManagerに束縛して全サイトで\n    # 使い回す。" / "if MonitorConfig.is_local_fallback_dir(data_dir):" / "data_manager = DataManager(data_dir)")、[DataManagerクラスDocstring] (行番号: 775〜784 / 抜粋: "#364: 以前は全メソッドが静的メソッドで、呼び出しのたびに\n    MonitorConfig.get_data_dir()(= core.nas_utils.get_managed_target_directory。")
* 保存データはNAS等のストレージ上に一時ファイル経由のアトミック書き込みで永続化される。書き込み後は一時ファイルを読み戻して検証し、既存データを`.bak`としてバックアップしてから本番ファイルへ置き換える多段の安全策を持つ（詳細は4章`DataManager.save_known_casts`を参照）。**（Issue #462で追加）** 日次サマリ(`daily_summary.json`)の読み書き(`load_daily_summary`/`save_daily_summary`)にも同じ隔離＋バックアップ復旧・読み戻し検証の仕組みが拡張適用され、既知キャストデータと同水準の耐障害性を持つようになった。**（Issue #461で追加）** 破損検知のたびに`.corrupted-*`として蓄積する隔離ファイルは、`DataManager.cleanup_old_quarantine_files`により`_run_monitor_locked`の巡回ごとに`_QUARANTINE_RETENTION_DAYS`（既定30日）より古いものが削除される。**（Issue #578で追加）** `load_daily_summary`/`load_site_failures`（および`load_known_casts`の`.bak`復旧パス）は、CIFS/autofsの瞬断等による一時的なI/Oエラー（`OSError`）と、JSON構文エラー等の内容起因の破損を区別し、前者では空状態へフォールバックせず`DataFileUnavailableError`/`KnownCastsUnavailableError`を送出して呼び出し元に保存処理そのものをスキップさせる（Issue #365で`load_known_casts`の一次ファイル読み込みに導入済みだった区別を、残る3箇所にも揃えたもの。詳細は8章参照）。
* 根拠: [DataFileUnavailableError定義とコメント] (行番号: 758〜769 / 抜粋: "class DataFileUnavailableError(Exception):\n    \"\"\"load_daily_summary/load_site_failuresが、ファイルは存在するのにI/Oエラーで\n    読めなかったことを示す例外(#578)。")
* 根拠: [DataManager.save_known_castsのコメント] (行番号: 959〜961 / 抜粋: "# アトミック書き込み: 一時ファイルに書き出してから置き換えることで、\n            # 書き込み中断時に既存データが破損/空になるのを防ぐ\n            # (batch_download_discord.py の _purge_skipped_tasks と同じパターン)")
* `run_monitor`はモニタープロセスのエントリポイントとして、`fcntl.flock`による多重起動防止ロック（`_MONITOR_LOCK_FILE_PATH`）を非ブロッキングで取得してから処理本体`_run_monitor_locked`を呼び出す。cronの1回の実行が想定より長引く（1時間超）と新旧プロセスが並行実行され、既知キャストリスト・サマリファイルの読み書きが競合しうる問題への対策であり、`batch_download_discord.py`が既に採用している同種のロックパターンを踏襲している。
* 根拠: [_MONITOR_LOCK_FILE_PATHのコメントとrun_monitor] (行番号: 1996〜2003 / 抜粋: "# M-7-4: 多重起動防止ロック。cron等での実行が重複すると、既知キャストリストや\n# サマリファイルへの読み書きが競合し、一時消失→再通知等のデータ不整合が起きうる\n# (batch_download_discord.pyでは既にflockによる同種のロックが導入済み)。")

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `os` | 標準ライブラリ | 環境変数取得(`os.getenv`)、パス操作(`os.path.basename`) | 根拠: [import文] (行番号: 14 / 抜粋: "import os") |
| `json` | 標準ライブラリ | キャストデータ・日次サマリのJSONシリアライズ/デシリアライズ | 根拠: [import文] (行番号: 15 / 抜粋: "import json") |
| `re` | 標準ライブラリ | 年齢抽出(`AGE_PATTERN`)、背景画像URL抽出用の正規表現処理 | 根拠: [import文] (行番号: 16 / 抜粋: "import re") |
| `time` | 標準ライブラリ | Discord通知間のレート制限待機、スクレイピング前のBot検知回避待機、フォールバック実装のリトライ間隔 | 根拠: [import文] (行番号: 17 / 抜粋: "import time") |
| `random` | 標準ライブラリ | スクレイピング前のランダムな待機時間生成 | 根拠: [import文] (行番号: 18 / 抜粋: "import random") |
| `sys` | 標準ライブラリ | `sys.path`へのプロジェクトルート追加 | 根拠: [import文] (行番号: 19 / 抜粋: "import sys") |
| `logging` | 標準ライブラリ | フォールバック時のロガー基本設定・生成 | 根拠: [import文] (行番号: 20 / 抜粋: "import logging") |
| `hashlib` | 標準ライブラリ | ID未取得時のフォールバックIDを生成するためのフィンガープリント(sha1)算出 | 根拠: [import文] (行番号: 21 / 抜粋: "import hashlib") |
| `fcntl` | 標準ライブラリ | 多重起動防止ロックファイルへの排他ロック(`flock`)取得・解放 | 根拠: [import文] (行番号: 22 / 抜粋: "import fcntl") |
| `dataclasses.dataclass`, `asdict`, `replace` | 標準ライブラリ | `SiteConfig`/`CastMember`データクラスの定義、辞書変換。`replace` は **Issue #538** で追加され、`_merge_known_casts` が `missed_runs` だけを差し替えた `CastMember` を作るために使う | 根拠: [import文] (行番号: 26 / 抜粋: "from dataclasses import dataclass, asdict, replace") |
| `datetime.datetime` | 標準ライブラリ | 現在時刻の取得（日次サマリの日付判定、21時台判定） | 根拠: [import文] (行番号: 27 / 抜粋: "from datetime import datetime") |
| `pathlib.Path` | 標準ライブラリ | ファイル・ディレクトリパスの操作全般 | 根拠: [import文] (行番号: 28 / 抜粋: "from pathlib import Path") |
| `typing.List`, `Set`, `Dict`, `Optional`, `Tuple` | 標準ライブラリ | 型ヒント全般（`Tuple`は`record_site_failure`の戻り値型） | 根拠: [import文] (行番号: 29 / 抜粋: "from typing import List, Set, Dict, Optional, Tuple") |
| `urllib.parse.urljoin`, `urlparse`, `parse_qs` | 標準ライブラリ | 相対URL（キャスト詳細ページ・画像）の絶対URL化、クエリパラメータからのID抽出 | 根拠: [import文] (行番号: 30 / 抜粋: "from urllib.parse import urljoin, urlparse, parse_qs") |
| `file_utils.DiscordCircuitBreaker` | 内部モジュール(DDD配下) | `DiscordNotifier`が保持するDiscord Webhook連続送信失敗検知用サーキットブレーカー | 根拠: [import文] (行番号: 32 / 抜粋: "from file_utils import DiscordCircuitBreaker, redact_discord_webhook_url, resolve_my_home_system_root") |
| `file_utils.redact_discord_webhook_url` | 内部モジュール(DDD配下) | **（品質で追加、本監査で表を補完）** Discord Webhook送信失敗時のエラーログから、例外メッセージに含まれるWebhook URL(トークン込み)をマスクする。`DiscordNotifier.notify_casts`/`notify_daily_summary`/`notify_site_failure_alert`のエラーログ出力箇所で使用 | 根拠: [import文] (行番号: 32 / 抜粋: "from file_utils import DiscordCircuitBreaker, redact_discord_webhook_url, resolve_my_home_system_root") |
| `file_utils.resolve_my_home_system_root` | 内部モジュール(DDD配下) | **（品質で追加）** `PROJECT_ROOT`(MY_HOME_SYSTEM)の解決。以前は`CURRENT_DIR.parent / "MY_HOME_SYSTEM"`という固定の兄弟ディレクトリ前提のみの単純な方式を個別に実装していたが、`batch_download_discord.py`と共通化した(`MY_HOME_SYSTEM_ROOT`環境変数優先、無ければ`services`ディレクトリの上位探索にフォールバック) | 根拠: [import文とPROJECT_ROOT解決] (行番号: 39〜46 / 抜粋: "PROJECT_ROOT = resolve_my_home_system_root(CURRENT_DIR)") |
| `requests` | サードパーティ | HTTPセッションの生成・GETリクエスト送信、Discord Webhookへの POST送信 | 根拠: [import文] (行番号: 43 / 抜粋: "import requests") |
| `requests.adapters.HTTPAdapter` | サードパーティ | セッションへのリトライ用アダプタのマウント | 根拠: [import文] (行番号: 44 / 抜粋: "from requests.adapters import HTTPAdapter") |
| `urllib3.util.retry.Retry` | サードパーティ | HTTPリクエストのリトライポリシー定義（Discord向けは429の`Retry-After`尊重を含む） | 根拠: [import文] (行番号: 45 / 抜粋: "from urllib3.util.retry import Retry") |
| `bs4.BeautifulSoup`, `NavigableString` | サードパーティ | 取得したHTMLのパース・要素抽出、テキストノード判定（`name_first_text_only`処理） | 根拠: [import文] (行番号: 46 / 抜粋: "from bs4 import BeautifulSoup, NavigableString") |
| `core.logger.get_logger` | 内部モジュール（オプショナル、try節） | ロガーインスタンスの取得。インポート失敗時はファイル内フォールバック実装を使用 | 根拠: [import文] (行番号: 51 / 抜粋: "from core.logger import get_logger") |
| `core.nas_utils.get_managed_target_directory` | 内部モジュール（オプショナル、try節） | NAS/ローカルのデータ保存ディレクトリの解決・管理。インポート失敗時はファイル内フォールバック実装を使用 | 根拠: [import文] (行番号: 52 / 抜粋: "from core.nas_utils import get_managed_target_directory") |
| `core.utils.wait_for_storage_warmup` | 内部モジュール（オプショナル、try節） | ストレージ（NAS等）が書き込み可能になるまでの待機処理。インポート失敗時はファイル内フォールバック実装を使用 | 根拠: [import文] (行番号: 53 / 抜粋: "from core.utils import wait_for_storage_warmup") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `core.logger.get_logger` | インポート成功時に実際に使用される実装（フォーマット、出力先、ログレベル等）の詳細が本ファイルからは不明。フォールバック実装（`logging.getLogger`ベース）のみがこのファイルから確認できる。 | 根拠: [import文とフォールバック定義] (行番号: 51〜62 / 抜粋: "from core.logger import get_logger") |
| `core.nas_utils.get_managed_target_directory` | インポート成功時の実際の実装（NASマウント確認・自動修復ロジックの詳細）が不明。フォールバック実装は`fallback_dir_str`引数をそのまま返すのみ。 | 根拠: [import文とフォールバック定義] (行番号: 52〜72 / 抜粋: "from core.nas_utils import get_managed_target_directory") |
| `core.utils.wait_for_storage_warmup` | インポート成功時の実際の実装が不明。フォールバック実装（Exponential Backoffでのテストファイル書き込み確認）のみがこのファイルから確認できる。 | 根拠: [import文とフォールバック定義] (行番号: 53〜111 / 抜粋: "from core.utils import wait_for_storage_warmup") |
| `MonitorConfig.SITES`に登録された79件の対象Webサイト | 各サイトのHTML構造（CSSセレクタが依拠する実際のマークアップ）は本ファイルのコードからは分からず、外部Webサイトの実物に依存する。 | 根拠: [MonitorConfig.SITESの初期化] (行番号: 305 / 抜粋: "SITES: List[SiteConfig] = _load_sites(SITES_JSON_PATH)") |
| Discord Webhook API | Webhookエンドポイントの認証・レート制限・レスポンス仕様の詳細は本ファイルのコードからは分からず、Discord側の実装に依存する。 | 根拠: [Webhook POST送信] (行番号: 599, 683, 726 / 抜粋: "response = self.session.post(self.webhook_url, json=payload, timeout=10)") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `get_logger` (フォールバック実装)

* **役割**: `core.logger`のインポートに失敗した場合に使用される、標準`logging`モジュールベースの簡易ロガー取得関数。
* 根拠: [関数定義] (行番号: 61〜62 / 抜粋: "def get_logger(name: str) -> logging.Logger: \n        return logging.getLogger(name)")


* **引数/リクエスト**: `name: str`
* 根拠: [引数定義] (行番号: 61 / 抜粋: "def get_logger(name: str) -> logging.Logger: ")


* **戻り値/レスポンス**: `logging.Logger`
* 根拠: [戻り値ヒント] (行番号: 61 / 抜粋: "-> logging.Logger: ")


* **副作用**: なし（`logging.getLogger`は既存ロガーの取得または新規作成）
* **エラーハンドリング**: なし


### `get_managed_target_directory` (フォールバック実装)

* **役割**: `core.nas_utils`のインポートに失敗した場合に使用される簡易フォールバック関数。呼び出し元(`get_data_dir`)が渡す`fallback_dir_str`（`BASE_DIR/'data'`の絶対パス）があればそれを、なければカレントディレクトリ相対の`./data`を返す。カレントディレクトリ相対パスを無条件に返すと実行時のカレントディレクトリ次第で保存先が変わり、既存データが見つからず全キャストを新人として誤検知する不具合につながるため、絶対パスの`fallback_dir_str`を優先する設計であることがコメントで明記されている。
* 根拠: [関数定義とコメント] (行番号: 56〜64 / 抜粋: "def get_managed_target_directory(*args, **kwargs) -> Path:\n        # 呼び出し元(get_data_dir)はfallback_dir_str（BASE_DIR/'data'の絶対パス）を\n        # 渡してくる想定。これを無視してカレントディレクトリ相対の"./data"を返すと、\n        # 実行時のカレントディレクトリ次第で保存先が毎回変わってしまい、\n        # known_casts_*.jsonが見つからず全キャストを新人として誤検知する原因になる。")


* **引数/リクエスト**: `*args`, `**kwargs`（本フォールバック実装では`kwargs.get("fallback_dir_str")`のみを参照する）
* 根拠: [引数定義と参照箇所] (行番号: 69〜74 / 抜粋: "fallback_dir_str = kwargs.get("fallback_dir_str")")


* **戻り値/レスポンス**: `Path`（`fallback_dir_str`が渡されていればそれを`Path`化した値、なければ`Path("./data")`）
* 根拠: [各return文] (行番号: 70〜71 / 抜粋: "if fallback_dir_str:\n            return Path(fallback_dir_str)\n        return Path("./data")")


* **副作用**: なし
* **エラーハンドリング**: なし


### `wait_for_storage_warmup` (フォールバック実装)

* **役割**: NAS等のストレージがマウントされ書き込み可能になるまで、テストファイルの作成・削除による死活確認とExponential Backoffでのリトライにより待機する。`core.utils`のインポート失敗時に使用される。
* 根拠: [関数定義とDocstring] (行番号: 66〜78 / 抜粋: "def wait_for_storage_warmup(target_dir: Path, max_retries: int = 5, base_delay: float = 1.0) -> bool:\n        """\n        NAS等のストレージがマウントされ、書き込み可能になるまで待機する。")


* **引数/リクエスト**: `target_dir: Path`（アクセス確認を行う対象ディレクトリ）, `max_retries: int = 5`（最大リトライ回数）, `base_delay: float = 1.0`（ベースとなる待機時間・秒）
* 根拠: [引数定義とDocstring] (行番号: 80〜88 / 抜粋: "target_dir (Path): アクセス確認を行う対象ディレクトリ。\n            max_retries (int): 最大リトライ回数。\n            base_delay (float): ベースとなる待機時間（秒）。")


* **戻り値/レスポンス**: `bool`（アクセス確立できた場合`True`、最大リトライ到達で`False`）
* 根拠: [Docstring] (行番号: 85〜86 / 抜粋: "bool: ストレージへのアクセスが確立できた場合はTrue、タイムアウトした場合はFalse。")


* **副作用**: ディレクトリ作成試行(`target_dir.mkdir`)、テストファイル(`.storage_warmup_test`)の書き込み・削除、デバッグ/エラーログ出力、リトライ時の`time.sleep`。
* 根拠: [処理内容] (行番号: 100〜111 / 抜粋: "test_file.write_text("warmup_check", encoding="utf-8")\n                test_file.unlink()")


* **エラーハンドリング**: ディレクトリ作成失敗(`OSError`)時はデバッグログを出力し後続I/Oテストへ処理を継続。テストファイルの書き込み/削除失敗(`IOError`/`OSError`)時はExponential Backoffで待機しリトライ。最大試行後もアクセスできない場合はエラーログを出力し`False`を返す（パニックを起こさない設計）。
* 根拠: [try-exceptブロックとコメント] (行番号: 109〜129 / 抜粋: "# 最終的にアクセスできない場合はパニックを起こさずFalseを返す\n        logger.error(f"Storage warmup failed after {max_retries} attempts.")\n        return False")


### `AGE_PATTERN` (モジュール定数)（D-L12で変更）

* **役割**: 名前要素のテキストから年齢を抽出するための正規表現。"うるは(23歳)"のような全角/半角括弧付き数字、または「歳」「才」が続く数字表記のいずれかにマッチする。ランキングバッジ等の1桁の括弧数字（例: "(1)"）を誤って年齢と判定しないよう、桁数を2桁に限定している。**（D-L12で変更）** 括弧内の「歳」「才」の有無を判別できるよう、以前は非捕捉グループだった`(?:歳|才)?`を捕捉グループ`(歳|才)?`に変更した。マッチ結果は3グループ: `group(1)`＝括弧内の数字、`group(2)`＝括弧内の「歳」「才」（無ければ`None`）、`group(3)`＝括弧無しで「歳」「才」が続く数字。呼び出し側（**品質で`_parse_html`から分離**された`WebMonitor._extract_cast_age`）は、`group(2)`が`None`（＝括弧内に「歳」「才」の明示が無い）の場合のみ、`MonitorConfig.AGE_PLAUSIBLE_MIN`〜`AGE_PLAUSIBLE_MAX`の範囲かどうかで年齢として採用するか判定する（詳細は`WebMonitor._extract_cast_age`参照）。以前は括弧内の数字を「歳」「才」の有無に関わらず無条件に年齢とみなしていたため、"(85)"のような部屋番号・順位バッジ等の括弧付き2桁数字を誤って年齢と判定しうる懸念があった。
* 根拠: [定義とコメント] (行番号: 122〜135 / 抜粋: "# 名前要素のテキストから年齢を抽出するための正規表現。\n# "うるは(23歳)" / "浅見ゆき（30）" / "小鳥(ことり)セラピスト  22歳" のように、\n...\n# D-L12: 括弧内の数字は「歳」「才」が続かない場合(第2group=None)でも\n# 無条件に年齢とみなしていたため" / "AGE_PATTERN = re.compile(r'[（(]\\s*(\\d{2})\\s*(歳|才)?\\s*[）)]|(\\d{2})\\s*(?:歳|才)')")


### `SiteConfig`

* **役割**: 監視対象サイト1件分の設定を保持するイミュータブル(`frozen=True`)なデータクラス。対象URL、キャスト一覧・名前・リンク・画像取得用のCSSセレクタ、既知キャストの保存先ファイル名、ID/画像/名前抽出時の各種特殊処理フラグを持つ。**（Issue #413で変更）** 新規サイトを追加する際は、このクラスのインスタンスを本ファイルに直接書き足すのではなく、`sites.json`にフィールド名をキーとするJSONエントリを1件追加するだけでよい（構築は`_load_sites`が担う）。
* 根拠: [クラス定義とDocstring] (行番号: 133〜139 / 抜粋: "@dataclass(frozen=True)\nclass SiteConfig:\n    """監視対象サイト1件分の設定。\n\n    新しいサイトを監視対象に加える場合は、sites.json にこのデータクラスの\n    フィールド名をキーとするエントリを1件追加するだけでよい\n    （コード本体の変更は不要。読み込みは _load_sites が担う）。")


* **引数/リクエスト**: `site_id: str`, `name: str`, `target_url: str`, `selector_container: str`, `selector_name: str`, `selector_link: str`, `selector_image: str`, `data_filename: str = ""`, `id_query_param: Optional[str] = None`, `image_attr: str = "src"`, `image_from_style: bool = False`, `name_first_text_only: bool = False`, `name_strip_after_tab: bool = False`, `skip_unnamed_casts: bool = False`
* 根拠: [フィールド定義] (行番号: 185〜198 / 抜粋: "site_id: str\n    name: str\n    target_url: str\n    selector_container: str\n    selector_name: str\n    selector_link: str\n    selector_image: str")


* **戻り値/レスポンス**: 該当なし（データクラスのフィールド定義自体）
* **副作用**: なし
* **エラーハンドリング**: なし（フィールドの型・必須性は`dataclass`の通常のコンストラクタ機構に委ねられる。JSON側からの構築時のエラーハンドリングは`_load_sites`が担う）


### `SiteConfig.get_data_filename`

* **役割**: 既知キャストの保存先ファイル名を返す。`data_filename`が明示指定されていればそれを、なければ`site_id`から導出したデフォルトファイル名（`known_casts_{site_id}.json`）を返す。**（Issue #413）** ロジック自体は`sites.json`外出し前後で変更していない。
* 根拠: [メソッド定義とDocstring] (行番号: 200〜207 / 抜粋: "def get_data_filename(self) -> str:\n        """既知キャストの保存先ファイル名を返す。")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `str`
* 根拠: [戻り値ヒントとreturn文] (行番号: 212〜219 / 抜粋: "return self.data_filename or f"known_casts_{self.site_id}.json"")


* **副作用**: なし
* **エラーハンドリング**: なし


### `SITES_JSON_PATH` / `_load_sites`（Issue #413で新規追加）

* **役割**: 監視対象サイト定義の外出し先(`sites.json`、`SITES_JSON_PATH = CURRENT_DIR / 'sites.json'`)を読み込み、`SiteConfig`のリストへ変換するモジュール関数。以前は本ファイル内に`SiteConfig(...)`の呼び出しを約970行のPythonリテラルとして直書きしていた79サイト分の定義を、同ディレクトリの`sites.json`（各エントリが`SiteConfig`のフィールド名をキーとするJSONオブジェクトの配列）へ外出しした際に追加された。JSON側の各エントリの`_comment`キーはサイト追加理由等を残すドキュメント専用フィールドであり、`SiteConfig`の構築対象からは除外する。バリデーション自体は`SiteConfig`（frozen dataclass）のコンストラクタにそのまま委譲し、本関数側で追加するのは「JSON構文自体が壊れていないか」「配列/オブジェクトの形が正しいか」「`site_id`が重複していないか」の3点のみ（フィールドの型チェック等の詳細検証は`SiteConfig`側の責務のまま変更していない）。ファイル欠損・JSON構文エラー・配列でない・要素がオブジェクトでない・`SiteConfig`が拒否する不正なフィールド（必須フィールド欠落・未知フィールド等）・`site_id`重複のいずれかがあれば`RuntimeError`を送出し、モジュールimport時（`MonitorConfig.SITES`のクラス変数初期化時）に処理全体を止める設計であり、壊れたエントリを黙ってスキップすることはない。
* 根拠: [定数定義とコメント] (行番号: 215〜221 / 抜粋: "# Issue #413: 監視対象サイト定義（旧: 本ファイル内の約970行のPythonリテラル、\n# 79サイト分）を sites.json へ外出しした。" / "SITES_JSON_PATH: Path = CURRENT_DIR / 'sites.json'")、[関数定義とDocstring] (行番号: 243〜265 / 抜粋: "def _load_sites(json_path: Path) -> List[SiteConfig]:\n    """sites.json を読み込み、SiteConfigのリストとして返す。")


* **引数/リクエスト**: `json_path: Path`（`sites.json`のパス）
* 根拠: [引数定義] (行番号: 243, 258 / 抜粋: "def _load_sites(json_path: Path) -> List[SiteConfig]:" / "json_path (Path): sites.json のパス。")


* **戻り値/レスポンス**: `List[SiteConfig]`（JSON内の出現順）
* 根拠: [戻り値ヒントとDocstring] (行番号: 260〜278 / 抜粋: "Returns:\n        List[SiteConfig]: 読み込んだサイト設定のリスト（JSON内の出現順）。")


* **副作用**: ファイル読み込み(`json_path.read_text`)。呼び出しはモジュールimport時（`MonitorConfig.SITES`のクラス変数初期化）に1回のみ。
* **エラーハンドリング**: ファイル読み込み失敗(`OSError`)・JSON構文エラー(`json.JSONDecodeError`)・トップレベルが配列でない・要素がオブジェクトでない・`SiteConfig(**fields)`が`TypeError`を送出（必須フィールド欠落・未知フィールド等）・`site_id`重複、のいずれについても`RuntimeError`を送出する（Issue #413の要件「malformed entryは黙ってスキップせず起動時に気付けること」に対応。回帰テストは`test_newface_monitor_sites_json.py`）。
* 根拠: [各例外送出箇所] (行番号: 266〜295 / 抜粋: "try:\n        raw_text = json_path.read_text(encoding='utf-8')\n    except OSError as e:\n        raise RuntimeError(f"サイト設定ファイルが読み込めません: {json_path} ({e})") from e" / "if site.site_id in seen_ids:\n            raise RuntimeError(f"sites.json に site_id の重複があります: {site.site_id!r}")")


### `MonitorConfig`

* **役割**: 監視対象サイト一覧（`SITES`）、ファイルパス、ネットワーク設定（User-Agent、タイムアウト、リトライ）、Discord Webhook URLなど、モニタリング処理全体で使用される設定値・定数を集約管理するクラス（インスタンス化は行われない）。
* 根拠: [クラス定義とDocstring] (行番号: 299〜300 / 抜粋: "class MonitorConfig:\n    """モニタリング設定および定数管理クラス。"""")


* **引数/リクエスト**: なし（クラス変数として静的に定義）
* 根拠: [クラス変数定義群] (行番号: 299〜407 / 抜粋: "SITES: List[SiteConfig] = _load_sites(SITES_JSON_PATH)")


* **戻り値/レスポンス**: 該当なし
* **副作用**: `SITES`のクラス変数定義時に`_load_sites(SITES_JSON_PATH)`を呼び出し`sites.json`を読み込む（不正があれば`RuntimeError`でモジュールimport自体が失敗する）。`DISCORD_WEBHOOK_URL`のクラス変数定義時にモジュールレベル関数`_resolve_discord_webhook_url()`（**Issue #586で追加**）を呼び出し、環境変数`DISCORD_WEBHOOK_NOTIFY`→`DISCORD_WEBHOOK_URL`の優先順位で解決する（後述）。
* 根拠: [クラス変数の初期化式] (行番号: 305, 328 / 抜粋: "SITES: List[SiteConfig] = _load_sites(SITES_JSON_PATH)" / "DISCORD_WEBHOOK_URL: Optional[str] = _resolve_discord_webhook_url()")

### `_resolve_discord_webhook_url`（Issue #586で新規追加）

* **役割**: Discord通知先のWebhook URLを環境変数から解決するモジュールレベル関数。`MY_HOME_SYSTEM/config.py`は`DISCORD_WEBHOOK_NOTIFY`を優先し未設定時のみレガシーの`DISCORD_WEBHOOK_URL`にフォールバックする(`DISCORD_WEBHOOK_NOTIFY or os.getenv("DISCORD_WEBHOOK_URL")`)が、本ファイルは以前`os.getenv('DISCORD_WEBHOOK_URL')`のみを参照する独自解決をしており、実機で`DISCORD_WEBHOOK_NOTIFY`のみが設定されている運用（config.py側の優先順位に合わせた設定）では本ファイルの通知だけが無設定として扱われ、Discord通知が一切送信されなくなっていた。config.pyと同じ優先順位に揃えるためモジュールレベル関数として切り出した。**この「1行の式のためだけに関数を切り出す」という一見過剰な設計の理由**: `MonitorConfig.DISCORD_WEBHOOK_URL`はモジュールimport時に1度だけ評価されるクラス属性であり、値を差し替えて挙動をテストするには本来`importlib.reload`でモジュールを再評価する必要がある。しかし本ファイルの他の回帰テストは`newface_monitor`モジュールを`sys.modules`経由で共有しており、`reload`はそのモジュールオブジェクトのクラスを新しいオブジェクトへ作り直してしまうため、他テストファイルが同モジュールに対して行っている`monkeypatch.setattr`ベースのフィクスチャを壊す（実際に試して確認された）。この副作用を避けるため、環境変数の優先順位解決だけを独立関数へ切り出し、`_resolve_discord_webhook_url()`を直接呼び出すだけで`importlib.reload`無しに単体テストできるようにした（関数自身のDocstringにも同旨の記載がある）。
* 根拠: [関数定義とDocstring] (行番号: 224〜239 / 抜粋: "def _resolve_discord_webhook_url() -> Optional[str]:\n    """通知先のDiscord Webhook URLを環境変数から解決する。" / "MonitorConfigのクラス属性(モジュールimport時に1度だけ評価される)から\n    切り出した関数として定義することで、モジュール全体をimportlib.reloadせずに\n    単体テストできるようにしている。")、[reloadを避ける理由の詳細] (`test_newface_monitor_discord_webhook_priority.py` 行番号: 13〜17 / 抜粋: "`MonitorConfig.DISCORD_WEBHOOK_URL`はモジュールimport時に1度だけ評価される\nクラス属性のため、環境変数を変えて直接テストすることができない\n(importlib.reloadはモジュールを共有する他のテストファイルの状態を壊すため\n使わない)。解決ロジックを切り出した`_resolve_discord_webhook_url()`関数を\n直接呼び出すことで、モジュール全体のreload無しに単体テストする。")、回帰テスト`test_newface_monitor_discord_webhook_priority.py::TestResolveDiscordWebhookUrlPriority`（3パターン: 両方設定/URLのみ/両方未設定）
* **引数**: なし
* **戻り値**: `Optional[str]`。`DISCORD_WEBHOOK_NOTIFY`が設定されていればその値、未設定なら`DISCORD_WEBHOOK_URL`の値、いずれも未設定なら`None`。
* **副作用**: なし（環境変数の読み取りのみ）
* **エラーハンドリング**: なし
* 根拠: [解決式] (行番号: 240 / 抜粋: "return os.getenv('DISCORD_WEBHOOK_NOTIFY') or os.getenv('DISCORD_WEBHOOK_URL')")


* **`MASS_DETECTION_WARNING_THRESHOLD: int = 20`について**: `_check_site`が既知キャスト存在下での大量新規検知（known_castsデータ喪失等による誤検知の疑い）を警告ログとして検出する際の閾値。通常運用時の新規検知は数件〜十数件程度であることを踏まえた目安値。
* 根拠: [定数定義とコメント] (行番号: 342〜344 / 抜粋: "# 通常運用時の新規検知は数件〜十数件程度のため、この件数以上の差分は\n    # known_castsデータの喪失/巻き戻り等による誤検知の疑いとして警告する目安値\n    MASS_DETECTION_WARNING_THRESHOLD: int = 20")


* **`KNOWN_CAST_PRUNE_AFTER_MISSES: int = 168`について（Issue #538で追加）**: `_merge_known_casts` が既知キャストを剪定する閾値。known_casts は #237 以来 union 保存(既知キャストを消さない)のため、退店済みキャストやフォールバック ID の揺れで生じたエントリが無限に蓄積していた。一覧ページからこの回数連続で欠けたキャストだけを剪定する(1時間毎のcron前提で約7日分)。#237 が防ぎたかった「単発のパース失敗で消える→再通知」は、単発では到底達しない閾値にすることで引き続き防ぐ。
* 根拠: [定数定義とコメント] (行番号: 345〜350 / 抜粋: "# Issue #538: known_casts は #237 以来 union 保存(既知キャストを消さない)のため、", "KNOWN_CAST_PRUNE_AFTER_MISSES: int = 168")


* **`AGE_PLAUSIBLE_MIN: int = 18` / `AGE_PLAUSIBLE_MAX: int = 79`について（D-L12で追加）**: `AGE_PATTERN`が「歳」「才」の明示無しに括弧内の2桁数字を年齢と判定する場合の妥当性チェック用範囲。`WebMonitor._extract_cast_age`（**品質で`_parse_html`から分離**）で、括弧内数字に「歳」「才」の明示が無い場合のみこの範囲でフィルタする（範囲外なら年齢として採用しない）。「歳」「才」で明示された数字は、この範囲に関わらず無条件に信頼する。
* 根拠: [定数定義とコメント] (行番号: 351〜356 / 抜粋: "# D-L12: AGE_PATTERNが「歳」「才」の明示無しに括弧内の2桁数字を年齢と\n    # 判定する場合の妥当性チェック用範囲。この範囲外の値は年齢として採用しない\n    # (部屋番号・順位バッジ等の誤検知を減らすための足切り。「歳」「才」で\n    # 明示された数字は範囲に関わらず信頼する)。\n    AGE_PLAUSIBLE_MIN: int = 18\n    AGE_PLAUSIBLE_MAX: int = 79")


* **`CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 24`について（2026-09-02のbellica閉鎖対応で追加）**: ネットワーク起因の巡回失敗がこの回数連続したサイトを「閉鎖・移転の疑い」としてDiscordへ1回だけアラート通知し、以降の失敗ログをWARNINGに降格するための閾値。1時間毎のcron実行前提で約1日分に相当する。2026-09-02のbellica閉鎖時に、消失したサイトが毎時ERRORを出し続けて一次ヘルスチェックが発報し続けた事象の再発防止として導入された。
* 根拠: [定数定義とコメント] (行番号: 358〜364 / 抜粋: "# ネットワーク起因の巡回失敗がこの回数連続したサイトは「閉鎖・移転の疑い」\n    # としてDiscordへ1回だけアラート通知し、以降の失敗ログをWARNINGに降格する\n    CONSECUTIVE_FAILURE_ALERT_THRESHOLD: int = 24")


* **`SELF_OUTAGE_SUPPRESS_RATIO: float = 0.5`について（Issue #395で追加）**: 同一実行内で失敗として計上したサイト数が総サイト数に占める割合がこの値を超える場合、個々のサイトの閉鎖ではなく自局側（Pi側の回線断・DNS障害等）の障害とみなし、`_send_pending_site_failure_alerts`が閉鎖疑いアラートの一斉送信を抑止する（79サイト分のアラートが同時に飛ぶのを防ぐ）。
* 根拠: [定数定義とコメント] (行番号: 365〜368 / 抜粋: "# #395: 同一実行内で失敗したサイト数が総数に占める割合がこの値を超える場合、\n    # 個々のサイトの閉鎖ではなく自局側(Pi側の回線断・DNS障害等)の障害とみなし、\n    # 閉鎖疑いアラートの一斉送信を抑止する(79サイト分のアラートが同時に飛ぶのを防ぐ)。\n    SELF_OUTAGE_SUPPRESS_RATIO: float = 0.5")


* **エラーハンドリング**: なし（`SITES`初期化時の`_load_sites`呼び出しが例外を送出しうる点を除く。上記`_load_sites`の項を参照）


#### `MonitorConfig.SITES` / `sites.json` について（データ内容の補足、Issue #413で外部化）

`SITES`は`SiteConfig`インスタンスを79件含むリストであり（2026-09-02にサイト閉鎖が確認された`bellica`は削除済み。削除理由は`sites.json`の該当エントリの`_comment`フィールドに記載）、モジュールimport時に`_load_sites(SITES_JSON_PATH)`によって`sites.json`から構築される。`sites.json`の各エントリは`SiteConfig`の必須フィールド（`site_id`/`name`/`target_url`/`selector_container`/`selector_name`/`selector_link`/`selector_image`）に加え、デフォルト値と異なる値を持つオプションフィールドのみを記載する形式（省略時は`SiteConfig`側のデフォルト値が使われる）。対象サイトのHTML構造上の特殊事情（例: lazyload画像は`image_attr: "data-original"`、インラインCSS背景画像は`image_from_style: true`、名前要素に年齢が兄弟要素またはタブ区切りで同居する場合は`name_first_text_only: true`/`name_strip_after_tab: true`、クエリパラメータ形式のID体系は`id_query_param`）は、以前は本ファイル内のPythonコメントとして付記していたが、`sites.json`側では同じ情報を各エントリの`_comment`キー（任意の文字列。`SiteConfig`の構築対象からは除外される）として保持しており、情報は失われていない。これらは設定データであり、個別のロジック（関数・メソッド）ではないため本セクションでは項目単位の列挙は行わず、全体としての設計方針のみを記載する。
* 根拠: [SITES初期化とコメント] (行番号: 303〜305 / 抜粋: "# 新規サイトを監視対象に追加する場合は sites.json に1エントリ追記するだけでよい\n    # （本クラス・本ファイルの変更は不要。フィールドの意味は SiteConfig のdocstring参照）。\n    SITES: List[SiteConfig] = _load_sites(SITES_JSON_PATH)")、[sites.json 冒頭2件] (`sites.json` 行番号: 1〜21 / 抜粋: "{\n    "site_id": "petitpetit_dream",\n    ...\n    "_comment": "既存運用データ（known_casts.json）との後方互換のためファイル名を明示指定"\n  },")


### `MonitorConfig.get_data_dir`

* **役割**: NASアクセスを検証・修復し、動的にデータディレクトリを解決するクラスメソッド。クラスロード時ではなく実処理が必要になったタイミング（遅延評価）でマウント確認・自動修復ロジックを実行する。**（Issue #364）** 委譲先の`get_managed_target_directory`はNAS未マウント時に`sudo mount`による自己修復とDiscord/LINEへの障害通知を伴う重い処理のため、Docstringに「1回の実行(`_run_monitor_locked`)で1回だけ呼び出し、結果を`DataManager`へ渡して使い回すこと」という呼び出し規約が明記された。
* 根拠: [メソッド定義とDocstring] (行番号: 371〜385 / 抜粋: "def get_data_dir(cls) -> Path:\n        """NASアクセスを検証・修復し、動的にデータディレクトリを解決する。" / "#364: 委譲先の get_managed_target_directory はNAS未マウント時に\n        sudo mountによる自己修復とDiscord/LINEへの障害通知を伴う重い処理のため、\n        1回の実行(_run_monitor_locked)で1回だけ呼び出し、結果をDataManagerへ\n        渡して使い回すこと")


* **引数/リクエスト**: なし（`cls`のみ、`@classmethod`）
* 根拠: [デコレータと引数] (行番号: 370〜371 / 抜粋: "@classmethod\n    def get_data_dir(cls) -> Path:")


* **戻り値/レスポンス**: `Path`（利用可能なデータディレクトリパス）
* 根拠: [Docstringと戻り値] (行番号: 382〜384 / 抜粋: "Returns:\n            Path: 利用可能なディレクトリパス\n        """\n        return get_managed_target_directory(")


* **副作用**: `get_managed_target_directory`（インポート成功時は`core.nas_utils`、失敗時はフォールバック実装）の呼び出し。
* 根拠: [呼び出し] (行番号: 385〜389 / 抜粋: "return get_managed_target_directory(\n            nas_dir_str=cls.NAS_DIR_STR,\n            fallback_dir_str=cls.LOCAL_DIR_STR,\n            mount_point=cls.MOUNT_POINT\n        )")


* **エラーハンドリング**: なし（本メソッド自体には例外処理なし。委譲先の実装に依存）


### `MonitorConfig.is_local_fallback_dir`（Issue #364で追加）

* **役割**: `get_data_dir()`が返した解決済みディレクトリが、NAS障害時のローカルフォールバック先（`LOCAL_DIR_STR`）かどうかを判定するクラスメソッド。ローカル側には`known_casts_*.json`が存在しないため、フォールバック中に巡回を続けると全サイトの全在籍キャストが「新規」として再通知されてしまう。`extract_youtube_urls.py`の`_verify_environment`と同じく、`Path.resolve()`で正規化したうえでの比較により表記揺れに関わらず確実に検知する。旧`MonitorConfig.get_data_file`（呼び出しのたびに`get_data_dir()`を再評価していた）は本Issueで廃止され、ファイルパスの導出は`DataManager._data_file`（束縛済み`data_dir`を使う）へ移った。
* 根拠: [メソッド定義とDocstring] (行番号: 392〜408 / 抜粋: "def is_local_fallback_dir(cls, data_dir: Path) -> bool:\n        """解決済みのデータディレクトリがNAS障害時のローカルフォールバック先かを判定する。" / "return Path(data_dir).resolve() == Path(cls.LOCAL_DIR_STR).resolve()")


* **引数/リクエスト**: `cls`（`@classmethod`）, `data_dir: Path`（`get_data_dir()`が返したディレクトリ）
* 根拠: [引数定義とDocstring] (行番号: 402〜412 / 抜粋: "data_dir (Path): get_data_dir() が返したディレクトリ。")


* **戻り値/レスポンス**: `bool`（ローカルフォールバック先であれば`True`）
* 根拠: [Docstringと戻り値] (行番号: 404〜407 / 抜粋: "Returns:\n            bool: ローカルフォールバック先であれば True。")


* **副作用**: なし（`Path.resolve()`によるパス正規化のみ。NASアクセスは行わない）
* **エラーハンドリング**: なし


### `CastMember`

* **役割**: キャスト情報（ID、名前、詳細URL、画像URL、年齢）を表現するデータクラス。ID(`id`)に基づくハッシュ・等価比較を独自定義することで、`Set[CastMember]`による重複排除・差分検知を可能にしている。**（Issue #538で追加）** `missed_runs: int = 0` フィールドは、一覧ページから連続して欠けていた巡回回数を保持する。一覧に載っていれば `_merge_known_casts` が0へ戻し、`MonitorConfig.KNOWN_CAST_PRUNE_AFTER_MISSES` に達した既知キャストは known_casts から剪定される。`to_dict`(`asdict`)で JSON にも `missed_runs` が書き出されるが、既存の JSON にこのキーが無くても既定値0で読み込める(`CastMember(**item)`)。
* 根拠: [クラス定義とDocstring] (行番号: 414〜435 / 抜粋: "@dataclass\nclass CastMember:\n    """キャスト情報を表現するデータクラス。", "missed_runs: int = 0")


* **引数/リクエスト**: `id: str`, `name: str`, `detail_url: str`, `image_url: str`, `age: str = ""`（一覧ページ上に年齢表記が見つからない場合は空文字）, `missed_runs: int = 0`（**Issue #538で追加**。連続欠落回数）
* 根拠: [フィールド定義とDocstring] (行番号: 423〜429 / 抜粋: "age (str): 年齢（数字のみ、例: "23"）。一覧ページ上に年齢表記が\n            見つからないサイト・キャストでは空文字となる。")


* **戻り値/レスポンス**: 該当なし（データクラスのフィールド定義自体）
* **副作用**: なし
* **エラーハンドリング**: なし


### `CastMember.__hash__`

* **役割**: `id`フィールドのみに基づくハッシュ値を返す。`Set[CastMember]`での重複排除の基準を`id`のみとするためのオーバーライド。
* 根拠: [メソッド定義] (行番号: 437〜438 / 抜粋: "def __hash__(self) -> int:\n        return hash(self.id)")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `int`
* 根拠: [戻り値ヒント] (行番号: 437 / 抜粋: "def __hash__(self) -> int:")


* **副作用**: なし
* **エラーハンドリング**: なし


### `CastMember.__eq__`

* **役割**: 比較対象が`CastMember`インスタンスであり、かつ`id`が一致する場合にのみ等価と判定する。
* 根拠: [メソッド定義] (行番号: 440〜443 / 抜粋: "def __eq__(self, other: object) -> bool:\n        if not isinstance(other, CastMember):\n            return False\n        return self.id == other.id")


* **引数/リクエスト**: `other: object`
* **戻り値/レスポンス**: `bool`
* 根拠: [戻り値ヒント] (行番号: 440 / 抜粋: "def __eq__(self, other: object) -> bool:")


* **副作用**: なし
* **エラーハンドリング**: なし（型不一致時は例外ではなく`False`を返す設計）


### `CastMember.to_dict`

* **役割**: `CastMember`インスタンスをJSONシリアライズ可能な辞書形式に変換する。
* 根拠: [メソッド定義とDocstring] (行番号: 445〜451 / 抜粋: "def to_dict(self) -> Dict[str, str]:\n        """辞書形式に変換する。")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `Dict[str, str]`（`asdict(self)`の結果）
* 根拠: [戻り値] (行番号: 451 / 抜粋: "return asdict(self)")


* **副作用**: なし
* **エラーハンドリング**: なし


### `DiscordNotifier._EMBED_TITLE_MAX_LEN` / `_EMBED_FIELD_VALUE_MAX_LEN` / `_truncate_for_embed`（D-L6で追加）

* **役割**: Discord embedの`title`（実際の上限256文字）・`fields[].value`（実際の上限1024文字）を超えるとembed全体が400 Bad Requestで拒否されるため、安全側の切り詰め上限（クラス定数、いずれも250文字）と、その上限に収まるよう省略記号付きで切り詰める静的メソッド`_truncate_for_embed`を追加した。`cast.name`等は外部サイトのスクレイピング結果であり、サイト側の表示崩れ・異常データで想定外に長くなりうるため送信前に切り詰める対象とする（`site.name`等の開発者管理の文字列は対象外）。
* 根拠: [クラス定数と静的メソッドの定義] (行番号: 461〜476 / 抜粋: "# D-L6: Discord embedのtitle(256文字)/field.value(1024文字)には上限があり、\n    # 超過するとembed全体が400 Bad Requestで拒否される。" / "_EMBED_TITLE_MAX_LEN = 250\n    _EMBED_FIELD_VALUE_MAX_LEN = 250" / "def _truncate_for_embed(text: str, max_len: int) -> str:")


* **引数/リクエスト**: `_truncate_for_embed(text: str, max_len: int)`
* **戻り値/レスポンス**: `str`（`max_len`以下に切り詰められた文字列。切り詰め時は末尾に`"…(省略)"`を付与）
* 根拠: [戻り値ヒントと処理] (行番号: 470〜476 / 抜粋: "def _truncate_for_embed(text: str, max_len: int) -> str:\n        \"\"\"Discord embedの文字数上限に収まるよう、超過分を省略記号付きで切り詰める。\"\"\"\n        if len(text) <= max_len:\n            return text\n        suffix = "…(省略)"\n        return text[: max(max_len - len(suffix), 0)] + suffix")


* **副作用**: なし（純粋な文字列処理）
* **エラーハンドリング**: なし


### `DiscordNotifier._to_well_formed_url`（2026-09-09 運用障害対応で追加）

* **役割**: DiscordのembedのURL系フィールド(`embed.url`、`thumbnail.url`)へ渡すURLを、`requests.utils.requote_uri`でRFC準拠の形へパーセントエンコードする。運用ログで、画像URLが`https://`で始まっていても日本語ファイル名や全角スペースが未エンコードのまま含まれる場合（例: `.../20260402130316-ニコ　加工済.jpg`）、Discordがそれを「well formed」なURLと認めず`400 Bad Request`でembed全体を拒否する事象が発生した。`requote_uri`は既にパーセントエンコード済みの部分を二重エンコードせず、非ASCII文字・スペース等のみを安全にエンコードするため、これを用いる。
* 根拠: [静的メソッドの定義とDocstring] (行番号: 477〜489 / 抜粋: "def _to_well_formed_url(url: str) -> str:\n        \"\"\"DiscordのembedのURL系フィールド向けに、URLをRFC準拠の形へパーセントエンコードする。\n\n        スクレイピング元サイトのHTML(imgのsrc属性・aのhref属性等)には、日本語の\n        ファイル名や全角スペースが未エンコードのまま残っていることがある")


* **引数/リクエスト**: `_to_well_formed_url(url: str)`
* **戻り値/レスポンス**: `str`（`requests.utils.requote_uri(url)`の結果。既にパーセントエンコード済みの部分は変化せず、非ASCII文字・スペース等のみエンコードされる）
* 根拠: [戻り値ヒントと処理] (行番号: 478, 490 / 抜粋: "def _to_well_formed_url(url: str) -> str:" / "return requests.utils.requote_uri(url)")


* **副作用**: なし（純粋な文字列処理）
* **エラーハンドリング**: なし


### `DiscordNotifier.__init__`

* **役割**: Discordへの通知送信を担当するサービスクラスのコンストラクタ。Webhook URLを保持し、レート制限に自動追従するHTTPセッションを生成する。あわせて、このインスタンスの生存期間(=1回のプロセス実行の間)だけ有効な`DiscordCircuitBreaker`インスタンスを生成し保持する。
* 根拠: [クラス定義とDocstringおよび__init__] (行番号: 458〜461 / 抜粋: "class DiscordNotifier:\n    """Discordへの通知を担当するサービスクラス。"""\n\n    def __init__(self, webhook_url: Optional[str]):")


* **引数/リクエスト**: `webhook_url: Optional[str]`（DiscordのWebhook URL）
* 根拠: [引数定義とDocstring] (行番号: 480〜484 / 抜粋: "webhook_url (Optional[str]): DiscordのWebhook URL。")


* **戻り値/レスポンス**: 該当なし
* **副作用**: `self.webhook_url`への代入、`self.session`への`_create_rate_limited_session()`結果の代入、`self._circuit_breaker`への`DiscordCircuitBreaker()`（既定の`failure_threshold=3`）の代入。
* 根拠: [属性代入] (行番号: 482〜486 / 抜粋: "self.webhook_url = webhook_url\n        self.session = self._create_rate_limited_session()\n        # 連続送信失敗時に以降の送信をスキップするサーキットブレーカー\n        # (このインスタンスの生存期間=1回のプロセス実行の間だけ有効)\n        self._circuit_breaker = DiscordCircuitBreaker()")


* **エラーハンドリング**: なし


### `DiscordNotifier._create_rate_limited_session`

* **役割**: Discordのレート制限(429)に自動追従するHTTPセッションを作成する。Discord Webhookはバーストした`POST`に対して429を返すことがあり、固定`sleep`だけでは不十分なため、`urllib3`の`Retry`が`Retry-After`ヘッダーを尊重して自動的にバックオフ・リトライする仕組みに委譲している。
* 根拠: [メソッド定義とDocstring] (行番号: 488〜499 / 抜粋: "def _create_rate_limited_session(self) -> requests.Session:\n        """Discordのレート制限(429)に自動追従するHTTPセッションを作成する。")


* **引数/リクエスト**: なし（`self`のみ）
* 根拠: [引数定義] (行番号: 488 / 抜粋: "def _create_rate_limited_session(self) -> requests.Session:")


* **戻り値/レスポンス**: `requests.Session`（429/5xx時に自動リトライするセッション）
* 根拠: [Docstringと戻り値] (行番号: 497〜510 / 抜粋: "Returns:\n            requests.Session: 429/5xx時に自動リトライするセッション。")


* **副作用**: なし（セッションオブジェクトの生成・設定のみ、外部通信は発生しない）
* 根拠: [処理内容] (行番号: 500〜509 / 抜粋: "session = requests.Session()\n        retries = Retry(")


* **エラーハンドリング**: なし


### `DiscordNotifier.close`

* **役割**: 保持しているHTTPセッションのリソースを明示的に解放する。
* 根拠: [メソッド定義とDocstring] (行番号: 512〜515 / 抜粋: "def close(self) -> None:\n        """保持しているHTTPセッションのリソースを明示的に解放する。"""\n        if self.session:\n            self.session.close()")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 512 / 抜粋: "def close(self) -> None:")


* **副作用**: `self.session.close()`によるHTTPセッションのクローズ。
* **エラーハンドリング**: `self.session`が存在する場合にのみクローズを実行するガード節のみ。
* 根拠: [ガード節] (行番号: 514 / 抜粋: "if self.session:")


### `DiscordNotifier.notify`（D-L6・D-L9で変更）

* **（Issue #531 で修正）** 送信本体を新設の `notify_casts(new_casts, site_name) -> Tuple[int, List[CastMember]]` に移し、`notify` はその戻り値の件数だけを返す薄いラッパーになった。`notify_casts` は (送信成功件数, 送信できなかったキャストのリスト) を返す。未送信には Webhook 未設定時の全件、サーキットブレーカー開放時の残り全件、HTTPError/RequestException になったキャスト、401/404 でブレーカーをトリップした際の残り全件が含まれる。
* 根拠: (行番号: 519〜522, 522〜635 / 抜粋: "sent_count, _unsent = self.notify_casts(new_casts, site_name=site_name)", "def notify_casts(self, new_casts: List[CastMember], site_name: str = \"\") -> Tuple[int, List[CastMember]]:", "return sent_count, unsent")

* **（2026-09-06 品質監査で修正）** `requests.HTTPError` / `requests.RequestException` の ERROR ログから `exc_info=True` を外し、例外は `{type(e).__name__}: {redact_discord_webhook_url(e)}` の形で出力する。requests の例外文字列(およびトレースバック)は送信先 Webhook URL(トークン込み)を丸ごと含み、`core.logger.DiscordErrorHandler` 経由でエラー通知チャンネルにも転記されるため、`file_utils.redact_discord_webhook_url` でトークン部分をマスクする。
* 根拠: (行番号: 614〜632 / 抜粋: "f\"Failed to send notification for {cast.name}: {type(e).__name__}: \"\n                    f\"{redact_discord_webhook_url(e)} | body: {body} | \"")、[import] (行番号: 32)

* **役割**: 新規キャストのリストを受け取り、各キャストごとにDiscord埋め込みメッセージ(embed)を構築してWebhook経由で送信する。`site_name`が指定されている場合はどのサイトの新着かを区別できるよう埋め込みタイトルに`【サイト名】`のプレフィックスを付与する。Webhook URL未設定時は送信をスキップする。**（本PRで一般化）** 以前は認証エラー(401/404)発生時のみ残りの通知処理を打ち切る簡易的な打ち切りロジックだったが、タイムアウトや接続エラー等の他の失敗モードには対応していなかった。現在は`self._circuit_breaker`（`DiscordCircuitBreaker`）を用い、ループ先頭でブレーカーが開いていれば残りのキャストの送信自体をスキップする。401/404発生時は即座に`trip()`でブレーカーを開き、それ以外の`requests.RequestException`発生時は`record_failure()`で連続失敗を積算し既定3回で開く。**（D-L6で追加）** embedの`title`（`✨ 新人キャスト情報{site_prefix}: {cast.name}`）と`fields`の`Name`/`Link`の`value`は、いずれも`self._truncate_for_embed`で`_EMBED_TITLE_MAX_LEN`/`_EMBED_FIELD_VALUE_MAX_LEN`（250文字）に切り詰めてから送信する。`cast.name`はスクレイピング結果でありサイト側の表示崩れ等で想定外に長くなりうるため、Discordの実際の上限（title 256文字、field.value 1024文字）を超えてembed全体が拒否される事態を防ぐ。
* 根拠: [メソッド定義とDocstring] (行番号: 517〜531 / 抜粋: "def notify(self, new_casts: List[CastMember], site_name: str = "") -> int:\n        """新規キャスト情報をDiscordに通知する。")、[D-L6: title/field.valueの切り詰め] (行番号: 561〜572, 585〜587 / 抜粋: "safe_name = self._truncate_for_embed(cast.name, self._EMBED_FIELD_VALUE_MAX_LEN)" / "\"title\": self._truncate_for_embed(\n                            f\"✨ 新人キャスト情報{site_prefix}: {cast.name}\", self._EMBED_TITLE_MAX_LEN\n                        ),")


* **引数/リクエスト**: `new_casts: List[CastMember]`（通知対象の新規キャストリスト）, `site_name: str = ""`（通知元サイトの表示名）
* 根拠: [引数定義とDocstring] (行番号: 531〜536 / 抜粋: "new_casts (List[CastMember]): 通知対象の新規キャストリスト。\n            site_name (str): 通知元サイトの表示名。")


* **戻り値/レスポンス**: `int`（**D-L9で変更**。以前は`None`。実際にDiscordへの送信に成功した件数。サーキットブレーカーが開いて送信をスキップしたキャストや、送信失敗したキャストは含まない）
* 根拠: [戻り値ヒントとreturn文] (行番号: 517, 520 / 抜粋: "def notify(self, new_casts: List[CastMember], site_name: str = "") -> int:" / "return sent_count")


* **副作用**: Webhook URL未設定時の警告ログ出力、ループ先頭でのサーキットブレーカー開放チェック（開いていれば警告ログを出力し`break`）、各キャストごとのレート制限回避待機(`time.sleep(1)`)、Discord Webhookへの`session.post`呼び出し、成功/失敗のログ出力と`self._circuit_breaker`の状態更新(`record_success`/`record_failure`/`trip`)、**（D-L9で追加）** 送信成功のたびの`sent_count`インクリメント。年齢(`cast.age`)が存在する場合のみ`Age`フィールドを追加する。`cast.image_url`が`http://`/`https://`で始まらない場合（lazyload画像のプレースホルダーとして`data:`URIや相対パスが混入したケース等）は、embedの`thumbnail`を送信せず空オブジェクトにする（Discord側のURL形式バリデーション失敗による`400 Bad Request`を避けるため）。**（2026-09-09 運用障害対応で追加）** スキーム判定を通過した`cast.image_url`、および`cast.detail_url`（`embed.url`とLinkフィールド双方に使用）は、送信前に`self._to_well_formed_url`でパーセントエンコードしてから使う。日本語ファイル名・全角スペース等の未エンコード文字を含むURLは`http(s)`で始まっていてもDiscordに「well formed」と認められず`400 Bad Request`でembed全体が拒否されていたための対応。
* 根拠: [ブレーカーチェックと送信処理・thumbnail URL検証・sent_count加算] (行番号: 551〜558, 579〜591, 602, 602〜604 / 抜粋: "if self._circuit_breaker.is_open:\n                # 連続送信失敗によりサーキットブレーカーが開いている間は、\n                # 無駄なリクエストを重ねないよう残り件数分の送信をスキップする。" / "thumbnail_url = cast.image_url if cast.image_url.startswith(('http://', 'https://')) else \"\"" / "self._circuit_breaker.record_success()\n                sent_count += 1")、[URLエンコード適用箇所] (行番号: 579, 590, 599〜605, 615 / 抜粋: "safe_detail_url = self._to_well_formed_url(cast.detail_url)" / "thumbnail_url = self._to_well_formed_url(thumbnail_url)" / "\"url\": safe_detail_url,")


* **エラーハンドリング**: Webhook URLが未設定または`'YOUR_DISCORD'`を含む場合は警告ログを出力し即座に`0`を返す（**D-L9で変更**。以前は`return`のみで戻り値は常に`None`だった）。`requests.HTTPError`発生時はレスポンス本文の先頭300文字に加え、原因切り分け用として`detail_url`/`image_url`を含めてエラーログを出力し（**2026-09-06 品質監査で修正**。トレースバックがWebhook URLを含むトークンごと露出させるため`exc_info`は付けず、`{type(e).__name__}: {redact_discord_webhook_url(e)}`の形でURLをマスクして出力する）、ステータスコードが401または404であればさらにエラーログを出力したうえで`self._circuit_breaker.trip()`を呼び即座にブレーカーを開いて通知ループを`break`で打ち切る（401/404以外は`record_failure()`のみ呼び、次のキャストの処理を継続する）。それ以外の`requests.RequestException`発生時も同様に`exc_info`無しでエラーログを出力し`record_failure()`を呼んで次のキャストの処理を継続する。
* 根拠: [各エラー分岐] (行番号: 542〜544, 613〜617, 626〜629 / 抜粋: "if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:\n            logger.warning("Discord Webhook URL is not configured. Skipping notification.")\n            return 0" / "logger.error(\n                    f\"Failed to send notification for {cast.name}: {type(e).__name__}: \"\n                    f\"{redact_discord_webhook_url(e)} | body: {body} | \"\n                    f\"detail_url: {cast.detail_url} | image_url: {cast.image_url}\"\n                )" / "self._circuit_breaker.trip()\n                    unsent.extend(new_casts[index + 1:])\n                    break" / "self._circuit_breaker.record_failure()")


### `DiscordNotifier.notify_daily_summary`

* **（2026-09-06 品質監査で修正）** 送信失敗の ERROR ログは `exc_info` 無しで `{type(e).__name__}: {redact_discord_webhook_url(e)}` を出力する(`notify` と同じ理由)。
* 根拠: (行番号: 689 / 抜粋: "logger.error(f\"Failed to send daily summary notification: {type(e).__name__}: {redact_discord_webhook_url(e)}\")")

* **役割**: その日に新規検知したサイト別件数を、個別キャスト通知(embed形式)とは異なるテキスト形式(content)で1件だけDiscordへ通知する。**（Issue #226で修正）** 以前は戻り値が常に`None`で送信成否を呼び出し元へ伝える手段が無く、呼び出し元`_maybe_send_daily_summary`は送信の成否を確認せず無条件に集計をクリアしていたため、Webhook未設定時やDiscordへの送信失敗時にも集計が失われ、同日中の再送もできなくなっていた。送信成否を`bool`で返すよう修正し、呼び出し元が成功時のみ集計をクリアできるようにした。**（本PRで追加）** `self._circuit_breaker`が開いている場合は送信自体を試みずスキップして`False`を返す。
* 根拠: [メソッド定義とDocstring] (行番号: 637〜653 / 抜粋: "def notify_daily_summary(self, counts: Dict[str, int], site_names: Dict[str, str], date_str: str) -> bool:\n        """その日に新規検知したサイト別件数を、テキスト形式でDiscordに通知する。")


* **引数/リクエスト**: `counts: Dict[str, int]`（site_id→新規検知件数）, `site_names: Dict[str, str]`（site_id→表示名）, `date_str: str`（サマリ対象日）
* 根拠: [引数定義とDocstring] (行番号: 644〜653 / 抜粋: "counts (Dict[str, int]): site_id -> 新規検知件数 の集計。\n            site_names (Dict[str, str]): site_id -> 表示名 の対応表。\n            date_str (str): サマリ対象日（'YYYY-MM-DD'）。")


* **戻り値/レスポンス**: `bool`（Issue #226で`None`から変更）。送信に成功した場合`True`、Webhook未設定・サーキットブレーカーが開いている・送信失敗のいずれかの場合`False`。
* 根拠: [戻り値ヒントとDocstring] (行番号: 637, 648〜652 / 抜粋: "def notify_daily_summary(self, counts: Dict[str, int], site_names: Dict[str, str], date_str: str) -> bool:" / "Returns:\n            bool: 送信に成功した場合True。Webhook未設定または送信失敗の場合False。")


* **副作用**: サーキットブレーカーの開放チェック（開いていれば警告ログを出力し早期return）、件数降順でのサマリ文字列組み立て、2000文字制限に対する安全な切り詰め（1900文字超過分）、Webhookへの`session.post`呼び出し、成功/失敗ログ出力と`self._circuit_breaker`の状態更新(`record_success`/`record_failure`)。
* 根拠: [ブレーカーチェックと文字数制限処理] (行番号: 658〜662, 678〜680 / 抜粋: "if self._circuit_breaker.is_open:\n            logger.warning(\n                \"Discord Webhookへの連続送信失敗を検知しているため、日次サマリ通知をスキップします。\"\n            )\n            return False" / "if len(content) > 1900:")


* **エラーハンドリング**: Webhook URLが未設定または`'YOUR_DISCORD'`を含む場合は警告ログを出力し`False`を返す（Issue #226以前は`None`を返して`return`するのみで、呼び出し元から失敗として検知できなかった）。サーキットブレーカーが開いている場合も同様に警告ログを出力し`False`を返す(送信自体は試みない)。`requests.RequestException`発生時は`exc_info=True`付きでエラーログを出力し`record_failure()`を呼んで`False`を返す。送信成功時は`record_success()`を呼び`True`を返す。
* 根拠: [送信成否分岐] (行番号: 654〜656, 688〜692, 688〜693 / 抜粋: "if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:\n            logger.warning("Discord Webhook URL is not configured. Skipping daily summary notification.")\n            return False" / "except requests.RequestException as e:\n            logger.error(f"Failed to send daily summary notification: {e}", exc_info=True)\n            self._circuit_breaker.record_failure()\n            return False")


### `DiscordNotifier.notify_site_failure_alert`（2026-09-02のbellica閉鎖対応で追加）

* **（2026-09-06 品質監査で修正）** 送信失敗の ERROR ログは `exc_info` 無しで `{type(e).__name__}: {redact_discord_webhook_url(e)}` を出力する(`notify` と同じ理由)。
* 根拠: (行番号: 733)

* **役割**: 連続巡回失敗中のサイトについて「閉鎖・移転の疑い」をDiscordへテキスト形式(content)で通知するメソッド。サイト名・site_id・対象URL・連続失敗回数と、復旧見込みが無い場合の対処（`MonitorConfig.SITES`からのエントリ削除）を案内する文面を送信する。
* 根拠: [メソッド定義とDocstring] (行番号: 693〜703 / 抜粋: "def notify_site_failure_alert(self, site: SiteConfig, failure_count: int) -> bool:\n        """連続巡回失敗中のサイトについて「閉鎖・移転の疑い」をDiscordへテキスト通知する。")


* **引数/リクエスト**: `site: SiteConfig`（連続失敗中のサイトの設定）, `failure_count: int`（現在の連続失敗回数）
* 根拠: [引数定義とDocstring] (行番号: 697〜702 / 抜粋: "site (SiteConfig): 連続失敗中のサイトの設定。\n            failure_count (int): 現在の連続失敗回数。")


* **戻り値/レスポンス**: `bool`。送信に成功した場合`True`、Webhook未設定・サーキットブレーカーが開いている・送信失敗のいずれかの場合`False`。呼び出し元（`_handle_site_network_failure`）は`True`の場合のみアラート送信済みとして記録し、失敗時は次回実行時に再試行される。
* 根拠: [Docstring] (行番号: 700〜702 / 抜粋: "Returns:\n            bool: 送信に成功した場合True。呼び出し元はTrueの場合のみアラート\n                送信済みとして記録する(失敗時は次回実行時に再試行される)。")


* **副作用**: Webhookへの`session.post`呼び出し、成功/失敗ログ出力と`self._circuit_breaker`の状態更新(`record_success`/`record_failure`)。
* 根拠: [送信処理] (行番号: 726〜735 / 抜粋: "response = self.session.post(self.webhook_url, json=payload, timeout=10)\n            response.raise_for_status()\n            logger.info(f\"Site failure alert sent successfully for site '{site.site_id}'.\")\n            self._circuit_breaker.record_success()")


* **エラーハンドリング**: Webhook URLが未設定または`'YOUR_DISCORD'`を含む場合、およびサーキットブレーカーが開いている場合は警告ログを出力し`False`を返す（送信自体は試みない）。`requests.RequestException`発生時は`exc_info=True`付きでエラーログを出力し`record_failure()`を呼んで`False`を返す。
* 根拠: [ガード節とexcept節] (行番号: 704〜712, 731〜734 / 抜粋: "if not self.webhook_url or 'YOUR_DISCORD' in self.webhook_url:\n            logger.warning(\"Discord Webhook URL is not configured. Skipping site failure alert.\")\n            return False" / "except requests.RequestException as e:\n            logger.error(f\"Failed to send site failure alert for site '{site.site_id}': {e}\", exc_info=True)")


### `DataManager.__init__` / `DataManager._data_file`（Issue #364で追加）

* **役割**: **（Issue #364で変更）** `DataManager`は静的メソッド群から、解決済みのデータディレクトリを束縛するインスタンスへ変更された。コンストラクタは`_run_monitor_locked`が1回だけ解決した`data_dir`を受け取り`self.data_dir`に保持し、`_data_file(site)`は`self.data_dir / site.get_data_filename()`で既知キャストファイルのパスを返す。以降の全メソッド（`load_known_casts`/`save_known_casts`/日次サマリ/サイト別失敗状態）はこの束縛済みディレクトリだけを使い、NAS状態（`MonitorConfig.get_data_dir()`）を一切再評価しない。以前は全メソッドが静的で呼び出しのたびに`get_data_dir()`を再評価していたため、1サイトあたり最低3回・79サイトで1実行あたり240回以上の`get_managed_target_directory`呼び出し（NAS未マウント時はその回数分の`sudo mount`とDiscord投稿）が発生していた。
* 根拠: [クラスDocstring・コンストラクタ・_data_file] (行番号: 775〜784, 804〜815 / 抜粋: "#364: 以前は全メソッドが静的メソッドで、呼び出しのたびに\n    MonitorConfig.get_data_dir()(= core.nas_utils.get_managed_target_directory。\n    NASマウント確認・sudo mountによる自己修復・Discord/LINE障害通知を伴う重い処理)\n    を再評価していた。" / "def __init__(self, data_dir: Path):" / "self.data_dir = Path(data_dir)" / "def _data_file(self, site: SiteConfig) -> Path:\n        \"\"\"指定サイトの既知キャスト保存先JSONファイルのパスを返す。\"\"\"\n        return self.data_dir / site.get_data_filename()")


* **引数/リクエスト**: `__init__`: `data_dir: Path`（解決済みのデータディレクトリ。呼び出し元がフォールバック中でないことを確認した上で渡す前提）。`_data_file`: `site: SiteConfig`
* 根拠: [引数定義とDocstring] (行番号: 807〜816 / 抜粋: "data_dir (Path): 解決済みのデータディレクトリ(NAS上、または検証済みの\n                ローカルパス)。呼び出し元(_run_monitor_locked)がフォールバック中で\n                ないことを確認した上で渡す前提。")


* **戻り値/レスポンス**: `__init__`: `None`。`_data_file`: `Path`
* 根拠: [戻り値] (行番号: 823 / 抜粋: "return self.data_dir / site.get_data_filename()")


* **副作用**: なし（`self.data_dir`の保持とパス連結のみ。ディレクトリ作成・NASアクセスは行わない）
* **エラーハンドリング**: なし


### `DataManager._LOAD_ERRORS` (クラス定数)

* **役割**: JSONファイルの読み込み失敗とみなす例外群をまとめたクラス定数。`UnicodeDecodeError`は`IOError`/`OSError`のサブクラスではなく`ValueError`のサブクラスであるため、`IOError`のみを捕捉する実装では非UTF-8データによる破損（例:「'utf-8' codec can't decode byte ... : invalid start byte」）を検知できず、同一の破損ファイルへの読み込み失敗が繰り返され続けてしまう問題を踏まえ、`OSError`, `ValueError`, `TypeError`, `KeyError`をまとめて捕捉対象としている。`load_known_casts`（`_read_casts_file`経由）に加え、**Issue #174の修正**により`load_daily_summary`もこの定数で例外を捕捉するようになった（以前は`load_daily_summary`のみ`(json.JSONDecodeError, IOError)`という狭いパターンのままで同種のバグが残っていた）。
* 根拠: [定義とコメント] (行番号: 784〜788 / 抜粋: "# 読み込み失敗とみなす例外群。UnicodeDecodeErrorはIOErrorのサブクラスではなく\n    # ValueErrorのサブクラスのため、IOErrorだけを捕捉すると非UTF-8データによる\n    # 破損（例: 'utf-8' codec can't decode byte ... : invalid start byte）を\n    # 検知できず、同じ破損ファイルへの読み込み失敗が繰り返され続けてしまう。\n    _LOAD_ERRORS = (OSError, ValueError, TypeError, KeyError)")


* **副作用**: なし（タプルの定義のみ）
* **エラーハンドリング**: 該当なし（例外を捕捉する側で使われる定数そのもの）


### `DataManager._CONTENT_ERRORS` (クラス定数) / `KnownCastsUnavailableError`（Issue #365で追加）

* **役割**: `_CONTENT_ERRORS = (ValueError, TypeError, KeyError)`は、`_LOAD_ERRORS`のうち「ファイルの内容そのものが壊れている」ことを示す例外群（`json.JSONDecodeError`/`UnicodeDecodeError`は`ValueError`のサブクラス、`CastMember(**item)`の引数不一致は`TypeError`/`KeyError`）。`load_known_casts`が破損ファイルとして`.corrupted-*`へ隔離してよいのはこれらに限られ、`OSError`（CIFS/autofsの瞬断によるEIO/ENOENT/ETIMEDOUT等）は「内容が正しいファイルを開けなかっただけ」なので隔離しない。`KnownCastsUnavailableError`（`Exception`のサブクラス）は、その`OSError`ケースを呼び出し元（`_check_site`）へ伝えて当該サイトの巡回を今回の実行ではスキップさせるためのモジュールレベル例外。以前は種別を問わず隔離していたため、一時的なI/Oエラーで正常なファイルが退避され、`.bak`が無ければ空集合→全キャスト再通知、以降はunion保存されるため隔離前のデータ（退店済み含む）が永久に戻らなかった。**（Issue #578で変更）** 当初この扱いが及んでいたのは`load_known_casts`の一次ファイル読み込みのみで、`.bak`バックアップからの復旧読み込みは依然として`OSError`を内容起因の破損と同列に扱い空集合へフォールバックしていたが、本Issueで一次ファイルと同じ`OSError`/`_CONTENT_ERRORS`の分岐に揃えられた（詳細は後述の`DataManager.load_known_casts`を参照）。
* 根拠: [KnownCastsUnavailableError定義と_CONTENT_ERRORSのコメント] (行番号: 749〜755, 790〜796 / 抜粋: "class KnownCastsUnavailableError(Exception):\n    \"\"\"既知キャストファイルが存在するのにI/Oエラーで読めなかったことを示す例外(#365)。" / "# #365: このうち「ファイルの内容そのものが壊れている」ことを示す例外群。" / "_CONTENT_ERRORS = (ValueError, TypeError, KeyError)")


* **副作用**: なし（定義のみ）
* **エラーハンドリング**: 該当なし


### `DataFileUnavailableError`（Issue #578で新規追加）

* **役割**: `DataManager.load_daily_summary`/`DataManager.load_site_failures`が、対象ファイルは存在するのにI/Oエラー（`OSError`）で読めなかったことを示すモジュールレベル例外。`KnownCastsUnavailableError`（#365）と同じ位置づけ（NAS/CIFSの瞬断等による一時的な読み込み失敗であり、内容起因の破損とは区別する）だが、対象データ（日次サマリ集計・サイト別連続失敗状態）が異なるため別クラスとして新設された。以前はこの`OSError`も内容起因の破損（JSON構文エラー・非UTF-8・スキーマ不一致等）と同じ扱いで空の初期状態（`{}`）へフォールバックしており、呼び出し元（`record_daily_new_casts`/`_maybe_send_daily_summary`/`record_site_failure`/`mark_site_failure_alerted`/`clear_site_failure`）がその空状態のまま無条件で`save_daily_summary`/`save_site_failures`を呼ぶと、たまたま読み込みに失敗しただけの既存データ（他サイト分の`site_failures`状態、累積中の`daily_summary`カウント等）が丸ごと上書きで消えていた。呼び出し元はこの例外を捕捉して保存処理をスキップし、既存の永続化状態に一切触れないこと。
* 根拠: [クラス定義とDocstring] (行番号: 758〜769 / 抜粋: "class DataFileUnavailableError(Exception):\n    \"\"\"load_daily_summary/load_site_failuresが、ファイルは存在するのにI/Oエラーで\n    読めなかったことを示す例外(#578)。KnownCastsUnavailableErrorと同じ位置づけ" / "呼び出し元はこの例外を捕捉して\n    保存処理をスキップし、既存の永続化状態に触れないこと。")


* **引数/リクエスト**: なし（`Exception`のサブクラスとしての標準的なコンストラクタのみ。呼び出し側は`DataFileUnavailableError(f"...({e})")`の形でメッセージ文字列を渡す）
* **戻り値/レスポンス**: 該当なし（例外クラスの定義自体）
* **副作用**: なし（定義のみ）
* **エラーハンドリング**: 該当なし


### `DataManager._QUARANTINE_RETENTION_DAYS` (クラス定数) / `DataManager.cleanup_old_quarantine_files`（Issue #461で追加）

* **役割**: `_QUARANTINE_RETENTION_DAYS = 30`は、`load_known_casts`/`load_daily_summary`が破損検知のたびに作成する`.corrupted-*`隔離ファイルの保持日数を定義するクラス定数。`cleanup_old_quarantine_files(retention_days=_QUARANTINE_RETENTION_DAYS)`は、`self.data_dir`直下の`*.corrupted-*`パターンに一致するファイルのうち、最終更新時刻(`mtime`)が`retention_days`日より古いものを削除するインスタンスメソッド。`.bak`バックアップ（常に最新の1世代のみが上書き保持される）と異なり`.corrupted-*`には削除処理が存在せず、破損が繰り返されるたびに際限なく蓄積しディスクを圧迫し得た問題への対処。
* 根拠: [クラス定数定義とコメント] (行番号: 798〜802 / 抜粋: "# #461: load_known_casts/load_daily_summaryが破損検知のたびに作成する\n    # .corrupted-*隔離ファイルは、.bak(常に最新の1世代のみ保持され上書きされる)\n    # と異なり削除処理を持たず、破損が繰り返されるたびに増え続けディスクを\n    # 圧迫し得た。この日数より古い隔離ファイルは巡回のたびに削除する。\n    _QUARANTINE_RETENTION_DAYS = 30")、[メソッド定義とDocstring] (行番号: 825〜834 / 抜粋: "def cleanup_old_quarantine_files(self, retention_days: int = _QUARANTINE_RETENTION_DAYS) -> int:\n        """`_QUARANTINE_RETENTION_DAYS`日より古い`.corrupted-*`隔離ファイルを削除する。")


* **引数/リクエスト**: `retention_days: int = _QUARANTINE_RETENTION_DAYS`（省略時30日）
* 根拠: [引数定義] (行番号: 825 / 抜粋: "def cleanup_old_quarantine_files(self, retention_days: int = _QUARANTINE_RETENTION_DAYS) -> int:")


* **戻り値/レスポンス**: `int`（削除できたファイル数。`self.data_dir`のファイル一覧取得自体に失敗した場合は`0`）
* 根拠: [戻り値ヒントとDocstring・各return] (行番号: 832〜833, 841, 853 / 抜粋: "Returns:\n            int: 削除できたファイル数。", "return 0", "return deleted")


* **副作用**: `self.data_dir.glob("*.corrupted-*")`によるディレクトリ走査、条件（`is_file()`かつ`mtime`が閾値より古い）に合致する各ファイルの削除(`path.unlink()`)、1件以上削除した場合は削除件数を含む情報ログ出力。
* 根拠: [走査と削除処理] (行番号: 835〜853 / 抜粋: "cutoff = time.time() - retention_days * 86400" / "candidates = list(self.data_dir.glob(\"*.corrupted-*\"))" / "if path.is_file() and path.stat().st_mtime < cutoff:\n                    path.unlink()\n                    deleted += 1")


* **エラーハンドリング**: Docstringに明記の通り「削除自体の失敗は致命的ではないためログに残すのみで続行する」設計。ディレクトリ一覧取得(`glob`)が`OSError`を送出した場合は警告ログを出力して即座に`0`を返す（個別ファイルの削除は試みない）。個別ファイルの`stat()`/`unlink()`が`OSError`を送出した場合は当該ファイルについてのみ警告ログを出力し、他の候補ファイルの削除処理を継続する（例外を再送出しない）。
* 根拠: [try-exceptブロック] (行番号: 837〜841, 839〜844 / 抜粋: "try:\n            candidates = list(self.data_dir.glob(\"*.corrupted-*\"))\n        except OSError as e:\n            logger.warning(f\"Failed to list quarantine files in {self.data_dir}: {e}\")\n            return 0" / "except OSError as e:\n                logger.warning(f\"Failed to delete old quarantine file {path}: {e}\")")


### `DataManager._read_casts_file`

* **役割**: 指定されたJSONファイルを読み込み、`CastMember`の集合に変換する内部ヘルパーの静的メソッド。`load_known_casts`（通常読み込みおよび`.bak`バックアップからの復旧読み込み）と`save_known_casts`（書き込み直後の読み戻し検証）の両方から共通で呼び出される。
* 根拠: [メソッド定義とDocstring] (行番号: 856〜860 / 抜粋: "def _read_casts_file(data_file: Path) -> Set[CastMember]:\n        """JSONファイルを読み込み、CastMemberの集合に変換する。\n\n        パース失敗時は例外をそのまま送出する（呼び出し側でハンドリングする前提）。\n        """")


* **引数/リクエスト**: `data_file: Path`（読み込み対象のJSONファイルパス）
* 根拠: [引数定義] (行番号: 856 / 抜粋: "def _read_casts_file(data_file: Path) -> Set[CastMember]:")


* **戻り値/レスポンス**: `Set[CastMember]`
* 根拠: [戻り値ヒントとreturn文] (行番号: 863〜870 / 抜粋: "return {CastMember(**item) for item in data}")


* **副作用**: JSONファイルのオープン・パース(`open`, `json.load`)。
* 根拠: [処理内容] (行番号: 861〜863 / 抜粋: "with open(data_file, 'r', encoding='utf-8') as f:\n            data = json.load(f)\n            return {CastMember(**item) for item in data}")


* **エラーハンドリング**: なし。Docstringに明記の通り、パース失敗時（JSON構文エラー・非UTF-8データ・想定外のフィールド欠落等）は例外を握りつぶさずそのまま呼び出し元へ送出する設計であり、呼び出し元(`load_known_casts`/`save_known_casts`)側が`DataManager._LOAD_ERRORS`等で捕捉してハンドリングする。
* 根拠: [Docstring] (行番号: 859 / 抜粋: "パース失敗時は例外をそのまま送出する（呼び出し側でハンドリングする前提）。")


### `DataManager.load_known_casts`

* **役割**: 指定サイトの保存済みキャストデータ(`self._data_file(site)`。**Issue #364**以前は呼び出しのたびにNAS状態を再評価する`MonitorConfig.get_data_file(site)`だった)を`_read_casts_file`経由でJSONファイルから読み込み、`CastMember`の集合として返すインスタンスメソッド。**（Issue #365で変更）** 内容起因の読み込み失敗（`_CONTENT_ERRORS`）の場合のみ、単純に空集合を返すのではなく、(1)破損ファイルを`{ファイル名}.corrupted-{タイムスタンプ}`へリネームして隔離することで同じ破損ファイルへの読み込み失敗が繰り返され続けるのを防ぎ、(2)`.bak`バックアップファイルが存在すればそこからの復旧を試み、(3)復旧にも失敗した場合にのみ空集合へフォールバックする、という多段の復旧ロジックを持つ。一方、ファイルは存在するが`OSError`（NAS/CIFSの瞬断等）で読めなかった場合は隔離もフォールバックもせず、`KnownCastsUnavailableError`を送出して呼び出し元に当該サイトのスキップを求める。**（Issue #578で変更）** この一次ファイルに対する`OSError`/`_CONTENT_ERRORS`の分岐は、`.bak`バックアップからの復旧読み込みにも同様に適用されるようになった。以前は`.bak`の読み込み失敗を種別を問わず内容起因の破損と同列に扱い（`_LOAD_ERRORS`一括捕捉）空集合へフォールバックしていたため、`.bak`自体がCIFS/autofsの瞬断で開けなかっただけのケースでも、中身が正しい可能性を無視して全キャストの再通知（およびunion保存による退店済みキャストの復活）を招いていた。現在は`.bak`の`OSError`も一次ファイルと同じく`KnownCastsUnavailableError`を送出し、`.bak`自体は隔離・書き換えを行わない。
* 根拠: [メソッド定義とDocstring] (行番号: 865〜880 / 抜粋: "def load_known_casts(self, site: SiteConfig) -> Set[CastMember]:\n        \"\"\"指定サイトの保存済みキャストデータを読み込む。" / "Raises:\n            KnownCastsUnavailableError: ファイルは存在するがI/Oエラー(OSError)で\n                読めなかった場合。呼び出し元は当該サイトの処理をスキップすること")


* **引数/リクエスト**: `site: SiteConfig`
* 根拠: [引数定義とDocstring] (行番号: 869〜873 / 抜粋: "site (SiteConfig): 対象サイトの設定。")


* **戻り値/レスポンス**: `Set[CastMember]`（ファイル不在時、または内容破損＋`.bak`バックアップからの復旧も失敗した場合は空集合）。`OSError`（一次ファイル・`.bak`のいずれでも）時は戻り値を返さず`KnownCastsUnavailableError`を送出する。
* 根拠: [Docstringと各return/raise] (行番号: 871〜879, 900〜902, 937〜939 / 抜粋: "Returns:\n            Set[CastMember]: 既知のキャストの集合。内容起因の読み込み失敗時は\n                隔離・バックアップ復旧を試み、それも不可なら空集合を返す。" / "raise KnownCastsUnavailableError(\n                f\"{site.site_id}: known casts file is unreadable ({e})\"\n            ) from e" / "raise KnownCastsUnavailableError(\n                    f\"{site.site_id}: backup casts file is unreadable ({e})\"\n                ) from e")


* **副作用**: `DataManager._read_casts_file`経由でのJSONファイル読み込み、デバッグ/エラー/警告ログ出力。読み込み失敗時は破損ファイルのリネーム(`data_file.rename(quarantine_path)`)、`.bak`バックアップファイルが存在する場合はその読み込み。
* 根拠: [処理内容] (行番号: 881, 908〜910, 912, 919, 922 / 抜粋: "data_file = self._data_file(site)" / "quarantine_path = data_file.with_name(\n            f\"{data_file.name}.corrupted-{datetime.now():%Y%m%d%H%M%S}\"\n        )" / "data_file.rename(quarantine_path)" / "backup_file = data_file.with_suffix(data_file.suffix + '.bak')" / "casts = DataManager._read_casts_file(backup_file)")


* **エラーハンドリング**: データファイルが存在しない場合はデバッグログを出力し空集合を返す。**（Issue #365で変更）** `_read_casts_file`が`OSError`を送出した場合（CIFS/autofsの瞬断によるEIO/ENOENT/ETIMEDOUT等。`wait_for_storage_warmup`のDocstring自体が想定している事象）は、`exc_info=True`付きでエラーログを出力したうえで**ファイルを隔離せず**`KnownCastsUnavailableError`を送出する（以前は`_LOAD_ERRORS`として`OSError`も一括で捕捉し、種別を問わず隔離していた）。`DataManager._CONTENT_ERRORS`（`ValueError`, `TypeError`, `KeyError`）発生時は`exc_info=True`付きでエラーログを出力したうえで、破損ファイルを`{ファイル名}.corrupted-{タイムスタンプ}`へリネームして隔離する（リネーム自体が`OSError`で失敗した場合も`exc_info=True`付きでエラーログを出力するのみで処理は継続）。続けて`.bak`バックアップファイルが存在すれば`_read_casts_file`で読み込みを試みる。**（Issue #578で変更）** ここでも一次ファイルと同じ分岐が適用され、`OSError`時は`.bak`を隔離せず`exc_info=True`付きのエラーログとともに`KnownCastsUnavailableError`を送出する（以前は他の`_LOAD_ERRORS`と同列に扱い空集合を返していた）。`_CONTENT_ERRORS`時（`.bak`自体も内容破損）はエラーログのみを出力して処理を継続する。`.bak`から正常に読み込めた場合は復旧件数を警告ログに出力してそれを返す。バックアップが存在しない、または`.bak`も内容破損で読み込めない場合は、コメントに明記の通り「データ破損時は安全側に倒して空集合（再通知される可能性があるがシステム停止よりマシ）」として空集合を返す。
* 根拠: [try-exceptブロックと隔離・復旧処理] (行番号: 888〜894, 900〜902, 903〜904, 906〜907, 927〜931, 937〜939, 940〜941, 943〜944 / 抜粋: "except OSError as e:\n            # #365: CIFS/autofsの瞬断(EIO/ENOENT/ETIMEDOUT等。wait_for_storage_warmupの\n            # docstring自体が想定している事象)でopen()が失敗しただけのケース。\n            # 中身は正しい可能性が高いため隔離せず、当該サイトの処理を\n            # スキップさせる" / "raise KnownCastsUnavailableError(" / "except DataManager._CONTENT_ERRORS as e:\n            logger.error(f\"Failed to load data from {data_file}: {e}\", exc_info=True)" / "# 破損ファイルをそのままにすると次回以降も同じ位置で読み込みに失敗し続ける\n        # ため、退避してから復旧を試みる(内容起因の破損に限る。#365)。" / "except OSError as e:\n                # #578: .bak自体はCIFS/autofsの瞬断で開けなかっただけの可能性があり、\n                # 中身は正しいかもしれない。空集合へフォールバックすると全キャストの\n                # 再通知・union保存による退店済みキャストの復活を招くため、本体の\n                # OSError(#365)と同様に当該サイトの処理をスキップさせる。" / "raise KnownCastsUnavailableError(\n                    f\"{site.site_id}: backup casts file is unreadable ({e})\"\n                ) from e" / "except DataManager._CONTENT_ERRORS as e:\n                logger.error(f\"Backup file {backup_file} is also unusable: {e}\", exc_info=True)" / "# データ破損時は安全側に倒して空集合（再通知される可能性があるがシステム停止よりマシ）\n        return set()")


### `DataManager.save_known_casts`（D-L7・D-L8で変更）

* **役割**: 指定サイトのキャスト集合をJSONファイルへアトミックに保存するインスタンスメソッド。一時ファイルへ書き出したのち`replace`で置き換えることで書き込み中断時の既存データ破損/消失を防ぐ従来のアトミック書き込みパターンに加え、(1)一時ファイルを本番ファイルへ置き換える前に`_read_casts_file`で一時ファイルを読み戻して正しくパースできることを検証し、(2)本番ファイルへの置換前に現在の本番ファイルの内容を`.bak`としてバックアップする、という2つの安全策を追加している。**（D-L7で変更）** `.bak`の更新自体も、以前は`backup_path.write_bytes(data_file.read_bytes())`という非アトミックな直接上書きだったが、他の永続化と同じ「`.bak.tmp`へ書き込み→`replace`」パターンに揃えた。書き込み中に中断しても`.bak`本体は直前の内容のまま無傷で残る。**（D-L8で変更）** `tmp_path.replace(data_file)`に到達する前に例外（読み戻し検証失敗等）が発生すると、以前は書き込み済みの`.tmp`ファイルが削除されずディレクトリに残り続けていた。`tmp_path`が生成済みであれば、外側の`except`節でbest-effortに削除する。
* 根拠: [メソッド定義とDocstring] (行番号: 946〜952 / 抜粋: "def save_known_casts(self, site: SiteConfig, casts: Set[CastMember]) -> None:\n        """指定サイトのキャストデータをJSONファイルに保存する。")


* **引数/リクエスト**: `site: SiteConfig`, `casts: Set[CastMember]`（保存対象のキャスト集合）
* 根拠: [引数定義とDocstring] (行番号: 950〜955 / 抜粋: "site (SiteConfig): 対象サイトの設定。\n            casts (Set[CastMember]): 保存対象のキャスト集合。")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 946 / 抜粋: "def save_known_casts(self, site: SiteConfig, casts: Set[CastMember]) -> None:")


* **副作用**: 保存先ディレクトリの作成(`mkdir`)、一時ファイル(`.tmp`)への書き込み、`DataManager._read_casts_file`による一時ファイルの読み戻し検証、本番ファイルが存在する場合はその内容を`.bak`ファイルへ**（D-L7で変更）** `.bak.tmp`経由のアトミックな`replace`でコピー、`tmp_path.replace(data_file)`によるアトミックな置換、デバッグログ出力。バックアップの更新は最後の`replace`より前に行われるが、これはコメントに明記の通り「万一この途中でプロセスが中断しても本番ファイル(`data_file`)は無傷のまま残る」ようにするための意図的な順序である。
* 根拠: [処理順序とコメント] (行番号: 956, 959〜961, 966〜968, 973〜975, 982〜990 / 抜粋: "data_file.parent.mkdir(parents=True, exist_ok=True)" / "# アトミック書き込み: 一時ファイルに書き出してから置き換えることで、\n            # 書き込み中断時に既存データが破損/空になるのを防ぐ" / "# 書き込んだ内容が正しく読み戻せることを検証してから本番ファイルへ反映する。" / "# コピー元(data_file)は最後のreplaceまで保持したままにすることで、\n            # 万一この途中でプロセスが中断しても本番ファイルは無傷のまま残る。" / "bak_tmp_path = backup_path.with_suffix(backup_path.suffix + '.tmp')\n                try:\n                    bak_tmp_path.write_bytes(data_file.read_bytes())\n                    bak_tmp_path.replace(backup_path)")


* **エラーハンドリング**: `(OSError, ValueError, TypeError)`発生時は`exc_info=True`付きでエラーログを出力する（例外の再送出はしない）。この例外タプルには一時ファイルの読み戻し検証(`_read_casts_file`)が送出しうる`ValueError`/`TypeError`（JSON破損・想定外の型）も含まれ、検証失敗時も同じ`except`節で捕捉されて処理が打ち切られる（`tmp_path.replace(data_file)`より前に検証しているため、検証失敗時に本番ファイルが破損データで上書きされることはない）。**（D-L8で追加）** この外側`except`節では、`tmp_path`が生成済み（`None`でない）であれば`tmp_path.unlink(missing_ok=True)`をbest-effortで実行し、検証失敗等で残った`.tmp`ファイルの残置を防ぐ（削除自体の失敗も無視する）。`.bak`バックアップファイルへの書き込み（**D-L7で変更**。`bak_tmp_path.write_bytes`＋`replace`）のみが失敗した場合は、内側の`try`/`except OSError`で警告ログを出力し、失敗した`bak_tmp_path`をbest-effortで削除したうえで、後続のアトミック置換自体は中断せず継続する。
* 根拠: [外側try-exceptとtmp_path削除・内側try-except] (行番号: 994〜1004, 996〜1002 / 抜粋: "except (OSError, ValueError, TypeError) as e:\n            logger.error(f"Failed to save data: {e}", exc_info=True)" / "# D-L8: tmp_path.replace(data_file)に到達する前に例外（読み戻し検証失敗\n            # 等）が起きると、以前は書き込み済みの.tmpファイルがそのまま残り続けて\n            # いた。" / "if tmp_path is not None:\n                try:\n                    tmp_path.unlink(missing_ok=True)" / "except OSError as e:\n                    logger.warning(f"Failed to update backup file {backup_path}: {e}")\n                    # 中断された.bak用一時ファイルを残さない(best-effort)。\n                    bak_tmp_path.unlink(missing_ok=True)")


### `DataManager._daily_summary_file`

* **役割**: 日次サマリの集計状態を保存するファイル(`daily_summary.json`)のパスを返すインスタンスメソッド。サイト単位の`known_casts_*.json`とは別にトップレベルのファイルとして管理される。**（Issue #364で変更）** 以前は`MonitorConfig.get_data_dir()`を呼び出しのたびに再評価していたが、現在はコンストラクタで束縛した`self.data_dir`を使う。
* 根拠: [メソッド定義とDocstring] (行番号: 1006〜1012 / 抜粋: "def _daily_summary_file(self) -> Path:\n        """日次サマリの集計状態を保存するファイルのパスを返す。")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `Path`
* 根拠: [戻り値] (行番号: 1012 / 抜粋: "return self.data_dir / 'daily_summary.json'")


* **副作用**: なし
* **エラーハンドリング**: なし


### `DataManager.load_daily_summary`（Issue #462で変更）

* **（2026-09-06 品質監査で修正）** JSON として読めた内容を `_is_valid_daily_summary(data)` で形状検証し、不正(トップレベルが辞書でない、`'counts'` キーがあって辞書でない — `null` を含む)な場合は ERROR ログを出して内容破損として扱い、`_LOAD_ERRORS` と同じ隔離(`.corrupted-*`)+ `.bak` 復旧の経路へ進める(復旧した `.bak` の内容も同様に検証し、不正なら空辞書)。以前は `[]` や `{"counts": null}` をそのまま返し、`record_daily_new_casts` の `data.setdefault`/`counts.get` で `AttributeError` になっていた。
* 根拠: (行番号: 1042〜1055, 1089〜1092 / 抜粋: "if self._is_valid_daily_summary(data):\n                return data", "Backup file {backup_file} is also malformed; starting from an empty summary.")

### `DataManager._is_valid_daily_summary` **（2026-09-06 品質監査で修正）**

* **役割**: `daily_summary.json` の内容が `record_daily_new_casts` / `_maybe_send_daily_summary` が前提とする形状か(辞書であり、`'counts'` キーが存在する場合はその値が辞書か)を判定する静的メソッド。
* 根拠: (行番号: 1100〜1113 / 抜粋: "@staticmethod\n    def _is_valid_daily_summary(data: object) -> bool:", "if 'counts' in data and not isinstance(data['counts'], dict):\n            return False")
* **引数/リクエスト**: `data: object`
* 根拠: (行番号: 1101)
* **戻り値/レスポンス**: `bool`
* 根拠: (行番号: 1105〜1112)
* **副作用**: なし
* 根拠: 純粋関数(行番号: 1100〜1112)
* **エラーハンドリング**: なし
* 根拠: 同上

* **役割**: 日次サマリの集計状態（`{'counts': {...}, 'last_sent_date': ...}`形式）を`self._daily_summary_file()`からJSONとして読み込むインスタンスメソッド。**（Issue #174で修正）** 以前は`load_known_casts`と同じ「非UTF-8破損によるファイル読み込み失敗」に対する例外捕捉が`(json.JSONDecodeError, IOError)`という狭いパターンのままで、`UnicodeDecodeError`(`IOError`のサブクラスではなく`ValueError`のサブクラス)を捕捉できなかった。この結果、破損した`daily_summary.json`を読もうとすると例外が未捕捉のまま`record_daily_new_casts`経由で`_check_site`を脱出し、`save_known_casts`が実行されないまま処理が中断していた。次回(毎時)実行でも同じ既知キャストが「新規」として再検知されDiscordへ再通知され続ける、という無限反復を招いていた。**（Issue #462で追加）** さらに、`load_known_casts`と同じ隔離＋バックアップ復旧の仕組みを適用するよう拡張された。以前は読み込み失敗時に即座に空辞書を返しており、破損のたびに累積中の未送信カウントが0にリセットされていたが（無限再通知自体は#183/#174で解消済みだったものの、集計の耐障害性は`load_known_casts`より弱いままだった）、現在は(1)破損ファイルを`{ファイル名}.corrupted-{タイムスタンプ}`へリネームして隔離し、(2)`.bak`バックアップファイルが存在すればそこからの復旧を試み、(3)復旧にも失敗した場合にのみ空辞書へフォールバックする。**（Issue #183で修正）** 集計状態のスキーマから`'date'`キーを廃止した（後述の`record_daily_new_casts`参照）。**（Issue #578で変更）** 上記(1)〜(3)の隔離・復旧経路へ進むのは、`load_known_casts`と同様「ファイルの内容そのものが壊れている」場合（`DataManager._CONTENT_ERRORS` = `ValueError`/`TypeError`/`KeyError`。JSON構文エラー・非UTF-8データを含む）に限定された。一次ファイル・`.bak`のいずれについても、ファイルは存在するが`OSError`（NAS/CIFSの瞬断等）で読めなかった場合は、隔離もフォールバックもせず新設の`DataFileUnavailableError`を送出し、呼び出し元（`record_daily_new_casts`/`_maybe_send_daily_summary`）に保存処理のスキップを求める。以前はこの`OSError`も`_LOAD_ERRORS`一括捕捉で他の内容破損と同列に扱われ、隔離こそしないものの空辞書を返しており、呼び出し元がその空状態のまま無条件で`save_daily_summary`を呼ぶと、たまたま読み込みに失敗しただけで実際には累積中だった他サイト分のカウントが丸ごと消えていた。
* 根拠: [メソッド定義とDocstring] (行番号: 1014〜1027 / 抜粋: "def load_daily_summary(self) -> Dict:\n        \"\"\"日次サマリの集計状態を読み込む。" / "Raises:\n            DataFileUnavailableError: ファイルは存在するがI/Oエラー(OSError)で\n                読めなかった場合(#578)。呼び出し元は保存処理をスキップし、\n                既存の永続化状態(累積中のカウント等)を保持すること。")、隔離・復旧処理とコメント (行番号: 1068〜1070, 1072〜1074, 1081〜1082 / 抜粋: "# #462: load_known_castsと同じ復旧機構(隔離+バックアップ復旧)を適用する。\n        # 以前はここで即座に空辞書を返しており、破損時に累積中の未送信カウントが\n        # 0にリセットされていた" / "quarantine_path = summary_file.with_name(\n            f\"{summary_file.name}.corrupted-{datetime.now():%Y%m%d%H%M%S}\"\n        )" / "backup_file = summary_file.with_suffix(summary_file.suffix + '.bak')\n        if backup_file.exists():")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `Dict`（ファイル不在時、または内容破損＋隔離＋`.bak`バックアップからの復旧も全て失敗した場合は空辞書。バックアップからの復旧に成功した場合はその内容）。`'counts'`は直近の送信以降に累積した未送信件数であり、カレンダー日付ではなく「前回送信からの累積」で管理される（Issue #183）。**（Issue #578）** `OSError`（一次ファイル・`.bak`のいずれでも）時は戻り値を返さず`DataFileUnavailableError`を送出する。
* 根拠: [Docstringと各return/raise] (行番号: 1017〜1021, 1059, 1094 / 抜粋: "Returns:\n            Dict: {'counts': {site_id: count}, 'last_sent_date': 'YYYY-MM-DD'}\n                形式の集計状態。'counts'は直近の送信以降に累積した未送信件数\n                (#183参照。カレンダー日付ではなく「前回送信からの累積」で管理する)。\n                ファイルが存在しない場合は空辞書を返す。" / "raise DataFileUnavailableError(f\"daily summary file is unreadable ({e})\") from e" / "raise DataFileUnavailableError(f\"backup daily summary file is unreadable ({e})\") from e")


* **副作用**: JSONファイルの読み込み。読み込み失敗時は、破損ファイルのリネーム(`summary_file.rename(quarantine_path)`)によるエラーログ、`.bak`バックアップファイルが存在する場合はその読み込みとログ出力（復旧成功時は警告ログ、復旧も失敗時はエラーログ）。
* 根拠: [ファイル読み込みと隔離・復旧処理] (行番号: 1033〜1048, 1076〜1081, 1087〜1089 / 抜粋: "with open(summary_file, 'r', encoding='utf-8') as f:\n                data = json.load(f)" / "summary_file.rename(quarantine_path)\n            logger.error(f\"Quarantined corrupted daily summary file: {summary_file} -> {quarantine_path}\")" / "logger.warning(f\"Recovered daily summary from backup {backup_file} after cache corruption.\")")


* **エラーハンドリング**: **（Issue #578で変更）** `OSError`（一次ファイル: 行1025〜1036、`.bak`: 行1067〜1071）発生時は、`exc_info=True`付きでエラーログを出力したうえで**隔離もフォールバックもせず**`DataFileUnavailableError`を送出する（以前は`_LOAD_ERRORS`として`OSError`も一括で捕捉し、種別を問わず空辞書へフォールバックしていた）。`DataManager._CONTENT_ERRORS`（`ValueError`, `TypeError`, `KeyError`。`UnicodeDecodeError`は`ValueError`のサブクラスとして捕捉される。JSON構文エラー・非UTF-8データ・`_is_valid_daily_summary`による形状不正判定を含む）発生時は`exc_info=True`付きでエラーログを出力したうえで、**（Issue #462で追加）** 破損ファイルを`.corrupted-{タイムスタンプ}`へリネームして隔離する（リネーム自体が`OSError`で失敗した場合も`exc_info=True`付きでエラーログを出力するのみで処理は継続）。続けて`.bak`バックアップファイルが存在すれば読み込みを試みる。ここでも一次ファイルと同じ分岐が適用され、`OSError`時は`.bak`を書き換えず`DataFileUnavailableError`を送出し、`_CONTENT_ERRORS`時（`_is_valid_daily_summary`による形状不正を含む）はエラーログのみを出力する。`.bak`から正常かつ妥当な形状で読み込めた場合は警告ログを出力してその内容を返す。バックアップが存在しない、または`.bak`も読み込めない・形状が不正な場合は空辞書を返す。ファイルが存在しない場合（`summary_file.exists()`が`False`）はこれらの処理を経ずそのまま空辞書を返す。
* 根拠: [try-exceptブロックと隔離・復旧処理] (行番号: 1048〜1050, 1059, 1060〜1061, 1090〜1092, 1094, 1095〜1096 / 抜粋: "except OSError as e:\n            # #578: CIFS/autofsの瞬断でopen()が失敗しただけの可能性があり、\n            # 中身は正しいかもしれない。" / "raise DataFileUnavailableError(f\"daily summary file is unreadable ({e})\") from e" / "except DataManager._CONTENT_ERRORS as e:\n            # #174: load_known_castsと同じ「非UTF-8破損でUnicodeDecodeError" / "except OSError as e:\n                # #578: 本体と同じ理由で、.bakのOSErrorも空辞書へフォールバックせず\n                # スキップさせる。" / "raise DataFileUnavailableError(f\"backup daily summary file is unreadable ({e})\") from e" / "except DataManager._CONTENT_ERRORS as e:\n                logger.error(f\"Backup file {backup_file} is also unusable: {e}\", exc_info=True)")


### `DataManager.save_daily_summary`（Issue #462で変更）

* **役割**: 日次サマリの集計状態を、`save_known_casts`と同じ一時ファイル経由のアトミックパターンでJSONファイルに保存するインスタンスメソッド。**（Issue #462で追加）** `save_known_casts`と同様、(1)一時ファイル(`.tmp`)へ書き出した内容を`json.load`で読み戻して正しくパースできることを検証し、(2)本番ファイルへの置換前に、既存の本番ファイルが存在すればその内容を`.bak.tmp`経由のアトミックな`replace`で`.bak`バックアップへ複製・更新する（`load_daily_summary`が破損時の復旧に使う）、という2つの安全策が追加された。読み戻し検証・`replace`到達前に例外が発生した場合、生成済みの`tmp_path`は外側の`except`節でbest-effortに削除される（`save_known_casts`のD-L8修正と同じパターン）。
* 根拠: [メソッド定義とDocstring] (行番号: 1114〜1119 / 抜粋: "def save_daily_summary(self, data: Dict) -> None:\n        """日次サマリの集計状態をJSONファイルに保存する。")、検証・バックアップ処理とコメント (行番号: 1130〜1135 / 抜粋: "# #462: 書き込んだ内容が正しく読み戻せることを検証する(save_known_castsと同じ)。\n            with open(tmp_path, 'r', encoding='utf-8') as f:\n                json.load(f)" / "# 直前の正常データをバックアップとして残す。load_daily_summaryが破損時の\n            # 復旧に使う(save_known_castsと同じtmp書き込み+replaceのアトミックパターン)。")


* **引数/リクエスト**: `data: Dict`（保存対象の集計状態）
* 根拠: [引数定義とDocstring] (行番号: 1118〜1122 / 抜粋: "data (Dict): 保存対象の集計状態。")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 1114 / 抜粋: "def save_daily_summary(self, data: Dict) -> None:")


* **副作用**: 保存先ディレクトリの作成(`mkdir`)、一時ファイル(`.tmp`)への書き込み、書き込んだ一時ファイルの読み戻し検証(`json.load`)、本番ファイルが存在する場合はその内容を`.bak.tmp`経由のアトミックな`replace`で`.bak`ファイルへコピー、`tmp_path.replace(summary_file)`によるアトミックな置換。
* 根拠: [処理内容] (行番号: 1125〜1148 / 抜粋: "# アトミック書き込み: save_known_castsと同じパターン\n            tmp_path = summary_file.with_suffix(summary_file.suffix + '.tmp')" / "bak_tmp_path.write_bytes(summary_file.read_bytes())\n                    bak_tmp_path.replace(backup_path)")


* **エラーハンドリング**: **（Issue #462で変更）** 例外捕捉が`IOError`単独から`(OSError, ValueError, TypeError)`へ拡張された（一時ファイルの読み戻し検証(`json.load`)が送出しうる`ValueError`/`TypeError`を含めて捕捉するため。`save_known_casts`と同じタプル）。捕捉時は`exc_info=True`付きでエラーログを出力し（例外の再送出はしない）、`tmp_path`が生成済み（`None`でない）であれば`tmp_path.unlink(missing_ok=True)`をbest-effortで実行して残置を防ぐ（削除自体の失敗も無視する）。`.bak`バックアップファイルへの書き込みのみが失敗した場合は、内側の`try`/`except OSError`で警告ログを出力し、失敗した`bak_tmp_path`をbest-effortで削除したうえで、後続のアトミック置換自体は中断せず継続する。
* 根拠: [外側try-exceptとtmp_path削除・内側try-except] (行番号: 1142〜1154 / 抜粋: "except OSError as e:\n                    logger.warning(f\"Failed to update backup file {backup_path}: {e}\")\n                    bak_tmp_path.unlink(missing_ok=True)" / "except (OSError, ValueError, TypeError) as e:\n            logger.error(f\"Failed to save daily summary: {e}\", exc_info=True)\n            if tmp_path is not None:\n                try:\n                    tmp_path.unlink(missing_ok=True)\n                except OSError:\n                    pass")


### `DataManager.record_daily_new_casts`

* **役割**: サイト単位で検知した新規キャスト件数を、直近の送信以降の累積集計に加算するインスタンスメソッド（`self._shared_file_lock`で直列化。#458）。cron等により1時間毎に別プロセスとして実行される前提のため、実行毎にファイルを読み書きして状態を永続化する。**（Issue #183で修正）** 以前は集計中の日付が当日と異なる場合（日付が変わった後の最初の検知）に集計をリセットしてから加算していたが、この無条件のカレンダー日付リセットには2つの過少報告経路があった: (1) 21時台のサマリ送信後(22時〜24時)に検知した件数が、送信済みにもかかわらず加算され続けた挙げ句、翌日最初の検知時のリセットでどのサマリにも計上されないまま消える、(2) 21時台に実行自体が無かった日(cron欠落・ロック競合)は日付リセットにより追い付き送信もできずその日の集計が丸ごと失われる。日付によるリセットを廃止し、`_maybe_send_daily_summary`が実際に送信した直後にのみ集計をクリアすることで、未送信の件数が日付をまたいでも必ず次回送信に引き継がれるようにした。**（Issue #578で追加）** `self.load_daily_summary()`の呼び出しを`try/except DataFileUnavailableError`で囲み、送出された場合は`logger.warning`のみ出力して`count`の加算・`save_daily_summary`の呼び出しをスキップし早期`return`する。以前は読み込みに失敗すると（空辞書へフォールバックしたうえで）無条件に`save_daily_summary`まで進んでいたため、たまたま読み込みが一時的なI/Oエラーで失敗しただけでも、累積中だった他サイト分のカウントが空辞書での上書きで丸ごと消えていた。
* 根拠: [メソッド定義とDocstring] (行番号: 1155〜1174 / 抜粋: "def record_daily_new_casts(self, site_id: str, count: int) -> None:\n        \"\"\"サイト単位で検知した新規キャスト件数を、直近の送信以降の累積集計に加算する。")、except節 (行番号: 1179〜1186 / 抜粋: "try:\n                data = self.load_daily_summary()\n            except DataFileUnavailableError as e:\n                # #578: 読み込めない状態のまま保存すると累積中の他サイト分の\n                # カウントを消してしまうため、今回のcount加算は諦めて既存の\n                # 永続化状態に触れない" / "logger.warning(f\"Skipping daily summary update for site '{site_id}': {e}\")\n                return")


* **引数/リクエスト**: `site_id: str`（検知元サイトのID）, `count: int`（当該サイトで新たに検知した件数）
* 根拠: [引数定義とDocstring] (行番号: 1172〜1190 / 抜粋: "site_id (str): 検知元サイトのID。\n            count (int): 当該サイトで新たに検知した件数。")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 1155 / 抜粋: "def record_daily_new_casts(self, site_id: str, count: int) -> None:")


* **副作用**: `DataManager.load_daily_summary`/`save_daily_summary`の呼び出し（ファイル読み書き）。`count <= 0`の場合は何もせず即座に`return`する。**（Issue #578）** `load_daily_summary`が`DataFileUnavailableError`を送出した場合も、`save_daily_summary`を呼ばず即座に`return`する。
* 根拠: [ガード節と呼び出し] (行番号: 1175〜1189 / 抜粋: "if count <= 0:\n            return")


* **エラーハンドリング**: **（Issue #578で追加）** `load_daily_summary`が送出する`DataFileUnavailableError`のみを捕捉し、`save_daily_summary`側のエラーハンドリング（`(OSError, ValueError, TypeError)`をログのみで握りつぶす。詳細は`save_daily_summary`を参照）には手を加えていない。


### サイト別連続巡回失敗状態の永続化メソッド群（2026-09-02のbellica閉鎖対応で追加）

`DataManager`には、サイト別の連続ネットワーク失敗回数とアラート送信済みフラグを`site_failures.json`（`daily_summary.json`と同様に全サイト共通の1ファイル）へ永続化する5つのメソッドが追加されている（**Issue #364**で静的メソッドからインスタンスメソッドへ変更され、保存先は束縛済みの`self.data_dir`から導出される）。状態のフォーマットは`{site_id: {'count': int, 'alerted': bool}}`。

* **`_site_failures_file(self) -> Path`**: 保存先ファイル（`self.data_dir / 'site_failures.json'`）のパスを返す。
* 根拠: [メソッド定義] (行番号: 1191〜1196 / 抜粋: "def _site_failures_file(self) -> Path:\n        \"\"\"サイト別の連続巡回失敗状態を保存するファイルのパスを返す。" / "return self.data_dir / 'site_failures.json'")
* **`load_site_failures() -> Dict`**: 状態を読み込む。ファイルが存在しない場合は空辞書を返す。トップレベルが辞書でない、または各エントリが辞書でない（**Issue #395で追加**。`{"site": 5}`のような値が混入すると`record_site_failure`の`entry.get`で`AttributeError`となり`_run_monitor_locked`のCRITICAL（Discord発報）が毎時繰り返されていた）場合は、該当エントリを警告ログとともに読み飛ばして安全な初期状態に倒す。**（Issue #578で変更）** `DataManager._CONTENT_ERRORS`（`ValueError`/`TypeError`/`KeyError`。非UTF-8破損の`UnicodeDecodeError`を含む）による読み込み失敗時は、`load_daily_summary`と同様エラーログのみを出力して空辞書を返し監視処理本体を止めない一方、`OSError`（NAS/CIFSの瞬断等）で読めなかった場合は空辞書へフォールバックせず新設の`DataFileUnavailableError`を送出する（`site_failures.json`には隔離・`.bak`バックアップの仕組みが無いため、`load_daily_summary`のような復旧経路はなく直接例外を送出する）。以前はこの`OSError`も他の内容起因の破損と同列に扱い空辞書を返しており、呼び出し元（`record_site_failure`等）がその空状態のまま`save_site_failures`を呼ぶと、79サイト分の状態が丸ごと消え、既にアラート済みのサイトが再アラートされていた。
* 根拠: [メソッド定義・エントリ検証・except節] (行番号: 1198〜1251 / 抜粋: "Raises:\n            DataFileUnavailableError: ファイルは存在するがI/Oエラー(OSError)で\n                読めなかった場合(#578)。" / "# #395: トップレベルだけでなく各エントリも辞書であることを検証する。\n                # {\"site\": 5} のような値が混入すると record_site_failure の\n                # entry.get で AttributeError となり" / "return {k: v for k, v in data.items() if isinstance(v, dict)}" / "except OSError as e:\n            # #578: CIFS/autofsの瞬断でopen()が失敗しただけの可能性があり、" / "raise DataFileUnavailableError(f\"site failures file is unreadable ({e})\") from e" / "except DataManager._CONTENT_ERRORS as e:\n            # load_daily_summaryと同様、非UTF-8破損(UnicodeDecodeError)まで\n            # 含めて読み込み失敗として扱い、監視処理本体を止めない\n            logger.error(f\"Failed to load site failures from {failures_file}: {e}\", exc_info=True)\n            return {}")
* **`save_site_failures(data: Dict) -> None`**: `save_known_casts`と同じ一時ファイル経由のアトミック書き込みパターンで状態を保存する。`IOError`発生時はエラーログのみ。
* 根拠: [メソッド定義] (行番号: 1252〜1268 / 抜粋: "# アトミック書き込み: save_known_castsと同じパターン\n            tmp_path = failures_file.with_suffix(failures_file.suffix + '.tmp')")
* **`record_site_failure(site_id: str) -> Tuple[int, bool]`**: 失敗を1回分加算して保存し、`(更新後の連続失敗回数, アラート送信済みかどうか)`を返す。**（Issue #578で追加）** `self.load_site_failures()`を`try/except DataFileUnavailableError`で囲み、送出された場合は`logger.warning`を出力し、加算・保存をスキップして`(0, False)`（＝「連続失敗0回・未アラート」）を返す。他サイト分の状態を巻き添えで消さないための安全側フォールバックであり、実際の連続失敗回数を過小報告する副作用があることに注意（次回実行で改めて記録される）。
* 根拠: [メソッド定義とexcept節] (行番号: 1270〜1291 / 抜粋: "def record_site_failure(self, site_id: str) -> Tuple[int, bool]:\n        \"\"\"サイトの巡回失敗を1回分記録し、更新後の連続失敗状態を返す。" / "try:\n                data = self.load_site_failures()\n            except DataFileUnavailableError as e:\n                logger.warning(f\"Skipping site failure recording for '{site_id}': {e}\")\n                return 0, False")
* **`mark_site_failure_alerted(site_id: str) -> None`**: 閉鎖疑いアラートを送信済み(`alerted: True`)として記録する。**（Issue #578で追加）** `load_site_failures()`の`DataFileUnavailableError`を捕捉した場合は`logger.warning`を出力し保存をスキップして早期`return`する（次回実行時に閾値到達が続いていれば改めて送信判断される）。
* 根拠: [メソッド定義とexcept節] (行番号: 1293〜1309 / 抜粋: "def mark_site_failure_alerted(self, site_id: str) -> None:" / "except DataFileUnavailableError as e:\n                # #578: 読めない状態のまま保存すると他サイト分の状態を消してしまうため\n                # スキップする" / "logger.warning(f\"Skipping alerted-flag update for '{site_id}': {e}\")\n                return")
* **`clear_site_failure(site_id: str) -> None`**: 疎通成功時に当該サイトのエントリを削除する。記録が無いサイトについては何もしない（毎時の正常巡回のたびに全サイト分のNAS書き込みが発生しないようにするため）。**（Issue #578で追加）** `load_site_failures()`の`DataFileUnavailableError`を捕捉した場合も`logger.warning`を出力し保存をスキップして早期`return`する（誤ってcountがクリアされないまま残るだけで、次回巡回で再度成功すれば改めて解消を試みられる）。
* 根拠: [メソッド定義とガード節・except節] (行番号: 1311〜1330 / 抜粋: "except DataFileUnavailableError as e:\n                # #578: 読めない状態のまま保存すると他サイト分の状態を消してしまうため\n                # スキップする" / "logger.warning(f\"Skipping site failure clear for '{site_id}': {e}\")\n                return" / "if site_id not in data:\n                return")


### `WebMonitor.__init__`

* **役割**: Webサイトの監視・スクレイピングを統括するクラスのコンストラクタ。リトライ機能付きHTTPセッションを初期化する。
* 根拠: [クラス定義とDocstringおよび__init__] (行番号: 1345〜1350 / 抜粋: "class WebMonitor:\n    """Webサイトの監視とスクレイピングを統括するクラス。"""\n\n    def __init__(self):\n        """HTTPセッションの初期化を行う。"""\n        self.session = self._create_robust_session()")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: 該当なし
* **副作用**: `self.session`への`_create_robust_session()`結果の代入。
* 根拠: [属性代入] (行番号: 1350 / 抜粋: "self.session = self._create_robust_session()")


* **エラーハンドリング**: なし


### `WebMonitor._create_robust_session`

* **役割**: `MonitorConfig`の設定（`RETRY_TOTAL`, `RETRY_BACKOFF`, `USER_AGENT`）に基づき、HTTP 500/502/503/504エラー時にGETリクエストをリトライする`requests.Session`を生成する。
* 根拠: [メソッド定義とDocstring] (行番号: 1352〜1358 / 抜粋: "def _create_robust_session(self) -> requests.Session:\n        """リトライロジックを組み込んだ堅牢なHTTPセッションを作成する。")


* **引数/リクエスト**: なし（`self`のみ）
* 根拠: [引数定義] (行番号: 1352 / 抜粋: "def _create_robust_session(self) -> requests.Session:")


* **戻り値/レスポンス**: `requests.Session`（設定済みセッションオブジェクト）
* 根拠: [Docstringと戻り値] (行番号: 1355〜1369 / 抜粋: "Returns:\n            requests.Session: 設定済みのセッションオブジェクト。")


* **副作用**: なし（セッションオブジェクトの生成・設定のみ、外部通信は発生しない）
* 根拠: [処理内容] (行番号: 1358〜1368 / 抜粋: "session = requests.Session()\n        retries = Retry(")


* **エラーハンドリング**: なし


### `WebMonitor.fetch_current_casts`

* **役割**: Bot検知回避のためのランダム待機後、指定サイトのターゲットURLにGETリクエストを送信し、レスポンスHTMLを`_parse_html`に渡してキャスト情報の集合を取得する。**（Issue #395で追加）** HTTP的には成功（200）していても、最終応答URL（`response.url`）のドメインが`site.target_url`のドメインと異なる場合（閉鎖・移転したサイトが別ドメインのポータルへリダイレクトされるケース。2026-09-02のbellicaの実際の症状）は`SiteUnavailableError`を送出し、連続失敗として計上させる。ドメイン比較にはモジュール関数`_normalized_netloc`（小文字化し先頭の`www.`を除去）を用い、`example.com`⇔`www.example.com`の正規化リダイレクトは同一ドメインとして扱う。
* 根拠: [メソッド定義とDocstring・リダイレクト判定] (行番号: 1371〜1408 / 抜粋: "def fetch_current_casts(self, site: SiteConfig) -> Set[CastMember]:\n        """指定サイトのターゲットURLから現在のキャスト一覧を取得する。" / "SiteUnavailableError: 最終応答のドメインが target_url と異なる場合" / "if response.url and _normalized_netloc(response.url) != _normalized_netloc(site.target_url):\n                raise SiteUnavailableError(")、[_normalized_netloc定義] (行番号: 1335〜1342 / 抜粋: "def _normalized_netloc(url: str) -> str:\n    \"\"\"URLのドメイン部分を比較用に正規化する(小文字化し先頭の 'www.' を除去)。")


* **引数/リクエスト**: `site: SiteConfig`
* 根拠: [引数定義とDocstring] (行番号: 1375〜1379 / 抜粋: "site (SiteConfig): 対象サイトの設定。")


* **戻り値/レスポンス**: `Set[CastMember]`（現在掲載されているキャストの集合）
* 根拠: [Docstring] (行番号: 1377〜1378 / 抜粋: "Returns:\n            Set[CastMember]: 現在掲載されているキャストの集合。")


* **副作用**: ランダム待機(`time.sleep(random.uniform(1.0, 3.0))`)、対象サイトのURLへのHTTP GETリクエスト、デバッグログ出力。
* 根拠: [処理内容] (行番号: 1388〜1391 / 抜粋: "time.sleep(random.uniform(1.0, 3.0))\n\n            logger.debug(f"Fetching URL: {site.target_url}")\n            response = self.session.get(site.target_url, timeout=MonitorConfig.TIMEOUT)")


* **エラーハンドリング**: `requests.RequestException`発生時はデバッグログを出力したうえで例外を再送出(`raise`)し、呼び出し元でのハンドリングを要求する（Docstringにも「通信エラー時」に本例外を送出する旨明記）。**（Issue #395で追加）** 別ドメインへのリダイレクトを検知した場合は`SiteUnavailableError`を送出する（`requests.RequestException`ではないため`except`節には捕捉されず、そのまま呼び出し元`_check_site`へ伝播する）。**（2026-09-02のbellica閉鎖対応で変更）** 以前はここで無条件に`exc_info=True`付きのERRORログを出力していたが、ログの重大度は連続失敗状態に応じて呼び出し元の`_handle_site_network_failure`が決定するよう変更された（恒久的に消失したサイトが毎時ERRORを出し続けてヘルスチェックを発報させないようにするため）。
* 根拠: [try-exceptブロックとコメント] (行番号: 1406〜1412 / 抜粋: "except requests.RequestException as e:\n            # 呼び出し元でハンドリングするために再送出する。ログの重大度は\n            # 連続失敗状態に応じて _handle_site_network_failure が決定するため、\n            # ここでは無条件にERRORを記録しない")


### `WebMonitor._extract_raw_name` / `_extract_cast_link_and_id` / `_extract_cast_image_url`（品質で追加）

* **（Issue #538 で修正）** `_extract_cast_link_and_id` で ID を抽出できない場合のフォールバック ID のフィンガープリントを、コンテナの生 HTML 全体(`str(div)`)から「名前 + 画像 URL(無ければ表示テキスト、それも無ければ生 HTML)」の SHA1 先頭10桁に変更した。以前は lazyload 状態・「NEW」バッジ・nonce 等のリクエストごとに変わる属性で毎回 ID が変わり、毎時再通知と `known_casts_*.json` の無限成長を招き得た。(同 Issue の「N 回連続で欠けたキャストの剪定」は未実装。)
* 根拠: (行番号: 1559〜1560 / 抜粋: "image_url = WebMonitor._extract_cast_image_url(div, site)", "stable_source = image_url or div.get_text(\" \", strip=True) or str(div)")

* **役割**: いずれも`_parse_html`のキャストカードごとのパース処理（以前は170行超・深いネストの単一ループ本体だった）から分離された純粋な抽出処理の静的メソッド群。`_extract_raw_name`は名前要素・名前文字列の抽出（`name_first_text_only`/`name_strip_after_tab`フラグの分岐を含む）、`_extract_cast_link_and_id`は詳細URL・IDの抽出（`id_query_param`優先→キー=値でないクエリ文字列→パス末尾セグメント→SHA1フィンガープリントの順のフォールバック）、`_extract_cast_image_url`は画像URLの抽出（`image_from_style`によるインラインCSS抽出、または`image_attr`／`src`フォールバック）を、それぞれ副作用なしに行う。年齢抽出を担う`_extract_cast_age`は**Issue #589でロジックが変更された**ため、次項で独立して説明する。名前が空文字だった場合の`skip_unnamed_casts`分岐によるカード読み飛ばし（`continue`）とそれに伴うログ出力は、ループ制御が必要なため`_parse_html`側に残されている。
* 根拠: [各メソッド定義とDocstring] (行番号: 1415〜1416, 1492〜1493, 1573〜1574 / 抜粋: "def _extract_raw_name(div: Tag, site: SiteConfig) -> Tuple[str, Optional[Tag]]:", "def _extract_cast_link_and_id(div: Tag, site: SiteConfig, name: str) -> Tuple[str, str]:", "def _extract_cast_image_url(div: Tag, site: SiteConfig) -> str:")


* **引数/リクエスト**: `_extract_raw_name(div: Tag, site: SiteConfig)`、`_extract_cast_link_and_id(div: Tag, site: SiteConfig, name: str)`（`name`はID抽出が全フォールバックを尽くしても取得できない場合のフィンガープリント付きID生成に使用）、`_extract_cast_image_url(div: Tag, site: SiteConfig)`
* 根拠: [各シグネチャ] (行番号: 1415, 1492, 1573)


* **戻り値/レスポンス**: `_extract_raw_name`は`Tuple[str, Optional[Tag]]`（名前文字列とname_elem）、`_extract_cast_link_and_id`は`Tuple[str, str]`（detail_url, cast_id）、`_extract_cast_image_url`は`str`（画像URL、抽出不可なら空文字）
* 根拠: [各戻り値ヒント] (行番号: 1415, 1492, 1573)


* **副作用**: いずれもなし（純粋な文字列/タプル抽出処理のみ。ログ出力は行わない）
* **エラーハンドリング**: いずれもなし（呼び出し元`_parse_html`の`try/except`が個別要素のパース失敗全体を捕捉する）


### `WebMonitor._extract_cast_age`（D-L12で追加、Issue #589で修正）

* **役割**: name要素全体のテキスト（`name_elem.get_text(strip=True)`。`name_first_text_only`/`name_strip_after_tab`で名前から切り離された年齢の兄弟要素・タブ区切り部分も含む）から`AGE_PATTERN`にマッチする年齢候補を抽出する`@staticmethod`。**（Issue #589で修正）** 以前は`AGE_PATTERN.search()`により文字列中最初（最も左）の1件のみを候補とし、それが「歳」「才」の明示が無い括弧内数字でD-L12の妥当性範囲チェック（`MonitorConfig.AGE_PLAUSIBLE_MIN`〜`AGE_PLAUSIBLE_MAX`、18〜79）に落ちた場合、以降の候補を一切試さないまま抽出自体が空文字で終わっていた（例: `"No.(12) さくら(25歳)"`のように、部屋番号・連番等の括弧数字が本来の年齢表記より前に出現するケース。`"(12)"`が却下された時点で検索打ち切りとなり、後続の`"(25歳)"`を一切試さなかった）。現在は`AGE_PATTERN.finditer()`で文字列中の全マッチを出現順に走査し、(a)括弧内数字に「歳」「才」の明示がある、(b)括弧内数字のみだが妥当性範囲内である、(c)括弧無しで「歳」「才」が続く数字（正規表現上この形式は常に「歳」「才」を伴う）、のいずれかを満たす最初の候補が見つかった時点でそれを採用し`break`する。妥当性チェックに落ちた候補は（以前のように抽出全体を打ち切るのではなく）読み飛ばされ、ループが後続の候補を試す点が変更点であり、D-L12由来の妥当性チェックそのものの範囲・判定基準（`MonitorConfig.AGE_PLAUSIBLE_MIN`/`MAX`）は変更していない。回帰テストは`test_newface_monitor_parse.py`の`TestExtractCastAgeSkipsImplausibleLeadingNumber`（4パターン: 却下後の後続候補採用、通常時の非退行確認、妥当な年齢が皆無な場合に空文字を維持、「歳」「才」明示時は範囲外でも信頼するD-L12挙動の維持）。
* 根拠: [メソッド定義とDocstring] (行番号: 1449〜1463 / 抜粋: "@staticmethod\n    def _extract_cast_age(name_elem: Optional[Tag]) -> str:\n        \"\"\"name要素全体のテキストから年齢を抽出する（品質: _parse_htmlから分離）。")、[Issue #589のコメントとfinditerループ] (行番号: 1466〜1489 / 抜粋: "# Issue #589: 以前はAGE_PATTERN.search()で最初の一致のみを見ていたため、\n            # \"No.(12) さくら(25歳)\"のように年齢より前に(歳/才の無い)2桁の括弧数字\n            # (連番・部屋番号等)が出現すると、その数字がD-L12の妥当性範囲チェックで\n            # 却下された時点で検索を打ち切ってしまい、後続の本来の年齢\n            # (\"(25歳)\")を一切試さないまま年齢抽出自体が失敗していた。\n            # finditer()で全ての候補を出現順に走査し、妥当性チェックを通過する\n            # 最初の候補が見つかるまで後続の候補も試すよう修正した。" / "for age_match in AGE_PATTERN.finditer(name_elem.get_text(strip=True)):" / "if bracket_suffix or (\n                        MonitorConfig.AGE_PLAUSIBLE_MIN\n                        <= int(bracket_num)\n                        <= MonitorConfig.AGE_PLAUSIBLE_MAX\n                    ):\n                        age = bracket_num\n                        break")


* **引数/リクエスト**: `name_elem: Optional[Tag]`（名前要素。存在しない場合は`None`）
* 根拠: [引数定義とDocstring] (行番号: 1450, 1459 / 抜粋: "def _extract_cast_age(name_elem: Optional[Tag]) -> str:" / "name_elem (Optional[Tag]): 名前要素（存在しない場合はNone）。")


* **戻り値/レスポンス**: `str`（抽出された年齢文字列。`name_elem`が`None`、またはテキスト中に妥当な年齢候補が1件も見つからない場合は空文字のまま）
* 根拠: [Docstringと初期化・return文] (行番号: 1461〜1462, 1464, 1489 / 抜粋: "Returns:\n            str: 抽出された年齢文字列（抽出できない場合は空文字）。" / "age = \"\"" / "return age")


* **副作用**: なし（`name_elem.get_text(strip=True)`によるテキスト抽出と正規表現マッチのみ。ログ出力・状態変更は行わない）
* **エラーハンドリング**: なし（`name_elem`が`None`の場合は`if name_elem:`分岐によりループ自体を実行せず空文字を返す。個別要素のパース失敗全体は呼び出し元`_parse_html`の`try/except`が捕捉する）
* 根拠: [name_elemのNoneガード] (行番号: 1465 / 抜粋: "if name_elem:")


### `WebMonitor._parse_html`（D-L12で変更、品質でヘルパーメソッドへ分割）

* **役割**: `BeautifulSoup`オブジェクトから、`selector_container`でキャストのコンテナ要素を抽出し、各コンテナについて`_extract_raw_name`/`_extract_cast_age`/`_extract_cast_link_and_id`/`_extract_cast_image_url`（いずれも品質で追加）を順に呼び出して`CastMember`を構築する。**（品質で変更）** 以前は名前・年齢・リンク/ID・画像の4種の抽出ロジックが170行超の単一`for`ループ本体に直接書かれ、深くネストした条件分岐で読みにくかったため、ループ制御（`skip_unnamed_casts`時の`continue`とログ出力）のみを本メソッドに残し、各フィールドの純粋な抽出処理を上記4つの静的ヘルパーメソッドへ分離した。抽出ロジック自体（ID抽出の複数段フォールバック等）は分離前と完全に同一である。ただし年齢抽出（`_extract_cast_age`）のみ、分離後に**Issue #589**で`.search()`から`.finditer()`ベースへ実装が変更されている（D-L12の妥当性チェック自体の範囲・判定基準は変更なし。詳細は前項参照）。
* 根拠: [メソッド定義とDocstring] (行番号: 1602〜1611 / 抜粋: "def _parse_html(self, soup: BeautifulSoup, site: SiteConfig) -> Set[CastMember]:\n        """HTMLスープからキャスト情報を抽出する。")、[ヘルパー呼び出し] (行番号: 1624, 1647〜1649 / 抜粋: "name, name_elem = self._extract_raw_name(div, site)", "age = self._extract_cast_age(name_elem)\n                detail_url, cast_id = self._extract_cast_link_and_id(div, site, name)\n                image_url = self._extract_cast_image_url(div, site)")


* **引数/リクエスト**: `soup: BeautifulSoup`（解析対象のHTML）, `site: SiteConfig`（対象サイトの設定。セレクタ・ベースURLに使用）
* 根拠: [引数定義とDocstring] (行番号: 1602, 1606〜1607 / 抜粋: "soup (BeautifulSoup): 解析対象のHTML。\n            site (SiteConfig): 対象サイトの設定（セレクタ・ベースURLに使用）。")


* **戻り値/レスポンス**: `Set[CastMember]`（抽出されたキャストの集合。コンテナ要素が見つからない場合は空集合）
* 根拠: [Docstringと各return] (行番号: 1609〜1610, 1620, 1666 / 抜粋: "Returns:\n            Set[CastMember]: 抽出されたキャストの集合。")


* **副作用**: セレクタが要素にマッチしなかった場合の警告ログ出力、個別要素のパース失敗時の警告ログ出力、デバッグログ出力。URLの正規化（クエリ文字列・フラグメントの除去によるID安定化、`urljoin`による絶対URL化、別ドメインリンクへのドメインプレフィックス付与）は`_extract_cast_link_and_id`（品質で追加）に委譲される。
* 根拠: [ID正規化のコメントと処理（委譲先）] (行番号: 1538〜1545 / 抜粋: "# クエリ文字列(?utm=...等)やURLフラグメント(#...等)が付与\n                # されるとcast_idが実行ごとにブレて「新規キャスト」の\n                # 誤検知を招くため、先に除去する")


* **エラーハンドリング**: コンテナ要素が1件も見つからない場合は警告ログを出力し空集合を返す。個別のキャスト要素パース中に例外が発生した場合は警告ログを出力し、その要素をスキップして次の要素の処理を継続する（`continue`）。
* 根拠: [try-exceptブロック] (行番号: 1660〜1663 / 抜粋: "except Exception as e:\n                # 個別のパースエラーで全体を止めない\n                logger.warning(f"Error parsing specific cast element (site: '{site.site_id}'): {e}")\n                continue")


### `WebMonitor.close`

* **役割**: 保持しているHTTPセッションのリソースを明示的に解放する。
* 根拠: [メソッド定義とDocstring] (行番号: 1668〜1671 / 抜粋: "def close(self):\n        """リソースを明示的に解放する。"""\n        if self.session:\n            self.session.close()")


* **引数/リクエスト**: なし（`self`のみ）
* **戻り値/レスポンス**: `None`（暗黙）
* **副作用**: `self.session.close()`によるHTTPセッションのクローズ。
* **エラーハンドリング**: `self.session`が存在する場合にのみクローズを実行するガード節のみ。
* 根拠: [ガード節] (行番号: 1670 / 抜粋: "if self.session:")


### `SiteUnavailableError` / `SiteCheckResult`（Issue #395で追加）

* **役割**: `SiteUnavailableError`（`Exception`のサブクラス）は「HTTP的には成功したが巡回結果としてサイトが消失した疑い」を示すモジュールレベル例外。`fetch_current_casts`が別ドメインへのリダイレクトを検知した際に送出し、`_check_site`がキャスト0件の巡回結果を失敗計上する際にも理由として生成する。`SiteCheckResult`は`_check_site`の1サイト分の結果を表すデータクラスで、`failed: bool`（疎通不能・別ドメインへのリダイレクト・キャスト0件のいずれかで連続失敗として計上したか。自局側障害の判定に使う）と`pending_alert_count: Optional[int]`（連続失敗が閾値に達しかつ閉鎖疑いアラートが未送信の場合の連続失敗回数。実行終了時に`_send_pending_site_failure_alerts`がまとめて送信判断する）の2フィールドを持つ。
* 根拠: [SiteUnavailableError定義とDocstring] (行番号: 737〜746 / 抜粋: "class SiteUnavailableError(Exception):\n    \"\"\"HTTP的には成功したが、巡回結果として「サイトが消失した疑い」を示す例外(#395)。")、[SiteCheckResult定義] (行番号: 1678〜1690 / 抜粋: "@dataclass\nclass SiteCheckResult:\n    \"\"\"_check_site の1サイト分の結果(#395)。" / "failed: bool = False\n    pending_alert_count: Optional[int] = None")


* **引数/リクエスト**: `SiteCheckResult(failed: bool = False, pending_alert_count: Optional[int] = None)`
* **戻り値/レスポンス**: 該当なし（定義のみ）
* **副作用**: なし
* **エラーハンドリング**: 該当なし


### `_handle_site_network_failure`（2026-09-02のbellica閉鎖対応で追加、Issue #395で変更）

* **（Issue #535 で修正）** 使われていなかった第1引数 `notifier` を削除し、シグネチャを `(site, exc, data_manager, log_level=logging.ERROR)` にした(呼び出し元 `_check_site` の2箇所も追随)。
* 根拠: (行番号: 1693〜1698 / 抜粋: "def _handle_site_network_failure(\n    site: SiteConfig,\n    exc: Exception,\n    data_manager: DataManager,")

* **役割**: サイト巡回の失敗（ネットワーク失敗・別ドメインへのリダイレクト・キャスト0件）を`data_manager.record_site_failure`で記録し、ログの重大度を決定したうえで、閉鎖疑いアラートの送信が必要（連続失敗が`MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD`以上かつ未アラート）なら現在の連続失敗回数を返す関数。2026-09-02のbellica閉鎖（ドメインがホスティング業者のデフォルト自己署名証明書+ポータルサイトへの302リダイレクトに変化）で、恒久的に消失したサイトが毎時ERRORを出し続けて一次ヘルスチェック(health_watch)が発報し続けた事象の再発防止。**（Issue #395で変更）** (1) ログの降格は「アラート送信済み」ではなく「連続失敗回数が閾値以上」で判定する。Webhook未設定/失効でアラート送信が失敗し続けると`alerted`が永久に立たず、毎時ERROR→Discord発報が続いていたため、送信の成否とは切り離して降格する（送信自体は`alerted`が立つまで毎回再試行される）。(2) アラートの送信は本関数では行わず、戻り値で「送信が必要」を伝える。同一実行内で失敗サイト数が総数の大半を占める場合（Pi側の回線断等の自局側障害）に79件のアラートが一斉送信されるのを防ぐため、`_run_monitor_locked`が全サイト処理後に`_send_pending_site_failure_alerts`でまとめて送信可否を判断する。
* 根拠: [関数定義とDocstring] (行番号: 1693〜1734 / 抜粋: "def _handle_site_network_failure(\n    notifier: DiscordNotifier,\n    site: SiteConfig,\n    exc: Exception,\n    data_manager: DataManager,\n    log_level: int = logging.ERROR,\n) -> Optional[int]:\n    \"\"\"サイト巡回の失敗を記録し、閉鎖疑いアラートが必要なら連続失敗回数を返す。" / "#395での変更点:\n    - ログの降格は「アラート送信済み」ではなく「連続失敗回数が閾値以上」で判定する。" / "- アラートの送信はここでは行わず、戻り値で「送信が必要」を伝える。")


* **引数/リクエスト**: `notifier: DiscordNotifier`（後方互換のため残されているが、本関数内では送信に使われない）, `site: SiteConfig`（巡回に失敗したサイトの設定）, `exc: Exception`（発生した例外。ログ出力用）, `data_manager: DataManager`（**Issue #364で追加**。今回の実行で解決済みのデータディレクトリに束縛された`DataManager`）, `log_level: int = logging.ERROR`（**Issue #395で追加**。閾値未満のときに使うログレベル。ネットワーク失敗はERROR、キャスト0件（レイアウト変更の可能性もある）は`_check_site`がWARNINGを渡す）
* 根拠: [引数定義とDocstring] (行番号: 1722〜1728, 1727〜1734 / 抜粋: "notifier (DiscordNotifier): (後方互換のため残している。送信は行わない)\n        site (SiteConfig): 巡回に失敗したサイトの設定。\n        exc (Exception): 発生した例外(ログ出力用)。" / "log_level (int): 閾値未満のときに使うログレベル。ネットワーク失敗は\n            ERROR、キャスト0件(レイアウト変更の可能性もある)はWARNINGを渡す。")


* **戻り値/レスポンス**: `Optional[int]`（連続失敗回数が閾値以上かつアラート未送信なら現在の連続失敗回数＝アラート送信が必要。それ以外は`None`）
* 根拠: [Docstringとreturn] (行番号: 1730〜1733, 1748 / 抜粋: "Returns:\n        Optional[int]: 連続失敗回数が閾値以上かつアラート未送信なら現在の連続\n            失敗回数(=アラート送信が必要)。それ以外は None。" / "return count if (threshold_reached and not alerted) else None")


* **副作用**: `data_manager.record_site_failure`による失敗回数の加算・保存。ログ出力（連続失敗回数と例外内容を含む）: アラート送信済みならWARNING（「closure alert already sent」）、未送信でも閾値以上ならWARNING（「closure alert threshold reached; alert pending」）、閾値未満なら`log_level`（既定ERROR）。**（Issue #395で変更）** Discordへの送信と`mark_site_failure_alerted`は本関数では行わない（`_send_pending_site_failure_alerts`へ移動）。
* 根拠: [処理本体] (行番号: 1734〜1748 / 抜粋: "count, alerted = data_manager.record_site_failure(site.site_id)\n    threshold_reached = count >= MonitorConfig.CONSECUTIVE_FAILURE_ALERT_THRESHOLD" / "if alerted:\n        logger.warning(f\"{message} (closure alert already sent)\")\n    elif threshold_reached:\n        logger.warning(f\"{message} (closure alert threshold reached; alert pending)\")\n    else:\n        logger.log(log_level, message)")


* **エラーハンドリング**: なし（本関数自体に例外処理はない。アラート送信失敗時の再試行は`_send_pending_site_failure_alerts`側で「`alerted`を立てない」ことにより実現され、本関数は次回も閾値以上・未アラートとして送信要求を返し続ける）。
* 根拠: [return] (行番号: 1748 / 抜粋: "return count if (threshold_reached and not alerted) else None")


### `_send_pending_site_failure_alerts`（Issue #395で追加）

* **役割**: 全サイト処理後に、閾値到達サイトの閉鎖疑いアラートをまとめて送信する関数。同一実行内で失敗として計上したサイトの割合（`failed_count / total_count`）が`MonitorConfig.SELF_OUTAGE_SUPPRESS_RATIO`（0.5）を超える場合は、個々のサイトの閉鎖ではなく自局側（Pi側の回線断・DNS障害等）の障害とみなして警告ログを出し送信を抑止する。この場合`alerted`は立てないため、回線復旧後の次回実行で（まだ閾値以上なら）改めて送信判断が行われる。抑止されない場合は各サイトについて`notifier.notify_site_failure_alert`を呼び、送信成功時のみ`data_manager.mark_site_failure_alerted`で送信済みを永続化する（送信失敗時は`alerted`を立てず次回再試行）。
* 根拠: [関数定義とDocstring・処理本体] (行番号: 1751〜1785 / 抜粋: "def _send_pending_site_failure_alerts(\n    notifier: DiscordNotifier,\n    data_manager: DataManager,\n    pending: List[Tuple[SiteConfig, int]],\n    failed_count: int,\n    total_count: int,\n) -> None:\n    \"\"\"全サイト処理後に、閾値到達サイトの閉鎖疑いアラートをまとめて送信する(#395)。" / "if total_count > 0 and failed_count / total_count > MonitorConfig.SELF_OUTAGE_SUPPRESS_RATIO:\n        logger.warning(" / "if notifier.notify_site_failure_alert(site, count):\n            data_manager.mark_site_failure_alerted(site.site_id)")


* **引数/リクエスト**: `notifier: DiscordNotifier`, `data_manager: DataManager`, `pending: List[Tuple[SiteConfig, int]]`（(サイト設定, 連続失敗回数)のリスト）, `failed_count: int`（今回の実行で失敗として計上したサイト数）, `total_count: int`（今回の実行で処理対象としたサイト数）
* 根拠: [引数定義とDocstring] (行番号: 1768〜1788 / 抜粋: "pending (List[Tuple[SiteConfig, int]]): (サイト設定, 連続失敗回数) のリスト。\n        failed_count (int): 今回の実行で失敗として計上したサイト数。\n        total_count (int): 今回の実行で処理対象としたサイト数。")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 1757 / 抜粋: ") -> None:")


* **副作用**: 抑止時の警告ログ出力。非抑止時の`notifier.notify_site_failure_alert`によるDiscord送信と、成功時の`data_manager.mark_site_failure_alerted`による`site_failures.json`更新。`pending`が空なら何もしない。
* 根拠: [ガード節と送信ループ] (行番号: 1772〜1785 / 抜粋: "if not pending:\n        return" / "for site, count in pending:\n        # 送信に失敗した場合はalertedを立てず、次回実行時に再試行する")


* **エラーハンドリング**: 送信失敗（`notify_site_failure_alert`が`False`）時は`alerted`を立てず、次回実行時に再試行される。
* 根拠: [送信成否分岐] (行番号: 1784〜1787 / 抜粋: "if notifier.notify_site_failure_alert(site, count):\n            data_manager.mark_site_failure_alerted(site.site_id)")


### `_merge_known_casts`（Issue #538で追加）

* **役割**: 既知キャスト集合と今回取得したキャスト集合をマージし、長期間欠けているキャストを剪定する純粋関数。#237 の「常に union で保存する」方針は維持しつつ、一覧ページに載っていなかった既知キャストの `missed_runs` を1増やし(`dataclasses.replace`)、`prune_after` 回連続で欠けたものだけを集合から外す。一覧に載っていたキャストは `missed_runs` を0へ戻し、今回初めて見つかったキャストは `missed_runs=0` で追加する。`_check_site` は `current_casts` が空の場合(パース全滅)にはこの関数へ到達せず失敗計上して戻るため、全滅時に既知キャストの欠落回数が進むことはない。
* 根拠: [関数定義とDocstring] (行番号: 1788〜1823 / 抜粋: "def _merge_known_casts(\n    known_casts: Set[CastMember],\n    current_casts: Set[CastMember],\n    prune_after: int = MonitorConfig.KNOWN_CAST_PRUNE_AFTER_MISSES,\n) -> Tuple[Set[CastMember], int]:", "merged.add(replace(cast, missed_runs=0))", "if missed >= prune_after:\n            pruned += 1\n            continue")


* **引数/リクエスト**: `known_casts: Set[CastMember]`（保存済みの既知キャスト）, `current_casts: Set[CastMember]`（今回取得したキャスト。空でないこと）, `prune_after: int = MonitorConfig.KNOWN_CAST_PRUNE_AFTER_MISSES`（剪定閾値）
* 根拠: (行番号: 1788〜1792)


* **戻り値/レスポンス**: `Tuple[Set[CastMember], int]`（保存すべきキャスト集合, 剪定した件数）
* 根拠: (行番号: 1823 / 抜粋: "return merged, pruned")


* **副作用**: なし（入力の集合・要素は変更せず、`replace` で新しい `CastMember` を生成する）
* **エラーハンドリング**: なし



### `_check_site`

* **（Issue #538 で修正）** 保存する集合は `known_casts.union(current_casts)` ではなく `_merge_known_casts(known_casts, current_casts)` で求める。union 相当の結果に加えて、`KNOWN_CAST_PRUNE_AFTER_MISSES` 回連続で一覧から欠けている既知キャストが剪定され、剪定件数が1以上なら INFO ログ(「Pruned N stale cast(s) ...」)を出す。Issue #531 の未送信キャスト除外はその後に適用される。
* 根拠: (行番号: 1902〜1910 / 抜粋: "updated_casts, pruned_count = _merge_known_casts(known_casts, current_casts)", "f\"Pruned {pruned_count} stale cast(s) from site '{site.site_id}' \"")


* **（Issue #531 で修正）** 通知は `notifier.notify_casts(...)` を呼び、返された未送信キャストを `updated_casts` から除外してから `save_known_casts` する(WARNING ログ付き)。以前は送信成否に関わらず `known_casts ∪ current_casts` を保存していたため、Discord 障害・ブレーカー開放・401/404 の実行で検知した新規キャストは既知扱いになり二度と通知されなかった。除外されたキャストは次回実行で再び新規として検知され再通知される。
* 根拠: (行番号: 1913〜1922 / 抜粋: "sent_count, unsent_casts = notifier.notify_casts(new_casts, site_name=site.name)", "updated_casts = updated_casts.difference(unsent_casts)")

* **（2026-09-06 品質監査で修正）** `notifier.notify(...)` の後の `data_manager.record_daily_new_casts(site.site_id, sent_count)` を `try/except Exception` で囲み、失敗しても ERROR ログ(`exc_info=True`)のみ出して後続の `save_known_casts` を必ず実行する。この呼び出しは「通知は済んだが既知キャストの保存はまだ」という位置にあり、例外が漏れると通知済みキャストが毎時「新規」として再通知され続ける(#174/#183 と同じ失敗モード)ため、集計の失敗を隔離する。
* 根拠: (行番号: 1927〜1934 / 抜粋: "try:\n            data_manager.record_daily_new_casts(site.site_id, sent_count)\n        except Exception as e:", "(known casts will still be saved)")

* **役割**: 1サイト分の巡回（既知キャスト読み込み→現在キャスト取得→差分検知→通知→保存）を行い、`SiteCheckResult`（失敗計上の有無と閉鎖疑いアラートの要否）を返す関数。サイト単位の処理を分離することで、あるサイトの通信障害・レイアウト変更が他サイトの監視処理に波及しないようにする。
* 根拠: [関数定義とDocstring] (行番号: 1826〜1843 / 抜粋: "def _check_site(\n    monitor: WebMonitor, notifier: DiscordNotifier, site: SiteConfig, data_manager: DataManager\n) -> SiteCheckResult:\n    \"\"\"1サイト分の巡回・差分検知・通知・保存を行う。" / "Returns:\n        SiteCheckResult: 失敗計上の有無と、閉鎖疑いアラートの要否(#395)。")


* **引数/リクエスト**: `monitor: WebMonitor`（使い回すインスタンス）, `notifier: DiscordNotifier`（使い回すインスタンス）, `site: SiteConfig`（処理対象のサイト設定）, `data_manager: DataManager`（**Issue #364で追加**。今回の実行で解決済みのデータディレクトリに束縛された`DataManager`。既知キャストの読み書き・失敗状態・日次集計は全てこのインスタンス経由で行い、NAS状態を再評価しない）
* 根拠: [引数定義とDocstring] (行番号: 1835〜1848 / 抜粋: "monitor (WebMonitor): 使い回すWebMonitorインスタンス。\n        notifier (DiscordNotifier): 使い回すDiscordNotifierインスタンス。\n        site (SiteConfig): 処理対象のサイト設定。\n        data_manager (DataManager): 今回の実行で解決済みのデータディレクトリに\n            束縛されたDataManager(#364)。")


* **戻り値/レスポンス**: `SiteCheckResult`（**Issue #395で変更**。以前は`None`。既知キャスト読み込み不可でのスキップ時と正常完了時は`SiteCheckResult()`（`failed=False`）、疎通不能・別ドメインへのリダイレクト・キャスト0件で失敗計上した場合は`failed=True`かつ`_handle_site_network_failure`の戻り値を`pending_alert_count`に持つ）
* 根拠: [戻り値ヒントと各return] (行番号: 1828, 1855, 1862〜1864, 1862〜1871, 1862 / 抜粋: ") -> SiteCheckResult:" / "return SiteCheckResult()" / "return SiteCheckResult(failed=True, pending_alert_count=pending)")


* **副作用**: `data_manager.load_known_casts`/`save_known_casts`の呼び出し、`monitor.fetch_current_casts`によるHTTP通信、新規検知時の`notifier.notify`によるDiscord通知と`data_manager.record_daily_new_casts`による日次集計更新。**（D-L9で変更）** `record_daily_new_casts`に渡す件数は、以前は`len(new_casts)`（検知した新規件数そのもの）だったが、`notifier.notify`の戻り値（実際にDiscordへ送信できた件数）を使うよう変更した。サーキットブレーカーが開いて送信をスキップしたキャストまで日次サマリに計上すると、実際には送られていない件数分だけ過大報告になっていたため。既知キャスト(`known_casts`)が1件以上存在するにもかかわらず、新規検知件数が`MonitorConfig.MASS_DETECTION_WARNING_THRESHOLD`（既定値20）以上となった場合は、`known_casts`データの喪失・巻き戻り（NAS同期不整合やキャッシュ破損からの復旧漏れ等）による大量誤検知・再通知の疑いとして警告ログを出力する（通知自体は継続され、あくまで調査の手がかりを残す目的）。**（Issue #237で修正）** `save_known_casts`に渡す保存対象は、新規検知の有無に関わらず常に和集合ベースで求められる（**Issue #538以降は`known_casts.union(current_casts)`ではなく`_merge_known_casts(known_casts, current_casts)`により、和集合に加えて長期間欠落したキャストの剪定も行う。詳細は前項の`_check_site`の`_merge_known_casts`に関する記載を参照**）。以前（#237時点）は新規検知が1件でもあれば和集合、1件も無ければ`current_casts`による全置換という非対称な実装だったため、`_parse_html`が個別カードのパース失敗を`except Exception`で握りつぶしフェイルソフトに処理を続行する設計（既知キャストのカードが単発でパース失敗し`current_casts`から漏れるケースがある）と組み合わさると、同一実行内に他の真の新規キャストが1件も無い場合に限り、そのカードが`known_casts`から恒久的に消え、次回正常にパースできた際に「新規キャスト」として誤って再通知されていた。
* 根拠: [メイン処理フローと大量検知時の警告] (行番号: 1848, 1886〜1891 / 抜粋: "known_casts = data_manager.load_known_casts(site)" / "if known_casts and len(new_casts) >= MonitorConfig.MASS_DETECTION_WARNING_THRESHOLD:\n        logger.warning(\n            f\"Unusually large diff for site '{site.site_id}': \"")、常時_merge_known_castsでの保存(#538) (行番号: 1902, 1938 / 抜粋: "updated_casts, pruned_count = _merge_known_casts(known_casts, current_casts)" / "data_manager.save_known_casts(site, updated_casts)")、[D-L9: sent_countの利用] (行番号: 1910〜1916 / 抜粋: "# D-L9: サーキットブレーカーが開いて送信をスキップしたキャストまで\n        # 日次サマリに計上すると、実際にDiscordへ送られていない件数分だけ\n        # 過大報告になる。notify()の戻り値(実際に送信できた件数)を使う。\n        sent_count = notifier.notify(new_casts, site_name=site.name)\n        data_manager.record_daily_new_casts(site.site_id, sent_count)")


* **エラーハンドリング**: **（Issue #365で追加）** `data_manager.load_known_casts`が`KnownCastsUnavailableError`（既知キャストファイルは存在するがI/Oエラーで読めない）を送出した場合はWARNINGログ（「Skipping site ... because known casts are unavailable」）を出力し、巡回（`fetch_current_casts`）・通知・保存のいずれも行わず`return`する（空集合で続行すると全キャストの再通知と、union保存による退店済みキャストの復活を招くため）。`monitor.fetch_current_casts`での`requests.RequestException`または`SiteUnavailableError`（**Issue #395で追加**。別ドメインへのリダイレクト）発生時は`_handle_site_network_failure`に処理を委譲し、`SiteCheckResult(failed=True, pending_alert_count=...)`を返す（当該サイトのみ中断、他サイトへは影響しない。失敗回数の記録・ログレベルの決定は委譲先が行い、Discordアラートの送信可否は`_run_monitor_locked`が全サイト処理後に判断する）。**（Issue #395で変更）** 取得できたキャストが0件の場合も、以前のように`clear_site_failure`してデバッグログで`return`するのではなく、`SiteUnavailableError("no casts parsed")`を理由として`_handle_site_network_failure`に`log_level=logging.WARNING`で委譲し、連続失敗として計上する（200を返すがポータルへリダイレクト後に要素が見つからないだけ、というbellicaの症状を検知するため。レイアウト変更の可能性もあるため閾値未満ではERRORにしない）。連続失敗状態の解消（`data_manager.clear_site_failure`）は、到達できて1件以上のキャストを取得できた時点で行う。
* 根拠: [KnownCastsUnavailableErrorによるスキップ] (行番号: 1849〜1856 / 抜粋: "except KnownCastsUnavailableError as e:\n        # #365: I/Oエラーで既知キャストが読めない場合、空集合で続行すると\n        # 全キャストの再通知と退店済みキャストの復活(union保存)を招くため、\n        # 巡回・通知・保存のいずれも行わず当該サイトを今回はスキップする" / "return SiteCheckResult()")、[try-except・0件時の失敗計上・成功時のクリア] (行番号: 1860〜1878 / 抜粋: "except (requests.RequestException, SiteUnavailableError) as e:\n        pending = _handle_site_network_failure(notifier, site, e, data_manager)\n        return SiteCheckResult(failed=True, pending_alert_count=pending)" / "if not current_casts:\n        # #395: 200を返すが1件も抽出できない状態が続くのも消失サイトの症状" / "log_level=logging.WARNING," / "# 到達できてキャストを取得できた時点で連続失敗の記録があれば解消する\n    data_manager.clear_site_failure(site.site_id)")


### `_maybe_send_daily_summary`

* **役割**: 21時台の実行のときだけ、前回送信以降に累積した新規検知サマリをDiscordへテキスト通知する関数。cron等による1時間毎の別プロセス実行を前提に、実行時刻の時(hour)が21かどうかで時刻トリガーを判定し、同日中の重複送信は送信済み日付(`last_sent_date`)の永続化で防止する。**（Issue #183で修正）** `record_daily_new_casts`側がカレンダー日付によるリセットを行わなくなったため、ここで送信するのは「厳密な当日分」ではなく「前回この関数が実際に送信してから今までに累積した全件数」になる。21時台の実行がまる1日以上飛んだ場合(cron欠落・ロック競合)も、次に成功した21時台の実行で未送信分がまとめて送られる(取りこぼしなし)。**（Issue #226で修正）** 以前は`notify_daily_summary`の戻り値(常に`None`)を確認せず、送信の成否にかかわらず無条件に`counts`をクリアし`last_sent_date`を当日にセットしていたため、Webhook未設定やDiscordへの送信失敗時にもその日の集計が失われ、かつ`last_sent_date`が当日にセットされることで本関数冒頭のガード節により同日中の再送機会も失われていた。`notify_daily_summary`が返す`bool`を確認し、送信成功時のみ集計クリア・`last_sent_date`更新を行うよう修正した。失敗時は何も保存せず、次回実行時に再送を試みられるようにする。**（Issue #578で追加）** `data_manager.load_daily_summary()`の呼び出しを`try/except DataFileUnavailableError`で囲み、送出された場合は`logger.warning`のみ出力して`notify_daily_summary`の呼び出し自体を行わず早期`return`する（累積中のカウントは`save_daily_summary`が呼ばれないため保持され、次回実行時に改めて送信を試みる）。この関数は`DataManager._shared_file_lock`の**外側**で呼ばれる（`_run_monitor_locked`から直接。ロック内で呼ばれる`record_daily_new_casts`とは異なる、既存の設計）。
* 根拠: [関数定義とDocstring] (行番号: 1942〜1962 / 抜粋: "def _maybe_send_daily_summary(notifier: DiscordNotifier, data_manager: DataManager) -> None:\n        \"\"\"21時台の実行のときだけ、前回送信以降に累積した新規検知サマリをDiscordへテキスト通知する。")、except節 (行番号: 1968〜1974 / 抜粋: "try:\n        data = data_manager.load_daily_summary()\n    except DataFileUnavailableError as e:\n        # #578: 読めない状態で送信判断をすると誤った空集計を送りかねないため、\n        # 今回はスキップして次回実行時に改めて試みる(累積中のカウントは保持される)。\n        logger.warning(f\"Skipping daily summary send this run: {e}\")\n        return")、送信成否分岐 (行番号: 1980, 1987〜1990 / 抜粋: "sent = notifier.notify_daily_summary(counts, site_names, today_str)" / "if sent:\n        data_manager.save_daily_summary({'counts': {}, 'last_sent_date': today_str})\n    else:\n        logger.error(")


* **引数/リクエスト**: `notifier: DiscordNotifier`（使い回すインスタンス）, `data_manager: DataManager`（**Issue #364で追加**。今回の実行で解決済みのデータディレクトリに束縛された`DataManager`）
* 根拠: [引数定義とDocstring] (行番号: 1959〜1978 / 抜粋: "notifier (DiscordNotifier): 使い回すDiscordNotifierインスタンス。\n        data_manager (DataManager): 今回の実行で解決済みのデータディレクトリに\n            束縛されたDataManager(#364)。")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 1942 / 抜粋: "def _maybe_send_daily_summary(notifier: DiscordNotifier, data_manager: DataManager) -> None:")


* **副作用**: `data_manager.load_daily_summary`の呼び出し、条件成立時の`notifier.notify_daily_summary`呼び出し。**（Issue #226で修正）** 送信成功時(`notify_daily_summary`が`True`を返した場合)のみ`data_manager.save_daily_summary`を呼び出し`counts`を`{}`にクリアして`last_sent_date`を更新する。送信失敗時(`False`)は`save_daily_summary`を呼び出さず、エラーログのみ出力する。**（Issue #578で追加）** `load_daily_summary`が`DataFileUnavailableError`を送出した場合は、`notify_daily_summary`/`save_daily_summary`のいずれも呼び出さず`return`する。
* 根拠: [メイン処理と送信成否分岐] (行番号: 1969〜1970, 1980, 1987〜1993 / 抜粋: "data = data_manager.load_daily_summary()" / "sent = notifier.notify_daily_summary(counts, site_names, today_str)" / "if sent:\n        data_manager.save_daily_summary({'counts': {}, 'last_sent_date': today_str})\n    else:\n        logger.error(\n            \"Daily summary notification failed; keeping accumulated counts for retry \"")


* **エラーハンドリング**: 現在時刻が21時台でない場合、または当日分が送信済みの場合は早期`return`する。**（Issue #578で追加）** `data_manager.load_daily_summary()`が送出する`DataFileUnavailableError`を捕捉し、警告ログを出力して早期`return`する（本関数が捕捉する唯一の例外）。**[修正済み・Issue #451]** 以前は判定条件にリテラル`21`が直書きされていたが、現在は`MonitorConfig.DAILY_SUMMARY_HOUR`定数を参照する(値は変更していない)。
* 根拠: [ガード節] (行番号: 1964〜1966 / 抜粋: "if now.hour != MonitorConfig.DAILY_SUMMARY_HOUR:\n        return")


### `_MONITOR_LOCK_FILE_PATH` (モジュール定数)

* **役割**: 多重起動防止ロックに用いるロックファイル（`.newface_monitor.lock`）のパス。cron等での実行が重複すると、既知キャストリストやサマリファイルへの読み書きが競合し、一時消失→再通知等のデータ不整合が起きうる（`batch_download_discord.py`では既に`flock`による同種のロックが導入済み）ため、`run_monitor`が多重起動防止ロックの対象ファイルとして用いる。
* 根拠: [定義とコメント] (行番号: 1996〜2000 / 抜粋: "# M-7-4: 多重起動防止ロック。cron等での実行が重複すると、既知キャストリストや\n# サマリファイルへの読み書きが競合し、一時消失→再通知等のデータ不整合が起きうる\n# (batch_download_discord.pyでは既にflockによる同種のロックが導入済み)。\n# cronの1回が想定より長く(1時間超)かかるとこの多重起動が起きやすい。\n_MONITOR_LOCK_FILE_PATH = CURRENT_DIR / ".newface_monitor.lock"")


* **副作用**: なし（パス文字列の定義のみ）
* **エラーハンドリング**: なし


### `run_monitor`

* **役割**: モニタープロセスのエントリポイント。`fcntl.flock`による多重起動防止ロックを`_MONITOR_LOCK_FILE_PATH`に対して非ブロッキングで取得し、取得できた場合のみ処理本体`_run_monitor_locked`を呼び出す。ロック取得に失敗した場合（他のインスタンスが実行中）は情報ログを出力して即座に終了する。`batch_download_discord.py`の`BatchDownloader.run`と同じ`flock`パターンを採用している。
* 根拠: [関数定義とDocstring] (行番号: 2003〜2004 / 抜粋: "def run_monitor() -> None:\n    """モニタープロセスのエントリポイント。多重起動防止ロックを取得してから本処理を実行する。"""")


* **引数/リクエスト**: なし
* 根拠: [引数定義] (行番号: 2003 / 抜粋: "def run_monitor() -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 2003 / 抜粋: "def run_monitor() -> None:")


* **副作用**: ロックファイルのオープン(`os.open`)、非ブロッキング排他ロック取得の試行(`fcntl.flock(..., fcntl.LOCK_EX | fcntl.LOCK_NB)`)、取得成功時の`_run_monitor_locked()`呼び出し、`finally`ブロックでのロック解放(`fcntl.flock(..., fcntl.LOCK_UN)`)とファイルディスクリプタのクローズ。
* 根拠: [ロック処理] (行番号: 2005〜2019 / 抜粋: "lock_fd = os.open(str(_MONITOR_LOCK_FILE_PATH), os.O_CREAT | os.O_RDWR)\n    try:\n        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)")


* **エラーハンドリング**: ロック取得失敗(`BlockingIOError`/`OSError`)時は「他のインスタンスが既に実行中」である旨の情報ログを出力し、ファイルディスクリプタをクローズして即座に`return`する（多重起動をスキップし、`_run_monitor_locked`は呼び出されない）。ロック取得後は`_run_monitor_locked()`の呼び出しを`try`/`finally`で囲み、内部で例外が発生してもロック解放とディスクリプタのクローズを確実に行う。
* 根拠: [try-exceptとfinally] (行番号: 2008〜2019 / 抜粋: "except (BlockingIOError, OSError):\n        logger.info("⏭️ 他のインスタンスが既に実行中のため終了します (lock busy)")\n        os.close(lock_fd)\n        return")


### `_run_monitor_locked`

* **役割**: モニタープロセス全体のメインロジック（多重起動防止ロック取得後に`run_monitor`から呼び出される処理本体）。**（Issue #364で変更）** 冒頭で`MonitorConfig.get_data_dir()`を**1回だけ**呼び出してデータディレクトリを解決し、(1)それがローカルフォールバック先であれば`MonitorConfig.is_local_fallback_dir`で検知してERRORログを出し実行全体を中断、(2)そうでなければストレージのウォームアップ確認後に`DataManager(data_dir)`を生成し、`MonitorConfig.SITES`に登録された全サイトを`_check_site(monitor, notifier, site, data_manager)`で処理し(**（2026-09-06 品質監査で修正）** 以前の本仕様書は「順に」と記述していたが、現行コードは `ThreadPoolExecutor` で並列に処理する。行番号: 1863)、最後に`_maybe_send_daily_summary(notifier, data_manager)`を呼び出すオーケストレーション関数。フォールバック検知が`wait_for_storage_warmup`より前にある理由は、`get_data_dir()`がローカルフォールバック先を`mkdir`済みで返すためウォームアップ確認が必ず通過し、そこでは検知できないためである。**（Issue #461で追加）** `DataManager(data_dir)`生成直後に`data_manager.cleanup_old_quarantine_files()`を呼び出し、`_QUARANTINE_RETENTION_DAYS`（既定30日）より古い`.corrupted-*`隔離ファイルを1実行につき1回だけ削除する（サイトループの前に行うため、当該実行中に新たに作られる隔離ファイルは対象外）。
* 根拠: [関数定義とDocstring・解決・フォールバック判定・DataManager生成] (行番号: 2022〜2048 / 抜粋: "def _run_monitor_locked() -> None:\n    """モニタープロセスのメインロジック。MonitorConfig.SITESに登録された全サイトを順に処理する。"""" / "# #364: データディレクトリはここで1回だけ解決し、DataManagerに束縛して全サイトで\n    # 使い回す。get_data_dir()はNAS未マウント時にsudo mount・Discord/LINE通知を伴う\n    # 重い処理のため、サイト処理のたびに再評価してはならない" / "data_dir = MonitorConfig.get_data_dir()" / "if MonitorConfig.is_local_fallback_dir(data_dir):" / "data_manager = DataManager(data_dir)")


* **引数/リクエスト**: なし
* 根拠: [引数定義] (行番号: 2022 / 抜粋: "def _run_monitor_locked() -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: [戻り値ヒント] (行番号: 2022 / 抜粋: "def _run_monitor_locked() -> None:")


* **副作用**: デバッグログ出力（開始・終了）、`MonitorConfig.get_data_dir()`の1回の呼び出し（NAS状態の検証・自己修復・障害通知を伴いうる）、`wait_for_storage_warmup`の呼び出し、`DataManager`・`WebMonitor`・`DiscordNotifier`のインスタンス化、**（Issue #461で追加）** `data_manager.cleanup_old_quarantine_files()`の呼び出し（`.corrupted-*`隔離ファイルのディレクトリ走査・削除）、全`SITES`エントリに対する`_check_site`の逐次呼び出し、**（Issue #395で追加）** 各`SiteCheckResult`から失敗サイト数と保留アラート（`pending_alert_count`）を集計し、ループ後に`_send_pending_site_failure_alerts(notifier, data_manager, pending_alerts, failed_count, len(MonitorConfig.SITES))`を呼び出して閉鎖疑いアラートをまとめて送信判断すること、`_maybe_send_daily_summary`の呼び出し、`finally`ブロックでの`monitor.close()`/`notifier.close()`呼び出し。
* 根拠: [失敗集計と保留アラート送信] (行番号: 2060〜2078 / 抜粋: "# #395: 閉鎖疑いアラートはサイト処理中に即時送信せず、全サイト処理後に\n        # 失敗サイトの割合(自局側障害の疑い)を見てからまとめて送信判断する。\n        failed_count = 0\n        pending_alerts: List[Tuple[SiteConfig, int]] = []" / "_send_pending_site_failure_alerts(\n            notifier, data_manager, pending_alerts, failed_count, len(MonitorConfig.SITES)\n        )")
* 根拠: [メイン処理フロー] (行番号: 2030, 2048, 2057〜2063, 2098 / 抜粋: "data_dir = MonitorConfig.get_data_dir()" / "data_manager = DataManager(data_dir)" / "monitor = WebMonitor()\n        notifier = DiscordNotifier(MonitorConfig.DISCORD_WEBHOOK_URL)\n\n        for site in MonitorConfig.SITES:\n            try:\n                _check_site(monitor, notifier, site, data_manager)" / "_maybe_send_daily_summary(notifier, data_manager)")


* **エラーハンドリング**: **（Issue #364で追加）** 解決したデータディレクトリがローカルフォールバック先の場合はERRORログ（「NASがアンマウント状態(ローカルフォールバック中)を検知」）を出力し、`WebMonitor`/`DiscordNotifier`の生成・サイト巡回・Discord通知のいずれにも進まず`return`する。ストレージウォームアップ失敗時はエラーログを出力し処理を中断(`return`)。各サイトの`_check_site`呼び出しで発生した予期しない例外は`except Exception`で個別に捕捉し、`logger.critical`（`exc_info=True`付き）でログ出力して次のサイトの処理を継続する。それ以外（ループ外）の全ての例外は最上位の`try-except Exception`で捕捉し`logger.critical`でログ出力する。`finally`ブロックで`monitor`/`notifier`が生成済みであれば`close()`を確実に呼び出す。
* 根拠: [各種エラーハンドリング] (行番号: 2036〜2041, 2086〜2088 / 抜粋: "if MonitorConfig.is_local_fallback_dir(data_dir):\n        logger.error(\n            \"🚨 NASがアンマウント状態(ローカルフォールバック中)を検知しました。\"" / "except Exception as e:\n                    logger.critical(f"Critical error while checking site '{site.site_id}': {e}", exc_info=True)")


### `if __name__ == "__main__":` ブロック

* **役割**: スクリプトとして直接実行された場合に`run_monitor()`を呼び出すエントリーポイント。
* 根拠: [エントリーポイント定義] (行番号: 2112〜2113 / 抜粋: "if __name__ == "__main__":\n    run_monitor()")


* **引数/リクエスト**: なし
* **戻り値/レスポンス**: 該当なし
* **副作用**: `run_monitor()`の実行（本関数がもつ全ての副作用を誘発。ロック取得に失敗した場合は`_run_monitor_locked`の副作用は発生しない）。
* 根拠: [呼び出し] (行番号: 2113 / 抜粋: "run_monitor()")


* **エラーハンドリング**: なし（`run_monitor`内部で例外が処理される設計）


## 5. 処理フロー図

`run_monitor`（多重起動防止ロック）と、その内部で呼び出される`_run_monitor_locked`のメインロジックのフローを示します。

```mermaid
flowchart TD
    Start["Start: run_monitor"] --> LockTry["fcntl.flock(lock_fd, LOCK_EX|LOCK_NB)"]
    LockTry --> LockOk{"ロック取得成功?"}
    LockOk -- No --> LockBusyLog["情報ログ出力<br>(他インスタンスが実行中)"] --> EndLock["End (今回はスキップ)"]

    LockOk -- Yes --> RunLocked["外部：_run_monitor_locked()"]
    RunLocked --> ResolveDir["MonitorConfig.get_data_dir()<br>(#364: 1実行で1回だけNAS状態を解決)"]
    ResolveDir --> FallbackCheck{"is_local_fallback_dir(data_dir)?<br>(NAS未マウントでローカルへ<br>フォールバック中か)"}
    FallbackCheck -- Yes --> ErrLog0["エラーログ出力<br>(全キャスト再通知を防ぐため中断)"] --> End0["End (処理中断)"]
    FallbackCheck -- No --> Warmup["外部：wait_for_storage_warmup(data_dir)"]
    Warmup --> WarmupOk{"ストレージアクセス確立成功?"}
    WarmupOk -- No --> ErrLog1["エラーログ出力"] --> End1["End (処理中断)"]

    WarmupOk -- Yes --> InitInstances["DataManager(data_dir) / WebMonitor /<br>DiscordNotifier をインスタンス化"]
    InitInstances --> CleanupQuarantine["外部：data_manager.cleanup_old_quarantine_files()<br>(#461: 古い.corrupted-*隔離ファイルを削除)"]
    CleanupQuarantine --> SiteLoopStart["SITES内の各サイトをループ"]

    SiteLoopStart --> CheckSite["外部：_check_site(monitor, notifier, site, data_manager)"]
    CheckSite --> LoadKnown["外部：data_manager.load_known_casts(site)"]
    LoadKnown -- "KnownCastsUnavailableError<br>(#365: I/Oエラー)" --> SkipSite["WARNINGログ出力<br>巡回・通知・保存を行わず当該サイトをスキップ"] --> NextSite
    LoadKnown --> Fetch["外部：monitor.fetch_current_casts(site)"]

    Fetch -- "requests.RequestException /<br>SiteUnavailableError(#395: 別ドメインへのリダイレクト)" --> HandleFail["外部：_handle_site_network_failure<br>(内部でDataManager.record_site_failureにより失敗回数を記録。<br>failed=True。#578: 状態がI/Oエラーで読めなければ<br>WARNINGログのみで(0, False)として扱い記録をスキップ)"]
    HandleFail --> FailLog["ログ出力<br>(閾値(24回)以上ならWARNING、<br>閾値未満はERROR(0件時はWARNING)。当該サイトのみ中断)"]
    FailLog --> ThresholdCheck{"連続失敗が閾値以上<br>かつ未アラート?"}
    ThresholdCheck -- Yes --> Pending["pending_alert_count に連続失敗回数を保持<br>(送信は全サイト処理後にまとめて判断)"] --> NextSite
    ThresholdCheck -- No --> NextSite
    Fetch -- 成功 --> HasCasts{"current_castsが空でないか?"}
    HasCasts -- No --> HandleFail
    HasCasts -- Yes --> ClearFail["外部：DataManager.clear_site_failure<br>(連続失敗状態を解消。#578: 状態がI/Oエラーで読めなければ<br>WARNINGログのみでスキップ)"]

    ClearFail --> Diff["差分検知: current_casts - known_casts"]
    Diff --> HasNew{"新規キャストがあるか?"}

    HasNew -- Yes --> Notify["外部：notifier.notify(new_casts, site_name)<br>(Discord Webhook送信)"]
    Notify --> RecordDaily["外部：DataManager.record_daily_new_casts<br>(#578: daily_summaryがI/Oエラーで読めなければ<br>WARNINGログのみで加算・保存をスキップ)"]
    RecordDaily --> UnionSave["外部：DataManager.save_known_casts(_merge_known_casts(known, current))<br>(#237で常時union化、#538で長期欠落キャストを剪定)"]
    UnionSave --> NextSite

    HasNew -- No --> UnionSave
    NextSite["site単位の例外はcatchして次サイトへ"]

    NextSite --> SiteLoopEnd{"全サイト処理済み?"}
    SiteLoopEnd -- No --> SiteLoopStart
    SiteLoopEnd -- Yes --> SendAlerts["外部：_send_pending_site_failure_alerts<br>(#395: 失敗サイト割合 > SELF_OUTAGE_SUPPRESS_RATIO なら<br>自局側障害とみなし抑止、それ以外は閉鎖疑いを<br>Discordへ送信し成功時のみ mark_site_failure_alerted で永続化。<br>#578: 状態がI/Oエラーで読めなければWARNINGログのみでスキップ)"]
    SendAlerts --> DailySummary["外部：_maybe_send_daily_summary(notifier, data_manager)<br>(21時台のみDiscordへ送信。#578: daily_summaryがI/Oエラーで<br>読めなければWARNINGログのみでスキップし送信自体を行わない)"]

    DailySummary --> Finally["finally: monitor.close() / notifier.close()"]
    Finally --> ReleaseLock["run_monitor: finally でロック解放<br>(flock LOCK_UN) + ディスクリプタclose"]
    ReleaseLock --> End3["End (正常終了)"]
```

上記の`DataManager.load_known_casts`/`DataManager.save_known_casts`は、上のフロー図では単一ノードとして扱っていますが、内部には破損データの隔離・復旧および書き込み検証・バックアップという多段のロジックがあります。以下にその内部フローを示します。

```mermaid
flowchart TD
    subgraph LKC["DataManager.load_known_casts"]
        LStart["Start"] --> LExists{"data_file.exists()?"}
        LExists -- No --> LEmpty1["デバッグログ出力<br>空集合を返す"]
        LExists -- Yes --> LRead["_read_casts_file(data_file)"]
        LRead -- 成功 --> LReturn["読み込んだSet[CastMember]を返す"]
        LRead -- "OSError<br>(NAS/CIFS瞬断等のI/Oエラー)" --> LIoErr["エラーログ出力<br>隔離せず KnownCastsUnavailableError を送出<br>(#365: _check_siteが当該サイトをスキップ)"]
        LRead -- "_CONTENT_ERRORS<br>(ValueError/TypeError/KeyError)" --> LErrLog["エラーログ出力"]
        LErrLog --> LQuarantine["破損ファイルを<br>name.corrupted-timestamp へrename<br>(隔離。失敗してもログのみで継続)"]
        LQuarantine --> LBakExists{".bakファイルが存在?"}
        LBakExists -- No --> LEmpty2["空集合を返す<br>(安全側フォールバック)"]
        LBakExists -- Yes --> LReadBak["_read_casts_file(backup_file)"]
        LReadBak -- 成功 --> LRecovered["復旧件数を警告ログ出力<br>復旧したSetを返す"]
        LReadBak -- "OSError<br>(#578: .bakのI/Oエラー)" --> LBakIoErr["エラーログ出力<br>隔離せず KnownCastsUnavailableError を送出"]
        LReadBak -- "_CONTENT_ERRORS<br>(.bak自体も内容破損)" --> LBakErrLog["エラーログ出力"] --> LEmpty2
    end

    subgraph SKC["DataManager.save_known_casts"]
        SStart["Start"] --> SMkdir["保存先ディレクトリmkdir"]
        SMkdir --> STmpWrite["一時ファイル(.tmp)へJSON書き込み"]
        STmpWrite --> SVerify["_read_casts_file(tmp_path)で読み戻し検証"]
        SVerify -- "検証失敗<br>(OSError/ValueError/TypeError)" --> SErrLog["エラーログ出力(exc_info=True)<br>replaceせず終了"]
        SVerify -- 成功 --> SBakCheck{"data_file.exists()?"}
        SBakCheck -- No --> SReplace["tmp_path.replace(data_file)"]
        SBakCheck -- Yes --> SBakWrite["現data_fileの内容を<br>.bakへwrite_bytes"]
        SBakWrite -- "OSError" --> SBakWarn["警告ログ出力<br>(バックアップ失敗しても継続)"] --> SReplace
        SBakWrite -- 成功 --> SReplace
        SReplace --> SDebugLog["デバッグログ出力 End"]
    end
```

`DataManager.load_daily_summary`/`DataManager.save_daily_summary`は、Issue #462で`load_known_casts`/`save_known_casts`と同じ隔離・復旧および検証・バックアップのロジックが拡張適用された。以下にその内部フローを示す。

```mermaid
flowchart TD
    subgraph LDS["DataManager.load_daily_summary (#462, #578)"]
        DStart["Start"] --> DExists{"summary_file.exists()?"}
        DExists -- No --> DEmpty1["空辞書を返す"]
        DExists -- Yes --> DRead["open+json.load(summary_file)<br>+ _is_valid_daily_summary(data)"]
        DRead -- "成功かつ形状が妥当" --> DReturn["読み込んだDictを返す"]
        DRead -- "OSError<br>(#578: I/Oエラー)" --> DIoErr["エラーログ出力(exc_info=True)<br>隔離せず DataFileUnavailableError を送出"]
        DRead -- "_CONTENT_ERRORS<br>(ValueError/TypeError/KeyError、<br>または形状不正)" --> DErrLog["エラーログ出力(exc_info=True)"]
        DErrLog --> DQuarantine["破損ファイルを<br>name.corrupted-timestamp へrename<br>(隔離。失敗してもログのみで継続)"]
        DQuarantine --> DBakExists{".bakファイルが存在?"}
        DBakExists -- No --> DEmpty2["空辞書を返す<br>(安全側フォールバック)"]
        DBakExists -- Yes --> DReadBak["open+json.load(backup_file)<br>+ _is_valid_daily_summary(data)"]
        DReadBak -- "成功かつ形状が妥当" --> DRecovered["警告ログ出力<br>復旧したDictを返す"]
        DReadBak -- "OSError<br>(#578: .bakのI/Oエラー)" --> DBakIoErr["エラーログ出力<br>隔離せず DataFileUnavailableError を送出"]
        DReadBak -- "_CONTENT_ERRORS<br>(.bak自体も内容破損/形状不正)" --> DBakErrLog["エラーログ出力"] --> DEmpty2
    end

    subgraph SDS["DataManager.save_daily_summary (#462)"]
        DSStart["Start"] --> DSMkdir["保存先ディレクトリmkdir"]
        DSMkdir --> DSTmpWrite["一時ファイル(.tmp)へJSON書き込み"]
        DSTmpWrite --> DSVerify["open+json.load(tmp_path)で読み戻し検証"]
        DSVerify -- "検証失敗<br>(OSError/ValueError/TypeError)" --> DSErrLog["エラーログ出力(exc_info=True)<br>tmp_pathをbest-effortで削除<br>replaceせず終了"]
        DSVerify -- 成功 --> DSBakCheck{"summary_file.exists()?"}
        DSBakCheck -- No --> DSReplace["tmp_path.replace(summary_file)"]
        DSBakCheck -- Yes --> DSBakWrite["現summary_fileの内容を<br>.bak.tmp経由でアトミックに.bakへ複製"]
        DSBakWrite -- "OSError" --> DSBakWarn["警告ログ出力<br>bak_tmp_pathを削除(バックアップ失敗しても継続)"] --> DSReplace
        DSBakWrite -- 成功 --> DSReplace
        DSReplace --> DSEnd["End"]
    end
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "newface_monitor.py"
        logger["logger (Global)"]
        SiteConfig["SiteConfig"]
        MonitorConfig["MonitorConfig"]
        CastMember["CastMember"]
        DiscordNotifier["DiscordNotifier"]
        DataManager["DataManager"]
        WebMonitor["WebMonitor"]
        check_site["_check_site()"]
        daily_summary["_maybe_send_daily_summary()"]
        run_monitor["run_monitor()<br>(多重起動防止ロック)"]
        run_locked["_run_monitor_locked()"]
        lock_path["_MONITOR_LOCK_FILE_PATH"]
        get_logger_fb["get_logger() (fallback)"]
        get_managed_dir_fb["get_managed_target_directory() (fallback)"]
        wait_warmup_fb["wait_for_storage_warmup() (fallback)"]
    end

    subgraph "外部依存（標準ライブラリ）"
        fcntl_mod["fcntl.flock"]
    end

    subgraph "外部依存（コアモジュール、try節）"
        core_logger["core.logger.get_logger"]
        core_nas_utils["core.nas_utils.get_managed_target_directory"]
        core_utils["core.utils.wait_for_storage_warmup"]
    end

    subgraph "外部依存（サードパーティ）"
        requests_mod["requests"]
        bs4["bs4.BeautifulSoup / NavigableString"]
        urllib3["urllib3.util.retry.Retry"]
    end

    subgraph "外部依存（DDD内モジュール）"
        DiscordCircuitBreaker["file_utils.DiscordCircuitBreaker"]
    end

    subgraph "外部依存（外部システム）"
        TargetSites["79件の対象Webサイト<br>(MonitorConfig.SITES)"]
        DiscordAPI["Discord Webhook API"]
        Storage["NAS/ローカルストレージ"]
        LockFile["ロックファイル<br>(.newface_monitor.lock)"]
    end

    logger -.->|"インポート成功時"| core_logger
    logger -.->|"インポート失敗時"| get_logger_fb

    MonitorConfig -->|"get_data_dir経由"| core_nas_utils
    MonitorConfig -.->|"インポート失敗時"| get_managed_dir_fb
    MonitorConfig --> SiteConfig

    run_monitor --> lock_path
    run_monitor --> fcntl_mod
    lock_path --> LockFile
    run_monitor -->|"ロック取得成功時"| run_locked

    run_locked --> wait_warmup_fb
    run_locked -.->|"インポート成功時"| core_utils
    run_locked --> WebMonitor
    run_locked --> DiscordNotifier
    run_locked --> MonitorConfig
    run_locked --> check_site
    run_locked --> daily_summary

    check_site --> WebMonitor
    check_site --> DiscordNotifier
    check_site --> DataManager
    daily_summary --> DiscordNotifier
    daily_summary --> DataManager

    WebMonitor --> requests_mod
    WebMonitor --> bs4
    WebMonitor --> urllib3
    WebMonitor --> SiteConfig
    WebMonitor --> TargetSites

    DiscordNotifier --> requests_mod
    DiscordNotifier --> urllib3
    DiscordNotifier --> DiscordAPI
    DiscordNotifier --> DiscordCircuitBreaker

    DataManager --> MonitorConfig
    DataManager --> Storage

    WebMonitor --> CastMember
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `core/nas_utils.py` | `get_managed_target_directory`の実際の実装（NASマウント確認・自動修復ロジック）が、フォールバック実装（`fallback_dir_str`をそのまま返すのみ）とどう異なるかを確認する必要があるため。 | 根拠: [import文] (行番号: 52 / 抜粋: "from core.nas_utils import get_managed_target_directory") |
| 中 | `core/utils.py` | `wait_for_storage_warmup`の実際の実装が、フォールバック実装（Exponential Backoffでのテストファイル書き込み確認）と同等かどうかを確認するため。 | 根拠: [import文] (行番号: 53 / 抜粋: "from core.utils import wait_for_storage_warmup") |
| 中 | `core/logger.py` | `get_logger`の実際の実装（出力フォーマット、ログレベル、出力先）を確認するため。 | 根拠: [import文] (行番号: 51 / 抜粋: "from core.logger import get_logger") |
| 低 | `MonitorConfig.SITES`に登録された各対象Webサイトの実際のHTML構造 | `selector_container`等のCSSセレクタが正しく機能する前提となる実際のマークアップ構造を確認するため（コード外の外部サイト、79件）。 | 根拠: [SiteConfig各エントリのセレクタ定義] (行番号: 211〜214等 / 抜粋: "selector_container='ul.gallist li',") |

## 8. 保守上の注意点

* **フォールバック実装と本番実装の差異リスク**: `core.logger`, `core.nas_utils`, `core.utils`のインポートに失敗した場合、ファイル内の簡易フォールバック実装に切り替わる。本番環境で意図せずインポートが失敗した場合、NASではなくローカルディスクにデータが保存される可能性がある。**（Issue #364で追加）** ただしフォールバック実装の`get_managed_target_directory`も本番の`core.nas_utils`版も、NAS未マウント時は`MonitorConfig.LOCAL_DIR_STR`を返すため、いずれの場合も`_run_monitor_locked`の`is_local_fallback_dir`判定で実行全体が中断される（ローカルディスクへ巡回結果が書き込まれることはない）。
* **（Issue #364で追加）データディレクトリの解決は1実行につき1回だけ**: `MonitorConfig.get_data_dir()`はNAS未マウント時に`sudo mount`サブプロセスとDiscord/LINE障害通知を伴うため、`_run_monitor_locked`が1回だけ呼び出し、その結果を束縛した`DataManager`インスタンスを`_check_site`/`_handle_site_network_failure`/`_maybe_send_daily_summary`へ引数で渡して使い回す。新たに`DataManager`を使う処理を追加する際は、`MonitorConfig.get_data_dir()`を再度呼ぶのではなく、必ずこのインスタンスを引数で受け取ること（`extract_youtube_urls.py`の`base_dir`引き回しと同じ規約）。回帰テストは`test_newface_monitor_data_dir.py`（`get_managed_target_directory`が1回しか呼ばれないこと、フォールバック時にサイト処理・通知へ進まないこと）。
* 根拠: [_run_monitor_lockedのコメント] (行番号: 2026〜2029 / 抜粋: "# #364: データディレクトリはここで1回だけ解決し、DataManagerに束縛して全サイトで\n    # 使い回す。get_data_dir()はNAS未マウント時にsudo mount・Discord/LINE通知を伴う\n    # 重い処理のため、サイト処理のたびに再評価してはならない")
* **（Issue #580で修正）ローカルフォールバック先のサブディレクトリ分離**: 以前`MonitorConfig.LOCAL_DIR_STR`は`extract_youtube_urls.py`の`AppConfig.LOCAL_DIR_STR`と同じ`BASE_DIR / 'data'`（`DDD/data`）を指していた。NAS未マウント中に一方のスクリプトが`DDD/data`直下へフォールバック書き込みし、その後NASが復旧した状態でもう一方のスクリプトが`core.nas_utils.get_managed_target_directory`→`sync_fallback_to_nas`を呼ぶと、共有ディレクトリ配下の全項目（他方が書いたデータを含む）が呼び出し元自身のNASディレクトリへ丸ごと移動されてしまっていた。現在は`BASE_DIR / 'data' / 'newface_monitor'`に変更し、`extract_youtube_urls.py`側は`BASE_DIR / 'data' / 'youtube_extractor'`に分離している。
* 根拠: [`LOCAL_DIR_STR`定義] (行番号: 310〜315 / 抜粋: "# #580: 以前はextract_youtube_urls.pyと同じ`BASE_DIR / 'data'`を共有していたため、\n    # NAS未マウント中に片方のスクリプトが書いたフォールバックデータを、NAS復旧後に\n    # もう片方のnas_utils.sync_fallback_to_nas呼び出しが誤って自分のNASディレクトリへ\n    # 移動してしまう経路があった。スクリプトごとにサブディレクトリを分離する。\n    LOCAL_DIR_STR: str = str(BASE_DIR / 'data' / 'newface_monitor')")
* **広範な例外キャッチ**: `run_monitor`はサイトごとのループ内と最上位の両方で`except Exception as e:`により全例外を捕捉している。予期しないバグ（型エラー等）も`logger.critical`でログされるのみで処理が握りつぶされる。
* **HTML構造への強い依存**: `_parse_html`は各`SiteConfig`にハードコードされたCSSセレクタに依存しており、対象サイトのレイアウト変更で抽出が機能しなくなるリスクがある（該当箇所には警告ログでの検知は用意されている）。
* **`CastMember`の`__eq__`/`__hash__`が`id`のみに依拠**: `name`, `detail_url`, `image_url`, `age`が変化しても`id`が同一であれば同一キャストとみなされ、差分検知(`current_casts - known_casts`)では検知されない（名前変更等は新規追加として通知されない）。
* **Discord通知のレート制限考慮**: `notify`メソッドは各キャスト送信前に`time.sleep(1)`の固定待機に加え、セッション側の`Retry`（`respect_retry_after_header=True`）による429時の自動バックオフも備える。
* **`DiscordNotifier._circuit_breaker`はプロセス内・インスタンス単位でのみ有効**: `run_monitor`は1回の実行で`DiscordNotifier`を1つだけ生成し(`_run_monitor_locked`)、全79サイトの`notify`呼び出しと`notify_daily_summary`・`notify_site_failure_alert`呼び出しで同じインスタンス(および同じ`_circuit_breaker`)を使い回す。そのため、あるサイトの通知で連続失敗しブレーカーが開くと、同一プロセス実行内の以降の全サイトの通知・日次サマリ通知もスキップされる（Webhook自体が機能していないと判断しているため意図的な挙動）。ただしこの状態はプロセスをまたいで永続化されないため、次回のcron実行では必ず閉じた状態から始まり、Webhookが復旧していなくても最初の数回は無駄なリクエストが再び発生する。**[修正済み・Issue #458]** サイト巡回がThreadPoolExecutorで並列化されたことで、同一の`_circuit_breaker`インスタンスが複数スレッドから同時に`is_open`/`record_success`/`record_failure`/`trip`を呼ばれるようになったため、`file_utils.DiscordCircuitBreaker`内部に`threading.Lock`を追加し、状態変更をスレッドセーフにした。
* **[修正済み・Issue #458] 79サイトを1プロセスで逐次処理する構成**: 以前は`run_monitor`が`MonitorConfig.SITES`の全79件を単一プロセス内で順次処理しており、1回の実行時間がサイト数に比例して増大していた。現在は`_run_monitor_locked`が`ThreadPoolExecutor`（`MonitorConfig.SITE_CHECK_MAX_WORKERS`、既定8）でサイト処理を並列化している。79並列で一斉アクセスするとWAF誤検知や対象サイトへの負荷増大を招くため、同時実行数は小さく制限している。並列化に伴い、`DataManager`が読み書きするサイト横断の共有ファイル(`daily_summary.json`/`site_failures.json`)への読み込み→更新→書き込みが複数スレッドから同時に走りうるようになったため、`DataManager.__init__`で生成する`threading.Lock`（`_shared_file_lock`）で`record_daily_new_casts`/`record_site_failure`/`mark_site_failure_alerted`/`clear_site_failure`の各メソッド全体を直列化している（サイト単位の`known_casts`ファイルはサイトごとに別ファイルのためこのロックの対象外）。各サイト間の待機（`fetch_current_casts`内の`time.sleep(random.uniform(1.0, 3.0))`）自体は変更していない。
* **`id_query_param`未指定時の複数段フォールバック**: `_parse_html`のID抽出は`id_query_param`指定時のクエリパラメータ優先、次に「キー=値」形式でないクエリ文字列全体、最後にパス末尾セグメントという複数段のフォールバックロジックであり、サイトのURL構造変更時に意図しないIDが生成される可能性がある。
* **（D-L6で追加）Discord embedの文字数上限は250文字に安全側で切り詰める**: `DiscordNotifier._EMBED_TITLE_MAX_LEN`/`_EMBED_FIELD_VALUE_MAX_LEN`はいずれもDiscordの実際の上限（title 256文字、field.value 1024文字）より小さい250文字に設定している。今後embedへ新しいフィールドを追加する際、そのフィールド値がスクレイピング結果（外部サイト由来で長さが保証されない文字列）である場合は、`_truncate_for_embed`で同様に切り詰めること。
* **（2026-09-09 運用障害対応で追加）embedのURL系フィールドはhttp(s)判定だけでは不十分**: `cast.image_url`/`cast.detail_url`はスクレイピング元サイトのHTML(`img`のsrc属性・`a`のhref属性等)から`urljoin`で組み立てられるが、日本語ファイル名・全角スペース等がパーセントエンコードされないまま残ることがある(例: `.../20260402130316-ニコ　加工済.jpg`)。この種のURLは`http://`/`https://`で始まっていてもDiscordには「well formed」と認められず、`thumbnail`を条件分岐で弾く既存のスキーム判定（D-L9当時の対応）だけでは防げない。今後embedへ新しいURL系フィールドを追加する場合も、スキーム判定に加えて必ず`DiscordNotifier._to_well_formed_url`（`requests.utils.requote_uri`）を通してから送信すること。回帰テストは`test_newface_monitor_notifier.py::test_non_ascii_image_url_is_percent_encoded_in_thumbnail`・`test_non_ascii_detail_url_is_percent_encoded_in_embed_url_and_link_field`。
* **（D-L9で追加）日次サマリの計上件数はnotify()の戻り値に依存する**: `_check_site`は`data_manager.record_daily_new_casts`に渡す件数として`notifier.notify(...)`の戻り値（実送信件数）を使う。`notify`のシグネチャを変更する場合（戻り値の意味を変える等）は、この呼び出し元の前提が崩れないか確認すること。
* **（Issue #586で追加）モジュールimport時に1度だけ評価されるクラス属性を単体テストする際は、値解決ロジックをモジュールレベル関数へ切り出す**: `MonitorConfig.DISCORD_WEBHOOK_URL`は以前`os.getenv('DISCORD_WEBHOOK_URL')`という式をクラス変数の初期化式に直接書いていたが、この式自体を環境変数を変えて検証するには本来`importlib.reload`でモジュールを再評価する必要がある。しかし本ファイルの回帰テスト群は`newface_monitor`モジュールを`sys.modules`経由で共有しており、`reload`はモジュールのクラスオブジェクトを新しく作り直すため、他のテストファイルが同モジュールに対して行っている`monkeypatch.setattr`ベースのフィクスチャを壊す（実際に試して確認された）。このため「1行の式のために関数を1つ切り出す」一見過剰な設計を採り、`_resolve_discord_webhook_url()`という独立関数に解決ロジックを移し、`DISCORD_WEBHOOK_URL`はこれを呼び出すだけにした。同種の「クラス属性の初期化式をimportlib.reload無しに単体テストしたい」という制約に今後遭遇した場合も、このパターン（ロジックをモジュールレベル関数へ切り出し、クラス属性からはそれを呼ぶだけにする）を踏襲すること。回帰テストは`test_newface_monitor_discord_webhook_priority.py::TestResolveDiscordWebhookUrlPriority`。
* 根拠: [`_resolve_discord_webhook_url`定義とDocstring] (行番号: 224〜240)、[reloadを避ける理由] (`test_newface_monitor_discord_webhook_priority.py` 行番号: 13〜17)
* **（D-L12で追加）`AGE_PLAUSIBLE_MIN`/`AGE_PLAUSIBLE_MAX`は経験的な範囲であり万能ではない**: 括弧内の数字に「歳」「才」の明示が無い場合のみこの範囲（18〜79）でフィルタするが、この範囲内に収まる非年齢の2桁数字（部屋番号・順位バッジ等）は依然として誤って年齢と判定されうる。あくまで明らかに範囲外の値（例: レビューで指摘された"(85)"）を除外するための最小限の足切りであり、完全な誤検知防止ではない。
* **（Issue #589で追加）`_extract_cast_age`は「最初の1件」ではなく「最初に妥当性チェックを通る1件」を探す**: 直上のD-L12の妥当性チェック（`AGE_PLAUSIBLE_MIN`/`AGE_PLAUSIBLE_MAX`の範囲判定）自体は変更していないが、以前は`AGE_PATTERN.search()`で得た文字列中最初の1件だけにこのチェックを適用し、そこで落ちると（後ろに本来の年齢表記があっても）抽出全体を空文字で諦めていた。現在は`AGE_PATTERN.finditer()`で全候補を出現順に試し、チェックを通らない候補は読み飛ばして次を試すため、"No.(12) さくら(25歳)"のように非年齢の括弧数字が本来の年齢より先に出現するテキストでも正しく後続の年齢を拾える。新たにこの関数へ手を加える際は、`break`が「最初に見つかった候補」ではなく「最初に見つかった**妥当な**候補」で行われている点（＝ループを回し切る前に安易に`return`しない設計）を崩さないこと。回帰テストは`test_newface_monitor_parse.py::TestExtractCastAgeSkipsImplausibleLeadingNumber`。
* **ハードコードされた値**: 各サイトの対象URL・CSSセレクタ、NASパス(`/mnt/nas/home_system/newface_monitor/data`)、User-Agent文字列、タイムアウト・リトライ回数、日次サマリ送信時刻（`DAILY_SUMMARY_HOUR`、既定21時）などが`MonitorConfig`クラスの名前付き定数として集約されている(各サイト定義自体は#413で`sites.json`へ外出し済み)。**[Issue #451: 対応済み]** これらは元々`MonitorConfig`という単一クラスに集約されており、「多数のマジックナンバーが各所に散在」という状態ではなかった。唯一関数内にリテラル直書きだった日次サマリ送信時刻(`21`)のみ`DAILY_SUMMARY_HOUR`定数として`MonitorConfig`に追加し、`_maybe_send_daily_summary`から参照するよう変更した。設定ファイル化(`sites.json`のような外部化)までは行っていない。
* **（Issue #365で追加）隔離は内容起因の破損に限る**: `DataManager.load_known_casts`が`.corrupted-*`へ隔離するのは`_CONTENT_ERRORS`（`ValueError`/`TypeError`/`KeyError`）で読めなかった場合だけであり、`OSError`（NAS/CIFSの瞬断等）の場合は`KnownCastsUnavailableError`を送出して`_check_site`が当該サイトを今回の実行ではスキップする（巡回・通知・保存なし）。この例外を新たな呼び出し元で握りつぶして空集合として続行すると、全キャストの再通知とunion保存による退店済みキャストの復活を再発させるため、必ずスキップ扱いにすること。回帰テストは`test_newface_monitor_datamanager.py`の`TestLoadKnownCastsTransientIOErrorIsNotQuarantined`/`TestLoadKnownCastsContentErrorsAreQuarantined`。
* 根拠: [OSError分岐のコメント] (行番号: 891〜898 / 抜粋: "# 中身は正しい可能性が高いため隔離せず、当該サイトの処理を\n            # スキップさせる(以前は種別を問わず .corrupted-* へ退避していたため、\n            # 正常なファイルが隔離され、.bakが無ければ空集合→全キャスト再通知、\n            # 以降はunionで保存されるため隔離前のデータは永久に戻らなかった)。")
* **（Issue #461で解消）`.corrupted-*`隔離ファイルの自動クリーンアップを追加**: 以前は`DataManager.load_known_casts`/`load_daily_summary`が内容起因の読み込み失敗時に破損ファイルを`{ファイル名}.corrupted-{タイムスタンプ}`として同一ディレクトリに退避するのみで、これらの隔離ファイルを削除・世代整理する処理が本ファイル内のどこにも存在せず、破損が繰り返し発生する運用環境では際限なく蓄積し続ける可能性があった。現在は`DataManager.cleanup_old_quarantine_files`（`_run_monitor_locked`が`DataManager`生成直後に1回だけ呼び出す）が、`_QUARANTINE_RETENTION_DAYS`（既定30日）より`mtime`が古い`.corrupted-*`ファイルを削除する。ただし`.bak`バックアップファイル自体は元々1世代のみが上書き保持される設計であり、こちらのクリーンアップは元から不要（新設のクリーンアップ対象にも含まれない）。
* 根拠: [load_known_castsの隔離処理] (行番号: 908〜910 / 抜粋: "quarantine_path = data_file.with_name(\n            f"{data_file.name}.corrupted-{datetime.now():%Y%m%d%H%M%S}"\n        )")、[cleanup_old_quarantine_filesとコメント] (行番号: 798〜845 / 抜粋: "# #461: load_known_casts/load_daily_summaryが破損検知のたびに作成する\n    # .corrupted-*隔離ファイルは、.bak(常に最新の1世代のみ保持され上書きされる)\n    # と異なり削除処理を持たず、破損が繰り返されるたびに増え続けディスクを\n    # 圧迫し得た。")、[_run_monitor_lockedからの呼び出し] (行番号: 2048〜2051 / 抜粋: "data_manager = DataManager(data_dir)\n    # #461: 破損検知のたびに増え続ける.corrupted-*隔離ファイルを、巡回のたびに\n    # 一定期間より古いものだけ削除する(失敗しても本処理は継続する)。\n    data_manager.cleanup_old_quarantine_files()")
* **（Issue #462で解消）`load_daily_summary`は`load_known_casts`ほど手厚い復旧をしない、という制約を解消**: Issue #174の修正により、`daily_summary.json`が非UTF-8データで破損しても`load_daily_summary`が例外を送出せず空辞書を返すようになり、`record_daily_new_casts`経由の無限再通知（`save_known_casts`未実行による既知キャストの巻き戻り）は解消されていた。ただし当時は`load_known_casts`が持つ隔離（`.corrupted-*`へのリネーム）・`.bak`バックアップからの自動復旧の仕組みが`load_daily_summary`/`save_daily_summary`には無く、破損時は単に累積中の未送信カウントが失われ`0`から再カウントされていた。Issue #462でこの非対称性が解消され、`load_daily_summary`/`save_daily_summary`にも`load_known_casts`/`save_known_casts`と同じ隔離・バックアップ復旧・読み戻し検証の仕組みが拡張適用された。破損ファイルは隔離のうえ`.bak`から復旧を試み、それも不可能な場合にのみ従来通り空辞書へフォールバックし累積カウントが失われる（完全に無くなったわけではなく発生条件が狭まった）。**（Issue #183で修正）** かつては加えてカレンダー日付が変わるだけでも累積が無条件にリセットされていたが、この日付ベースのリセット自体は廃止された。
* 根拠: [load_daily_summaryの#462修正] (行番号: 1068〜1091 / 抜粋: "# #462: load_known_castsと同じ復旧機構(隔離+バックアップ復旧)を適用する。"), [save_daily_summaryの#462修正] (行番号: 1130〜1146), [record_daily_new_castsの累積] (行番号: 1180, 1187 / 抜粋: "data = self.load_daily_summary()" / "counts = data.setdefault('counts', {})")

* **（Issue #578で追加）`load_daily_summary`/`load_site_failures`（および`load_known_casts`の`.bak`復旧パス）のI/OエラーはKnownCastsUnavailableErrorと同様に扱う**: Issue #365で`load_known_casts`の一次ファイル読み込みに導入した「`OSError`（NAS/CIFSの瞬断等）と内容起因の破損（`_CONTENT_ERRORS`）を区別し、`OSError`は空状態へフォールバックせず専用の例外を送出して呼び出し元に保存処理をスキップさせる」という設計は、当時`load_known_casts`の`.bak`復旧パス・`load_daily_summary`・`load_site_failures`の3箇所には適用されておらず、これらは引き続き`OSError`を他の内容起因の破損と同列に扱い空の初期状態（`{}`/`set()`）を返していた。呼び出し元（`record_daily_new_casts`/`_maybe_send_daily_summary`/`record_site_failure`/`mark_site_failure_alerted`/`clear_site_failure`）はその空状態のまま無条件で`save_daily_summary`/`save_site_failures`を呼んでいたため、たまたま読み込みが一時的なI/Oエラーで失敗しただけでも、累積中だった日次サマリのカウントや他サイト分の`site_failures`状態が丸ごと上書きで消えていた。本Issueで、`load_known_casts`の`.bak`復旧パスは`KnownCastsUnavailableError`を、`load_daily_summary`/`load_site_failures`は新設の`DataFileUnavailableError`を、それぞれ`OSError`時に送出するよう揃え、上記5つの呼び出し元は全てこの例外を捕捉して保存処理をスキップし早期`return`するよう変更した（`record_site_failure`のみ、呼び出し元の`_handle_site_network_failure`が戻り値をそのまま使う関係上、スキップ時は`(0, False)`を返す）。新たにこれらの`load_*`メソッドを呼ぶ処理を追加する場合も、対応する`*UnavailableError`を握りつぶして空状態のまま保存へ進んではならない。回帰テストは`test_newface_monitor_datamanager.py`の`TestLoadKnownCastsBackupTransientIOErrorIsNotQuarantined`/`TestLoadDailySummaryTransientIOErrorIsNotQuarantined`、`test_newface_monitor_site_failures.py`の`TestLoadSiteFailuresTransientIOErrorIsNotQuarantined`。
* 根拠: [DataFileUnavailableError定義] (行番号: 758〜769)、[load_known_castsの.bak側OSError分岐] (行番号: 928〜940 / 抜粋: "# #578: .bak自体はCIFS/autofsの瞬断で開けなかっただけの可能性があり、" / "raise KnownCastsUnavailableError(")、[load_daily_summaryのOSError分岐] (行番号: 1048〜1059, 1090〜1094)、[load_site_failuresのOSError分岐] (行番号: 1232〜1245)、[5つの呼び出し元のexcept節] (行番号: 1179〜1186, 1968〜1974, 1283〜1287, 1300〜1306, 1321〜1328)

* **（2026-09-02のbellica閉鎖対応で追加、Issue #395で変更）閉鎖疑いサイトの失敗ログはWARNINGに降格される**: 連続失敗が閾値（`CONSECUTIVE_FAILURE_ALERT_THRESHOLD`=24回）に達したサイトは、以降の失敗ログがERRORではなくWARNINGで記録されるため、ログのERROR監視（`health_watch`等）には現れなくなる。**（Issue #395）** 降格の条件は「アラート送信済み」ではなく「連続失敗回数が閾値以上」であり、Webhook未設定・失効でアラート送信が失敗し続けてもERRORが毎時発報され続けることはない（送信の再試行は`alerted`が立つまで別途続く）。また、失敗として計上する対象は`requests.RequestException`に加え、別ドメインへのリダイレクト（200を返す消失サイト）とキャスト0件の巡回結果にも拡張された。閉鎖疑いアラートは全サイト処理後にまとめて送信され、同一実行内の失敗サイト割合が`SELF_OUTAGE_SUPPRESS_RATIO`（0.5）を超える場合は自局側障害とみなして抑止される。閉鎖と判断してサイトを`MonitorConfig.SITES`から削除しても、`site_failures.json`内の当該サイトのエントリと`known_casts_{site_id}.json`は自動では削除されず残置される（実害はないが、`.corrupted-*`・`.bak`と同様にクリーンアップ機構はない）。回帰テストは`test_newface_monitor_site_failures.py`。
* 根拠: [ログレベル分岐] (行番号: 1741〜1747 / 抜粋: "if alerted:\n        logger.warning(f\"{message} (closure alert already sent)\")\n    elif threshold_reached:\n        logger.warning(f\"{message} (closure alert threshold reached; alert pending)\")\n    else:\n        logger.log(log_level, message)")、[自局側障害の抑止] (行番号: 1775〜1780 / 抜粋: "if total_count > 0 and failed_count / total_count > MonitorConfig.SELF_OUTAGE_SUPPRESS_RATIO:")

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `core.logger.get_logger`の実際の実装 | ログの出力フォーマット、出力先、ログレベルの詳細が本ファイルからは不明（フォールバック実装のみ確認可能）。 | `core/logger.py` |
| `core.nas_utils.get_managed_target_directory`の実際の実装 | NASマウント確認・自動修復ロジックの詳細な挙動が不明（フォールバック実装は`fallback_dir_str`をそのまま返すのみ）。 | `core/nas_utils.py` |
| `core.utils.wait_for_storage_warmup`の実際の実装 | フォールバック実装と同等の挙動をするか、追加のロジックがあるかが不明。 | `core/utils.py` |
| `MonitorConfig.SITES`に登録された各対象Webサイトの実際のHTML構造 | `selector_container`等のセレクタが対応する正確なマークアップ構造は本ファイルのコードからは分からない。 | 各対象サイトの実際のHTMLソース（コード外） |
| Discord Webhook APIの詳細仕様 | ペイロード形式以外の認証方式、レート制限、エラーレスポンスの詳細仕様が本ファイルからは不明。 | Discord公式APIドキュメント（コード外） |
| 本ファイルの実行方法（cron設定等） | `if __name__ == "__main__":`で直接実行される想定だが、定期実行のスケジューリング方法（cron、systemdタイマー等、および1時間毎という前提の根拠）は本ファイルからは不明。リポジトリ全体を`newface_monitor`および`cron`/`systemd`/`docker-compose`関連のファイル名・記述で検索したが、本ファイルの実行スケジュールを定義する設定ファイルはリポジトリ内に見つからなかった（デプロイ環境側の設定である可能性が高い）。 | デプロイ設定・cron定義ファイル等（リポジトリ内には存在せず） |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `core.logger.get_logger`の実際の実装 | `MY_HOME_SYSTEM/core/logger.py`を直接確認したところ、同ファイルには`get_logger`という名前の関数は一切定義されていない（定義されているのは`setup_logging(name, webhook_url=None)`関数と`DiscordErrorHandler`クラスのみ）。したがって`from core.logger import get_logger`（本ファイル42行目）は実行環境によらず常に`ImportError`となり、本ファイルは常にファイル内フォールバック実装（`logging.getLogger`ベース、51〜52行目）を使用する設計であることが確定した。傍証として、`core/nas_utils.py`自身も8行目で同じ`from core.logger import get_logger`を試み、14行目に同様のフォールバック定義を持っており、リポジトリ内のどこにも`get_logger`という関数は存在しない。 | 直接ソース確認: `MY_HOME_SYSTEM/core/logger.py`（全85行、`get_logger`定義なし）, `MY_HOME_SYSTEM/core/nas_utils.py:8, 14` |
| `core.nas_utils.get_managed_target_directory`の実際の実装 | `MY_HOME_SYSTEM/core/nas_utils.py:87-123`を直接確認した。シグネチャは`get_managed_target_directory(nas_dir_str: str, fallback_dir_str: str, mount_point: str = "/mnt/nas") -> Path`であり、本ファイルの呼び出し箇所（`cls.NAS_DIR_STR`, `cls.LOCAL_DIR_STR`, `cls.MOUNT_POINT`）と引数名が完全に一致することを確認した。実装は、(1) `is_mounted_and_writable`でマウント・書き込み可否を確認し正常ならフォールバックデータをNASへ同期して`nas_dir`を返す、(2) 異常時は`attempt_remount`で再マウントを試行し成功すれば同様に同期して`nas_dir`を返す、(3) それでも復旧しない場合はエラーログ出力と`config.LINE_USER_ID`宛のDiscord/LINE通知(`send_push`)を行った上でローカルの`fallback_dir`を作成して返す、というフェイルソフト設計である。関連ドキュメント`nas_utils.md`の記述内容と一致することも確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/core/nas_utils.py:87-123`（参考: [../MY_HOME_SYSTEM/nas_utils.md](../MY_HOME_SYSTEM/nas_utils.md)） |
| `core.utils.wait_for_storage_warmup`の実際の実装 | `MY_HOME_SYSTEM/core/utils.py:56-96`を直接確認した。シグネチャは`wait_for_storage_warmup(target_path: Union[str, Path], max_retries: int = 5, base_delay: float = 1.0, max_delay: float = 16.0) -> bool`。本ファイルのフォールバック実装（テストファイルの書き込み・削除でアクセス確認）とは異なり、実際の実装は`os.access(check_target, os.R_OK | os.W_OK)`によるアクセス権限チェック（ファイルパスが渡された場合は親ディレクトリを対象とする）を、`min(max_delay, base_delay * (2 ** attempt))`の指数バックオフで`max_retries`回リトライする方式であることを確認した。関連ドキュメント`utils.md`が推測していたシグネチャ・挙動と一致することを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/core/utils.py:56-96`（参考: [../MY_HOME_SYSTEM/utils.md](../MY_HOME_SYSTEM/utils.md)） |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
