# ラズパイ自動ログ監視・調査ランブック（設計）

Claude Code がラズパイ（`home_system.service` 稼働機）上でログを定期監視 → 異常検知時に調査・原因特定・改修案提示までを自動化するための構成メモ。**現時点では設計段階であり、未実装**（実装には後述の準備が必要）。

> **改訂履歴**: 当初はクラウド側（Claude Code Remote）からCloudflare Tunnel経由でSSHする構成を検討していたが、準備手順が煩雑なため、**ラズパイ上で完結する方式**に変更した（Cloudflare Access・SSH到達性の設定が一切不要になる）。

## 目的

- ラズパイ側の障害（`home_system.service` のクラッシュ、ディスク/メモリ逼迫、NASマウント断など）を、人間が能動的にダッシュボードを見に行かなくても検知したい。
- 検知した異常について、原因調査・改修案の提示までを自動化し、対応の初動を早める。
- ただし、自動適用（コード修正の自動デプロイ・`systemctl restart` の自動実行）は行わない。ラズパイは家庭用の本番システムであり、誤診断による停止のリスクを避けるため、**「調査・提案」までを自動化し、適用は人間判断**とする。

## 全体構成（ラズパイ上で完結）

```
[ラズパイ: cron / systemdタイマー、目安1時間毎]
        │
        ▼
[watchdogスクリプト（シェルのみ・API呼び出しなし）]
        │
        ├─ journalctl -u home_system.service --since <前回マーカー> -p err..emerg
        ├─ logs/*.log の ERROR/CRITICAL grep
        ├─ ディスク/メモリ使用率の閾値チェック（df, free）
        └─ マーカーファイル（前回チェック時刻）を更新
        │
        ├─ 異常なし → 何もせず終了（Claude Code CLIは呼び出さない。APIコストゼロ）
        │
        └─ 異常あり → Claude Code CLI をヘッドレス起動（claude -p、`--allowedTools`で許可コマンドを限定）
              - 本リポジトリのソース（services/*, routers/* 等）と突き合わせて原因を特定
              - 改修案（diff）を作成
              - `gh issue create` または `gh pr create --draft` でGitHub上に起票
              - Discord/LINE Webhookへ要約を通知（notification_service.pyと同じURLを再利用）
              - `--allowedTools` に systemctl・force push・rm 等は含めない（自動適用・自動再起動はできない構成にする）
```

なぜこの構成か（採用しなかった案との比較）:

| 案 | 内容 | 採否 |
| --- | --- | --- |
| クラウド側からCloudflare Tunnel経由でSSH | 定期的にクラウドのセッションがラズパイへSSH接続 | **不採用**。Cloudflare Access・Service Token・SSH到達性の設定が煩雑なため見送り |
| **ラズパイ上で完結（採用）** | `cron`/systemdタイマーが安価な一次チェックをシェルのみで行い、異常時のみ`claude -p`をローカル起動 | 準備がシンプル（新規ネットワーク経路が不要）。ただし**ラズパイ本体が完全に落ちた場合は自己診断できない**（後述） |

**この構成のトレードオフ（承知の上で採用）**: ラズパイ自体が電源断・OSクラッシュ等で完全停止した場合、watchdog自体も動かないため異常を検知できない。この方式では「ラズパイは動いているがアプリ/リソースに異常がある」ケースのみをカバーする。ラズパイ本体の死活監視が別途必要な場合は、外形監視（外部サービスからのping等）を別途検討すること（本ランブックのスコープ外）。

## 必要な準備（ラズパイ側の作業のみ）

> **重要**: 以下の `--permission-mode` / `--allowedTools` 等のフラグ名・値は、実装時点のClaude Code CLIの仕様確認に基づく想定であり、**CLIのバージョンによって変わりうる**。実装前に必ずラズパイ上で `claude --help` および `claude -p --help` を実行し、実際に使えるフラグ名・値を確認してから反映すること。誤った権限設定のまま運用すると「ガードレールが効いていない」状態になりうるため、ここは絶対に確認を飛ばさないこと。

1. **Claude Code CLIのインストール**: ラズパイに `claude` コマンドをインストールする（`claude --version` で確認）。
2. **認証**: ヘッドレス実行にはAPIキーまたは長期トークンが必要（ラズパイにはブラウザがないため、対話的OAuthログインはそのままでは使えない）。
   - 推奨: ブラウザが使える別マシン（PC）で `claude setup-token` を実行し、長期トークンを発行 → ラズパイ側の環境変数 `CLAUDE_CODE_OAUTH_TOKEN` に設定する。
   - 代替: Anthropic ConsoleでAPIキーを発行し、`ANTHROPIC_API_KEY` として設定する（コスト管理の観点ではOAuthトークンの方がスコープが狭く推奨）。
   - いずれも**リポジトリには絶対にコミットしない**（`.env` 等、gitignore対象の場所に置く）。
3. **GitHub連携**: `gh` CLIをインストールし、Issue/PR作成権限のみを持つトークンで認証する（`gh auth login` または `GH_TOKEN` 環境変数）。既存のCI等で使っているトークンとは別に、この自動化専用の最小権限トークンを発行することを推奨。
4. **通知Webhook**: `notification_service.py` が使っているDiscord/LINEのWebhook URLを、watchdogスクリプト用の環境変数（例 `WATCHDOG_NOTIFY_WEBHOOK_URL`）として渡す。新たな通知経路は増やさない。
5. **権限まわりの確認**（前述の注意点を参照）: `claude -p` をヘッドレス実行する際に、読み取り系コマンドと `gh issue create`/`gh pr create --draft` のみを許可し、`systemctl`・`rm`・`git push --force` 等は許可リストに含めないことを、実際のCLIのフラグで確認・設定する。
6. **cron/systemdタイマーへの登録**: `MY_HOME_SYSTEM/scripts/claude_log_watchdog.sh`（本リポジトリに追加済み。後述）を1時間毎に実行するよう登録する。
   ```
   0 * * * * /home/masahiro/develop/MY_HOME_SYSTEM/scripts/claude_log_watchdog.sh >> /home/masahiro/develop/MY_HOME_SYSTEM/logs/watchdog_cron.log 2>&1
   ```

## watchdogスクリプト

`MY_HOME_SYSTEM/scripts/claude_log_watchdog.sh` として本リポジトリに雛形を追加した。内容は以下の通り（詳細はスクリプト本体を参照）。

- 一次チェック（journalctl・logs/*.log・ディスク/メモリ）はシェルのみで行い、Claude Code CLIを呼び出さない。異常がなければAPIコストはゼロ。
- 異常検知時のみ `claude -p` をヘッドレス起動し、検知内容をプロンプトとして渡す。
- `--allowedTools` で許可するコマンドを、読み取り系（`git log`/`git diff`/`git status`等）と `gh issue create`/`gh pr create --draft` のみに限定し、破壊的操作は一切許可しない。
- 結果をDiscord/LINE Webhookへ通知する。

**このスクリプトはまだ実機で検証していない雛形**であり、特に権限フラグ名は前述の通り実装前の確認が必須。

## マーカーファイル規約

- 置き場所: `MY_HOME_SYSTEM/logs/.claude_watch_marker`（`logs/` は `start_all.sh` が起動時に `mkdir -p` している既存ディレクトリ）。
- 内容: 前回チェック完了時刻のISO8601文字列のみ。
- `home_system.service` 本体のコードには手を入れない（監視の追加が本体アプリの挙動に影響しないようにするため）。

## 異常検知の一次チェック基準（実装時のたたき台）

- `journalctl -u home_system.service --since <marker> -p err..emerg` に出力がある
- `logs/*.log` に前回マーカー以降で `ERROR` または `CRITICAL` を含む行がある
- ディスク使用率・メモリ使用率が閾値（要調整、例: 90%）を超えている
- 上記はいずれも `services/analysis_service.py` の `get_system_logs` / `get_disk_usage` / `get_memory_usage` と同種の取得方法（`journalctl`, `shutil.disk_usage`, `free -m`）に合わせる。ダッシュボード（`log_tab.py`）の実装と判定基準がズレないようにするため。

## 詳細調査〜改修案提示のガードレール（実装時に必ず守ること）

- コード修正の自動適用・自動デプロイは行わない（GitHub IssueまたはDraft PRとして提案するのみ）。
- `systemctl restart` 等の破壊的操作は自動実行しない（`log_tab.py` の再起動ボタンが確認チェックボックス必須になっているのと同じ思想）。`--allowedTools` の許可リストにこれらのコマンドを含めないことで担保する。
- `--dangerously-skip-permissions`（全許可モード）は絶対に使わない。
- 通知は `notification_service.py` が使うWebhook設定を再利用し、新たな認証情報経路を増やさない。
- 誤検知（flakyな一時的エラー等）が続く場合は、一次チェックの閾値・パターン側を見直す。詳細調査を毎回発火させる方向でのチューニングは行わない（コスト増につながるため）。

## コスト面の注意

- `claude -p` の1回の呼び出しはステートレス（前回までの文脈は引き継がない）。異常検知時のみ呼ばれる設計なので、平常時のコストはゼロ。
- 呼び出し時のコストは `--output-format json` のレスポンスに含まれる想定（コスト関連フィールドの正確な名称は要確認）なので、必要であればログに記録して監視する。

## 未実装の理由・次のステップ

上記「必要な準備」（Claude Code CLIのインストール・認証、`gh` CLI認証、権限フラグの実機確認）はいずれもラズパイ側の作業が必要で、このセッションから直接実施できない。準備が整い次第、以下を実施して実装に進む。

1. `claude --help` / `claude -p --help` で実際に使える権限関連フラグを確認し、`claude_log_watchdog.sh` 内の該当箇所を実際の値に更新する。
2. 異常を模擬した状態（例: ダミーのERRORログを書き込む）で一度スクリプトを手動実行し、一次チェック〜詳細調査〜Issue/PR起票〜通知までの一連の動作をテストする。
3. cron/systemdタイマーに登録し、本番運用を開始する。
4. 本ランブックに実装後の実際の設定値（チェック間隔等、機密情報を含まない範囲）を追記する。
