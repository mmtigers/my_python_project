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
> として `ExecStartPre` に分離し、ダッシュボードは `home_dashboard.service`(当時の下記セクション)へ
> 切り出した。**（Issue #829で変更）** その後Streamlit版ダッシュボードは廃止され、`unified_server.py`
> 自身が配信するようになったため、`home_dashboard.service`自体が不要になり削除された(下記参照)。
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

## home_dashboard.service（廃止・Issue #829）

以前はStreamlit ダッシュボード(`dashboard.py`、認証なしのため `127.0.0.1:8501` のみにバインド)を
`home_system.service` から独立したユニットとして常駐実行していた(Issue #646。以前は `start_all.sh` の
Phase 4 が `nohup` で起動していた)。Streamlit版ダッシュボードの廃止に伴い、`dashboard.py`・
`deploy/systemd/home_dashboard.service` ともにリポジトリから削除され、このユニット自体が不要になった。
ダッシュボードは `home_system.service` が起動する `unified_server.py` 自身が配信する(`routers/dashboard_router.py`)。
実機で本ユニットが有効化されたままであれば `sudo systemctl disable --now home_dashboard.service` で停止・
無効化し、`/etc/systemd/system/home_dashboard.service` を削除すること。

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

## nvr-entrance.service / nvr-garden.service / nvr-parking.service

カメラ3台の常時録画。`ffmpeg` が RTSP を `-c copy`(無劣化・CPU負荷最小)で受け、
NAS(`/mnt/nas/home_system/nvr_recordings/<カメラ>/`)へ **10分ごとの MP4** を直接書く。
録画が止まっていないかは `monitors/health_watch.py` のチェック10が、この
`{YYYYMMDD}_{HHMMSS}.mp4` という命名の最新ファイル時刻を見て検知する(Issue #716)。

> **Issue #773**: 以前この3本は**リポジトリ外**(`/etc/systemd/system/` に直接手書き)で管理され、
> **RTSP の認証情報が `ExecStart` に平文**で書かれたファイルが **644** で置かれていた。
> 差分・履歴・レビューの対象外だったため3本の間で設定がばらつき、`-timeout` が garden だけ
> 欠落していた(Issue #772。録画が無音で止まりうる状態だった)。本ディレクトリへ取り込み、
> 認証情報は `/etc/nvr/<カメラ>.env`(600・root所有)へ切り出した。

### 認証情報ファイル(実機のみ・リポジトリには置かない)

カメラごとに1ファイル。中身は RTSP の URL 1行だけ:

```
NVR_RTSP_URL=rtsp://<ユーザー>:<パスワード>@<カメラのIP>:554/<ストリームのパス>
```

作成手順(実機側):

```bash
sudo install -d -m 700 -o root -g root /etc/nvr
sudo install -m 600 -o root -g root /dev/null /etc/nvr/entrance.env
sudo tee /etc/nvr/entrance.env >/dev/null <<'EOF'
NVR_RTSP_URL=rtsp://...
EOF
# garden / parking も同様
sudo chmod 600 /etc/nvr/*.env
```

> URL は移行前のユニット(`/etc/systemd/system/nvr-*.service.bak.*`)の `-i` 引数に入っている。
>
> **移行時の注意(2026-09-20 に実際に踏んだ)**: ユニット本文では `%` が systemd の指定子
> (specifier)の開始文字なので `%%` と二重に書く必要があるが、**EnvironmentFile の値では
> 指定子展開が行われない**ため `%` は1つで書く。旧ユニットからURLをコピーするときに `%%`
> のまま貼ると、ffmpeg にはリテラルの `%%` が渡り、パスワード不一致で
> `401 Unauthorized` になって録画が起動しない(`Restart=always` で延々とリトライし続ける)。
> パスワードにURLエンコード(`%21` = `!` 等)が含まれる場合に該当する。
>
> ```bash
> # 旧ユニットから移すときは %% を % に戻す
> sudo sed -i 's/%%/%/g' /etc/nvr/*.env
> ```
>
> 逆に `$` は EnvironmentFile 側でも変数参照として解釈されるため、値に `$` を含む場合は
> `$$` とエスケープする。

### 導入手順(実機側)

```bash
sudo cp deploy/systemd/nvr-entrance.service /etc/systemd/system/nvr-entrance.service
sudo cp deploy/systemd/nvr-garden.service   /etc/systemd/system/nvr-garden.service
sudo cp deploy/systemd/nvr-parking.service  /etc/systemd/system/nvr-parking.service
sudo chmod 644 /etc/systemd/system/nvr-*.service   # 認証情報は含まれないので 644 でよい
sudo systemctl daemon-reload
sudo systemctl restart nvr-entrance nvr-garden nvr-parking
```

確認(カメラごとに、restart 後の時刻で新しいセグメントが作られること):

```bash
systemctl is-active nvr-entrance nvr-garden nvr-parking
for c in entrance garden parking; do ls -t /mnt/nas/home_system/nvr_recordings/$c | head -1; done
journalctl -u nvr-garden --since "-5 min"    # ffmpeg のエラーが出ていないこと
```

切り戻し: 移行前のユニットは `/etc/systemd/system/nvr-*.service.bak.*` に残してある。

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


## host-config-backup.timer / host-config-backup.service / host-config-backup-failure.service

ホスト側(`/etc`)の運用設定を NAS へバックアップする `tools/backup_host_config.py` を
**毎日 04:10 に実行する**(Issue #774)。04:00 の DB バックアップ
(`deploy/cron/crontab` の `backup_service.py`)と重ならないよう10分ずらしている。

`deploy/cron/crontab` ではなく timer を使う理由は、本 CLI が `/etc/smbcredentials` や
`/etc/nvr/*.env`(いずれも 600・root 所有)の**メタデータ**を読むために root を必要とし、
`deploy/cron/crontab` が一般ユーザー(`masahiro`)用だからである。

導入手順(実機側):

```bash
sudo cp deploy/systemd/host-config-backup.service \
        deploy/systemd/host-config-backup-failure.service \
        deploy/systemd/host-config-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now host-config-backup.timer

# 確認
systemctl list-timers host-config-backup.timer
sudo systemctl start host-config-backup.service   # 手動で1回流して確認する
journalctl -u host-config-backup.service -n 50
```

`enable` するのは **timer だけ**でよい(`host-config-backup.service` は timer から起動される)。

### 3ユニットに分けている理由

- **`host-config-backup.service`** — 本体。`Type=oneshot` で root。`Requires=mnt-nas.mount` で
  NAS 未マウント時に走らないようにしている(未マウントのまま走ると SD カード上の空の
  `/mnt/nas` へ書き、しかも「成功」してしまう)。
- **`host-config-backup-failure.service`** — `OnFailure=` から起動される失敗通知。
  **`User=masahiro` で動かすのが要点。** `tools/notify_task_failure.py` は `core.logger` 経由で
  `logs/run_task.log` に記録するため、root で走らせるとこのログが root 所有で作られ、
  以後 `run_task.sh`(masahiro)からの失敗通知が書き込めなくなり、**失敗通知そのものが
  無音で壊れる**。本体を `run_task.sh` 経由にしていないのも同じ理由。
- **`host-config-backup.timer`** — スケジュール。`Persistent=true` で、Pi が停止していて
  発火を逃した場合は次回起動時に取り返す(これが無いと「電源を落としていた日は黙って
  バックアップされない」経路が残る)。

`.github/scripts/test_systemd_units.py` が、timer に発火条件があること・起動対象の
`.service` が実在すること・`WantedBy=timers.target` があること・oneshot に `Restart=` を
書いていないことを CI で検査する。
