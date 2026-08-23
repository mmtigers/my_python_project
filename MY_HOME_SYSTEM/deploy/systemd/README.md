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

> **既知の不具合**: `After=` に指定されている `unified-server.service` というユニットは
> 実機に存在しない(存在するのは `home_system.service`)。おそらく `home_system.service`
> へリネームされた際に追従漏れした死んだ依存関係で、systemdはエラーにせず順序制約を
> 無視するだけのため気づかれずに残っていた。`After=network-online.target home_system.service`
> に修正して実機に反映し、このファイルもそれに合わせて更新すること。

## home_system.service

`start_all.sh` を `ExecStart` で実行するoneshotユニット(`RemainAfterExit=yes`)。
`start_all.sh` が内部で `unified_server.py` を `nohup` バックグラウンド起動し、
`unified_server.py` がさらに `scheduler_boot.py` 等を起動する。

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

## pi-monitor.service

Raspberry Pi本体の汎用モニタリングサービス。`ExecStart` が指す `/opt/monitoring/monitor.py`
は本リポジトリの外(`MY_HOME_SYSTEM` 対象外)にあるスクリプトであり、このリポジトリでは
管理していない。ユニットファイル自体の変更履歴管理のみを目的として配置している。

実機の設定を変更した場合は、このファイルにも反映してコミットすること。
