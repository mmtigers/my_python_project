# MY_HOME_SYSTEM 仕様書一覧

IoT機器の制御、環境データの収集・分析、各種API・Webhookの統合ルーティングを担うFastAPIバックエンドの仕様書索引（全102件）。全体像は[全体設計書.md](../全体設計書.md)を参照。カテゴリA〜Fは全体設計書「2.1 コンポーネント一覧と役割」の分類に、G「その他」は各仕様書の記述をもとに追加で割り振ったもの。

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
| [keep_alive_anker.md](./keep_alive_anker.md) | Anker SoundCore Bluetoothスピーカーがオートパワーオフでスリープしないよう、可聴域外の無音波(15Hz)を定期再生してキープアライブするシェルスクリプト。 |
| [keep_alive_speaker.md](./keep_alive_speaker.md) | 無音MP3ファイルを定期再生し、Bluetoothスピーカー等のオーディオ経路を維持する「ハートビート」送信用シェルスクリプト。 |
| [clinic_monitor.md](./clinic_monitor.md) | 伊丹たかの小児科の予約ページHTMLを定期取得し、前回とのハッシュ差分がある場合のみ保存する監視スクリプト。 |
| [clinic_analyzer.md](./clinic_analyzer.md) | 蓄積された小児科予約ページHTML群を解析し、午前・午後の予約人数／院内待ち人数をCSVへ抽出するバッチスクリプト。 |
| [clinic_visualizer.md](./clinic_visualizer.md) | `clinic_analyzer.py`が出力したCSVから、直近日数分の予約総数・院内待ち人数の推移を折れ線グラフ画像として保存するモジュール。 |
| [car_presence_checker.md](./car_presence_checker.md) | RTSPカメラ映像の画像処理（輝度／色比率）により車庫内の車の有無を判定し、状態変化時にDB記録・Discord通知するバッチスクリプト。 |
| [bicycle_parking_monitor.md](./bicycle_parking_monitor.md) | 近鉄系駐輪場の定期利用待機状況ページをスクレイピングし、特定エリアの待機人数をDBに記録するモニタースクリプト。 |
| [bluetooth_monitor.md](./bluetooth_monitor.md) | 指定Bluetoothデバイスの接続状態を常時監視し、切断時は自動再接続を試みDiscordへ通知する常駐スクリプト。 |
| [suumo_monitor.md](./suumo_monitor.md) | SUUMOの新着賃貸物件情報をスクレイピングし、Gemini APIで評価した上でLINE/Discordへ通知するモニタースクリプト。 |

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
| [line_flex.md](./line_flex.md) | LINE Botで使用するFlex Message（体調選択カルーセル・記録確認・サマリー表示）を構築するビュー専用モジュール。 |
| [haircut_advisor.md](./haircut_advisor.md) | 散髪予約履歴から次回の散髪推奨日を計算し、通知時期になるとLINE/Discordへ提案メッセージを送信するスクリプト。 |
| [haircut_monitor.md](./haircut_monitor.md) | Gmail(IMAP)を監視しHotPepper Beautyの予約確定メールを検知してDB記録・LINE/Discord通知するモジュール。 |
| [shopping_monitor.md](./shopping_monitor.md) | Gmail(IMAP)のAmazon・楽天注文確認メールを解析して商品名・金額をDB記録し、LINE/Discordへ要約通知するモジュール。 |
| [land_price_service.md](./land_price_service.md) | 国土交通省「不動産情報ライブラリ」APIから指定エリアの土地取引価格情報を取得・DB記録し、新規取引をDiscord通知するサービス。 |
| [app_ranking_service.md](./app_ranking_service.md) | Apple App Store公式RSSフィードから日次アプリランキングを取得・DB記録し、新着・急上昇等を分析してLINE/Discord通知するサービス。 |
| [salary_analyzer.md](./salary_analyzer.md) | Gmail(IMAP)から給与明細PDF付きメールを取得し、パスワード解除の上1ページ目を画像化して保存するクラス（AI解析は行わずアーカイブに特化）。 |

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
| [recover_mom.md](./recover_mom.md) | `quest_users`テーブルに'mom'ユーザーのレコードが存在しない場合、固定値で復旧INSERTする単発スクリプト。 |
| [fix_quest_reset_period.md](./fix_quest_reset_period.md) | `quest_master`の`reset_period`が特定条件に一致するレコードを'daily'へ一括更新するワンショット修正スクリプト。 |
| [add_quest_columns.md](./add_quest_columns.md) | `quest_master`テーブルに`days`/`description`カラムを未追加の場合のみ追加するマイグレーションスクリプト。 |
| [migrate_boss_columns.md](./migrate_boss_columns.md) | `party_state`テーブルにボス戦用カラム（`max_hp`等4種）を追加し、初期レコードがなければ挿入するマイグレーションスクリプト。 |
| [migrate_bounty.md](./migrate_bounty.md) | `bounties`テーブルが存在しない場合にのみ作成するマイグレーションスクリプト（`init_unified_db.py`と同一定義）。 |
| [quest_admin_tool.md](./quest_admin_tool.md) | 対話式コマンドラインでプレイヤーの所持金・経験値・メダル数を選択・変更するDB管理ツール。 |

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
| [migrations.md](./migrations.md) | `migrations/`配下の`*.sql`ファイルを順に適用し、適用済みバージョンを`schema_migrations`テーブルで管理する軽量マイグレーションランナー。 |
| [dashboard_common.md](./dashboard_common.md) | `views/dashboard`配下の各タブから共通利用されるCSSスタイル定義とステータスカードHTML生成関数を提供するモジュール（同名の`common.py`Facadeとはファイル名衝突のため別名で管理）。 |
| [quest_tab.md](./quest_tab.md) | Streamlitダッシュボードの「Family Quest」タブ。家族メンバーの経験値・ゴールドとランキング・達成ログを表示する。 |
| [log_tab.md](./log_tab.md) | Streamlitダッシュボードの「ログ分析」「トレンド」「システム管理」の3タブを描画するモジュール。 |
| [misc_tab.md](./misc_tab.md) | Streamlitダッシュボードの「電車遅延」「防犯カメラ」「駐輪場」タブを描画するモジュール。 |
| [health_tab.md](./health_tab.md) | Streamlitダッシュボードの「健康管理」タブ。子供の体調・排便・食事のデータフレームを表形式で表示する。 |
| [sensor_tab.md](./sensor_tab.md) | Streamlitダッシュボードの「電力・環境」「気温詳細」「高砂実家」タブを描画するモジュール。 |
| [summary.md](./summary.md) | Streamlitダッシュボードのトップ画面の9個のステータスカード（在宅状況・電気代・NAS死活等）を判定・描画するモジュール。 |
| [db_fix.md](./db_fix.md) | `device_records`テーブルに`battery_level`カラムを追加するワンショットのDB修正スクリプト。 |
| [update_schema.md](./update_schema.md) | `quest_history`への`status`カラム、食事記録テーブルへの`menu_category`/`meal_time_category`カラムを不足時のみ追加するスクリプト。 |
| [scheduled_timelapse.md](./scheduled_timelapse.md) | 監視カメラ録画から指定時間帯のタイムラプス動画をFFmpegで生成し、Discordへ通知送信するスケジュール実行スクリプト。 |
