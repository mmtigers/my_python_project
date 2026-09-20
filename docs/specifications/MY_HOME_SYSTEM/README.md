# MY_HOME_SYSTEM 仕様書一覧

IoT機器の制御、環境データの収集・分析、各種API・Webhookの統合ルーティングを担うFastAPIバックエンドの仕様書索引（全90件）。全体像は[全体設計書.md](../全体設計書.md)を参照。カテゴリA〜Fは全体設計書「2.1 コンポーネント一覧と役割」の分類に、G「その他」は各仕様書の記述をもとに追加で割り振ったもの。

## A. コアサーバー・ルーティング機構

| 仕様書 | 概要 |
| --- | --- |
| [unified_server.md](./unified_server.md) | FastAPIサーバーの起動・設定を行う統合エントリーポイント。ルートディレクトリ解決、CORS設定、IP検証、各種ルーターの統合を行う。 |
| [system_router.md](./system_router.md) | 手動バックアップをトリガーするPOSTエンドポイントを提供するFastAPIルーター。 |
| [webhook_router.md](./webhook_router.md) | 外部システム（LINE Bot・SwitchBot等）からのWebhookリクエストを受け取り、適切なハンドラ・サービスへルーティングする。 |
| [camera_router.md](./camera_router.md) | カメラのライブ配信（HLS）・録画セグメントの一覧取得や配信APIを提供する（`camera_service.py`に処理を委譲）。 |
| [dashboard_router.md](./dashboard_router.md) | Streamlitダッシュボードを`config.DASHBOARD_BASE_PATH`（既定`/dashboard`）配下で配信するルーター。中継処理は`dashboard_proxy_service.py`へ委譲する。 |
| [dashboard_proxy_service.md](./dashboard_proxy_service.md) | localhost束縛のStreamlitダッシュボード(8501)へHTTPとWebSocketの双方を中継するリバースプロキシ。スマートフォンからの到達をCloudflare Access配下の8000番経由に一本化するための実装。 |

## B. ハードウェア・IoT制御モジュール

| 仕様書 | 概要 |
| --- | --- |
| [switchbot_service.md](./switchbot_service.md) | SwitchBotデバイス（プラグ、ボット等）の制御およびステータス（電源状態、消費電力等）の取得。 |
| [nature_remo_monitor.md](./nature_remo_monitor.md) | Nature Remoを介した家電制御と、温度・湿度等の環境センサーデータの監視。 |
| [camera_monitor.md](./camera_monitor.md) | ネットワークカメラの稼働監視およびスナップショット取得。 |
| [tv_lock_monitor.md](./tv_lock_monitor.md) | TVの稼働時間を監視し、規定時間を超えた場合のロック制御を行う。 |
| [connect_speaker.md](./connect_speaker.md) | スマートスピーカー等への音声出力・通知連携。 |
| [camera_service.md](./camera_service.md) | ONVIF対応カメラのRTSP URL取得、ffmpegによるライブHLS配信・録画VODプレイリスト生成を担うサービス層。 |
| [network_logger.md](./network_logger.md) | カメラのIPアドレスに対しICMP PingとRTSPポートへのTCP接続試行を定期実行し、レイテンシとステータスをCSVに記録する。 |
| [sound_manager.md](./sound_manager.md) | イベントキーに基づく音声ファイルの非同期再生、および音声ファイル欠損時のデフォルトディレクトリからの復旧を行う。 |
| [switchbot.md](./switchbot.md) | SwitchBot関連のWebhookペイロード・API状態レスポンスのデータ構造を定義するPydanticモデル群。 |
| [switchbot_power_monitor.md](./switchbot_power_monitor.md) | 監視対象のSwitchBotデバイスから電力・温湿度・電源状態を定期取得し、後続の処理サービスへ連携するデバイス監視スクリプト。 |
| [sensor_service.md](./sensor_service.md) | センサーおよび電力計からのデータ受信（Webhook・ポーリング）を処理し、重複排除・状態管理・ログ保存・通知送信を行う。 |
| [keep_alive_anker.md](./keep_alive_anker.md) | Anker SoundCore Bluetoothスピーカーがオートパワーオフでスリープしないよう、可聴域外の無音波(15Hz)を定期再生してキープアライブするシェルスクリプト。 |
| [keep_alive_speaker.md](./keep_alive_speaker.md) | **廃止**: 無音MP3ファイルを定期再生し、Bluetoothスピーカー等のオーディオ経路を維持する「ハートビート」送信用シェルスクリプト。Issue #664 で [keep_alive_anker.md](./keep_alive_anker.md) へ一本化し、ソースごと削除された(接続確認・自動再接続を持たず、音源がNASマウント依存だったため)。仕様書は廃止noticeつきで履歴として残している。 |

## C. 外部サービス・通知連携

| 仕様書 | 概要 |
| --- | --- |
| [alexa_handler.md](./alexa_handler.md) | Alexaカスタムスキル「ファミクエ」のリクエストハンドラ群。LaunchRequestをAPL(画面)または読み上げでファミリークエストの状況表示にディスパッチする。 |
| [line_logic.md](./line_logic.md) | LINE Messaging APIのWebhook PostbackEvent（ボタン操作）専用の処理ロジック。 |
| [line_handler.md](./line_handler.md) | LINE Bot APIのWebhookイベント（メッセージ・ポストバック）を解析し、各処理へ振り分けるディスパッチャ。 |
| [line.md](./line.md) | LINE連携のイベント・Postbackデータ構造を定義するPydanticモデル群。 |
| [line_service.md](./line_service.md) | LINEメッセージからの情報記録・取得、クエストステータス照会、承認・却下処理を担う。 |
| [notification_service.md](./notification_service.md) | DiscordおよびLINEへのメッセージ（テキスト・画像）通知を行い、LINE送信失敗時にDiscordへフォールバックする。 |
| [train_service.md](./train_service.md) | JR西日本の運行情報API、およびYahoo!路線情報から運行状況・最短経路を取得する（フェイルソフト設計）。 |

## D. AI・分析エンジン

| 仕様書 | 概要 |
| --- | --- |
| [ai_service.md](./ai_service.md) | LLMを活用した推論・テキスト生成の共通インターフェース。 |
| [log_analyzer.md](./log_analyzer.md) | 蓄積された各種ログ（センサー、タスク消化、システムログ）のパターンを分析する。 |
| [weekly_analyze_report.md](./weekly_analyze_report.md) | 週次で家庭内の状況（健全性、タスク消化率など）をAIで要約し、レポートとして出力（LINE等へ送信）する。 |
| [analysis_service.md](./analysis_service.md) | DB・OS情報・外部APIからデータを取得し、Pandas等で加工・集計するデータ分析用サービス層。 |

## E. クエストバックエンド (Family Quest用API)

| 仕様書 | 概要 |
| --- | --- |
| [quest_router.md](./quest_router.md) | フロントエンド(Family Quest)からのリクエストを処理するクエストAPIルーティング。 |
| [quest_service.md](./quest_service.md) | Issue #550で下記6ファイルへ分割された後に残った、既存importパス互換のための再エクスポート層(シム)。 |
| [quest_locks.md](./quest_locks.md) | クエスト完了・承認・購入・アイテム使用のプロセス内排他ロックと、YouTubeごほうび券クールダウン判定・JST/ロール等の共有定数。 |
| [quest_user_service.md](./quest_user_service.md) | 家族統計(レベル・ゴールド合計、達成クエスト数)の集計とアバター画像の更新・孤立ファイル削除。 |
| [quest_quest_service.md](./quest_quest_service.md) | クエストの完了のドメインロジックと、兄妹連携クエスト・TV解錠・連続達成ボーナス計算。承認・却下・取消は quest_approval_service.md へ分離済み(Issue #662)。 |
| [quest_approval_service.md](./quest_approval_service.md) | クエストの承認・却下・取消のドメインロジックと、残高ロックによる並行実行時の lost update 防止。 |
| [quest_rewards.md](./quest_rewards.md) | クエスト報酬(gold/exp/medal)の付与。承認系と完了系が共有する唯一の実装。 |
| [quest_shop_service.md](./quest_shop_service.md) | 報酬購入時のゴールド減算・在庫付与をアトミックに行う。 |
| [quest_inventory_service.md](./quest_inventory_service.md) | 所持アイテムの一覧取得と、YouTubeごほうび券のクールダウンを考慮したアイテム使用処理。 |
| [quest_game_system.md](./quest_game_system.md) | quest_data(マスターデータ)とDBの同期、およびFamily Questフロントエンド向け画面集約データの生成。 |
| [quest_master_sync_sql.md](./quest_master_sync_sql.md) | quest_master/reward_masterへのUPSERT文とパラメータ組み立ての一元管理(Issue #664)。GameSystem.sync_master_dataが使う。 |
| [quest_master_sync_marker.md](./quest_master_sync_marker.md) | quest_data.py/routine_data.pyの内容ダイジェストをマーカーに記録し、差分があるときだけマスタ同期を走らせる冪等判定(Issue #700)。sync_strict.py --if-staleが使う。 |
| [game_logic.md](./game_logic.md) | レベルアップ必要経験値・最大HP・ドロップ報酬計算といったゲームルールロジック。旧版に記載のあった「ボス討伐状況の更新」はボス機能の廃止（`d1599d6`）に伴い該当ロジックが削除されている。 |
| [quest.md](./quest.md) | クエストシステムのドメイン/リクエスト/レスポンス/インベントリモデルを定義するPydanticモデル群。 |
| [quest_data.md](./quest_data.md) | Family Questのマスターデータ（ユーザー情報、クエスト定義、報酬定義）を定義する純粋なデータ定義モジュール。 |
| [reset_game.md](./reset_game.md) | Family QuestのDB上のユーザーゲームデータ（レベル・経験値・ゴールド・メダル数）をリセットするCLIスクリプト。 |
| [sync_strict.md](./sync_strict.md) | マスターデータ（QUESTS, REWARDS）とDBのマスターテーブルを完全同期する手動CLI。同期の実体はGameSystem.sync_master_data(strict=True)にあり(Issue #664)、本体は引数解析と安全ガードのみ。 |

## F. インフラ・監視タスク (フェイルソフト機構)

| 仕様書 | 概要 |
| --- | --- |
| [server_watchdog.md](./server_watchdog.md) | サーバープロセスの死活監視。異常検知時の再起動処理など。 |
| [nas_monitor.md](./nas_monitor.md) | NASの接続状態、ディスク容量の監視。枯渇前の事前アラート発報。 |
| [memory_monitor.md](./memory_monitor.md) | システムのメモリ使用量を監視し、OOM (Out of Memory) を未然に防ぐ。 |
| [backup_service.md](./backup_service.md) | データベースのバックアップを実行しNASへ転送する。転送失敗時は即時通知を行う。 |
| [post_boot_health_check.md](./post_boot_health_check.md) | システム起動直後にハードウェア・ネットワーク・DB・周辺機器・各種サービスの健全性を一括チェックするスクリプト。 |
| [scripts_firewall_apply.md](./scripts_firewall_apply.md) | アプリポート(8000)への接続元を loopback・直結サブネット・Tailscale に限定する iptables ルールを適用する(2026-09-19 新設。`home_firewall.service` から起動時に実行)。 |
| [switchbot_webhook_fix.md](./switchbot_webhook_fix.md) | 環境変数のベースURLを用いて、SwitchBotおよびLINE BotのWebhookエンドポイントを自動的に更新・修復する。 |
| [notify_task_failure.md](./notify_task_failure.md) | `run_task.sh` 経由の cron タスクが失敗したことを Discord へ通知する CLI(2026-09-20 新設、Issue #751)。タスクごとのクールダウン状態ファイルで通知の洪水を防ぐ。 |
| [db_retention_service.md](./db_retention_service.md) | SQLite の**行**の保持期間削除(2026-09-20 新設、Issue #733)。従来の保持期間削除はすべて「ファイル」が対象で、DB の行を消す経路が無かった。既定はドライランで1行も削除せず、削除予定件数を報告するだけ。有効時も直近のバックアップを確認してからバッチ分割して削除する。 |
| [db_retention.md](./db_retention.md) | 上記を実機で確認・実行するための CLI(2026-09-20 新設、Issue #733)。引数なしで現状と削除予定のレポート、`--apply` で削除、`--vacuum` でファイル縮小。 |

## G. その他

全体設計書に明示の記載がなく、上記A〜Fのいずれにも直接該当しない共通基盤・バッチ処理・ダッシュボード関連のファイル群。

| 仕様書 | 概要 |
| --- | --- |
| [common.md](./common.md) | **廃止(Issue #664)**: 下位互換性のために維持されていたFacadeパターンのモジュール。全依存を `core.*`/`services.*` の直importへ移行し、`MY_HOME_SYSTEM/common.py` は削除済み。仕様書は履歴として残している。 |
| [config.md](./config.md) | システム全体の環境変数、定数、ディレクトリパスの定義と初期化を行う。 |
| [daily_timelapse_job.md](./daily_timelapse_job.md) | カメラ録画から特定日時の動画チャンクを検索し、動き検知に基づくタイムラプス動画を生成してDiscordへ通知・アップロードする日次バッチ。 |
| [discord.md](./discord.md) | Discord Webhook への POST を集約する低レベルユーティリティ。2000字上限の分割・429/5xx のリトライ・Webhook URL のマスクを担う(Issue #661)。 |
| [dashboard.md](./dashboard.md) | Streamlit製ダッシュボードアプリケーションのエントリーポイント。センサー等の各種データを5つのタブ（ホーム/おでかけ/見守り/くらし/システム）で表示する。 |
| [database.md](./database.md) | SQLiteデータベースへの接続、クエリ実行、データの書き込みを管理するユーティリティ機能を提供する。 |
| [init_unified_db.md](./init_unified_db.md) | SQLiteデータベースの初期化とスキーマ整合性検証を行うスクリプト。テーブル・インデックス作成、マイグレーション適用を行う。 |
| [logger.md](./logger.md) | システム全体のログ出力設定を管轄するモジュール。コンソール出力、ファイル保存、エラー時のDiscord通知を行う。 |
| [onvif_utils.md](./onvif_utils.md) | ONVIF の WSDL ディレクトリを `sys.path` から探索する小さな共通ユーティリティ(Issue #661 で camera_monitor / camera_service の重複を集約)。 |
| [nas_utils.md](./nas_utils.md) | NASディレクトリへのアクセス状態確認、再マウント試行、ローカルへのフォールバック、復旧時の同期機能を提供するユーティリティ。 |
| [run_task.md](./run_task.md) | 指定されたPythonスクリプトを所定のディレクトリ・仮想環境下で実行し、実行結果をログファイルに記録する。 |
| [scheduler_boot.md](./scheduler_boot.md) | 指定間隔でプロジェクト内のPythonスクリプトを定期的にサブプロセスとして実行・管理する無限ループのスケジューラ。 |
| [routine_deadline_job.md](./routine_deadline_job.md) | デイリールーティンの締切(チェックポイント時刻)超過処理を60秒間隔で起動する、スケジューラの定期タスク。DBは触らず `POST /api/routine/deadlines/process` を叩くだけのHTTPクライアント(Issue #738 / AUDIT-008)。 |
| [smart_timelapse_generator.md](./smart_timelapse_generator.md) | OpenCVの背景差分で動画中の動きのある領域を検出し、FFmpegで該当部分を結合したタイムラプス動画を生成、Discordへアップロードする。 |
| [start_all.md](./start_all.md) | MY_HOME_SYSTEMのクリーンアップ、初期設定、および関連プロセス群の起動を統括するスクリプト。 |
| [state_file.md](./state_file.md) | 監視スクリプトの状態ファイル(JSON / 1行テキスト)を flock + tmp + os.replace で原子的に読み書きする共通ヘルパー(Issue #661)。 |
| [utils.md](./utils.md) | システム全体で共通して使用されるユーティリティ関数群（タイムゾーン処理、指数バックオフによるリトライ機能等）を提供する。 |
| [migrations.md](./migrations.md) | `migrations/`配下の`*.sql`ファイルを順に適用し、適用済みバージョンを`schema_migrations`テーブルで管理する軽量マイグレーションランナー。 |
| [dashboard_common.md](./dashboard_common.md) | `views/dashboard`配下の各モジュールから共通利用されるCSS（スマホ幅のメディアクエリを含む）、ステータスカードのグリッド描画、`safe_section`を提供するモジュール（同名の`common.py`Facadeとはファイル名衝突のため別名で管理）。 |
| [quest_tab.md](./quest_tab.md) | **廃止**: Streamlitダッシュボードの「Family Quest」タブ。同じ内容をスマホ最適化済みのPWA `family-quest`(`/quest`)が持つ二重管理だったため、スマホ対応の再設計でソースごと撤去された。仕様書は廃止noticeつきで履歴として残している。 |
| [log_tab.md](./log_tab.md) | Streamlitダッシュボードのセンサーログ分析と、「🔧 システム」タブ配下（リソース状況・NAS状態・サーバーログ・メンテナンス操作）を描画するモジュール。 |
| [misc_tab.md](./misc_tab.md) | Streamlitダッシュボードの「電車遅延」「防犯カメラ」「駐輪場」タブを描画するモジュール。 |
| [health_tab.md](./health_tab.md) | Streamlitダッシュボードの「健康管理」タブ。子供の体調・排便・食事のデータフレームを表形式で表示する。 |
| [sensor_tab.md](./sensor_tab.md) | Streamlitダッシュボードの「電力・環境」「気温詳細」「高砂実家」タブを描画するモジュール。 |
| [summary.md](./summary.md) | Streamlitダッシュボード「🏠 ホーム」タブの9個のステータスカード（在宅状況・電気代・NAS死活等）を判定し、CSS Gridのグリッドとして描画するモジュール。 |

## 廃止済み仕様書一覧

以下は対応するソースファイルが削除済みのため、仕様書ファイル自体も削除したもの(Issue #402)。`.github/scripts/check_spec_drift.py` の週次監査で「孤立ドキュメント」として報告され続けるのを避けるため、記録はこの一覧のみに残す。内容が必要な場合は git 履歴(削除コミット以前)を参照すること。

| 旧仕様書(旧ソース) | 廃止理由 |
| --- | --- |
| `bounty_router.md` (`routers/bounty_router.py`) | 報酬（ギルド討伐依頼）システムのAPIルーティング。2026-08のFamily Quest大改修(ギルド機能廃止、`d1599d6`/`ffdc8c2`/`1818d5a`)に伴い削除。 |
| `ai_logic.md` (`handlers/ai_logic.py`) | Gemini Function Calling用の宣言スタブ。呼び出し経路(`line_logic.handle_message()`)ごと到達不能なデッドコードだったため削除。後継は[ai_service.md](./ai_service.md)。 |
| `scripts_claude_log_watchdog.md` (`scripts/claude_log_watchdog.sh`) | Issue #339 対応(`e7b4175`)で削除。一次チェック部分は[health_watch.md](./health_watch.md)へ、`claude -p`起動部分は[scripts_claude_investigate.md](./scripts_claude_investigate.md)へ分割移設。 |
| `timelapse_runner.md` (`monitors/timelapse_runner.py`) / `timelapse_generator.md` (`monitors/timelapse_generator.py`) | Issue #485: `scheduler_boot.py`でコメントアウト済み・`deploy/cron/crontab`にも未登録で、実行経路が存在しない到達不能コード(本番434行)だったため削除。手動運用は無いことを確認済み。姉妹システムの[smart_timelapse_generator.md](./smart_timelapse_generator.md)/`daily_timelapse_job.py`は別系統のため引き続き稼働中。 |
