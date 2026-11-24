## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `power_page_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [power_analysis_service.md](./power_analysis_service.md) - 表示する内容(`build_power_report`)の計算
* [dashboard_page_service.md](./dashboard_page_service.md) - ページの外枠(`_page_shell`)とCSSの共有元
* [dashboard_router.md](./dashboard_router.md) - `GET /dashboard/power`

## 2. ファイルの概要

⚡電気ページ(`/dashboard/power`)のHTML組み立て。`power_analysis_service.build_power_report`の戻り値を受け取って描くだけで、計算はしない。グラフはインラインSVG(NASの容量グラフと同じ方式。JS・外部ライブラリなし)。ページの外枠とCSSは`dashboard_page_service`と共通。数値はすべてサーバー側で整形した数値で、外部由来の文字列は出さない(それでも見出し・ラベルは`html.escape`する)。

フッターナビ(`dashboard_page_service._page_shell`が付ける)では、このページはくらし配下として「くらし」を現在地にする(上部の切替タブも同様)。

構成: くらしへ戻る→🧾今月のまとめ(**2026-11-24追加**。今月の電気代(今日まで)・先月の同じ日まで・先月との差を並べ、今月の日別の電気代は`<details>`で折りたたむ。くらしページの「💰 今月の電気代」カードのタップ先)→単価と「概算」の注記→表示期間(30/60/90日)のリンク→(1)📈月ごとの推移(今月の昨日までを先月・去年の同じ月と比較、13か月の棒グラフ。一部の日だけ記録のある月は薄く表示、月ごとの数字は`<details>`)→(2)📅日別の電気代(気温・使用量・記録率。記録が欠けた日に⚠️、当日は「途中」。最初の10日だけ展開し残りは`<details>`)→(3)🌡️気温との関係(気温帯別の1日あたりの電気代。気温データが無いときは`weather_monitor.py --backfill-days 400`の案内)→(4)📺テレビの使い方(点灯時間・電気代・家全体に占める割合・視聴回数・最長視聴・平均点灯電力・待機電力、時刻別の点灯分を平日と休日の2系列、日別の数字は`<details>`。プラグ未設定・記録なしはそれぞれの案内)。各枠は独立して、データが無い・不足のときは理由を表示する。
* 根拠: [関数定義] (行番号: 283 / 抜粋: "def render_power_page("), [関数定義] (行番号: 246 / 抜粋: "def _this_month_section("), [関数定義] (行番号: 45 / 抜粋: "def svg_bars(")

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `services.dashboard_page_service` | 内部 | `_page_shell`・`_back_link`(ページの外枠)、`_render_daily_cost_history`(今月の日別の横棒リスト) |
| `services.power_analysis_service` | 内部 | 定数(表示期間・月数・視聴の間隔)と気温帯のラベル |
| `core.utils.get_now_jst` | 内部 | レポートに時刻が無いときの「今日」 |

## 4. 関数

* `svg_bars(values, labels, *, titles, faded, aria, value_labels)`: 棒グラフ(1系列。0を基準に最大値でスケール、全部0でも描ける)。`faded`の棒は薄く、棒が14本以下のときだけ値のラベルを付ける。
* `svg_grouped_bars(series, labels, *, label_every, titles, aria)`: 2系列を並べた棒グラフ(系列1は`bar-alt`)。同じ目盛りでスケールし、ラベルは`label_every`個おき。
* `_this_month_section(monthly_cost, last_month_cost, daily_cost_rows, today)`: 🧾今月のまとめ。`monthly_cost`が None なら「今月の電気代を取得できませんでした」。先月分(`last_month_cost`)が0/None なら「先月の同じ日まで: 記録なし」で差は出さない。差は`+N円(+M%)`(`_diff_percent`)。`daily_cost_rows`のうち`today`と同じ年月の日だけを`page._render_daily_cost_history(month_rows, today)`で描き、`<details>`(「今月の日別を見る(N日)」)に入れる。今月の日が無ければ日別は出さない。
  * 根拠: [関数定義] (行番号: 246 / 抜粋: "def _this_month_section(")
* `render_power_page(report, *, dashboard_path, now=None, monthly_cost=None, last_month_cost=None, daily_cost_rows=None)`: ページ全体。後ろの3つは`home_status_service.get_cached_materials()`の同名の値(ルーターが渡す)で、くらしのカードと同じ数字を今月のまとめに使う。
  * 根拠: [関数定義] (行番号: 283 / 抜粋: "def render_power_page(")

**(2026-11-12 UX改善)** ページ上部のリンクは`page._back_link(dashboard_path, 'life', 'くらし')`(「← くらしへ戻る」)になった(以前は`page._back_to_home_link`による「ホームへ戻る」とページ切替タブ)。電気ページは下部ナビに無いので、親の「くらし」へ戻るリンクだけを持つ。
