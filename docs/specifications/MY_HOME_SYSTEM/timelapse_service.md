## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `timelapse_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [daily_timelapse_job.md](./daily_timelapse_job.md) - 動画(`*_summary.mp4`と`.jpg`)を生成する側。ファイル名の形式の出どころ
* [dashboard_page_service.md](./dashboard_page_service.md) - 一覧の描画(`_render_timelapse_section`)
* [dashboard_router.md](./dashboard_router.md) - `GET /dashboard/watch`(`?tl_all=1`)
* [nas_monitor.md](./nas_monitor.md) - 保持期間(`RECORDING_RETENTION_DAYS`)を過ぎた動画の削除

## 2. ファイルの概要

お家ダッシュボードの見守りページに出す「🎞️ タイムラプス」の一覧用に、生成済みの動画を**読み取り専用**で列挙するサービス。動画は`daily_timelapse_job.py`が`BASE_DIR/assets/timelapse/`に`<カメラ>_<日付>[_<開始>-<終了>]_summary.mp4`(例: `entrance_2026-10-16_0730-0900_summary.mp4`)として書き、同名の`.jpg`をサムネイルとして添える。ここは何も書かず・消さず、この形式に完全一致する通常ファイル(シンボリックリンクは除く)だけを新しい順(日付→開始時刻)で返す。動画自体は`unified_server`が`/assets`として配信している静的ファイル(`/assets/timelapse/<ファイル名>`)をそのまま使う(専用のAPI・変換は無く、Rangeリクエストによるシークは静的配信側が対応する)。生成・Discordへの送信には触れない。例外は送出しない(読めないときは空リスト+警告ログ)。
* 根拠: `def list_timelapses(limit: int | None = None, *, now: float | None = None) -> list[Timelapse]:` (行番号: 58)

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `config.BASE_DIR` | 内部 | 動画の置き場所(`assets/timelapse`)の基準 |
| `core.logger.setup_logging` | 内部 | 読み取り失敗の警告ログ |

## 4. 主要要素の定義

* `URL_PREFIX`(`/assets/timelapse`): 動画・サムネイルのURLの接頭辞。`MIN_AGE_SEC`(90): 最終更新からこの秒数が経っていないファイルは、生成の途中とみなして一覧に出さない。0バイトのファイルも出さない。
* `Timelapse`(frozen dataclass): `filename`/`camera`/`day`/`start`/`end`(`HHMM`。時間指定が無ければ`None`)/`size_bytes`/`has_thumbnail`。
* `timelapse_dir()`: `BASE_DIR/assets/timelapse`(`smart_timelapse_generator.setup_directories`と同じ)。
* `list_timelapses(limit=None, *, now=None)`: 一覧。ファイル名は正規表現に完全一致したものだけ(日付が不正なもの・パス区切りを含むもの・`.tmp`などは無視)。ディレクトリが無ければ空、読めなければ空+警告ログ。`limit`で先頭から切る。

## 5. 保守上の注意点

* ファイル名の形式を変える場合は、`daily_timelapse_job.py`の出力名と`_FILENAME_RE`を一緒に直すこと。
* 保持期間(既定30日)を過ぎた動画は`nas_monitor`が削除するため、一覧に出るのは直近の分だけ。

## 6. 不明事項一覧

* 実機のタイムラプスの本数・ファイルサイズは、この環境から確認できていない。
