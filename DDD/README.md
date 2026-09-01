# DDD

動画・画像等のデータ自動収集を担うバッチ処理群。仕様の詳細は
[docs/specifications/DDD/](../docs/specifications/DDD/README.md)、
システム全体の構成は[全体設計書.md](../docs/specifications/全体設計書.md)を参照。

## 主なスクリプト

| スクリプト | 役割 |
| --- | --- |
| `batch_download_discord.py` | `list/`配下のURLリストから動画を一括ダウンロードし、Discordへ通知する。 |
| `newface_monitor.py` | 対象サイトの新人紹介ページを定期巡回し、新規追加をDiscordへ通知する。 |
| `extract_youtube_urls.py` | 指定チャンネル・プレイリストから動画URLを抽出する。 |
| `split_prompts.py` | 番号付きMarkdownを項目ごとの個別ファイルへ分割する。 |

いずれもMY_HOME_SYSTEM側の`.venv`(`/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/python`)で実行する
(DDD専用のvenvは持たない)。実機での定期実行はリポジトリルートの
[deploy/cron/](../deploy/cron/README.md)で管理している。

単身赴任先PC(Windows)での`batch_download_discord.py`実行環境構築については
[setup_remote_pc_windows.md](./setup_remote_pc_windows.md)を参照。

## セットアップ

```bash
/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/pip install -r requirements.txt
```

## テスト

```bash
/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/pytest DDD/
```
