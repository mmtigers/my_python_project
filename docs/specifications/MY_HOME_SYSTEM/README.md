# MY_HOME_SYSTEM 仕様書一覧

IoT機器の制御、環境データの収集・分析、各種API・Webhookの統合ルーティングを担うFastAPIバックエンドの仕様書索引（全69件）。全体像は[全体設計書.md](../全体設計書.md)を参照。カテゴリA〜Fは全体設計書「2.1 コンポーネント一覧と役割」の分類に、G「その他」は各仕様書の記述をもとに追加で割り振ったもの。

## A. コアサーバー・ルーティング機構

| 仕様書 | 概要 |
| --- | --- |
| [unified_server.md](./unified_server.md) | FastAPIサーバーの起動・設定を行う統合エントリーポイント。ルートディレクトリ解決、CORS設定、IP検証、各種ルーターの統合を行う。 |
| [system_router.md](./system_router.md) | 手動バックアップをトリガーするPOSTエンドポイントを提供するFastAPIルーター。 |
| [webhook_router.md](./webhook_router.md) | 外部システム（LINE Bot・SwitchBot等）からのWebhookリクエストを受け取り、適切なハンドラ・サービスへルーティングする。 |
| [bounty_router.md](./bounty_router.md) | （廃止）報酬（ギルド討伐依頼）システムに関するAPIルーティング。2026-08のFamily Quest大改修に伴い削除済み。 |
| [camera_router.md](./camera_router.md) | カメラのライブ配信（HLS）・録画セグメントの一覧取得や配信APIを提供する（`camera_service.py`に処理を委譲）。 |

## B. ハードウェア・IoT制御モジュール

| 仕様書 | 概要 |
| --- | --- |
| [switchbot_service.md](./switchbot_service.md) | SwitchBotデバイス（プラグ、ボット等）の制御およびステータス（電源状態、消費電力等）の取得。 |
| [nature_remo_monitor.md](./nature_remo_monitor.md) | Nature Remoを介した家電制御と、温度・湿度等の環境センサーデータの監視。 |
| [camera_monitor.md](./camera_monitor.md) | ネットワークカメラの稼働監視およびスナップショット取得。 |
| [camera_digest_service.md](./camera_digest_service.md) | カメラの録画・スナップショットから要約（ダイジェスト）コンテンツを生成する。 |
| [tv_lock_monitor.md](./tv_lock_monitor.md) | TVの稼働時間を監視し、規定時間を超えた場合のロック制御を行う。 |
| [connect_speaker.md](./connect_speaker.md) | スマートスピーカー等への音声出力・通知連携。 |
| [camera_service.md](./camera_service.md) | ONVIF対応カメラのRTSP URL取得、ffmpegによるライブHLS配信・録画VODプレイリスト生成を担うサービス層。 |
| [collect_onvif_logs.md](./collect_onvif_logs.md) | ONVIF対応カメラからイベントログを非同期ポーリングで収集し、ローカルファイルへ保存するスクリプト。 |
| [network_logger.md](./network_logger.md) | カメラのIPアドレスに対しICMP PingとRTSPポートへのTCP接続試行を定期実行し、レイテンシとステータスをCSVに記録する。 |
| [sound_manager.md](./sound_manager.md) | イベントキーに基づく音声ファイルの非同期再生、および音声ファイル欠損時のデフォルトディレクトリからの復旧を行う。 |
| [switchbot.md](./switchbot.md) | SwitchBot関連のWebhookペイロード・API状態レスポンスのデータ構造を定義するPydanticモデル群。 |
| [switchbot_power_monitor.md](./switchbot_power_monitor.md) | 監視対象のSwitchBotデバイスから電力・温湿度・電源状態を定期取得し、後続の処理サービスへ連携するデバイス監視スクリプト。 |
| [sensor_service.md](./sensor_service.md) | センサーおよび電力計からのデータ受信（Webhook・ポーリング）を処理し、重複排除・状態管理・ログ保存・通知送信を行う。 |

## C. 外部サービス・通知連携

| 仕様書 | 概要 |
| --- | --- |
| [line_logic.md](./line_logic.md) | LINE Messaging APIのWebhook PostbackEvent（ボタン操作）専用の処理ロジック。 |
| [line_handler.md](./line_handler.md) | LINE Bot APIのWebhookイベント（メッセージ・ポストバック）を解析し、各処理へ振り分けるディスパッチャ。 |
| [google_photos_service.md](./google_photos_service.md) | Google Photos APIと連携し、特定アルバムへの写真アップロードや取得を行う。Gemini APIによる要約レポート生成も担う。 |
| [weather_service.md](./weather_service.md) | 外部の天気APIから気象情報を取得し、システム内の判断材料（例：雨の日の特定タスク発火など）に利用する。 |
| [line.md](./line.md) | LINE連携のイベント・Postbackデータ構造を定義するPydanticモデル群。 |
| [line_service.md](./line_service.md) | LINEメッセージからの情報記録・取得、クエストステータス照会、承認・却下処理を担う。 |
| [news_service.md](./news_service.md) | Google NewsのRSSフィードから地域（伊丹市、奈良県）および全国のニュースを取得・整形する。 |
| [notification_service.md](./notification_service.md) | DiscordおよびLINEへのメッセージ（テキスト・画像）通知を行い、LINE送信失敗時にDiscordへフォールバックする。 |
| [train_service.md](./train_service.md) | JR西日本の運行情報API、およびYahoo!路線情報から運行状況・最短経路を取得する（フェイルソフト設計）。 |
| [send_child_health_check.md](./send_child_health_check.md) | 朝の子供の体調確認と当日の記念日等をチェックし、LINE/Discordへ通知を送信する。 |
| [send_food_question.md](./send_food_question.md) | 夕食メニューの過去履歴を集計し、頻出メニューのランキングを含むLINE Flex Messageを送信する。 |

## D. AI・分析エンジン

| 仕様書 | 概要 |
| --- | --- |
| [ai_service.md](./ai_service.md) | LLMを活用した推論・テキスト生成の共通インターフェース。 |
| [log_analyzer.md](./log_analyzer.md) | 蓄積された各種ログ（センサー、タスク消化、システムログ）のパターンを分析する。 |
| [weekly_analyze_report.md](./weekly_analyze_report.md) | 週次で家庭内の状況（健全性、タスク消化率など）をAIで要約し、レポートとして出力（LINE等へ送信）する。 |
| [ai_logic.md](./ai_logic.md) | （廃止）AI解析用の宣言スタブファイル。未接続の到達不能コードとして削除済み、後継はai_service.py。 |
| [analysis_service.md](./analysis_service.md) | DB・OS情報・外部APIからデータを取得し、Pandas等で加工・集計するデータ分析用サービス層。 |
| [send_ai_report.md](./send_ai_report.md) | 日次データを収集し、Gemini APIを利用して家族向け状況レポートを生成、LINE/Discordへ送信する。 |

## E. クエストバックエンド (Family Quest用API)

| 仕様書 | 概要 |
| --- | --- |
| [quest_router.md](./quest_router.md) | フロントエンド(Family Quest)からのリクエストを処理するクエストAPIルーティング。 |
| [quest_service.md](./quest_service.md) | ユーザーのレベル計算、経験値(コイン)の付与・消費、報酬インベントリの管理を計算し、DBへ永続化する。 |
| [game_logic.md](./game_logic.md) | ボス討伐状況の更新など、レベルアップ必要経験値・最大HP・ドロップ報酬計算といったゲームルールロジック。 |
| [quest.md](./quest.md) | クエストシステムのドメイン/リクエスト/レスポンス/インベントリモデルを定義するPydanticモデル群。 |
| [quest_data.md](./quest_data.md) | Family Questのマスターデータ（ユーザー情報、クエスト定義、報酬定義）を定義する純粋なデータ定義モジュール。 |
| [reset_game.md](./reset_game.md) | Family QuestのDB上のユーザーゲームデータ（レベル・経験値・ゴールド・メダル数）をリセットするCLIスクリプト。 |
| [sync_strict.md](./sync_strict.md) | マスターデータ（QUESTS, REWARDS）とDBのマスターテーブルを完全同期する。 |

## F. インフラ・監視タスク (フェイルソフト機構)

| 仕様書 | 概要 |
| --- | --- |
| [server_watchdog.md](./server_watchdog.md) | サーバープロセスの死活監視。異常検知時の再起動処理など。 |
| [nas_monitor.md](./nas_monitor.md) | NASの接続状態、ディスク容量の監視。枯渇前の事前アラート発報。 |
| [memory_monitor.md](./memory_monitor.md) | システムのメモリ使用量を監視し、OOM (Out of Memory) を未然に防ぐ。 |
| [cron_reporter.md](./cron_reporter.md) | 定期実行ジョブ（Cron）の実行結果をまとめ、管理者にレポートする。 |
| [backup_service.md](./backup_service.md) | データベースのバックアップを実行しNASへ転送する。転送失敗時は即時通知を行う。 |
| [post_boot_health_check.md](./post_boot_health_check.md) | システム起動直後にハードウェア・ネットワーク・DB・周辺機器・各種サービスの健全性を一括チェックするスクリプト。 |
| [switchbot_webhook_fix.md](./switchbot_webhook_fix.md) | 環境変数のベースURLを用いて、SwitchBotおよびLINE BotのWebhookエンドポイントを自動的に更新・修復する。 |

## G. その他

全体設計書に明示の記載がなく、上記A〜Fのいずれにも直接該当しない共通基盤・バッチ処理・ダッシュボード関連のファイル群。

| 仕様書 | 概要 |
| --- | --- |
| [common.md](./common.md) | 下位互換性のために維持されているFacadeパターンのモジュール。core/servicesの各種機能を集約してインポートする。 |
| [config.md](./config.md) | システム全体の環境変数、定数、ディレクトリパスの定義と初期化を行う。 |
| [daily_timelapse_job.md](./daily_timelapse_job.md) | カメラ録画から特定日時の動画チャンクを検索し、動き検知に基づくタイムラプス動画を生成してDiscordへ通知・アップロードする日次バッチ。 |
| [dashboard.md](./dashboard.md) | Streamlit製ダッシュボードアプリケーションのエントリーポイント。センサー等の各種データやAIレポートをタブ形式で表示する。 |
| [database.md](./database.md) | SQLiteデータベースへの接続、クエリ実行、データの書き込みを管理するユーティリティ機能を提供する。 |
| [download_from_url.md](./download_from_url.md) | 指定URLからHTMLを取得し動画ファイルのURLを抽出、ローカル環境（NAS等）へダウンロードするCLIツール。 |
| [download_mp3.md](./download_mp3.md) | YouTube動画URLを元に音声をダウンロードし、MP3形式へ変換して保存するCLIスクリプト。 |
| [financial_service.md](./financial_service.md) | 住宅ローンの返済スケジュールと積立資産の成長スケジュールをシミュレーションするStreamlit UIコンポーネント。 |
| [init_unified_db.md](./init_unified_db.md) | SQLiteデータベースの初期化とスキーマ整合性検証を行うスクリプト。テーブル・インデックス作成、マイグレーション適用を行う。 |
| [logger.md](./logger.md) | システム全体のログ出力設定を管轄するモジュール。コンソール出力、ファイル保存、エラー時のDiscord通知を行う。 |
| [menu_service.md](./menu_service.md) | 晩御飯のメニュー提案を支援するバックエンドサービス。夕食履歴の取得、給料日等の特別な日の判定を行う。 |
| [nas_utils.md](./nas_utils.md) | NASディレクトリへのアクセス状態確認、再マウント試行、ローカルへのフォールバック、復旧時の同期機能を提供するユーティリティ。 |
| [network.md](./network.md) | HTTPエラーに対するリトライ設定を組み込んだHTTPセッション構築機能、およびAPI呼び出し用のリトライデコレータを提供する。 |
| [run_task.md](./run_task.md) | 指定されたPythonスクリプトを所定のディレクトリ・仮想環境下で実行し、実行結果をログファイルに記録する。 |
| [scheduler_boot.md](./scheduler_boot.md) | 指定間隔でプロジェクト内のPythonスクリプトを定期的にサブプロセスとして実行・管理する無限ループのスケジューラ。 |
| [smart_timelapse_generator.md](./smart_timelapse_generator.md) | OpenCVの背景差分で動画中の動きのある領域を検出し、FFmpegで該当部分を結合したタイムラプス動画を生成、Discordへアップロードする。 |
| [start_all.md](./start_all.md) | MY_HOME_SYSTEMのクリーンアップ、初期設定、および関連プロセス群の起動を統括するスクリプト。 |
| [timelapse_generator.md](./timelapse_generator.md) | DBのイベント検知時刻をもとにNVR録画からクリップを抽出・結合してタイムラプス動画を生成し、Discordへアップロードする。 |
| [timelapse_runner.md](./timelapse_runner.md) | timelapse_generator.pyを定時または手動実行の条件に基づきサブプロセスとして起動・管理するランナースクリプト。 |
| [utils.md](./utils.md) | システム全体で共通して使用されるユーティリティ関数群（タイムゾーン処理、指数バックオフによるリトライ機能等）を提供する。 |
