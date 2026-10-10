## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `power_analysis_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [power_page_service.md](./power_page_service.md) - 結果の表示(`/dashboard/power`)
* [analysis_service.md](./analysis_service.md) - `load_smart_meter_rows`/`load_power_rows`/`load_weather_range`(DB読み取り)
* [weather_history_service.md](./weather_history_service.md) - 気温データの書き込み元
* [dashboard_router.md](./dashboard_router.md) - `GET /dashboard/power`

## 2. ファイルの概要

電気代・電力の分析(月ごとの推移・日別・気温との関係・テレビの使い方)。元データは`power_usage`(スマートメーターと各プラグの瞬時電力。5分ごと)と`weather_history`(日次の気温)。

* 電気代は既存の概算(`analysis_service._calculate_cost_between`)と同じ考え方: 瞬時電力(W)に「直前の記録からの経過時間」を掛けてkWhにし、単価`config.ELECTRICITY_YEN_PER_KWH`(既定31円)を掛ける。経過時間が`MAX_INTERVAL_HOURS`(1時間)を超える区間(記録の欠落)は数えない。
* **日ごとに区切って経過時間を取る**(日をまたぐ区間は数えない)ことも既存の日別と同じで、月の値は日の値の合計。そのため、月初からの累計を一括で数えるホームのカードとは、1日あたり1区間(5分)ぶん(約0.3%)ずれることがある。
* 記録が欠けた日は実際より低く出るため、日ごとに**記録率**(数えられた時間 ÷ (その日の経過時間×メーター台数)。当日は0時から現在まで)を出す。記録率が`MIN_COVERAGE_FOR_STATS`(0.8)未満の日と当日は、気温帯別の集計から外す。
* 月ごとの推移は`MONTHS_SHOWN`(13)か月分。電力ログの保持は400日なので、それより古い月は欠ける(`partial`)。先月・前年同月との比較は、その期間の記録がすべて揃っているときだけ値を出す。
* どの組み立て関数も例外を送出しない。電力・気温の枠とテレビの枠は独立で、片方の取得・計算に失敗してももう片方は表示する。
* 根拠: [関数定義] (行番号: 61 / 抜粋: "def compute_intervals("), [関数定義] (行番号: 282 / 抜粋: "def build_power_report(")

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `pandas` | 外部ライブラリ | 区間・日別・月別・気温帯の集計 |
| `services.analysis_service` | 内部 | DB読み取り(`load_smart_meter_rows`/`load_power_rows`/`load_weather_range`) |
| `core.jp_holidays.is_offday` | 内部 | テレビの平日・休日の区別(土日・祝日・家の休み) |
| `config.ELECTRICITY_YEN_PER_KWH`/`TV_PLUG_DEVICE_ID`/`TV_POWER_ON_THRESHOLD_WATTS`/`WEATHER_LOCATION_NAME` | 内部 | 単価・テレビのプラグ・点灯の閾値・気温の地点 |

## 4. 定数

`MAX_INTERVAL_HOURS`=1.0、`MONTHS_SHOWN`=13、`DAYS_CHOICES`=(30, 60, 90)・`DEFAULT_DAYS`=30、`MIN_COVERAGE_FOR_STATS`=0.8、`TEMP_BAND_EDGES`=(5, 10, 15, 20, 25, 30)、`SESSION_GAP_MINUTES`=15、`REPORT_CACHE_TTL_SEC`=600.0。

## 5. 関数

* `compute_intervals(df)`: 記録から区間ごとの経過時間とkWhを求める(device_id・日ごとに差を取り、1時間超と各グループの最初の記録は除く)。必要な列が無ければ空。負の電力(逆潮流)はそのまま減算される。
* `daily_energy(intervals, days, now, n_devices)`: 日ごとのkWh・電気代・記録率(`days`の全日を返す。記録の無い日は0)。
* `monthly_totals(daily)`: 日別から月ごと(古い順)に合計。`partial`は記録のある日が月の日数より少ない月。
* `month_to_date_comparison(daily, today)`: 今月の「昨日まで」を、先月・前年同月の同じ日数と比べる(当日は含めない。月初なら`None`。先月に同じ日数が無い、または記録の欠けた日があれば、その比較先は`None`)。
* `join_temperature(daily, weather)`: 日別に最低・最高・平均気温を結合(平均は両方あるときだけ(最低+最高)÷2)。
* `temperature_bands(daily_with_temp, today)`: 平均気温の帯ごとに1日あたりの使用量・電気代を平均する(帯の下限は含む。日数0の帯も返す)。
* `tv_analysis(rows, days, threshold_watts, total_kwh)`: テレビのプラグの記録から、日別(点灯時間・kWh・電気代)、時刻別(1日あたりの点灯分。平日・休日別に日数で割る)、視聴(セッション)の回数・最長、点灯中の平均電力、待機電力、家全体に占める割合を求める。**点灯**は閾値以上の記録(5分間隔なので誤差±5分程度)。**視聴**は点灯の記録どうしの間隔が`SESSION_GAP_MINUTES`分以内で続く間(長さは点灯していた時間の合計)。`days`の範囲外の記録は無視する。記録が無ければ`has_data: False`。
* `build_power_report(days=DEFAULT_DAYS, now=None)`: 画面用の内容を組み立てる。`now`を省略したときだけ`REPORT_CACHE_TTL_SEC`のキャッシュを使う。`clear_cache()`はテスト用。
