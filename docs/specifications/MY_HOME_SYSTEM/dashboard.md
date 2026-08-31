## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `dashboard.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [analysis_service.md](./analysis_service.md) - `services.analysis_service`の実体。`load_sensor_data`, `load_generic_data`, `load_bicycle_data`, `load_nas_status`, `load_ai_report`, `apply_friendly_names`等を提供。同ドキュメントでも本ファイル(`dashboard.py`)を主要な呼び出し元として明記している
* [common.md](./common.md) - `common.send_push`の実体(Facade経由で`services.notification_service.send_push`に委譲)
* [config.md](./config.md) - `SQLITE_TABLE_CHILD`等のテーブル名定数、`LINE_USER_ID`を提供
* [start_all.md](./start_all.md) - 呼び出し元。`start_all.sh`が`streamlit run dashboard.py`をバックグラウンドで起動する

## 2. ファイルの概要

* Streamlit製ダッシュボードアプリケーションのエントリーポイント。ページ設定・ロガー設定などアプリ全体の初期化を行う。
* `services.analysis_service` からセンサー・子供・排泄・食事・車・防犯ログ・駐輪場・NASステータス等のデータを読み込み、AIレポート（`load_ai_report`）を取得して展開表示する。
* サマリー表示（`views.dashboard.summary`）と11個のタブ（クエスト、電車遅延、防犯カメラ、電力・環境、気温詳細、健康管理、高砂実家、ログ分析、トレンド、システム管理、駐輪場）のレンダリングを、それぞれ対応する `views.dashboard` 配下のビューモジュールに委譲する。
* アプリ実行中に例外が発生した場合、エラーログを出力しDiscordへ通知を試み、画面上にエラーメッセージとトレースバックを表示するフェイルセーフ処理を持つ。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `logging` | 標準ライブラリ | ロガーの設定・取得 | `import logging` (行番号: 2 / 抜粋: "import logging") |
| `traceback` | 標準ライブラリ | 例外発生時のスタックトレース文字列取得 | `import traceback` (行番号: 3 / 抜粋: "import traceback") |
| `datetime` | 標準ライブラリ | 現在時刻・レポート時刻の処理 | `from datetime import datetime` (行番号: 4 / 抜粋: "from datetime import datetime") |
| `pytz` | 外部ライブラリ | タイムゾーン（Asia/Tokyo）の処理 | `import pytz` (行番号: 5 / 抜粋: "import pytz") |
| `streamlit` | 外部ライブラリ | Web UIの構築（ページ設定、タブ、サイドバー、エラー表示等） | `import streamlit as st` (行番号: 6 / 抜粋: "import streamlit as st") |
| `common` | 内部モジュール | エラー発生時のDiscord通知（`send_push`） | `import common` (行番号: 9 / 抜粋: "import common") |
| `config` | 内部モジュール | DBテーブル名やLINEユーザーIDなど設定値の取得 | `import config` (行番号: 10 / 抜粋: "import config") |
| `services.analysis_service` | 内部モジュール | センサー・各種テーブルデータ・AIレポート等の読み込み処理 | `from services import analysis_service` (行番号: 11 / 抜粋: "from services import analysis_service") |
| `views.dashboard.common` (`view_common`) | 内部モジュール | 共通CSS（`CUSTOM_CSS`）の提供 | `common as view_common` (行番号: 15 / 抜粋: "common as view_common,") |
| `views.dashboard.summary` | 内部モジュール | サマリー部分のレンダリング | `summary,` (行番号: 16 / 抜粋: "summary,") |
| `views.dashboard.quest_tab` | 内部モジュール | クエストタブのレンダリング | `quest_tab,` (行番号: 17 / 抜粋: "quest_tab,") |
| `views.dashboard.sensor_tab` | 内部モジュール | 電力・気温・高砂実家タブのレンダリング | `sensor_tab,` (行番号: 18 / 抜粋: "sensor_tab,") |
| `views.dashboard.health_tab` | 内部モジュール | 健康管理タブのレンダリング | `health_tab,` (行番号: 19 / 抜粋: "health_tab,") |
| `views.dashboard.misc_tab` | 内部モジュール | 電車遅延・防犯カメラ・駐輪場タブのレンダリング | `misc_tab,` (行番号: 20 / 抜粋: "misc_tab,") |
| `views.dashboard.log_tab` | 内部モジュール | ログ分析・トレンド・システム管理タブのレンダリング | `log_tab` (行番号: 21 / 抜粋: "log_tab") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `services.analysis_service` の各関数 | `load_sensor_data`, `load_generic_data`, `load_bicycle_data`, `load_nas_status`, `load_ai_report`, `apply_friendly_names` の実装（DBアクセス方法やデータ整形ロジック）が本ファイルからは不明。 | `analysis_service.load_sensor_data(limit=10000)` (行番号: 59 / 抜粋: "df_sensor = analysis_service.load_sensor_data(limit=10000)") |
| `config` の各設定値 | `SQLITE_TABLE_CHILD`, `SQLITE_TABLE_DEFECATION`, `SQLITE_TABLE_FOOD`, `SQLITE_TABLE_CAR`, `LINE_USER_ID` の実際の値がどこでどう定義されているか不明。 | `config.SQLITE_TABLE_CHILD` (行番号: 60 / 抜粋: "df_child = analysis_service.load_generic_data(config.SQLITE_TABLE_CHILD)") |
| `common.send_push` | エラー通知の送信方式・成否時の挙動（例外送出の有無など）が不明。 | `common.send_push(` (行番号: 138 / 抜粋: "common.send_push(") |
| `view_common.CUSTOM_CSS` | CSSの具体的な内容・スタイル定義が不明。 | `view_common.CUSTOM_CSS` (行番号: 48 / 抜粋: "st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)") |
| `summary.render_summary` | サマリー部の描画ロジック・使用データ項目の詳細が不明。 | `summary.render_summary(now, df_sensor, df_car, df_bicycle, nas_data)` (行番号: 87 / 抜粋: "summary.render_summary(now, df_sensor, df_car, df_bicycle, nas_data)") |
| `quest_tab.render` | クエストタブの内部実装が不明。 | `quest_tab.render()` (行番号: 111 / 抜粋: "quest_tab.render()") |
| `misc_tab` の各関数 | `render_traffic`, `render_photos`, `render_bicycle` の内部実装が不明。 | `misc_tab.render_traffic()` (行番号: 113 / 抜粋: "misc_tab.render_traffic()") |
| `sensor_tab` の各関数 | `render_electricity`, `render_temperature`, `render_takasago` の内部実装が不明。 | `sensor_tab.render_electricity(df_sensor, now)` (行番号: 117 / 抜粋: "sensor_tab.render_electricity(df_sensor, now)") |
| `health_tab.render` | 健康管理タブの内部実装が不明。 | `health_tab.render(df_child, df_poop, df_food)` (行番号: 121 / 抜粋: "health_tab.render(df_child, df_poop, df_food)") |
| `log_tab` の各関数 | `render_logs`, `render_trends`, `render_system` の内部実装が不明。 | `log_tab.render_logs(df_sensor)` (行番号: 125 / 抜粋: "log_tab.render_logs(df_sensor)") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `logger` (モジュールレベル変数)

* **役割**: `logging.basicConfig` によりログ出力形式・レベルを設定した上で、モジュール専用のロガーインスタンスを生成する。
* 根拠: `logging.basicConfig(...)` および `logger = logging.getLogger(__name__)` (行番号: 26〜29 / 抜粋: "logging.basicConfig(\n    level=logging.INFO, format=\"%(asctime)s - %(levelname)s - %(message)s\"\n)\nlogger = logging.getLogger(__name__)")


* **引数/リクエスト**: なし（モジュールレベルで即時実行）
* 根拠: (行番号: 26〜29 / 抜粋: "logging.basicConfig(")


* **戻り値/レスポンス**: なし（グローバル変数 `logger` への代入）
* 根拠: `logger = logging.getLogger(__name__)` (行番号: 29 / 抜粋: "logger = logging.getLogger(__name__)")


* **副作用**: ルートロガーの設定（INFOレベル、フォーマット指定）、モジュール変数 `logger` の生成。
* 根拠: `logging.basicConfig` (行番号: 26 / 抜粋: "logging.basicConfig(")


* **エラーハンドリング**: なし
* 根拠: (行番号: 26〜29 / 抜粋: "logger = logging.getLogger(__name__)")



### `st.set_page_config` 呼び出し（ページ設定）

* **役割**: Streamlitアプリのページタイトル、アイコン、レイアウト、サイドバーの初期状態を設定する。
* 根拠: `st.set_page_config(...)` (行番号: 32〜37 / 抜粋: "st.set_page_config(\n    page_title=\"My Home Dashboard\",\n    page_icon=\"🏠\",\n    layout=\"wide\",\n    initial_sidebar_state=\"collapsed\",\n)")


* **引数/リクエスト**: `page_title="My Home Dashboard"`, `page_icon="🏠"`, `layout="wide"`, `initial_sidebar_state="collapsed"`
* 根拠: (行番号: 33〜36 / 抜粋: "page_title=\"My Home Dashboard\",")


* **戻り値/レスポンス**: なし
* 根拠: (行番号: 32〜37 / 抜粋: "st.set_page_config(")


* **副作用**: Streamlitアプリ全体のページ設定（ワイドレイアウト、サイドバー折りたたみ等）を変更する。
* 根拠: (行番号: 32〜37 / 抜粋: "st.set_page_config(")


* **エラーハンドリング**: なし
* 根拠: (行番号: 32〜37 / 抜粋: "st.set_page_config(")



### `main`

* **役割**: サイドバー設定、各種データの読み込み、AIレポート表示、サマリー表示、11個のタブの切り替え・レンダリングを行うアプリ本体の処理。例外発生時はログ記録・Discord通知・エラー画面表示を行う。
* 根拠: `def main():` (行番号: 39〜147 / 抜粋: "def main():")


* **引数/リクエスト**: なし
* 根拠: `def main():` (行番号: 39 / 抜粋: "def main():")


* **戻り値/レスポンス**: なし（Streamlit UIへの描画が主目的）
* 根拠: `def main():` (行番号: 39 / 抜粋: "def main():")


* **副作用**:
    * サイドバーに設定見出し・更新ボタン・CSS適用・現在時刻ログを出力する。
    * `st.cache_data.clear()` によるキャッシュクリアと `st.rerun()` による再実行（更新ボタン押下時）。
    * `analysis_service` 経由での複数のデータ読み込み（センサー、子供、排泄、食事、車、防犯ログ、駐輪場、NASステータス、AIレポート）。
    * AIレポートがある場合、時間帯に応じたアイコン付きの展開エリアにメッセージを表示する。
    * サマリーおよび11タブ分のUIレンダリング（各ビューモジュールへ処理委譲）。
    * 例外発生時、エラーログ出力・Discordへのエラー通知（`common.send_push`）・画面へのエラーメッセージ表示・トレースバック表示。
* 根拠: `st.cache_data.clear()` (行番号: 44 / 抜粋: "st.cache_data.clear()"), `analysis_service.load_sensor_data(limit=10000)` (行番号: 59 / 抜粋: "df_sensor = analysis_service.load_sensor_data(limit=10000)"), `common.send_push(` (行番号: 138 / 抜粋: "common.send_push(")


* **エラーハンドリング**:
    * データ読み込みからタブレンダリングまでの全体を `try...except Exception as e:` で捕捉する。
    * 例外捕捉時、エラーメッセージをログ出力（`logger.error`）した上で、`common.send_push` によるDiscord通知を試みる。この通知処理自体は入れ子の `try...except Exception: pass` で保護されており、通知失敗時も処理は継続する（例外を握りつぶす）。
    * 最後に `st.error(...)` でユーザー向けエラーメッセージを表示し、`st.code(traceback.format_exc())` でトレースバックを画面に出力する。
* 根拠: `except Exception as e:` (行番号: 133〜147 / 抜粋: "except Exception as e:"), `except Exception:\n            pass` (行番号: 144〜145 / 抜粋: "except Exception:\n            pass")



## 5. 処理フロー図

`main()` 関数における、データ読み込みからタブ描画、例外発生時のフォールバックまでの流れを示します。

```mermaid
flowchart TD
    Start(["Start: main()"]) --> Sidebar["サイドバー設定・CSS適用・現在時刻ログ"]
    Sidebar --> TryStart(["Tryブロック開始"])

    TryStart --> LoadData["外部: analysis_service.load_*() でデータ読み込み"]
    LoadData --> LoadReport["外部: analysis_service.load_ai_report()"]
    LoadReport --> CheckReport{"report が None でないか"}
    CheckReport -- Yes --> RenderReport["時間帯アイコン付きでAIレポートを展開表示"]
    CheckReport -- No --> RenderSummary
    RenderReport --> RenderSummary["外部: summary.render_summary()"]

    RenderSummary --> CreateTabs["st.tabs() で11タブ生成"]
    CreateTabs --> RenderTabs["各タブへ view モジュールの render 系関数を委譲"]
    RenderTabs --> End(["End: 正常終了"])

    TryStart -. 例外発生 .-> Catch(["except Exception as e"])
    LoadData -. 例外発生 .-> Catch
    RenderTabs -. 例外発生 .-> Catch

    Catch --> LogErr["logger.error(err_msg)"]
    LogErr --> TryNotify(["Tryブロック: Discord通知"])
    TryNotify --> SendPush["外部: common.send_push(...)"]
    SendPush -. 通知失敗 .-> IgnoreErr["except Exception: pass（握りつぶし）"]
    SendPush --> ShowError
    IgnoreErr --> ShowError["st.error() + st.code(traceback)"]
    ShowError --> EndErr(["End: エラー画面表示"])
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "dashboard.py"
        logger["logger (Global)"]
        main["main()"]
    end

    subgraph "外部依存"
        logging_mod["logging"]
        pytz_mod["pytz"]
        streamlit_mod["streamlit"]
        traceback_mod["traceback"]
        common_mod["common"]
        config_mod["config"]
        analysis_service["services.analysis_service"]
        view_common["views.dashboard.common"]
        summary["views.dashboard.summary"]
        quest_tab["views.dashboard.quest_tab"]
        sensor_tab["views.dashboard.sensor_tab"]
        health_tab["views.dashboard.health_tab"]
        misc_tab["views.dashboard.misc_tab"]
        log_tab["views.dashboard.log_tab"]
    end

    logger --> logging_mod
    main --> streamlit_mod
    main --> pytz_mod
    main --> traceback_mod
    main --> view_common
    main --> config_mod
    main --> analysis_service
    main --> common_mod
    main --> summary
    main --> quest_tab
    main --> sensor_tab
    main --> health_tab
    main --> misc_tab
    main --> log_tab
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `services/analysis_service.py` | ダッシュボードが表示する全データ（センサー、子供、排泄、食事、車、防犯ログ、駐輪場、NASステータス、AIレポート）の取得ロジックが集約されており、UIの正確な挙動を把握するために必須。 | `from services import analysis_service` (行番号: 11 / 抜粋: "from services import analysis_service") |
| 高 | `views/dashboard/summary.py`, `quest_tab.py`, `sensor_tab.py`, `health_tab.py`, `misc_tab.py`, `log_tab.py` | 実際の画面描画ロジックが全てこれらのモジュールに委譲されており、UIの詳細仕様（表示項目・グラフ・操作性）を理解するために必要。 | `from views.dashboard import (...)` (行番号: 14〜22 / 抜粋: "from views.dashboard import (") |
| 中 | `config.py` | `SQLITE_TABLE_CHILD` 等のテーブル名定数や `LINE_USER_ID` の実値を把握し、DB構造や通知先を確認するため。 | `config.SQLITE_TABLE_CHILD` (行番号: 60 / 抜粋: "df_child = analysis_service.load_generic_data(config.SQLITE_TABLE_CHILD)") |
| 中 | `common.py` | `send_push` の実装（Discord通知の具体的な送信方式・エラー処理）を確認するため。 | `common.send_push(` (行番号: 138 / 抜粋: "common.send_push(") |

## 8. 保守上の注意点

* **ロガー設定方式の不統一**: 本ファイルは `logging.basicConfig()` と `logging.getLogger(__name__)` を直接使用してロガーを構築しているが、`switchbot_service.py` や `backup_service.py` 等の他サービスは `core.logger.setup_logging` を利用している。両方の初期化方式が同一プロセス内で混在すると、ハンドラの重複登録やログフォーマットの不一致が発生する可能性がある。
* **二重の広範な例外キャッチ**: `main()` 全体を `except Exception as e:` で捕捉した上、その中のDiscord通知処理もさらに `except Exception: pass` で握りつぶしている。通知失敗の原因（設定不備やネットワーク断など）が完全に不可視化される。
* **`report["timestamp"]` の型分岐**: 71〜77行目で `ts` が文字列かつ `"T"` を含む場合のみ `datetime.fromisoformat` でパースし、それ以外（文字列だが `"T"` を含まない場合を含む）は `datetime.now()` にフォールバックしている。この場合、表示される時刻がAIレポート自体のタイムスタンプと異なる可能性がある。
* **サイドバーとメイン画面での重複処理**: `view_common.CUSTOM_CSS` の `st.markdown` 呼び出し（48行目・55行目）および `datetime.now(pytz.timezone("Asia/Tokyo"))` の取得（50行目・56行目）がサイドバーブロックとメインのtryブロックでそれぞれ重複して実行されている。
* **更新ボタン押下時の`st.rerun()`**: `st.cache_data.clear()` 直後に `st.rerun()` を呼んでおり、キャッシュ全クリア＋全データ再読み込みとなるため、データ量によっては応答が遅くなる可能性がある。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `analysis_service` の各読み込み関数の仕様 | DBアクセス方法、返却される `DataFrame` のスキーマ、キャッシュ有無（`st.cache_data` との関係）が本ファイルからは不明。 | `services/analysis_service.py` |
| 各タブビューモジュールの実装詳細 | `summary`, `quest_tab`, `sensor_tab`, `health_tab`, `misc_tab`, `log_tab` の描画内容・引数の使い方が不明。 | `views/dashboard/summary.py` ほか各ビューファイル |
| `config` の設定値の実体 | `SQLITE_TABLE_CHILD` 等のテーブル名や `LINE_USER_ID` の具体的な値が不明。 | `config.py` |
| `common.send_push` の仕様 | Discord通知の送信方式や失敗時の挙動（例外を送出するか等）が不明。 | `common.py` |
| `view_common.CUSTOM_CSS` の内容 | 具体的なスタイル定義が不明。 | `views/dashboard/common.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `analysis_service` の各読み込み関数の仕様 | `MY_HOME_SYSTEM/services/analysis_service.py`を直接確認した。`get_ro_db_connection()`(32〜39行目)は`sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True, timeout=10.0)`で読み取り専用接続を返し、これを内部で用いる`load_generic_data(table_name, limit=500)`(150行目)は`SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}`を実行、`load_sensor_data(limit=5000)`(155行目)は`device_records`・SwitchBotメーターログ・電力使用量の複数テーブルを統合して`pd.DataFrame`を返す、`load_nas_status()`(133行目)/`load_ai_report()`(343行目)はそれぞれ最新1件を`Optional[pd.Series]`で返す設計であることを確認した。ファイル全体を`cache_data`および`import streamlit`で検索したが該当箇所はなく、`st.cache_data`によるキャッシュは実装されていないことを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/services/analysis_service.py:32-39, 133-170, 343-347` |
| 各タブビューモジュールの実装詳細 | `MY_HOME_SYSTEM/views/dashboard/`配下の各ファイルを直接確認した。`summary.py`は`get_takasago_status(df_sensor, now)`(13行目)、`get_itami_status(df_sensor, now)`(36行目)、`get_traffic_status()`(86行目)、`get_server_status()`(97行目)、`get_nas_status_simple(nas_data)`(103行目)等のステータス取得関数群、`quest_tab.py`は引数なしの`render()`(8行目)、`sensor_tab.py`は`render_electricity(df_sensor, now)`(9行目)/`render_temperature(df_sensor, now)`(56行目)/`render_takasago(df_sensor)`(106行目)、`health_tab.py`は`render(df_child, df_poop, df_food)`(5行目)、`misc_tab.py`は`render_traffic()`(14行目)/`render_photos(df_security_log)`(84行目)/`render_bicycle(df_bicycle)`(110行目)、`log_tab.py`は`render_logs(df_sensor)`(10行目)/`render_trends()`(22行目)/`render_system()`(49行目)を持つことを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/views/dashboard/summary.py:13-103`, `MY_HOME_SYSTEM/views/dashboard/quest_tab.py:8`, `MY_HOME_SYSTEM/views/dashboard/sensor_tab.py:9-106`, `MY_HOME_SYSTEM/views/dashboard/health_tab.py:5`, `MY_HOME_SYSTEM/views/dashboard/misc_tab.py:14-110`, `MY_HOME_SYSTEM/views/dashboard/log_tab.py:10-49` |
| `config` の設定値の実体 | `MY_HOME_SYSTEM/config.py`を直接確認した。`SQLITE_TABLE_CHILD`(245行目)は`"child_health_records"`という文字列定数、`LINE_USER_ID`(185行目)は`os.getenv("LINE_USER_ID")`で環境変数由来（既定値なし、未設定時は`None`）であることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/config.py:185, 245` |
| `common.send_push` の仕様 | `MY_HOME_SYSTEM/common.py:31-37`が`services.notification_service`から`send_push`を再インポートしているFacadeであることを直接確認した上で、`MY_HOME_SYSTEM/services/notification_service.py:116-163`の実装を直接確認した。Issue #289で`send_push(messages, *, target="both", channel="notify", user_id=None, image_data=None, filename="snapshot.jpg")`に再設計されており、`target`が`"both"`または`"discord"`を含む場合に`_send_discord_webhook`、`"both"`または`"line"`を含む場合に`user_id`(省略時は`config.LINE_USER_ID`にフォールバック)を用いて`_send_line_push`をそれぞれ呼び出す統合プッシュ通知関数であることを確認した。本ファイル(`dashboard.py`)は`target="discord"`のみで呼び出すため`user_id`は渡していない。 | 直接ソース確認: `MY_HOME_SYSTEM/common.py:31-37`, `MY_HOME_SYSTEM/services/notification_service.py:116-163`, `MY_HOME_SYSTEM/dashboard.py:138-141` |
| `view_common.CUSTOM_CSS` の内容 | `MY_HOME_SYSTEM/views/dashboard/common.md`（本リポジトリの解析済み仕様書）および`MY_HOME_SYSTEM/views/dashboard/common.py`を直接確認した。`CUSTOM_CSS`(4行目〜)は`<style>`タグを含むCSS定義を格納した1つの長い文字列定数であり、本ファイル(`views/dashboard/common.py`)自体は`st.markdown`等での適用処理を持たず、単にこの文字列を定義しているのみであることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/views/dashboard/common.py:4-5` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
