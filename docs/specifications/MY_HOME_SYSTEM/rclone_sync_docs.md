## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `tools/rclone_sync_docs.sh` |
| 言語 | Bash |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

- [run_task.md](./run_task.md) — 本スクリプトを呼び出し、非0終了のときだけ失敗通知する起動ラッパー(`.sh` は `bash` で実行される)

## 2. ファイルの概要

* `docs/` を Google Drive(rclone リモート `gdrive:`)へ `rclone sync` で同期する。毎日 03:00 に crontab から `run_task.sh` 経由で起動される。
* Issue #947: 以前は crontab に `> /dev/null 2>&1` 付きで直書きされ、認証切れ・ネットワーク断・クォータ超過が常に無言だった。本スクリプトは rclone の終了コードをそのまま返し、出力は `run_task.sh` がログへ追記する。
* 根拠: [ヘッダコメント] (行番号: 2-7 / 抜粋: "# docs/ を Google Drive へ同期する")

## 3. 外部依存関係

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `/usr/bin/rclone` | 外部コマンド | 同期の実行。リモート `gdrive` の認証設定は実機側にあり、本ファイルからは不明 | `/usr/bin/rclone sync "${SRC}" "${DEST}" -v` (行番号: 13 / 抜粋: "/usr/bin/rclone sync") |

## 4. 主要要素の定義

* `set -u`: 未定義変数の参照をエラーにする。`-e` は無く、最終コマンドの終了コードがスクリプトの終了コードになる。根拠: `set -u` (行番号: 8 / 抜粋: "set -u")
* `SRC`: 同期元 `/home/masahiro/develop/docs`(実機の固定パス。可搬ではない)。根拠: `SRC="/home/masahiro/develop/docs"` (行番号: 10 / 抜粋: "SRC=\"/home/masahiro/develop/docs\"")
* `DEST`: 同期先 `gdrive:お家開発/docs`。根拠: `DEST="gdrive:お家開発/docs"` (行番号: 11 / 抜粋: "DEST=\"gdrive:お家開発/docs\"")

## 8. 保守上の注意点

* `rclone sync` は同期元に無いファイルを同期先から削除する。`SRC` の取り違えは Drive 側の `docs` を消すことになる。
* 出力を捨てる(`> /dev/null`)形に戻さないこと。失敗通知は終了コードと `run_task.sh` のログに依存する。
