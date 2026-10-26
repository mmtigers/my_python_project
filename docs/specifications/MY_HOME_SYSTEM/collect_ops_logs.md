## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | tools/collect_ops_logs.sh |
| 解析基準コミット | Issue #1042 での新規作成時点(2026-10-26) |
| 言語 | Bash (Shell Script) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [heartbeat.md](./heartbeat.md) - 出力に含める生存ログ(`data/heartbeat/*.txt` と `home_system.log*` の heartbeat 行)
* [health_watch.md](./health_watch.md) - 出力末尾に載せる `logs/health_watch.log` の書き手

## 2. ファイルの概要

実機の運用ログ調査用に、`logs/*.log` だけでは見えない情報を、読み取り専用でまとめて標準出力へ出す収集スクリプト。Issue #1042 で、次の項目が従来の調査用ログに含まれておらず判定できなかったため追加した。

* `nvr-{entrance,garden,parking}.service`(常時録画。`systemctl status` と警告以上の `journalctl`)
* `cloudflared`(エッジ到達性。警告以上)
* `home_system.service`(警告以上と、`Started/Stopped/Main process exited` 等の起動履歴)
* カーネルログ(OOM killer・電圧低下・CIFS/I/O エラー)
* 実機の `crontab -l` と `deploy/cron/crontab` の差分
* 生存ログ(heartbeat、ローテーション済みを含む)と、`data/heartbeat/*.txt` の日付
* `health_watch.log` の末尾

第1引数に `journalctl --since` へ渡せる形式(例: `"-6h"`、`today`。既定は `-24h`)を取る。何も変更・再起動せず、`journalctl` や `crontab` が無い環境でも項目ごとに「無い環境」と出して最後まで走る。

Webhook URL(Discord)、`rtsp://user:pass@`、`token` / `password` / `secret` / `api_key` 系の値は `mask` 関数でマスクして出す。ただしマスクは既知の形だけが対象なので、共有前に目視で確認すること。

## 3. 外部依存関係

| 名称 | 種類 | 用途 |
| --- | --- | --- |
| `journalctl` / `systemctl` / `crontab` | OSコマンド | ログとサービス状態、crontab の取得(無ければ読み飛ばす) |
| `zgrep` / `sed` / `diff` / `tail` | OSコマンド | heartbeat の検索(gz 含む)、マスク、差分 |
| `deploy/cron/crontab` | リポジトリのファイル | 実機の crontab との比較元 |
| `LOG_DIR` / `TAIL_LINES` | 環境変数 | ログ置き場(既定 `MY_HOME_SYSTEM/logs`)と、各項目の最大行数(既定 200) |

## 8. 保守上の注意点

* 読み取り専用であること。`systemctl restart` などの変更系コマンドを足さないこと。
* マスクの対象を増やすときは、`mask` 関数の `sed` 式に足す。新しい種類の認証情報を出力に含める項目を足したら、必ずここも更新する。
* 実機のサービス名(`nvr-*`、`cloudflared`、`home_system.service`)を変えたら、このスクリプトの対象も合わせる。
