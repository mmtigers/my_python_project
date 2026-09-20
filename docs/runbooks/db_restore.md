# DB・設定ファイルの復元手順(runbook)

`services/backup_service.py`(毎日 04:00 cron、`deploy/cron/crontab`)が NAS の
`<NAS_PROJECT_ROOT>/db_backups/`(既定 `/mnt/nas/home_system/db_backups/`)へ書き出す
バックアップから、実機(Raspberry Pi)の状態を復元する手順。Issue #649 で新設した。

## バックアップに含まれるもの / 含まれないもの

| ファイル | バックアップ名 | 含まれるか |
| --- | --- | --- |
| `home_system.db`(SQLite 本体) | `home_system_<YYYYMMDD_HHMMSS>.db`(`sqlite3` の backup API で作成、WAL 反映済みの整合スナップショット) | 含まれる |
| `config.py` | `config_<timestamp>.py` | 含まれる |
| `devices.json` | `devices_<timestamp>.json` | 含まれる |
| `.env`(全シークレット) | — | **含まれない**(Issue #649。以前は平文で NAS へコピーしていたが、NAS 共有の閲覧権限がそのままシークレットの閲覧権限になるため除外した) |
| `family_members.local.json` / `quest_users.local.json` | — | 含まれない(`config.BACKUP_FILES` に無い。必要なら追加する) |

保持期間は `DB_BACKUP_RETENTION_DAYS`(既定 30 日)で、`monitors/nas_monitor.py` の
`run_retention_cleanup` が古いものを削除する。

**`.env` はリポジトリにも NAS にも無い。** 復元時に必要な値(SwitchBot / LINE / Discord / Gemini の
トークン、`SWITCHBOT_WEBHOOK_TOKEN`、`ALEXA_SKILL_ID` 等。一覧は `MY_HOME_SYSTEM/.env.example`)は
パスワードマネージャ等リポジトリ外の秘匿情報として別途保管しておくこと。

## 0. NAS ごと失われた場合(オフサイトの最新1世代から復元)

2026-09-19 から、`.env` に `DB_BACKUP_OFFSITE_REMOTE`(例: `gdrive:お家開発/db_backup_latest`)を
設定していれば、毎日 04:00 のバックアップ成功後に最新世代が `home_system_latest.db` として
オフサイトへ上書き複製される(`services/backup_service.py` の `_copy_latest_offsite`)。
NAS が故障して `db_backups/` ごと失われた場合は、ここから取得して下記4の「復元」に進む。

### 前提: rclone の `gdrive` に自分用の OAuth クライアント ID を設定しておくこと

**2026-09-19 時点で実機のオフサイト複製は一時無効**(`.env` の `DB_BACKUP_OFFSITE_REMOTE` を
コメントアウト済み)。実機の rclone(v1.60.1)の `gdrive` リモートは `client_id` を持たず、
rclone に組み込みの**全ユーザー共用の OAuth クライアント**(`project_number:202264815644`)で
Google Drive API を呼んでいる。この共用枠の毎分上限に当たり
(`403: Quota exceeded for quota metric 'Queries' ... rateLimitExceeded`)、155MB の DB は
データ本体を送り終えても最後の確定処理が完了しなかった(既定の 8MB 分割でも 64MB 分割でも
同じ。20MB 程度の小さなファイルは通る)。毎晩 03:00 の `docs/` の rclone 同期も同じ共用枠を使う。

次の手順で自分用のクライアント ID を作り、rclone を再認証してから `.env` の
`DB_BACKUP_OFFSITE_REMOTE` のコメントを外す(参考: rclone 公式ドキュメント
"Making your own client_id")。

1. Google Cloud Console でプロジェクトを作成し、「Google Drive API」を有効化する
2. 「OAuth 同意画面」を外部(External)で作成し、自分のアカウントをテストユーザーに追加する。
   テスト状態のままだと更新トークンが7日で失効するため、作成後に「アプリを公開」して
   本番状態にする(個人利用なら審査は不要。認証時に「未確認のアプリ」の警告が出るが続行できる)
3. 「認証情報」→「OAuth クライアント ID」を種類「デスクトップアプリ」で作成し、
   クライアント ID とシークレットを控える(**リポジトリには書かない**)
4. ブラウザのある PC で `rclone authorize "drive" "<クライアントID>" "<シークレット>"` を実行し、
   表示されたトークン(JSON)を控える
5. ラズパイで `rclone config` → `gdrive` を編集し、`client_id` / `client_secret` を設定したうえで、
   手順4のトークンを貼り付けて再認証する
6. 疎通確認: `rclone copyto <NAS上の最新バックアップ> gdrive:お家開発/db_backup_latest/home_system_latest.db -v`
   が数分以内に `Copied` で終わること
7. `.env` の `DB_BACKUP_OFFSITE_REMOTE` のコメントを外す(翌朝 04:00 のバックアップから有効)

```bash
rclone copyto "gdrive:お家開発/db_backup_latest/home_system_latest.db" /tmp/home_system_latest.db
sqlite3 /tmp/home_system_latest.db "PRAGMA integrity_check;"   # ok と出ること
```

オフサイトには**最新1世代しか無い**(過去の世代・`devices.json` は NAS 側にしか無い)。
`devices.json` はカメラの接続情報を含むためオフサイトへは送っていない。

## 1. 復元前の確認

```bash
ls -lt /mnt/nas/home_system/db_backups/ | head        # 最新のバックアップを確認
sqlite3 /mnt/nas/home_system/db_backups/home_system_<timestamp>.db "PRAGMA integrity_check;"   # ok と出ること
sqlite3 /mnt/nas/home_system/db_backups/home_system_<timestamp>.db "SELECT COUNT(*) FROM schema_migrations;"  # 適用済みマイグレーション数
```

## 2. サービス停止(WAL を閉じる)

`home_system.db` は WAL モードで、稼働中は `home_system.db-wal` に未反映の書き込みが残る。
稼働中のファイルを差し替えると WAL と本体が食い違うため、必ず止めてから作業する。

```bash
sudo systemctl stop home_system.service home_dashboard.service
pgrep -af "unified_server.py|scheduler_boot.py|camera_monitor.py"   # 何も出ないこと
```

## 3. 現在のファイルを退避

```bash
cd /home/masahiro/develop/MY_HOME_SYSTEM
mkdir -p ~/restore_backup_$(date +%Y%m%d_%H%M%S) && cp -a home_system.db* config.py devices.json ~/restore_backup_*/ 2>/dev/null
```

## 4. 復元

```bash
cd /home/masahiro/develop/MY_HOME_SYSTEM
rm -f home_system.db home_system.db-wal home_system.db-shm
# バックアップは backup API で作成した整合スナップショットなので、そのままコピーでよい
cp /mnt/nas/home_system/db_backups/home_system_<timestamp>.db home_system.db
# (別の方法) sqlite3 home_system.db ".restore /mnt/nas/home_system/db_backups/home_system_<timestamp>.db"
chown masahiro:masahiro home_system.db
sqlite3 home_system.db "PRAGMA integrity_check;"

# 設定ファイル(必要な場合のみ。config.py は通常 git 管理の最新版を使うので、バックアップからの復元は
# git のチェックアウトより古い版に戻したいときだけ)
cp /mnt/nas/home_system/db_backups/devices_<timestamp>.json devices.json
```

`.env` はパスワードマネージャ等から `.env.example` の構成に沿って再作成する。

## 5. 起動と検証

```bash
sudo systemctl start home_system.service home_dashboard.service
sleep 20
systemctl status home_system.service                # active (running)、Main PID が unified_server.py
curl -s http://127.0.0.1:8000/health                  # {"status":"healthy"}
curl -s http://127.0.0.1:8000/api/quest/data | head -c 300   # クエストデータが返ること
journalctl -u home_system.service -n 50 --no-pager    # Migration failed / CRITICAL が無いこと
```

起動時に `lifespan` が未適用のマイグレーション(`migrations/NNNN_*.sql`)を自動適用するため、
バックアップ時点より新しいスキーマ変更があっても手作業は不要。`post_boot_health_check.py`
(`health-check.service`)を手動で流してもよい: `.venv/bin/python3 post_boot_health_check.py`。

## 6. 復元演習

年に1回程度、上記 1〜5 を**別の SQLite ファイル名**(例: `restore_test.db`)で通し、
`PRAGMA integrity_check` と `SELECT COUNT(*) FROM quest_history` 等でデータが読めることを確認する。
演習で気づいた落とし穴はこのファイルに追記する。

### 実施記録

| 実施日 | 範囲 | 結果 |
| --- | --- | --- |
| 2026-09-19 | 手順1・4・5の**データ検証部分**のみ(サービス停止・本番ファイル差し替えは行わない非破壊版) | 合格。落とし穴なし |

2026-09-19 の演習内容（`REPOSITORY_AUDIT_2026-09-18.md` 1.5節の確認事項7、AUDIT-024）:

```bash
# 最新世代を作業用ディレクトリへ復元(本番 home_system.db には触らない)
cp /mnt/nas/home_system/db_backups/home_system_20260919_040002.db /tmp/<work>/restored.db
```

| 検証 | 結果 |
| --- | --- |
| `PRAGMA integrity_check` | `ok`(155MB / 1.7秒でNASからコピー完了) |
| 3点セット(db / `config_*.py` / `devices_*.json`)の揃い | 直近5世代すべて揃っている |
| `schema_migrations` | 11件(最新 `0010_add_routine_step_events` の1つ前)。**バックアップ取得時点(04:00)ではまだ `0011`/`0012` が未適用** |
| 未適用マイグレーションの自動適用 | 復元したファイルに対して `init_unified_db.init_db()` を実行 → `0011`・`0012` が適用され、テーブル数が43→44、`routine_step_events` テーブルと `routine_progress.skipped_keys` 列が生成された。`Schema Integrity Validation Passed` |
| 同じ復元DBへの再実行(冪等性) | 2回目は差分なしで正常終了 |
| 主要テーブルの行数 | 復元(04:00時点) ≤ 本番(現在) の関係が全テーブルで成立。`quest_history` 3390/3399、`switchbot_meter_logs` 331,627/332,182 等 |
| 復元DBの最新データ時刻 | `switchbot_meter_logs` が 03:59:05 で、04:00 のバックアップ取得時刻と整合 |

**注意点(演習で分かったこと)**:

- 復元直後の `quest_master` は、退役クエストを含む**バックアップ取得時点のマスタ**である（この日の演習では復元側56件・本番51件で、差分は同日のマスタ同期で削除した退役6件だった）。本番へ復元したあとは、`quest_data.py` の内容に合わせてマスタ同期を実行すること（Issue #700）。**Issue #739 以降、このAPIは親(`role_adult`)の `admin_id` を要求する**ので、`curl -X POST -H 'Content-Type: application/json' -d '{"admin_id":"dad"}' http://127.0.0.1:8000/api/quest/sync_master` のようにボディを付ける（`admin_id` を省くと 422、子のIDだと 403）。APIを使わず `python sync_strict.py` を直接実行してもよい（CLI経路はサービス層を直接呼ぶため認可の対象外）。
- 上記の非破壊版では**手順2(サービス停止)・3(現物退避)・5(起動と検証)は実行していない**。実際の障害時に初めて通す部分が残っているため、次回は家族の利用が少ない時間帯にサービス停止を含めた通し演習を行うこと。

## 関連

- `MY_HOME_SYSTEM/services/backup_service.py`、[backup_service.md](../specifications/MY_HOME_SYSTEM/backup_service.md)
- `MY_HOME_SYSTEM/config.py` の `BACKUP_FILES` / `DB_BACKUP_RETENTION_DAYS`
- `MY_HOME_SYSTEM/deploy/systemd/README.md`(サービスの停止・起動)
- Issue #649
