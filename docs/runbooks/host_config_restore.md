# ホスト設定（`/etc` 側）のバックアップと復元

Issue #774。SDカードが飛んだとき、DB とアプリは `docs/runbooks/db_restore.md` で戻せるが、
**常時録画・外部公開・NAS マウントはホスト側（`/etc`）の設定**に依存しており、
そこは日次バックアップ（`services/backup_service.py` / `config.BACKUP_FILES`）の対象外だった。
本 runbook はその保全と復元を扱う。

## 前提: 秘密はバックアップに入っていない

**秘密を含むファイルは中身をコピーしない。** 台帳（`MANIFEST.json`）にパス・所有者・
パーミッション・サイズ・sha256 だけを記録する。

これは既存の2つの判断に揃えたものである。

| 判断 | 内容 |
| --- | --- |
| #649 | `.env` を意図的にバックアップ対象から外した（NAS 上に 664 で残った旧コピーの掃除も含む） |
| #773 | RTSP 認証情報を `/etc/nvr/*.env`（600・root）へ切り出し、リポジトリには含めず手順だけを残した |

NAS 共有は 664 で見えるため、平文の認証情報を置くと**バックアップを増やしたことで
秘密の露出面が増える**。台帳があれば「どのパスに・どの所有者とパーミッションで・
どんな内容（sha256）のファイルが必要か」は復元時に分かるので、**値はパスワード
マネージャから入れ直す**という運用が成立する。

秘密の値そのものの保管場所（パスワードマネージャ等）は、このリポジトリの管理外である。

## バックアップ

実機の `MY_HOME_SYSTEM/` で:

```bash
# 何を拾うか・どれが秘密扱いかの確認（1バイトも書かない）
sudo .venv/bin/python tools/backup_host_config.py --dry-run

# 1世代を書き出す
sudo .venv/bin/python tools/backup_host_config.py
```

### 定期実行（Issue #774）

毎日 04:10 に systemd timer で自動実行される。`deploy/cron/crontab`（一般ユーザー用）では
なく timer を使うのは、本 CLI が root を必要とするため。導入手順は
`MY_HOME_SYSTEM/deploy/systemd/README.md` の「host-config-backup.timer」を参照。

```bash
systemctl list-timers host-config-backup.timer   # 次回発火とその前回の実行
journalctl -u host-config-backup.service -n 50   # 直近の実行ログ
```

失敗すると `OnFailure=` で `host-config-backup-failure.service` が起動し、
`tools/notify_task_failure.py`（Issue #751 の通知経路）から Discord へ通知される。
**無音では落ちない**が、通知にはタスクごとに1時間のクールダウンがある。

`/etc/nvr/*.env`（600・root）や `smbcredentials` の**メタデータ**を読むために root が必要。
root でなくても動くが、読めなかったものは台帳に `error` として残り、CLI は
**exit 1** を返す（黙って飛ばすと、復元時に「そんなファイルがあったこと自体」を
知る手段が無くなるため）。

### ⚠️ 初回は `--dry-run` の「不在」を必ず確認すること

`services/host_config_backup_service.py` の `HOST_CONFIG_TARGETS` は、**cloudflared や
`smbcredentials` の置き場所について候補パスを複数宣言している**（ディストリ・導入方法で
変わり、コードからは実機の配置を確認できない）。初回は `--dry-run` を流し、

- 「不在（候補パス）」に出たものが**本当に存在しないのか**（＝候補から削ってよい）
- 実在するのに拾えていないパスが**無いか**（＝候補に足す）

を確認し、実態に合わせて `HOST_CONFIG_TARGETS` を直すこと。

### 出力

`<NAS>/home_system/host_config_backups/<YYYYmmdd_HHMMSS>/`

| 中身 | 説明 |
| --- | --- |
| `files/<復元先のパス>` | 秘密を含まないファイルのコピー。階層は**復元先の絶対パス**で再現される |
| `MANIFEST.json` | 全対象の台帳。コピーしていない秘密ファイルも、不在だった候補パスも載る |
| `README.txt` | この世代が何で、復元手順がどこにあるか |

`files/` に入るファイルも `redact_secrets()` を通すため、`password=` / `token=` 等の
**値**は `***REDACTED-BY-host_config_backup***` に置き換わることがある
（置換件数は台帳の `redactions`）。`username=` と `credentials=`（＝秘密ファイルへの
**パス**）は復元に必要なので意図的に残す。

保持世代は `HOST_CONFIG_BACKUP_RETENTION_DAYS`（既定 90 日）。名前が
`%Y%m%d_%H%M%S` として解釈できないディレクトリには触らない（人が置いたものを消さないため）。

## 復元

### 1. 台帳を読む

```bash
cd <NAS>/home_system/host_config_backups/<最新の世代>
python3 -m json.tool MANIFEST.json | less
```

`files` 配列の各エントリの `status` が復元方法を決める。

| `status` | 意味 | 復元方法 |
| --- | --- | --- |
| `copied` | 中身がある | `files/` からコピーし、台帳の `mode` / `owner` / `group` を復元 |
| `manifest_only` | **秘密なので中身は無い** | 値をパスワードマネージャから入れ直し、`sha256` で照合 |
| `missing` | この世代の時点で存在しなかった | 対応不要（候補パスの1つ） |
| `error` | 読めなかった | 台帳の `detail` を見る。root で取れていない可能性 |

### 2. リポジトリに正があるものは、リポジトリから入れる

systemd ユニットは `MY_HOME_SYSTEM/deploy/systemd/` が正である（#773）。
バックアップ側のコピーは**実機と repo のドリフト検出**のために取っているだけなので、
復元は repo から行うこと。手順は `MY_HOME_SYSTEM/deploy/systemd/README.md`。

```bash
# 台帳の sha256 と repo のファイルを突き合わせ、実機だけで変更されていたものを洗い出す
sha256sum MY_HOME_SYSTEM/deploy/systemd/*.service
```

一致しないものがあれば、**実機側だけで直した変更が repo に取り込まれていない**
ということなので、repo から入れる前に差分を確認する。

### 3. 秘密ファイルを入れ直す

`manifest_only` のものは値が無い。`/etc/nvr/*.env` の入れ方は
`MY_HOME_SYSTEM/deploy/systemd/README.md` の nvr 節にある（`%` のエスケープの注意も含む）。
入れ終わったら台帳の `sha256` と照合する。

```bash
sha256sum /etc/nvr/entrance.env   # MANIFEST.json の sha256 と一致するか
sudo chmod 600 /etc/nvr/*.env
sudo chown root:root /etc/nvr/*.env
```

一致しない場合、**入れた値が当時と違う**（改行・末尾空白の混入を含む）。

### 4. NAS マウントと samba

`files/etc/fstab` は redact されている可能性があるため、そのまま上書きしない。
CIFS 行のオプション（`noserverino` 等）を参照しつつ、認証情報は
`smbcredentials` を入れ直して `credentials=` で参照させる。

```bash
sudo mount -a
mountpoint -q /mnt/nas && echo "OK"
```

### 5. 外部公開（cloudflared）

トンネル資格情報（`*.json`）は `manifest_only` なので入っていない。
Cloudflare のダッシュボードでトンネルを再発行するか、保管してある資格情報を戻す。
`/dashboard` 等のバイパス設定の点検は `docs/runbooks/cloudflare_access_connectivity_check.md`。

## 未対応として残していること

- **暗号化**: 秘密を暗号化して保全する方式（鍵の置き場所を決める必要がある）は採っていない。
  現状は「秘密は保全しない・台帳だけ残す」方針。鍵管理を決められるなら、
  #753（バックアップの暗号化）と合わせて設計する
- **出力先のパーミッション（受け入れ済みの残存リスク）**: ツールは出力ディレクトリを 0700 で
  作ろうとするが、NAS は `/etc/fstab` の CIFS オプションが `dir_mode=0775` 固定のため効かない
  （実行時に警告が出て、`MANIFEST.json` の `dir_mode_effective` にも残る）。マウント全体の
  オプションを変えると録画・DB バックアップ等にも影響するため、変えずに受け入れている。
  生成物に平文の秘密は入らない（`db_backups/` 側も 2026-09-21 の #829 で平文の秘密を撤去し、
  以後はコピー時に伏せ字化される）。**残るリスクは台帳の sha256 だけ**: 秘密ファイルの
  中身の形式が分かっている（例: `NVR_RTSP_URL=rtsp://<user>:<pass>@<ip>/...`）ため、
  パスワードが弱ければ NAS を読める者が sha256 から総当たりで推測できる。LAN 内の NAS に
  限られるので現状は許容するが、パスワードは推測されにくいものにしておくこと。
  台帳の照合を保ったままこれを避けるなら、sha256 ではなく鍵付きの HMAC にする
  （鍵の置き場所が要るので、上の「暗号化」と同じ設計の話になる）

## 復元リハーサルの記録

### 2026-09-21（初回。`20260921_041053` の世代）

実機の `/etc` には書き込まず、一時ディレクトリへ「復元」して検証した。**問題0件**。

| 検証 | 結果 |
| --- | --- |
| 台帳 | copied 11 / manifest_only 4 / missing 5 |
| コピー済み（伏せ字なし）10件: 復元した中身が実機の `/etc` と一致するか | **10/10 一致** |
| 秘密ファイル 4件（`/etc/smbcredentials`、`/etc/nvr/*.env`）: 実機の現物の sha256 が台帳と一致するか | **4/4 一致**（＝手順3の「入れ直したら照合」が機能する） |
| 伏せ字あり 1件（`cloudflared.service`）: 台帳の sha256 が実機の原本と一致するか | **一致**（トークンを入れ直した後に照合できる） |
| mode / owner が台帳に揃っているか | **全件あり** |
| 手順2: リポジトリ管理のユニット（`deploy/systemd/` の 10 service + 1 timer）と実機の差分 | **11/11 一致**（実機だけで変更されたユニットは無い） |

分かったこと:

- 手順どおりに戻せば、**秘密以外は完全に元どおり**になり、秘密は照合まで含めて手順が成立する
- 「不在」5件（`/etc/cloudflared/*` 等）は、この実機が cloudflared をトークン方式で動かしている
  ことによる想定内の不在で、復元の妨げにはならない（トークンは cloudflared のユニット内。手順5）

### 次にやるとき

`/etc` やユニットを変更したら、同じ検証を流すと変更が次のバックアップに入ったかも確かめられる。
やり方は「最新の世代を一時ディレクトリへコピーし、`MANIFEST.json` の各行について
`copied` は復元した中身と実機の現物の sha256、`manifest_only` は実機の現物の sha256 と台帳を
比べる。`redactions` のあるものは台帳の sha256 と実機の原本を比べる」。秘密ファイルを読むので
`sudo` が要る。実機の `/etc` には書き込まないこと（リハーサルの目的は手順の検証であって、
上書きではない）。

## 関連

- `docs/runbooks/db_restore.md` — DB 本体の復元
- `MY_HOME_SYSTEM/deploy/systemd/README.md` — systemd ユニットの導入手順・`/etc/nvr` の作り方
- #753 — バックアップの暗号化・復元リハーサル（本件と設計が重複する）
- #649 — `.env` をバックアップ対象から外した判断
- #773 — RTSP 認証情報を `/etc/nvr` へ切り出した判断
