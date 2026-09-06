# systemd units

実機(Raspberry Pi)の `/etc/systemd/system/` に配置されている systemd ユニットファイルを、
故障時の復旧・変更履歴管理のためにこのリポジトリでも管理する。

## health-check.service

`post_boot_health_check.py` を起動時(`multi-user.target` 到達時)に1回実行し、
Discordへ起動レポートを送信する。

導入手順(実機側):

```bash
sudo cp deploy/systemd/health-check.service /etc/systemd/system/health-check.service
sudo systemctl daemon-reload
sudo systemctl enable health-check.service
```

> **（修正済み）** 以前 `After=` に `unified-server.service` という実機に存在しないユニット名
> (おそらく `home_system.service` へのリネーム前の旧名)が指定されており、死んだ依存関係に
> なっていた(systemdはエラーにせず順序制約を無視するだけのため気づかれずに残っていた)。
> 実機・本ファイルともに `After=network-online.target home_system.service` へ修正済み。

## home_system.service

`start_all.sh` を `ExecStart` で実行するoneshotユニット(`RemainAfterExit=yes`)。
`start_all.sh` が内部で `unified_server.py` を `nohup` バックグラウンド起動し、
`unified_server.py` がさらに `scheduler_boot.py` 等を起動する。

> **`Restart=always` を使わない理由(Issue #492・決定: 案A)**: `Type=oneshot` +
> `RemainAfterExit=yes` の構成では、`start_all.sh` 自体が正常終了した時点で
> systemdは「成功して完了した」と扱う。実際のサーバープロセス(`unified_server.py`)は
> `nohup ... & disown` でsystemdの管理下から外れているため、`Restart=always` を
> 付けても`start_all.sh`が異常終了しない限り再起動はトリガーされず、
> `unified_server.py`単独のクラッシュに対しては実質的に無効。`Type=simple` +
> `Restart=on-failure` へ移行する案(`start_all.sh`のバックグラウンド起動をやめ、
> `unified_server.py`をsystemdのフォアグラウンドプロセスにする)も検討したが、
> `start_all.sh` Phase 0(旧プロセスのクリーンアップ)・Streamlitダッシュボードの
> 別プロセス起動との整合を取り直す必要があり変更規模が大きいため、今回は見送った。
>
> 代わりに、`unified_server.py`単独のクラッシュ検知は
> [health_watch.md](../../../docs/specifications/MY_HOME_SYSTEM/health_watch.md)
> （`monitors/health_watch.py`、cron駆動でこのサービスのプロセスツリーから独立、
> `deploy/cron/crontab`に毎時10分で登録、Issue #339/#484）に委ねる。
> `scheduler_boot.py`配下の監視群(`server_watchdog.py`等)は本サービスと同じ
> プロセスツリーで動くため、本サービスごと落ちると一緒に停止し検知できないが、
> cron駆動の`health_watch.py`はその穴を塞ぐ位置づけになる。**自動復旧は行わず、
> Discord通知を受けて人間が`systemctl restart home_system.service`等で復旧する**
> 運用とする(runbookのガードレール「自動適用・自動デプロイ・`systemctl restart`の
> 自動実行は行わない」と整合)。

導入手順(実機側):

```bash
sudo cp deploy/systemd/home_system.service /etc/systemd/system/home_system.service
sudo systemctl daemon-reload
sudo systemctl enable home_system.service
```

## network_logger.service

`monitors/network_logger.py` を常駐実行し、カメラ群のネットワーク状態をログ記録する。

導入手順(実機側):

```bash
sudo cp deploy/systemd/network_logger.service /etc/systemd/system/network_logger.service
sudo systemctl daemon-reload
sudo systemctl enable network_logger.service
```

実機の設定を変更した場合は、このファイルにも反映してコミットすること。

## (削除済み) pi-monitor.service

以前は「Raspberry Pi本体の汎用モニタリングサービス」としてユニットファイルのみを
本リポジトリで管理していたが、実機で調査した結果 `disabled`・`inactive (dead)` で、
`ExecStart` が指す `/opt/monitoring/monitor.py` および `/opt/monitoring/` ディレクトリ
自体が実機に存在せず、journalにも起動履歴が一切残っていないことを確認した。実質的に
使われていない(または一度もデプロイされなかった)ユニットと判断し、リポジトリからは
削除した。
