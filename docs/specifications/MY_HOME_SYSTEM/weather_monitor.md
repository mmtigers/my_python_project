## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `weather_monitor.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [weather_history_service.md](./weather_history_service.md) - 取得・保存の実体
* [scheduler_boot.md](./scheduler_boot.md) - 6時間ごとの定期実行(`TASKS`に登録)

## 2. ファイルの概要

日次の気温を取得して`weather_history`へ保存する監視スクリプト。`scheduler_boot.py`が6時間(21600秒)ごとに実行する。電気代と気温を突き合わせる分析(`/dashboard/power`)の気温データの元。毎回、直近`RECENT_DAYS`日分を更新するので、数日の取得失敗があっても次回で補われる。

過去分の取り込み(電力データのある期間まで遡る。初回だけ手で実行する): `python monitors/weather_monitor.py --backfill-days 400`(電力ログの保持が400日のため)。

* 取得に失敗しても終了コードは0(外部APIの一時的な不調で、スケジューラ側のエラー扱い・通知を起こさない。失敗は警告ログに残り、次回の実行で再試行される)。
* 根拠: [関数定義] (行番号: 27 / 抜粋: "def main(")

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `argparse` | 標準 | `--backfill-days`の解釈 |
| `services.weather_history_service` | 内部 | `update_recent`・`backfill` |
| `core.logger.setup_logging` | 内部 | ロガー(`monitor`名`weather_monitor`) |

## 4. 関数

### `main`

* **役割**: `--backfill-days N`(N>0)があれば、先に`backfill(N)`を実行して保存件数をINFOログに残す。続いて`update_recent()`で直近分を更新し、保存できなければ警告ログを出す。
* 根拠: `def main(argv: list[str] | None = None) -> int:` (行番号: 27)
* **戻り値**: 常に0。
