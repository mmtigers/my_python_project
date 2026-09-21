## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `MY_HOME_SYSTEM/tools/backup_host_config.py` |
| 言語 | Python |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |
| 解析基準コミット | Issue #774 での新規追加時点 |

## 関連ドキュメント

* [host_config_backup_service.md](./host_config_backup_service.md) - 実処理の実体。本 CLI は `perform_backup` / `format_summary_lines` を呼ぶ薄い層
* [config.md](./config.md) - `HOST_CONFIG_BACKUPS_DIR` の定義元
* [db_retention.md](./db_retention.md) - 同じ `tools/` 配下の CLI。`sys.path` へ親ディレクトリを足してから `config` を import する構成が共通
* `docs/runbooks/host_config_restore.md` - 復元手順（仕様書ツリー外）

## 2. ファイルの概要

ホスト側（`/etc` 等）の運用設定をバックアップする CLI。

**Issue #774**: SDカードが飛んだときに常時録画（nvr）・外部公開（cloudflared）・NAS マウント（`smbcredentials` / `fstab`）を手作業で組み直さずに済むよう、これらを NAS の `config.HOST_CONFIG_BACKUPS_DIR` へ1世代ずつ書き出す。

* 根拠: モジュールdocstring (行番号: 3〜31 / 抜粋: "ホスト側(`/etc` 等)の運用設定をバックアップする CLI (Issue #774)。")

**秘密を含むファイルは中身をコピーせず、台帳に メタデータだけを記録する**（#649 / #773 と同じ判断。詳細は実体側の仕様書）。

`/etc/nvr/*.env`（600・root）や `smbcredentials` の**メタデータ**を読むために root 権限が必要。root でなくても動くが、読めなかったものは台帳に `error` として残る。

## 3. 外部依存関係

### インポート一覧

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `argparse` | 標準 | CLI 引数の解析 | 根拠: [インポート宣言] (行番号: 31 / 抜粋: "import argparse") |
| `os` | 標準 | `sys.path` への親ディレクトリ追加 | 根拠: [インポート宣言] (行番号: 32 / 抜粋: "import os") |
| `sys` | 標準 | 終了コード・stderr | 根拠: [インポート宣言] (行番号: 33 / 抜粋: "import sys") |
| `config` | 自作 | 既定の出力先の表示 | 根拠: [インポート宣言] (行番号: 37 / 抜粋: "import config") |
| `services.host_config_backup_service` | 自作 | 実処理 | 根拠: [インポート宣言] (行番号: 38 / 抜粋: "from services import host_config_backup_service as svc") |

## 4. 主要な構成要素

### `main`

* **役割**: 引数を解析して `svc.perform_backup` を呼び、要約を表示して終了コードを返す
* 根拠: [定義] (行番号: 41 / 抜粋: "def main(argv: list[str] | None = None) -> int:")
* **引数**: `--dry-run`（1バイトも書かず、何を拾うかだけ表示）、`--dest`（出力先ルートの上書き）
* **戻り値/終了コード**:

| 条件 | 終了コード |
| --- | --- |
| 成功 | 0 |
| 読めなかったファイルがある（`errors > 0`） | 1 |
| 対象が1件も見つからない（`--dry-run` 以外） | 1 |
| `--dry-run` で対象0件 | 0（「確認」用途なので失敗にしない） |

* **判定順序の制約**: `errors` を**先に**見る。読めなかったファイルは `copied` にも `manifest_only` にも入らないため、順序を逆にすると root で実行していないケースが「候補パスが実態と合っていません」と誤って案内される（`tests/test_backup_host_config_cli.py` が固定）
* **なぜ終了コードを分けるか**: バックアップは cron / systemd から回す想定で、「何も保全できていない」を exit 0 で返すと成功として扱われる（#753 が `backup_service.py` の `__main__` で踏んだのと同じ失敗モード）

## 5. 保守上の注意点

* 初回は実機で `--dry-run` を流し、「不在（候補パス）」に出たものを確認して `HOST_CONFIG_TARGETS` を実態に合わせること。cloudflared / `smbcredentials` の置き場所はコードからは確認できない
* 本 CLI は cron / systemd に登録していない。登録する場合は root が必要なため、一般ユーザー用の `deploy/cron/crontab` ではなく root の crontab か systemd timer を使う
