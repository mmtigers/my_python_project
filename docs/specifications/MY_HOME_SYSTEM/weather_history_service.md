## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `weather_history_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [weather_monitor.md](./weather_monitor.md) - 定期実行する呼び出し元(`monitors/weather_monitor.py`)
* [power_analysis_service.md](./power_analysis_service.md) - 気温の利用側(電気代と気温の分析)
* [analysis_service.md](./analysis_service.md) - `load_weather_range`(読み出し側)
* [config.md](./config.md) - `WEATHER_LOCATION_NAME`/`WEATHER_LATITUDE`/`WEATHER_LONGITUDE`

## 2. ファイルの概要

日次の気温を Open-Meteo(キー不要の無料API)から取得して`weather_history`へ書き込むサービス。`weather_history`は以前のレガシーな天気取得スクリプト(`weather_service.py`。レガシースクリプト整理で削除)が書いていたが、削除以来、書き込む経路が無かった。これはその後継(気温のみ。降水確率・傘判定の列`max_pop`/`umbrella_level`は書かない・上書きしない)。

* 直近分: `forecast` API(`past_days`で数日前まで遡れる)。毎回、直近`RECENT_DAYS`(7)日分を更新するので、数日の取得失敗があっても次回の実行で補われる。
* 過去分: `archive` API(`start_date`〜`end_date`)。電力データのある期間まで遡るバックフィル用。archive APIには数日分の反映遅れがあるため終端は前日までとし、直近は`update_recent`が埋める。`BACKFILL_CHUNK_DAYS`(90)日ずつ区切り、ある区間の取得に失敗しても残りは続ける。
* 書き込みは「UPDATE → 0行ならINSERT」。`UNIQUE(date, location)`の有無に依存せず、`weather_desc`は新しい値が無いときに既存の値を消さない。
* 外部通信・HTTPエラー・JSON不正は例外にせず`None`(警告ログのみ)で返す。気温は`-60〜60℃`の範囲外・非数値・非有限(NaN/inf)を欠損として捨て、最低・最高がどちらも欠損の日は捨てる。
* 根拠: [関数定義] (行番号: 123 / 抜粋: "def fetch_recent("), [関数定義] (行番号: 134 / 抜粋: "def save_rows("), [関数定義] (行番号: 172 / 抜粋: "def backfill(")

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `requests` | 外部ライブラリ | Open-Meteoの`forecast`/`archive`APIの呼び出し(タイムアウト`REQUEST_TIMEOUT_SEC`=20秒) |
| `core.database.get_db_cursor` | 内部 | `weather_history`への書き込み |
| `core.utils.get_now_iso`/`get_now_jst` | 内部 | 記録時刻・バックフィルの基準日(JST) |
| `config.WEATHER_LATITUDE`/`WEATHER_LONGITUDE`/`WEATHER_LOCATION_NAME` | 内部 | 取得地点と書き込む地点名(既定は伊丹) |

## 4. 関数

* `describe_weather_code(code)`: WMO weather codeを日本語(快晴/晴れ/晴れ時々くもり/くもり/霧/霧雨/雨/雪/雷雨/その他)にする。コードが無い・数値でないときは`None`。
* `parse_daily(payload)`: 応答の`daily`を`{date, min_temp, max_temp, weather_desc}`のリストにする。形式が想定と違う・日付が不正・両気温が欠損の日は捨てる。値のリストが短くても例外にしない。
* `fetch_recent(days=RECENT_DAYS)`: 直近`days`日分(今日を含む。上限92)。`fetch_range(start, end)`: 期間(両端を含む)。どちらも失敗は`None`。
* `save_rows(rows, location=None)`: 書き込んだ日数を返す。DB書き込みに失敗したら警告ログにして0。
* `update_recent()`: 直近分を取得して保存する。保存した日数(取得失敗は0)。
* `backfill(days, today=None)`: 前日から`days`日さかのぼって取得・保存する。保存した日数の合計。
