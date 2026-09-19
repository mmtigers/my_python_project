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

`unified_server.py`(FastAPI サーバー本体)を `Type=simple` のフォアグラウンドプロセスとして起動し、
異常終了時は `Restart=on-failure`(10秒後)で自動復旧する(Issue #646)。起動前に `ExecStartPre` で
`start_all.sh --prepare` を実行し、旧プロセスの掃除・NASマウント待ち・`.venv`/`dist/` の鮮度チェック・
Webhook再登録(Phase 0〜3)を行う。`unified_server.py` は内部で `scheduler_boot.py` と
`monitors/camera_monitor.py` を子プロセスとして起動し、30秒ごとの死活監視で予期せず終了した子を
再起動する(1時間に5回を超えたらクラッシュループとみなして自動再起動を止め、CRITICALをDiscordへ通知する)。

> **Issue #492(決定: 案A)からの変更(Issue #646)**: 以前は `Type=oneshot` + `RemainAfterExit=yes` で
> `start_all.sh` が `unified_server.py` を `nohup ... & disown` でバックグラウンド起動しており、
> サーバー本体が systemd の管理外にあった。そのため `unified_server.py` 単独のクラッシュに対して
> systemd は何もできず(oneshot には `Restart=` が効かない)、`scheduler_boot.py` が落ちると配下の
> 6監視タスクが静かに止まり、検知は毎時cronの `health_watch.py`、復旧は人手だった。
> 当時 `Type=simple` への移行を見送った理由は「Phase 0(旧プロセス掃除)・Streamlitダッシュボードの
> 別プロセス起動との整合を取り直す必要がある」ことだったため、Phase 0〜3 を `start_all.sh --prepare`
> として `ExecStartPre` に分離し、ダッシュボードは `home_dashboard.service`(下記)へ切り出した。
> `start_all.sh` を引数なしで実行する従来の経路(手動運用・開発用)は残している。
> runbook(`docs/runbooks/raspi_claude_log_monitoring.md`)の「自動 `systemctl restart` は行わない」は
> Claude 自動調査側のガードレールであり、systemd 自身の `Restart=` による復旧はその対象ではない。
> `health_watch.py`(毎時cron、本サービスから独立)による検知・通知は引き続き行う。

> **AUDIT-002(2026-09-19 実機で確定)**: クラッシュループの歯止め
> `StartLimitIntervalSec=300` / `StartLimitBurst=5` は当初 `[Service]` セクションに
> 置いていたが、これらは systemd v229/v230 以降 `[Unit]` セクションのディレクティブで、
> 新名の `StartLimitIntervalSec=` は `[Service]` では解釈されない。実機(systemd 257)の
> `systemd-analyze verify deploy/systemd/home_system.service` が
> `Unknown key 'StartLimitIntervalSec' in section [Service], ignoring.` を出すことを確認し、
> `[Unit]` へ移動した。誤配置は `systemd-analyze verify` でも終了コード 0 になる(=CIのゲートに
> できない)ため、セクション配置の回帰テストを `.github/scripts/test_systemd_units.py` に置いている。

導入手順(実機側):

```bash
sudo cp deploy/systemd/home_system.service /etc/systemd/system/home_system.service
sudo systemctl daemon-reload
sudo systemctl enable home_system.service
sudo systemctl restart home_system.service
systemctl status home_system.service   # Active: active (running) で Main PID が python3 unified_server.py であること
```

サーバーの標準出力・標準エラーは journal に入る(`journalctl -u home_system.service -f`)。
アプリログは従来どおり `core/logger.py` が `logs/home_system.log` に書く。

## home_dashboard.service

Streamlit ダッシュボード(`dashboard.py`、認証なしのため `127.0.0.1:8501` のみにバインド)を
`home_system.service` から独立したユニットとして常駐実行する(Issue #646。以前は `start_all.sh` の
Phase 4 が `nohup` で起動していた)。

導入手順(実機側):

```bash
sudo cp deploy/systemd/home_dashboard.service /etc/systemd/system/home_dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now home_dashboard.service
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
反映漏れは、毎時cronの `monitors/health_watch.py`(チェック7: 実機構成ドリフト検知)が
本ディレクトリの `*.service` と `/etc/systemd/system/` 側の同名ファイルをコメント・空行を除いて比較し、
差分・未導入があれば Discord の error チャンネルへ通知する(自動で書き戻しはしない)。

## (削除済み) pi-monitor.service

以前は「Raspberry Pi本体の汎用モニタリングサービス」としてユニットファイルのみを
本リポジトリで管理していたが、実機で調査した結果 `disabled`・`inactive (dead)` で、
`ExecStart` が指す `/opt/monitoring/monitor.py` および `/opt/monitoring/` ディレクトリ
自体が実機に存在せず、journalにも起動履歴が一切残っていないことを確認した。実質的に
使われていない(または一度もデプロイされなかった)ユニットと判断し、リポジトリからは
削除した。

## home_firewall.service

`unified_server.py` の 8000番への接続元を loopback・直結サブネット・Tailscale に限定する
iptables ルールを、起動時(ネットワーク確立後・`home_system.service` より前)に適用する
oneshot ユニット(2026-09-19 新設)。実体は `scripts/firewall_apply.sh` で、設計の経緯・
許可範囲・fail-open の挙動は同スクリプト冒頭と
`docs/specifications/MY_HOME_SYSTEM/scripts_firewall_apply.md` を参照。
**8000番以外(SSH・Samba 等)には触れない。**

導入手順(実機側):

```bash
sudo cp deploy/systemd/home_firewall.service /etc/systemd/system/home_firewall.service
sudo systemctl daemon-reload
sudo systemctl enable --now home_firewall.service
sudo iptables -S HOME_APP_8000                 # 許可3種 → LOG → DROP の順に並んでいること
sudo iptables -C INPUT -p tcp --dport 8000 -j HOME_APP_8000 && echo "入口あり"
```

確認: LAN の端末からファミクエが開けること、外部 Webhook(SwitchBot 等)が引き続き 200 で
届いていること(`journalctl -u home_system.service | grep webhook`)。想定外の遮断は
`journalctl -k | grep "HOME_APP_8000 DROP"` に残る(毎分5件まで)。

切り戻し:

```bash
sudo systemctl stop home_firewall.service      # ExecStop が --remove を呼び、ルールを外す
sudo systemctl disable home_firewall.service   # 再起動後も適用しない場合
```

