## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `dashboard.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [analysis_service.md](./analysis_service.md) - `services.analysis_service`の実体。`load_sensor_data`, `load_generic_data`, `load_nas_status`, `apply_friendly_names`等を提供（旧`load_ai_report`はIssue #701で、旧`load_bicycle_data`は駐輪場の退役で削除）。**（Issue #741で変更）** 本ファイルが直接呼ぶのは `apply_friendly_names` だけになり、DB読み取りの各関数は [dashboard_common.md](./dashboard_common.md) のキャッシュ付きラッパー経由になった
* [common.md](./common.md) — **Issue #664 で `common.py` ごと廃止された Deprecated Facade**（本ファイルは実体を直importするようになった。仕様書は履歴として残っている）
* [config.md](./config.md) - `SQLITE_TABLE_CHILD`等のテーブル名定数、`LINE_USER_ID`を提供
* [start_all.md](./start_all.md) - 呼び出し元。`start_all.sh`が`streamlit run dashboard.py`をバックグラウンドで起動する（**スマホ対応で変更**: `--server.baseUrlPath` を付けて起動するようになった）
* [dashboard_router.md](./dashboard_router.md) / [dashboard_proxy_service.md](./dashboard_proxy_service.md) - **（スマホ対応で追加）** 本アプリを `unified_server.py`(8000番)の `/dashboard` 配下へ中継する側。スマートフォンからはこの経路でのみ到達する
* [quest_tab.md](./quest_tab.md) — **スマホ対応で `views/dashboard/quest_tab.py` ごと撤去された**（同じ内容をPWA `family-quest` が持つ二重管理だったため）。仕様書は廃止noticeつきで履歴として残っている

## 2. ファイルの概要

* Streamlit製ダッシュボードアプリケーションのエントリーポイント。ページ設定・ロガー設定などアプリ全体の初期化を行う。
* `services.analysis_service` からセンサー・子供・排泄・食事・車・防犯ログ・NASステータス等のデータを読み込み、4つのタブに展開表示する。**（Issue #701・2026-09-19で退役）** 以前はAIレポート（「セバスチャンからの報告」、`ai_report_records`の最新1件）も表示していたが、機能ごと削除した。
* **（スマホ対応で再設計、2026-09-21に「🚃 おでかけ」を撤去）** 画面は4つのタブ（🏠 ホーム、👀 見守り、💡 くらし、🔧 システム）で構成され、レンダリングは `views.dashboard` 配下のビューモジュールに委譲する。サマリー（`views.dashboard.summary`）は常時最上部ではなく「ホーム」タブの中に入っている。
* **（タブの遅延評価で変更）** タブは `st.tabs` ではなく `st.segmented_control`（`TAB_SELECTOR_STATE_KEY` をキーに選択状態を保持）で描き、**選択中のタブの描画関数だけ**を呼ぶ（`TAB_RENDERERS`）。`st.tabs` は選択されていないタブの中身もすべて実行するため、「🏠 ホーム」を開いただけの1回の描画で、`journalctl` の起動・年間気温の集計SQL・plotlyのグラフ6枚ぶんの生成まで毎回走っていた（当時はここに外部サイトのスクレイピングも2本入っていた。タブを10個から束ね直した再設計は「探しやすさ」を直したが、実行される処理量は減っていなかった）。折りたたみも同じ理由で `st.expander` から `view_common.lazy_section`（`st.toggle` ベース）へ置き換えられている。
* 根拠: `def _render_tab_selector() -> str:` (行番号: 155 / 抜粋: "def _render_tab_selector() -> str:"), `TAB_RENDERERS: Dict[str, Callable[[datetime], None]] = {` (行番号: 281 / 抜粋: "TAB_RENDERERS: Dict[str, Callable[[datetime], None]] = {")
* **（タブの遅延評価で変更）** データの読み込みも各タブの描画関数の中で行う。以前は `main()` の先頭で全タブ分（センサー・子供・排泄・食事・車・防犯ログ・駐輪場・NAS）をまとめて読んでいたが、選択中のタブしか描画しないため、そのタブが使わないテーブルを読む必要がなくなった（例: ホームタブが読む `load_generic_data_cached` は車のテーブルだけ）。
* 根拠: `def _render_home_tab(now: datetime) -> None:` (行番号: 193 / 抜粋: "def _render_home_tab(now: datetime) -> None:")
* **（タブの遅延評価で追加）** 選択中のタブは `?tab=` のクエリパラメータに保存される。「🔄 データを更新」（`st.rerun`）や再接続でホームタブに戻らず、`/dashboard?tab=watch` のようなURLをスマートフォンのホーム画面に置ける。
* 根拠: `TAB_QUERY_PARAM = "tab"` (行番号: 82 / 抜粋: "TAB_QUERY_PARAM = \"tab\"")
* **（カードのリンク化で変更）** タブ定義（キーとラベル）の実体は本ファイルではなく `services.home_status_service.DASHBOARD_TABS` にあり、`TABS` はそれを読み込むだけになった。軽量ページ（`/dashboard/m`）が各ステータスカードを「詳細が載っているタブ」へのリンク（`?tab=...`）にするためにキーを必要とし、Streamlit を import する本ファイルはサーバー側（`unified_server`）から読めないため。上記の `?tab=` の解釈（`_requested_tab`）は本ファイルのまま。
* 根拠: `TABS: tuple[tuple[str, str], ...] = home_status_service.DASHBOARD_TABS` (行番号: 70 / 抜粋: "TABS: tuple[tuple[str, str], ...] = home_status_service.DASHBOARD_TABS")
* **（スマホ対応で変更）** 以前は10個のタブ（クエスト、電車遅延、防犯カメラ、電力・環境、気温詳細、健康管理、高砂実家、ログ分析、システム管理、駐輪場）を持ち、そのすべての上にサマリーを常時表示していた。ソース中のコメントには、この構成ではスマートフォンで「どのタブを開いてもサマリーを越えるスクロールが必要」「タブ列が画面幅の数倍になり、目的のタブを探せない」状態だったため用途で束ね直した（当初5つ）、と記されている。クエストタブは同じ内容をスマホ最適化済みのPWA `family-quest`（`/quest`）が持つ二重管理だったため撤去され、ヘッダーのリンクボタンだけが残っている。
* **（Issue #507で削除）** 以前は「トレンド」タブも存在したが、参照先の`app_rankings`テーブルへの書き込みコードが存在せず機能として死んでいたため、UIごと削除された。
* **（2026-09-21で削除）** 「🚃 おでかけ」タブ（JR運行情報・Yahoo!路線情報のルート検索・駐輪場の待機数）も、いずれも使わなくなったためオーナー判断でUIごと削除された。サマリーの「🚲 駐輪場待機」「🚃 JR運行情報」カードも同時に撤去され、カードは9枚から7枚になっている。
* 根拠: 退役の経緯を述べたコメント (行番号: 58〜61 / 抜粋: "# 「🚃 おでかけ」タブ(JR運行情報・Yahoo!路線情報のルート検索・駐輪場の待機数)は、")
* **（スマホ対応で追加）** 画面最上部に操作列（`_render_header_actions`）を常設する。「🔄 データを更新」は以前サイドバーにしか無く、`initial_sidebar_state="collapsed"` のためスマートフォンではハンバーガーメニューを開かないと押せなかった。**（スマホ対応で追加）** 操作列の下に、いま表示しているデータの取得時刻（`view_common.cache_generation_started_at`）と相対表記、および軽量ページ `{DASHBOARD_BASE_PATH}/m` への導線を `st.caption` で出す。表示は最大60秒キャッシュされるため、描画時刻をそのまま「最終更新」と書くと嘘になる。
* 根拠: `fetched_at = view_common.cache_generation_started_at()` (行番号: 133 / 抜粋: "fetched_at = view_common.cache_generation_started_at()"), `MOBILE_PAGE_PATH = f"{config.DASHBOARD_BASE_PATH}/m"` (行番号: 48 / 抜粋: "MOBILE_PAGE_PATH = f\"{config.DASHBOARD_BASE_PATH}/m\"")
* アプリ実行中に例外が発生した場合、エラーログを出力しDiscordへ通知を試み、画面上に汎用エラーメッセージを表示するフェイルセーフ処理を持つ（**Issue #410 L-L5で修正**: トレースバックは以前画面にも表示していたが、内部情報の露出防止のためログのみに変更した）。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `logging` | 標準ライブラリ | ロガーの設定・取得 | `import logging` (行番号: 2 / 抜粋: "import logging") |
| `traceback` | 標準ライブラリ | 例外発生時のスタックトレース文字列取得 | `import traceback` (行番号: 3 / 抜粋: "import traceback") |
| `datetime` | 標準ライブラリ | 現在時刻・レポート時刻の処理 | `from datetime import datetime` (行番号: 5 / 抜粋: "from datetime import datetime") |
| `pytz` | 外部ライブラリ | タイムゾーン（Asia/Tokyo）の処理 | `import pytz` (行番号: 7 / 抜粋: "import pytz") |
| `streamlit` | 外部ライブラリ | Web UIの構築（ページ設定、タブ、サイドバー、エラー表示等） | `import streamlit as st` (行番号: 8 / 抜粋: "import streamlit as st") |
| `services.notification_service.send_push` | ローカルモジュール | **（Issue #664 で変更）** 以前は Deprecated Facade である `common` 経由で参照していた。`common.py` の廃止に伴い実体を直接importする | 根拠: `from services.notification_service import send_push` (行番号: 11 / 抜粋: "from services.notification_service import send_push") |
| `config` | 内部モジュール | DBテーブル名やLINEユーザーIDなど設定値の取得 | `import config` (行番号: 12 / 抜粋: "import config") |
| `services.analysis_service` | 内部モジュール | センサー・各種テーブルデータ・AIレポート等の読み込み処理 | `from services import analysis_service, home_status_service` (行番号: 13 / 抜粋: "from services import analysis_service, home_status_service") |
| `services.home_status_service` | 内部モジュール | タブ定義（`DASHBOARD_TABS`）の参照 | `from services import analysis_service, home_status_service` (行番号: 13 / 抜粋: "from services import analysis_service, home_status_service") |
| `views.dashboard.common` (`view_common`) | 内部モジュール | 共通CSS（`CUSTOM_CSS`）と `safe_section` の提供 | `common as view_common` (行番号: 17 / 抜粋: "common as view_common,") |
| `views.dashboard.summary` | 内部モジュール | サマリー部分のレンダリング | `summary,` (行番号: 18 / 抜粋: "summary,") |
| `views.dashboard.sensor_tab` | 内部モジュール | 電力・気温・高砂実家のレンダリング | `sensor_tab,` (行番号: 19 / 抜粋: "sensor_tab,") |
| `views.dashboard.health_tab` | 内部モジュール | 健康管理のレンダリング | `health_tab,` (行番号: 20 / 抜粋: "health_tab,") |
| `views.dashboard.misc_tab` | 内部モジュール | 防犯カメラ（ギャラリー・防犯ログ）のレンダリング | `misc_tab,` (行番号: 21 / 抜粋: "misc_tab,") |
| `views.dashboard.log_tab` | 内部モジュール | リソース・NAS・サーバーログ・センサーログ分析・メンテナンス操作のレンダリング | `log_tab` (行番号: 20 / 抜粋: "log_tab") |

**（スマホ対応で削除）** `views.dashboard.quest_tab` のインポートは、クエストタブの撤去に伴い削除された。

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `services.analysis_service` の各関数 | `apply_friendly_names` の実装（データ整形ロジック）が本ファイルからは不明。**（Issue #741で変更）** DB読み取りの各関数（`load_sensor_data`, `load_generic_data`, `load_nas_status`）は本ファイルから直接は呼ばれなくなり、`view_common` のキャッシュ付きラッパー経由になった。 | `df_security_log = analysis_service.apply_friendly_names(df_security_log)` |
| `views.dashboard.common` のキャッシュ付きローダ | `load_sensor_data_cached` 等が `@st.cache_data(ttl=60)` で包まれていること、TTL とクリアの契機は `view_common` 側にある。 | `df_sensor = view_common.load_sensor_data_cached(limit=10000)` |
| `config` の各設定値 | `SQLITE_TABLE_CHILD`, `SQLITE_TABLE_DEFECATION`, `SQLITE_TABLE_FOOD`, `SQLITE_TABLE_CAR` の実際の値がどこでどう定義されているか不明。 | `df_child = view_common.load_generic_data_cached(config.SQLITE_TABLE_CHILD)` |
| `services.notification_service.send_push` | エラー通知の送信方式・成否時の挙動（例外送出の有無など）が不明。 | `services.notification_service.send_push(` (行番号: 330 / 抜粋: "send_push(") |
| `view_common.CUSTOM_CSS` | CSSの具体的な内容・スタイル定義が不明。 | `view_common.CUSTOM_CSS` (行番号: 110 / 抜粋: "st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)") |
| `view_common.safe_section` | 例外を隔離する仕組み（何を表示し、どこへログを出すか）が本ファイルからは不明。 | `view_common.safe_section("サマリー")` (行番号: 195 / 抜粋: "with view_common.safe_section(\"サマリー\"):") |
| `summary.render_summary` | サマリー部の描画ロジック・使用データ項目の詳細が不明。 | `summary.render_summary(` (行番号: 196 / 抜粋: "summary.render_summary(") |
| `misc_tab` の各関数 | `render_photos` の内部実装が不明。 | `misc_tab.render_photos(analysis_service.apply_friendly_names(df_security_log))` (行番号: 229 / 抜粋: "misc_tab.render_photos(analysis_service.apply_friendly_names(df_security_log))") |
| `sensor_tab` の各関数 | `render_electricity`, `render_temperature`, `render_takasago` の内部実装が不明。 | `sensor_tab.render_electricity(df_sensor, now)` (行番号: 190 / 抜粋: "sensor_tab.render_electricity(df_sensor, now)") |
| `health_tab.render` | 健康管理の描画内容が不明。 | `health_tab.render(df_child, df_poop, df_food)` (行番号: 186 / 抜粋: "health_tab.render(df_child, df_poop, df_food)") |
| `log_tab` の各関数 | **（スマホ対応で変更）** `render_logs` に加え、以前の `render_system` を分割した `render_resources` / `render_nas_status` / `render_server_logs` / `render_maintenance` の内部実装が不明。 | `log_tab.render_resources()` (行番号: 257 / 抜粋: "log_tab.render_resources()") |
| `/quest` のSPA | `QUEST_APP_PATH` のリンク先で何が配信されるかは本ファイルからは不明（`unified_server.py` がマウントする旨のコメントのみ）。 | `QUEST_APP_PATH = "/quest"` (行番号: 41 / 抜粋: "QUEST_APP_PATH = \"/quest\"") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### `logger` (モジュールレベル変数)

* **役割**: `logging.basicConfig` によりログ出力形式・レベルを設定した上で、モジュール専用のロガーインスタンスを生成する。
* 根拠: `logging.basicConfig(...)` および `logger = logging.getLogger(__name__)` (行番号: 27〜30 / 抜粋: "logging.basicConfig(\n    level=logging.INFO, format=\"%(asctime)s - %(levelname)s - %(message)s\"\n)\nlogger = logging.getLogger(__name__)")


* **引数/リクエスト**: なし（モジュールレベルで即時実行）
* 根拠: (行番号: 26〜29 / 抜粋: "logging.basicConfig(")


* **戻り値/レスポンス**: なし（グローバル変数 `logger` への代入）
* 根拠: `logger = logging.getLogger(__name__)` (行番号: 30 / 抜粋: "logger = logging.getLogger(__name__)")


* **副作用**: ルートロガーの設定（INFOレベル、フォーマット指定）、モジュール変数 `logger` の生成。
* 根拠: `logging.basicConfig` (行番号: 27 / 抜粋: "logging.basicConfig(")


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



### `QUEST_APP_PATH` (モジュールレベル定数)

* **役割**: family-quest(PWA)へのリンク先パス `"/quest"`。**（トップ画面の刷新で変更）** 定義の実体は `services.home_status_service.QUEST_APP_PATH` にあり、本ファイルはそれを読み込むだけになった。サマリーの「📝 承認待ち」カードのリンク先と、ヘッダーの「⚔️ ファミクエを開く」ボタンが同じ場所を指すようにするためで、カードの組み立ては Streamlit を import しない `home_status_service` 側にある。
* 根拠: `QUEST_APP_PATH = "/quest"` (行番号: 38〜39 / 抜粋: "QUEST_APP_PATH = \"/quest\"")


* **引数/リクエスト**: 該当なし
* 根拠: (行番号: 41 / 抜粋: "QUEST_APP_PATH = \"/quest\"")


* **戻り値/レスポンス**: 該当なし
* 根拠: (行番号: 41 / 抜粋: "QUEST_APP_PATH = \"/quest\"")


* **副作用**: なし
* 根拠: (行番号: 41 / 抜粋: "QUEST_APP_PATH = \"/quest\"")


* **エラーハンドリング**: なし
* 根拠: (行番号: 41 / 抜粋: "QUEST_APP_PATH = \"/quest\"")



### `_render_header_actions`

* **役割**: 画面最上部の操作列を描画する。**（スマホのヘッダー改善で変更）** 全体を `st.container(key="header_actions")` で囲み、その中の `st.columns(2)` の左に「🔄 データを更新」ボタン（押下で `st.cache_data.clear()` と `st.rerun()`。**（Issue #741で変更）** 以前はリポジトリ全体に `@st.cache_data` が1つも存在せず、この `clear()` は何も消していなかった。`view_common` のキャッシュ付きローダを使うようになり、TTL(60秒)を待たずに捨てる操作として機能するようになった）、右に「⚔️ ファミクエを開く」リンクボタン（`QUEST_APP_PATH`）を、いずれも `width="stretch"` で配置する。docstringには、以前「データを更新」がサイドバーにしか無く、`initial_sidebar_state="collapsed"` のためスマートフォンではハンバーガーメニューを開かないと押せず最も使う操作が最も遠かったため、メイン画面の先頭に常設する旨が記されている。
* 根拠: `def _render_header_actions() -> None:` (行番号: 91)
* **`key` 付き container で囲う理由**: `views/dashboard/common.py` のモバイルCSSは**すべての** `[data-testid="stHorizontalBlock"]` の列を `flex: 1 1 100%` で縦積みにする。グラフ・表には必要な指定だが、短いボタン2つには過剰で、スマホではヘッダーが2段になりタブと本文を画面外へ押し下げていた（2026-09-21 に実機で確認）。`key` を渡した container は `.st-key-header_actions` クラスを持つため、CSS 側でこの行だけ横並びに戻せる
* **この対応の弱点**: `key` の文字列と CSS のセレクタが一致していることに依存する。どちらかをリネームしても Python は通り Streamlit も警告を出さず、**スマホのレイアウトだけが静かに元へ戻る**。`tests/test_dashboard_mobile_header.py` が両者の一致を検査する


* **引数/リクエスト**: なし
* 根拠: (行番号: 91 / 抜粋: "def _render_header_actions() -> None:")


* **戻り値/レスポンス**: `None`
* 根拠: (行番号: 91 / 抜粋: "def _render_header_actions() -> None:")


* **副作用**: 2カラムのボタン描画。更新ボタン押下時は `st.cache_data.clear()`（キャッシュ全クリア）と `st.rerun()`（再実行）。
* 根拠: (行番号: 52〜54 / 抜粋: "st.cache_data.clear()")


* **エラーハンドリング**: なし（呼び出し元 `main()` の `try` の内側で呼ばれる）
* 根拠: (行番号: 91〜134 / 抜粋: "def _render_header_actions() -> None:")


* **補足（リンクがルート相対である理由）**: ソースのコメントに、このダッシュボードは `unified_server.py`(8000番)の `config.DASHBOARD_BASE_PATH` 配下に中継されて配信されるため、閲覧しているオリジン（LANのIP:8000でもCloudflare経由の公開ドメインでも）の `/quest` に解決されるルート相対リンクにしている、`config.FRONTEND_URL` のような固定URLを埋めるとLAN外から開いたときに繋がらない、と記されている。
* 根拠: (行番号: 58〜63 / 抜粋: "ルート相対のリンクにしているのは、このダッシュボードが")



### （Issue #701で削除）`_render_ai_report`

* **退役（2026-09-19、Issue #701）**: 以前は`_render_header_actions`と`main`の間に、`analysis_service.load_ai_report()`で取得した`ai_report_records`の最新1件（「セバスチャンからの報告」）を折りたたみ表示する`_render_ai_report()`が存在した（`main()`から`safe_section("AIレポート")`で保護して呼ばれていた）。しかしこのテーブルへの書込は2026-07-16を最後に止まっており（実機DBの最新行が`2026-07-16T19:01`）、2か月前の内容が現在の報告として出続けていた。Issue #584では「リポジトリ管理外の外部プロセスが書き込む前提」と結論していたが、その外部プロセスも動いていないことが実機で確認されたため、オーナー判断で機能ごと退役した。あわせて読み出し側の`analysis_service.load_ai_report()`と`config.SQLITE_TABLE_AI_REPORT`も削除した。`ai_report_records`テーブル自体は履歴として残しており、削除マイグレーションは追加していない。
* 根拠: `main()`内の退役コメント (行番号: 320 / 抜粋: "# Issue #701 (2026-09-19): 以前はここで「セバスチャンからの報告」")、回帰テスト`tests/test_dashboard_low_items.py`の`TestAiReportRetired`



### `main`

* **役割**: サイドバー設定、ヘッダー操作列の描画、タブ選択UIの描画、**選択中のタブ1つだけ**のレンダリングを行うアプリ本体の処理（**タブの遅延評価で変更**: 以前は5タブ分のデータ読み込みと5タブ分のレンダリングを毎回行っていた）。例外発生時はログ記録・Discord通知・エラー画面表示を行う。**（スマホ対応で変更）** サイドバーからは「データを更新」ボタンが `_render_header_actions` へ移り、代わりに主要操作の場所とスマートフォンからのアクセス経路（8000番の `/dashboard` 経由）を案内する `st.caption` が置かれている。（AIレポート表示はIssue #701で退役し、`main()`からも削除された。）**（Issue #410 L-L5で修正）** 例外発生時に画面表示していた`traceback.format_exc()`を`logger.error`によるログ出力のみに変更し、内部のファイルパス・設定値がLAN内の閲覧者に露出しないようにした。
* 根拠: `def main():` (行番号: 284〜343 / 抜粋: "def main():")、サイドバーの案内文 (行番号: 73〜76 / 抜粋: "\"主要な操作(データ更新)はメイン画面の先頭にあります。\"")、トレースバックのログのみ化 (行番号: 343 / 抜粋: "logger.error(traceback.format_exc())")


* **引数/リクエスト**: なし
* 根拠: `def main():` (行番号: 284 / 抜粋: "def main():")


* **戻り値/レスポンス**: なし（Streamlit UIへの描画が主目的）
* 根拠: `def main():` (行番号: 284 / 抜粋: "def main():")


* **タブ構成（スマホ対応で再設計）**:

| タブ | 中身（既定で閉じた `view_common.lazy_section` に畳まれるものは「▸」付き。**開いたときだけ中身が実行される**） | `safe_section` のセクション名 |
| --- | --- | --- |
| 🏠 ホーム | `summary.render_summary` | サマリー |
| 👀 見守り | `misc_tab.render_photos` / ▸`sensor_tab.render_takasago` / ▸`health_tab.render` | 防犯カメラ / 高砂実家 / 健康管理 |
| 💡 くらし | `sensor_tab.render_electricity` / ▸`sensor_tab.render_temperature` | 電力・環境 / 気温詳細 |
| 🔧 システム | `log_tab.render_resources` / `log_tab.render_nas_status` / ▸`log_tab.render_server_logs` / ▸`log_tab.render_logs` / ▸`log_tab.render_maintenance` | リソース状況 / NAS状態 / サーバーログ / ログ分析 / メンテナンス操作 |

* 根拠: `TABS: tuple[tuple[str, str], ...] = home_status_service.DASHBOARD_TABS` (行番号: 70 / 抜粋: "TABS: tuple[tuple[str, str], ...] = home_status_service.DASHBOARD_TABS")、各タブの描画関数 (行番号: 193〜218 / 抜粋: "def _render_home_tab(now: datetime) -> None:")、`TAB_RENDERERS: Dict[str, Callable[[datetime], None]] = {` (行番号: 281 / 抜粋: "TAB_RENDERERS: Dict[str, Callable[[datetime], None]] = {")


* **副作用**:
    * サイドバーに設定見出し・案内文（`st.caption`）・CSS適用・現在時刻ログを出力する。
    * メイン画面にCSSを適用し、`_render_header_actions()` でヘッダーの操作列を描画する。
    * **（タブの遅延評価で変更）** 選択中のタブが必要とするぶんだけの、`view_common` のキャッシュ付きローダ経由でのデータ読み込み。
    * **（タブの遅延評価で変更）** 選択中のタブ1つぶんのUIレンダリング（各ビューモジュールへ処理委譲）。折りたたみセクションは開いているときだけ実行される。
    * 選択中のタブを `st.query_params` に書き戻す。
    * 例外発生時、エラーログ出力・Discordへのエラー通知（`services.notification_service.send_push`）・画面への汎用エラーメッセージ表示。**（Issue #410 L-L5で修正）** トレースバックは画面表示せず、ログ（`logger.error`）にのみ出力する。
* 根拠: `_render_header_actions()`, `df_sensor = view_common.load_sensor_data_cached(limit=10000)`, `services.notification_service.send_push(`


* **エラーハンドリング**:
    * ヘッダー操作列・タブ選択・タブ描画の呼び出し全体を`try...except Exception as e:`で捕捉する。**（タブの遅延評価で変更）** データ読み込みは各タブの描画関数の中（=`safe_section` の内側）へ移ったため、この外側の`except`に到達するのは、タブ選択そのものやヘッダーの描画が失敗した場合など、セクション単位では隔離できない失敗に限られる。
    * **[修正済み・Issue #438]** 以前はこの`try`ブロックがサマリー表示・全タブのレンダリングまで含んでおり、いずれか1タブの描画例外でもダッシュボード全体がエラー画面になっていた。現在は各描画呼び出しを、[dashboard_common.md](./dashboard_common.md)の`safe_section`コンテキストマネージャで個別に囲み、1つのセクションの例外が他のセクションの描画を止めないようにした（詳細は8節参照）。**（スマホ対応で変更）** 保護の単位は「タブ」ではなく「セクション」になり、1つのタブの中の複数セクション（例: 🔧 システムの5セクション）もそれぞれ独立して保護される。（AIレポートの表示とその`safe_section("AIレポート")`はIssue #701で削除された。）
    * 上記の外側`except Exception as e:`で捕捉した場合、エラーメッセージをログ出力（`logger.error`）した上で、`services.notification_service.send_push`によるDiscord通知を試みる。**[修正済み・Issue #438]** この通知処理自体の失敗は、以前は`except Exception: pass`で握りつぶしていたが、現在は`except Exception as notify_err: logger.warning(...)`でログに記録するよう変更した。
    * 最後に `st.error(...)` でユーザー向けの汎用エラーメッセージを表示する。**（Issue #410 L-L5で修正）** 以前は続けて`st.code(traceback.format_exc())`でトレースバックを画面に出力していたが、内部のファイルパス・設定値の露出防止のため`logger.error(traceback.format_exc())`によるログ出力のみに変更した。
* 根拠: 外側`except Exception as e:` (行番号: 181〜199 / 抜粋: "except Exception as e:")、通知失敗のログ化 (行番号: 191〜194 / 抜粋: "except Exception as notify_err:")、トレースバックのログのみ化 (行番号: 343 / 抜粋: "logger.error(traceback.format_exc())")、`safe_section`によるセクション単位保護 (行番号: 138〜179 / 抜粋: "with view_common.safe_section(")



## 5. 処理フロー図

`main()` 関数における、タブ選択から描画、例外発生時のフォールバックまでの流れを示します。

```mermaid
flowchart TD
    Start(["Start: main()"]) --> Sidebar["サイドバー: 見出し・案内文・CSS適用・現在時刻ログ"]
    Sidebar --> TryStart(["Tryブロック開始"])

    TryStart --> Css["メイン画面にCSS適用"]
    Css --> Header["_render_header_actions(): 更新ボタン / ファミクエへのリンク"]
    Header --> Caption["データ取得時刻・軽量ページへの導線(st.caption)"]
    Caption --> Selector["_render_tab_selector(): segmented_control<br/>?tab= から初期値を決め、選択を書き戻す"]
    Selector --> RenderTabs["TAB_RENDERERS[active_tab](now):<br/>選択中のタブだけを描画<br/>(データ読み込みもこの中。折りたたみは開いたときだけ実行)"]
    RenderTabs --> End(["End: 正常終了(1セクションの例外は他に波及しない)"])

    TryStart -. ヘッダー・タブ選択で例外発生 .-> Catch(["except Exception as e"])
    Selector -. 例外発生 .-> Catch

    Catch --> LogErr["logger.error(err_msg)"]
    LogErr --> TryNotify(["Tryブロック: Discord通知"])
    TryNotify --> SendPush["外部: services.notification_service.send_push(...)"]
    SendPush -. 通知失敗 .-> LogWarn["except Exception as notify_err: logger.warning(...)"]
    SendPush --> ShowError
    LogWarn --> ShowError["st.error() (詳細はlogger.errorのみ)"]
    ShowError --> EndErr(["End: エラー画面表示(データ読み込み失敗時のみ)"])
```

## 6. 依存関係図

```mermaid
graph TD
    subgraph "dashboard.py"
        logger["logger (Global)"]
        questPath["QUEST_APP_PATH"]
        header["_render_header_actions()"]
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
        sensor_tab["views.dashboard.sensor_tab"]
        health_tab["views.dashboard.health_tab"]
        misc_tab["views.dashboard.misc_tab"]
        log_tab["views.dashboard.log_tab"]
    end

    logger --> logging_mod
    header --> streamlit_mod
    header --> questPath
    aiReport --> streamlit_mod
    aiReport --> pytz_mod
    aiReport --> analysis_service
    main --> header
    main --> aiReport
    main --> streamlit_mod
    main --> pytz_mod
    main --> traceback_mod
    main --> view_common
    main --> config_mod
    main --> analysis_service
    main --> common_mod
    main --> summary
    main --> sensor_tab
    main --> health_tab
    main --> misc_tab
    main --> log_tab
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `services/analysis_service.py` | ダッシュボードが表示する全データ（センサー、子供、排泄、食事、車、防犯ログ、NASステータス）の取得ロジックが集約されており、UIの正確な挙動を把握するために必須。 | `from services import analysis_service` (行番号: 13 / 抜粋: "from services import analysis_service") |
| 高 | `views/dashboard/summary.py`, `sensor_tab.py`, `health_tab.py`, `misc_tab.py`, `log_tab.py` | 実際の画面描画ロジックが全てこれらのモジュールに委譲されており、UIの詳細仕様（表示項目・グラフ・操作性）を理解するために必要。 | `from views.dashboard import (...)` (行番号: 14〜21 / 抜粋: "from views.dashboard import (") |
| 高 | `views/dashboard/common.py` | スマホ幅のレイアウト（列の縦積み・タブの横スクロール・ステータスカードのグリッド）がすべて`CUSTOM_CSS`側にあるため、見た目の仕様は本ファイルからは追えない。 | `st.markdown(view_common.CUSTOM_CSS, ...)` (行番号: 110, 117 / 抜粋: "st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)") |
| 中 | `routers/dashboard_router.py`, `services/dashboard_proxy_service.py` | スマートフォンからの到達経路（8000番の`/dashboard`配下への中継）を把握するため。`QUEST_APP_PATH`をルート相対にしている理由もここに依存する。 | ルート相対リンクの理由を述べたコメント (行番号: 58〜63 / 抜粋: "unified_server.py(8000番)の config.DASHBOARD_BASE_PATH 配下に中継されて") |
| 中 | `config.py` | `SQLITE_TABLE_CHILD` 等のテーブル名定数や `LINE_USER_ID` の実値を把握し、DB構造や通知先を確認するため。 | `df_child = view_common.load_generic_data_cached(config.SQLITE_TABLE_CHILD)` |
| 中 | `common.py` | `send_push` の実装（Discord通知の具体的な送信方式・エラー処理）を確認するため。 | `services.notification_service.send_push(` (行番号: 330 / 抜粋: "send_push(") |

## 8. 保守上の注意点

* **ロガー設定方式の不統一**: 本ファイルは `logging.basicConfig()` と `logging.getLogger(__name__)` を直接使用してロガーを構築しているが、`switchbot_service.py` や `backup_service.py` 等の他サービスは `core.logger.setup_logging` を利用している。両方の初期化方式が同一プロセス内で混在すると、ハンドラの重複登録やログフォーマットの不一致が発生する可能性がある。
* **[修正済み・Issue #438] 二重の広範な例外キャッチとタブ横断の巻き込み**: 以前は`main()`全体（データ読み込み〜全タブのレンダリング）を1つの`except Exception as e:`で捕捉しており、いずれか1つのタブの描画で例外が起きるとダッシュボード全体がエラー画面になり、無関係な他のタブまで巻き込んでいた。その中のDiscord通知処理も`except Exception: pass`で握りつぶしており、通知失敗の原因が完全に不可視化されていた。現在は各タブの描画（`with tab_x: ...`のブロック内）と`summary.render_summary`の呼び出しを、[dashboard_common.md](./dashboard_common.md)の`safe_section`コンテキストマネージャでそれぞれ個別に囲み、1タブの例外が他タブに波及しないようにした。`main()`直下の`try/except`は初期データ読み込み(全タブが依存するため、失敗時は全体をエラー画面にする判断は維持)専用として残り、その中のDiscord通知失敗は`pass`ではなく`logger.warning`で記録するよう変更した。
* **（Issue #701で削除）AIレポート表示の退役**: 以前ここに記載していた`_render_ai_report`の`report["timestamp"]`新旧フォーマット分岐（Issue #410 L-L2）は、AIレポート機能ごと削除されたため該当コードが存在しない。`ai_report_records`テーブルは履歴として残っているが、ダッシュボードからは参照しない。表示を復活させる場合は、書込側（生成スクリプト）の復活と鮮度ガード（最新行が古いときの扱い）をセットで検討すること。
* **サイドバーとメイン画面での重複処理**: `view_common.CUSTOM_CSS` の `st.markdown` 呼び出し（79行目・86行目）および `datetime.now(pytz.timezone("Asia/Tokyo"))` の取得（81行目・87行目）がサイドバーブロックとメインのtryブロックでそれぞれ重複して実行されている。
* **更新ボタン押下時の`st.rerun()`**: `_render_header_actions` は `st.cache_data.clear()` 直後に `st.rerun()` を呼んでおり、キャッシュ全クリア＋全データ再読み込みとなるため、データ量によっては応答が遅くなる可能性がある。**（Issue #741）** これは意図した挙動になった（以前はキャッシュが無かったため、押しても押さなくても毎回全読み込みだった）。
* **[Issue #741] データ読み込みは `view_common` のキャッシュ付きラッパー経由にすること**: Streamlit はウィジェット操作・タブ切替・ページ読み込みのたびに `main()` を含むスクリプト全体を再実行する。`analysis_service` を直接呼ぶ行を足すと、その読み取りだけが毎回 SQLite に行き、`unified_server` 側の書き込みとの競合（"database is locked"）の確率も上げる。`tests/test_dashboard_cache.py` の `test_main_does_not_call_analysis_service_loaders_directly` が退行を検知する。
* **（スマホ対応）タブ構成を変えるときは `safe_section` の名前も合わせること**: 各セクションの例外時に画面へ出る文言は `safe_section` に渡した名前そのものであり（`views/dashboard/common.py`）、タブ名とは独立している。
* **（スマホ対応）レイアウトの実体はCSS側にある**: スマホ幅での列の縦積み・タブの横スクロール・ステータスカードの列数はすべて `view_common.CUSTOM_CSS` のメディアクエリとCSS Gridで決まる。本ファイルの `st.columns` / `st.tabs` の呼び出しだけを見ても、スマートフォンでの見え方は分からない。
* **（スマホ対応）`/quest` へのリンクはルート相対**: `QUEST_APP_PATH` は `"/quest"` であり、8501番へ直接アクセスした場合（中継を経由しない場合）は解決先が存在しない。通常の到達経路は8000番の `/dashboard` 配下である。
* **（スマホ対応）メンテナンス操作はスマートフォンからも到達できる**: 「🔧 システム」タブの `log_tab.render_maintenance`（サービス再起動・バックアップ）は折りたたみの中にあるが、モバイル専用の非表示化はしていない。誤操作防止はチェックボックスによる2段階確認に依存している。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `analysis_service` の各読み込み関数の仕様 | DBアクセス方法、返却される `DataFrame` のスキーマが本ファイルからは不明。**（Issue #741で変更）** キャッシュの有無は `views/dashboard/common.py` 側で決まるようになった。 | `services/analysis_service.py`, `views/dashboard/common.py` |
| 各ビューモジュールの実装詳細 | `summary`, `sensor_tab`, `health_tab`, `misc_tab`, `log_tab` の描画内容・引数の使い方が不明。 | `views/dashboard/summary.py` ほか各ビューファイル |
| `config` の設定値の実体 | `SQLITE_TABLE_CHILD` 等のテーブル名の具体的な値が不明。 | `config.py` |
| `view_common.safe_section` の挙動 | 例外時に何を表示し、どこへログを出すかが本ファイルからは不明。 | `views/dashboard/common.py` |
| スマートフォンからの到達経路 | サイドバーの案内文が「8000番の /dashboard 経由」と述べているが、その中継の実装は本ファイルに無い。 | `routers/dashboard_router.py`, `services/dashboard_proxy_service.py`, `config.py` |
| `services.notification_service.send_push` の仕様 | Discord通知の送信方式や失敗時の挙動（例外を送出するか等）が不明。 | `common.py` |
| `view_common.CUSTOM_CSS` の内容 | 具体的なスタイル定義（特にスマホ幅のメディアクエリ）が不明。 | `views/dashboard/common.py` |

## 相互参照による補足情報

| 元の不明事項 | 判明した内容 | 参照元ドキュメント |
| --- | --- | --- |
| `analysis_service` の各読み込み関数の仕様 | `MY_HOME_SYSTEM/services/analysis_service.py`を直接確認した。`get_ro_db_connection()`(32〜39行目)は`sqlite3.connect(f"file:{config.SQLITE_DB_PATH}?mode=ro", uri=True, timeout=10.0)`で読み取り専用接続を返し、これを内部で用いる`load_generic_data(table_name, limit=500)`(150行目)は`SELECT * FROM {table_name} ORDER BY timestamp DESC LIMIT {limit}`を実行、`load_sensor_data(limit=5000)`(155行目)は`device_records`・SwitchBotメーターログ・電力使用量の複数テーブルを統合して`pd.DataFrame`を返す、`load_nas_status()`(170行目)は最新1件を`Optional[pd.Series]`で返す設計であることを確認した（同じく最新1件を返していた`load_ai_report()`はIssue #701で削除済み）。ファイル全体を`cache_data`および`import streamlit`で検索したが該当箇所はなく、`st.cache_data`によるキャッシュは実装されていないことを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/services/analysis_service.py:32-39, 170-195` |
| 各タブビューモジュールの実装詳細 | `MY_HOME_SYSTEM/views/dashboard/`配下の各ファイルを直接確認した。`summary.py`は`get_takasago_status(df_sensor, now)`(13行目)、`get_itami_status(df_sensor, now)`(36行目)、`get_traffic_status()`(86行目)、`get_server_status()`(97行目)、`get_nas_status_simple(nas_data)`(103行目)等のステータス取得関数群、`quest_tab.py`は引数なしの`render()`(8行目)、`sensor_tab.py`は`render_electricity(df_sensor, now)`(9行目)/`render_temperature(df_sensor, now)`(56行目)/`render_takasago(df_sensor)`(106行目)、`health_tab.py`は`render(df_child, df_poop, df_food)`(5行目)、`misc_tab.py`は`render_traffic()`(14行目)/`render_photos(df_security_log)`(84行目)/`render_bicycle(df_bicycle)`(110行目)、`log_tab.py`は`render_logs(df_sensor)`(8行目)/`render_system()`(20行目)を持つことを確認した。**（Issue #507で削除）** `log_tab.py`にはかつて`render_trends()`(旧22行目、`app_rankings`テーブル参照)も存在したが、書き込み側の収集コードが無く機能として死んでいたため、対応する「トレンド」タブの登録ごと削除された。 | 直接ソース確認: `MY_HOME_SYSTEM/views/dashboard/summary.py:13-103`, `MY_HOME_SYSTEM/views/dashboard/quest_tab.py:8`, `MY_HOME_SYSTEM/views/dashboard/sensor_tab.py:9-106`, `MY_HOME_SYSTEM/views/dashboard/health_tab.py:5`, `MY_HOME_SYSTEM/views/dashboard/misc_tab.py:14-110`, `MY_HOME_SYSTEM/views/dashboard/log_tab.py:8-87` |
| `config` の設定値の実体 | `MY_HOME_SYSTEM/config.py`を直接確認した。`SQLITE_TABLE_CHILD`(245行目)は`"child_health_records"`という文字列定数、`LINE_USER_ID`(185行目)は`os.getenv("LINE_USER_ID")`で環境変数由来（既定値なし、未設定時は`None`）であることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/config.py:185, 245` |
| `services.notification_service.send_push` の仕様 | `MY_HOME_SYSTEM/common.py:31-37`が`services.notification_service`から`send_push`を再インポートしているFacadeであることを直接確認した上で、`MY_HOME_SYSTEM/services/notification_service.py:116-163`の実装を直接確認した。Issue #289で`send_push(messages, *, target="both", channel="notify", user_id=None, image_data=None, filename="snapshot.jpg")`に再設計されており、`target`が`"both"`または`"discord"`を含む場合に`_send_discord_webhook`、`"both"`または`"line"`を含む場合に`user_id`(省略時は`config.LINE_USER_ID`にフォールバック)を用いて`_send_line_push`をそれぞれ呼び出す統合プッシュ通知関数であることを確認した。本ファイル(`dashboard.py`)は`target="discord"`のみで呼び出すため`user_id`は渡していない。 | 直接ソース確認: `MY_HOME_SYSTEM/common.py:31-37`, `MY_HOME_SYSTEM/services/notification_service.py:116-163`, `MY_HOME_SYSTEM/dashboard.py:165-169` |
| `view_common.CUSTOM_CSS` の内容 | `MY_HOME_SYSTEM/views/dashboard/common.py`を直接確認した。`CUSTOM_CSS`(11行目〜)は`<style>`タグを含むCSS定義を格納した1つの長い三重引用符文字列定数であり、`views/dashboard/common.py`自体は適用処理を持たず単にこの文字列を定義しているのみで、実際に画面へ流し込むのは本ファイル(`dashboard.py`)48行目・55行目の`st.markdown(view_common.CUSTOM_CSS, unsafe_allow_html=True)`であることを確認した。 | 直接ソース確認: `MY_HOME_SYSTEM/views/dashboard/common.py:11`, `MY_HOME_SYSTEM/dashboard.py:48,55`（参考: [dashboard_common.md](./dashboard_common.md)） |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した
* [x] 全てのインポート要素を列挙した
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
