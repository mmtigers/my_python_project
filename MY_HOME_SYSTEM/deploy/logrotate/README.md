# logrotate 設定

実機(Raspberry Pi)の `/etc/logrotate.d/` に配置するログローテーション設定を、
`deploy/systemd/` と同様に故障時の復旧・変更履歴管理のためこのリポジトリで管理する。

## home_system

`MY_HOME_SYSTEM/logs/*.log` と `DDD/logs/newface_monitor.log` を毎日ローテーション
(14世代保持、2世代目以降はgzip圧縮)する。

背景(棚卸し2026-08 課題5): 従来は `core/logger.py` の `TimedRotatingFileHandler` が
プロセスごとに `home_system.log` をrenameしようとしてローテーションが実質破損しており
(旧backupファイルに書き込み続ける)、`batch_download_discord.log` など
シェルリダイレクトのログは無制限に肥大していた。ローテーションをlogrotateに一元化し、
Python側は `WatchedFileHandler` で追記のみ行うよう変更した。

導入手順(実機側):

```bash
sudo cp deploy/logrotate/home_system /etc/logrotate.d/home_system
sudo chmod 644 /etc/logrotate.d/home_system
# 設定の検証(dry-run)
sudo logrotate -d /etc/logrotate.d/home_system
```

Debian系では `logrotate.timer` が毎日自動実行するため、cron等の追加登録は不要。

実機の設定を変更した場合は、このファイルにも反映してコミットすること。
反映漏れは、毎時cronの `monitors/health_watch.py`(チェック7: 実機構成ドリフト検知)が
本ディレクトリのファイル(README除く)と `/etc/logrotate.d/` 側の同名ファイルをコメント・空行を除いて比較し、
差分・未導入があれば Discord の error チャンネルへ通知する(自動で書き戻しはしない)。
