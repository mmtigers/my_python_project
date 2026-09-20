## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `MY_HOME_SYSTEM/services/host_config_backup_service.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | Issue #774 での新規追加時点 |

## 関連ドキュメント

* [backup_service.md](./backup_service.md) - DB とリポジトリ配下の設定ファイルを扱う既存のバックアップ。本モジュールはその**対象外だったホスト側(`/etc`)**を担う。両者は独立しており、互いを呼ばない
* [config.md](./config.md) - `HOST_CONFIG_BACKUPS_DIR` / `HOST_CONFIG_BACKUP_RETENTION_DAYS` の定義元
* [backup_host_config.md](./backup_host_config.md) - 本モジュールを呼ぶ CLI
* `docs/runbooks/host_config_restore.md` - 復元手順（仕様書ツリー外）

## 2. ファイルの概要

ホスト側（`/etc` 等）の運用設定をバックアップするサービス。

**Issue #774**: 日次バックアップ（`backup_service.py`）の対象は `config.BACKUP_FILES` に列挙されたリポジトリ配下のファイルだけで、ホスト側の運用設定は丸ごと対象外だった。SDカードが飛ぶと DB とアプリは戻せても、常時録画（`nvr-*.service`）・外部公開（cloudflared）・NAS マウント（`smbcredentials` と `/etc/fstab` の CIFS 設定）・samba の設定は手作業で組み直しになる。

* 根拠: モジュールdocstring (行番号: 1〜52 / 抜粋: "ホスト側(`/etc` 等)の運用設定のバックアップ (Issue #774)。")

### 秘密情報をコピーしない方針

**秘密を含むファイルは中身をコピーせず、台帳（`MANIFEST.json`）にメタデータだけを残す。** 記録するのはパス・所有者・パーミッション・サイズ・sha256 で、値そのものは残さない。

これは新しい方針ではなく、既にこのリポジトリが2度下している判断に揃えたものである。

| 判断 | 内容 |
| --- | --- |
| #649 | `.env` を意図的にバックアップ対象から外した |
| #773 | RTSP 認証情報を `/etc/nvr/*.env`(600・root)へ切り出し、リポジトリには含めず手順だけを残した |

NAS 共有は 664 で見えるため、平文の認証情報を置けば「バックアップを取ったことで秘密の露出面が増える」ことになる。台帳だけあれば復元時に必要なファイルとその属性は分かり、値はパスワードマネージャから入れ直す運用が成立する。

### 宣言上「秘密でない」ファイルも素通しにしない

実機の中身は環境によって違う（例: `/etc/fstab` に CIFS の `password=` を直書きしている構成もありうる）。コードからは実機の中身を確認できないため、**コピーする全ファイルに `redact_secrets()` を通す。**

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `contextlib` | 標準 | `chmod` 失敗の抑制 | 根拠: [インポート宣言] (行番号: 54 / 抜粋: "import contextlib") |
| `datetime` | 標準 | 世代ディレクトリ名・保持期間の判定 | 根拠: [インポート宣言] (行番号: 55 / 抜粋: "import datetime") |
| `glob` | 標準 | 対象パターンの展開 | 根拠: [インポート宣言] (行番号: 56 / 抜粋: "import glob") |
| `grp` / `pwd` | 標準 | uid/gid を名前へ解決 | 根拠: [インポート宣言] (行番号: 57,62 / 抜粋: "import grp") |
| `hashlib` | 標準 | sha256 の算出 | 根拠: [インポート宣言] (行番号: 58 / 抜粋: "import hashlib") |
| `json` | 標準 | 台帳の書き出し | 根拠: [インポート宣言] (行番号: 59 / 抜粋: "import json") |
| `re` | 標準 | redact 対象の判定 | 根拠: [インポート宣言] (行番号: 63 / 抜粋: "import re") |
| `shutil` | 標準 | 古い世代の削除 | 根拠: [インポート宣言] (行番号: 64 / 抜粋: "import shutil") |
| `config` | 自作 | 出力先・保持日数 | 根拠: [インポート宣言] (行番号: 69 / 抜粋: "import config") |
| `core.logger.setup_logging` | 自作 | ロガー取得（`DiscordErrorHandler` が付く） | 根拠: [インポート宣言] (行番号: 70 / 抜粋: "from core.logger import setup_logging") |

### ブラックボックスとなる外部要素

| 名称 | 理由 |
| --- | --- |
| 実機の `/etc` の実際の配置 | cloudflared / `smbcredentials` の置き場所はディストリ・導入方法で変わる。コードからは確認できないため候補パスを複数宣言し、**存在しなければ台帳に `missing` として記録する**形にしてある。初回は `--dry-run` で実態を確認して候補を整理する運用 |
| NAS(CIFS) のマウントオプション | 出力先を 0o700 で作るが、マウントオプション次第で効かない。効かなかった実際の値を台帳の `dir_mode_effective` に残す |

## 4. 主要な構成要素

### `HostConfigTarget`

* **役割**: バックアップ対象の1エントリ。`pattern` は glob。`secret` が `True` のものは中身をコピーせず台帳のみ。`repo_managed` は「リポジトリにも正がある」もので、実機のコピーが repo とずれていないかを sha256 で突き合わせるために記録する
* 根拠: [定義] (行番号: 76 / 抜粋: "class HostConfigTarget:")

### `HOST_CONFIG_TARGETS`

* **役割**: 対象の宣言。systemd ユニット（repo にも正がある）・NAS マウント・外部公開・録画の認証情報の4系統
* 根拠: `HOST_CONFIG_TARGETS: tuple[HostConfigTarget, ...] = (` (行番号: 94)

### `redact_secrets`

* **役割**: `password=...` 等の**値だけ**を落とした本文と、落とした件数を返す
* 根拠: [定義] (行番号: 137 / 抜粋: "def redact_secrets(text: str) -> tuple[str, int]:")
* **値の終端**: `,` / 空白 / 引用符 / `;` で止める。`\S+` にすると `credentials=/etc/samba/smbcredentials,noserverino,vers=3.0` のようなカンマ区切りのマウントオプションを丸ごと飲み込み、**復元に必要な `noserverino` まで消してしまう**（実装時に実際に踏んだため `tests/test_host_config_backup_service.py` に回帰テストがある）
* **意図的に対象外にしているキー**: `username=`（それ自体は秘密ではない。ユーザー名とパスワードが揃って秘密になる `smbcredentials` は `secret=True` 側で扱う）と `credentials=` / `credentials-file:`（実際には**秘密ファイルへのパス**が入るため、落とすと復元先が分からなくなる）

### `FileRecord`

* **役割**: 台帳の1行。`path` は**復元先の絶対パス**、`source` は実際に読んだパス（通常は同じで、その場合は台帳に出さない）
* 根拠: [定義] (行番号: 150 / 抜粋: "class FileRecord:")
* **なぜ分けるか**: 実装当初は実際に読んだパスをそのまま記録しており、「どこへ戻すのか」が台帳から分からなくなっていた

### `plan`

* **役割**: 何をどう扱うかを決めるだけの読み取り専用フェーズ。1バイトも書かない
* 根拠: [定義] (行番号: 237 / 抜粋: "def plan(root: str = \"/\") -> list[FileRecord]:")
* **読めなかったファイル**は黙って飛ばさず `status="error"` として記録する（黙って飛ばすと、復元時に「そんなファイルがあったこと自体」を知る手段が無くなる）

### `perform_backup`

* **役割**: ホスト設定を収集して1世代分を書き出し、最後に `prune` を呼ぶ
* 根拠: [定義] (行番号: 306 / 抜粋: "def perform_backup(")
* **`dry_run=True`** なら `plan()` の結果だけを返し、1バイトも書かない
* **テキストとして読めないファイル**（バイナリ等）は、値を確認できないのでコピーせず台帳のみに落とす（素通しで NAS へ置くより安全側に倒す）
* **副作用**: 出力先ディレクトリの作成（0o700）、`files/` へのファイル書き出し（0o600）、`MANIFEST.json` / `README.txt` の書き出し、`prune` による古い世代の削除

### `prune`

* **役割**: 保持期間（`HOST_CONFIG_BACKUP_RETENTION_DAYS`、既定 90 日）を超えた世代ディレクトリを削除する
* 根拠: [定義] (行番号: 368 / 抜粋: "def prune(base: Path, retention_days: int | None = None,")
* **名前が `%Y%m%d_%H%M%S` として解釈できないディレクトリには触らない**（人が置いたものを消さないため）
* 保持日数が 0 以下なら何もしない

### `format_summary_lines`

* **役割**: CLI / ログ向けの人が読む要約
* 根拠: [定義] (行番号: 408 / 抜粋: "def format_summary_lines(outcome: BackupOutcome) -> list[str]:")
* 出力先のパーミッションが 0o700 でなければ警告行を足す

## 5. 保守上の注意点

* **秘密の値を `files/` に書く変更を入れないこと。** `tests/test_host_config_backup_service.py` の `TestSecretsAreNeverWritten` が出力ツリー全体を走査して秘密文字列が現れないことを固定している
* 新しい対象を足すときは `secret` の判断を必ず付けること。判断に迷うものは `secret=True`（台帳のみ）に倒す
* 本 CLI は cron / systemd に登録していない。登録する場合は root が必要なため、一般ユーザー用の `deploy/cron/crontab` ではなく root の crontab か systemd timer を使う
* 暗号化・復元リハーサルは**未対応**で、#753 と設計が重複する。鍵の置き場所を決められるなら合わせて設計する
