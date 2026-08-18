# DDD 仕様書一覧

`DDD/` 配下の情報収集・コンテンツ生成バッチスクリプト群の仕様書。ソースファイル1つにつきMarkdown仕様書1つが対応する規約(`docs/specifications/README.md`参照)。

| 仕様書 | 対象ソース | 概要 |
| --- | --- | --- |
| [batch_download_discord.md](./batch_download_discord.md) | `batch_download_discord.py` | 複数URLリストからの動画バッチダウンロードCLI。`yt-dlp`/スクレイピングの戦略パターン、ロックファイルによる多重起動防止、実行許可時間帯制御、Discord通知を備える。 |
| [file_utils.md](./file_utils.md) | `file_utils.py` | `batch_download_discord.py`・`extract_youtube_urls.py`で重複していたファイル名サニタイズ処理を集約した共通ユーティリティ(`sanitize_filename`)。 |
| [newface_monitor.md](./newface_monitor.md) | `newface_monitor.py` | 対象サイトの新人キャスト紹介ページを定期巡回し、新規追加をDiscord Webhookで通知するバッチスクリプト。 |

> 各仕様書には「関連ドキュメント」セクションがあり、他サブシステム(MY_HOME_SYSTEMのNAS監視系等)との相互参照リンクを含みます。全体像は[全体設計書.md](../全体設計書.md)を参照してください。
