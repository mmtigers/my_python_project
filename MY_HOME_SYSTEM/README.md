# MY_HOME_SYSTEM

IoT機器の制御、環境データの収集・分析、各種API・Webhookの統合ルーティングを担うFastAPIバックエンド。
Raspberry Pi上で常駐稼働し、`family-quest`(`/quest/`)の配信元でもある。仕様の詳細は
[docs/specifications/MY_HOME_SYSTEM/](../docs/specifications/MY_HOME_SYSTEM/README.md)、
システム全体の構成は[全体設計書.md](../docs/specifications/全体設計書.md)を参照。

## セットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env              # 値を実環境に合わせて編集
cp devices.json.example devices.json   # カメラ・SwitchBotデバイス等を実環境に合わせて編集
```

`.env`・`devices.json`はいずれもgitignore対象(実際の資格情報・機器情報を含むため)。

## 起動

```bash
./start_all.sh
```

`unified_server.py`(APIサーバー、内部で`scheduler_boot.py`を起動)と、ローカルホスト限定の
Streamlitダッシュボード(`dashboard.py`、認証なしのため外部非公開)をバックグラウンド起動する。
実機では`deploy/systemd/home_system.service`経由(`systemctl start home_system`)で起動される。

## 定期実行ジョブ

DBバックアップ・NAS古い録画削除・タイムラプス生成などのcronジョブは、単発スクリプトの実行に
`run_task.sh`(PYTHONPATHの設定・ログ出力を統一するラッパー)を使う:

```bash
./run_task.sh services/backup_service.py
```

実機のcrontab本体はリポジトリルートの[deploy/cron/](../deploy/cron/README.md)で管理している。

## テスト

```bash
.venv/bin/pytest
```

## デプロイ構成管理

実機の`/etc/systemd/system/`・logrotate設定は[deploy/](./deploy)配下でバージョン管理している。
実機側の設定を変更した場合は、対応する`deploy/`配下のファイルにも反映してコミットすること。
