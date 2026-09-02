## 1. 解析メタ情報

| 項目 | 内容 |
| --- | --- |
| 対象ファイル | `scripts/claude_investigate.sh` |
| 言語 | Shell (bash) |
| 解析対象 | 提供されたコードのみ |
| 推測・補完 | 一切なし |

## 関連ドキュメント

* [health_watch.md](./health_watch.md) - 本スクリプトを起動する層1(一次ヘルスチェック)。`_fire_investigate_hook()`が異常サマリを標準入力で渡して本スクリプトをfire-and-forget起動する
* [scripts_claude_log_watchdog.md](./scripts_claude_log_watchdog.md) - 廃止された前身の雛形。一次チェック部分はhealth_watch.pyへ、`claude -p`起動部分は本スクリプトへ分割移設された
* [config.md](./config.md) - 起動経路となる`config.HEALTH_WATCH_INVESTIGATE_HOOK`の定義元
* [notification_service.md](./notification_service.md) - `WATCHDOG_NOTIFY_WEBHOOK_URL`が「再利用する想定」としているDiscord Webhook通知の実体(本スクリプト自身は`curl`でPOSTする)
* `docs/runbooks/raspi_claude_log_monitoring.md` - 設計背景・ガードレールの意図・有効化手順を記載したランブック

## 2. ファイルの概要

ラズパイ監視の層2(Issue #339)。層1(`monitors/health_watch.py`)が異常を検知したときに`config.HEALTH_WATCH_INVESTIGATE_HOOK`経由で起動される調査専用スクリプトで、一次チェックは行わない(層1の責務)。標準入力で受け取った異常サマリをプロンプトに埋めてClaude Code CLI(`claude -p`)をヘッドレス起動し、リポジトリのソース・ログと突き合わせた原因調査と、GitHub Issue/Draft PRの起票(ドライラン時は調査結果の出力のみ)を行わせ、結果を任意のWebhookへ通知する。冒頭コメントに明記されている通り、`--permission-mode`/`--allowedTools`/`--max-turns`/`--output-format`のフラグ名・値は**実機未検証**であり、運用開始前に実機の`claude -p --help`で確認・修正が必要である。

* 根拠: 冒頭コメント (行番号: 2-38 / 抜粋: "# 異常時自動調査スクリプト (claude_investigate.sh)", "# ★重要(未検証フラグ):")

## 3. 外部依存関係

### インポート一覧

本スクリプトはシェルスクリプトのため`import`文はないが、実行に必須の外部コマンド・環境変数は以下の通り。

| 名称 | 種類 | 用途 | 根拠 |
| --- | --- | --- | --- |
| `flock` | 外部コマンド | ロックファイル(`logs/.claude_investigate.lock`)による多重起動防止 | 根拠: [ロック取得] (行番号: 53-57 / 抜粋: "exec 9>\"$LOCK_FILE\"\nif ! flock -n 9; then") |
| `timeout` | 外部コマンド(coreutils) | `claude -p`の暴走対策(既定900秒、SIGTERM後10秒でSIGKILL昇格) | 根拠: [起動コマンド] (行番号: 98 / 抜粋: "RESULT=$(timeout --kill-after=10 \"$TIMEOUT_SEC\" claude -p \"$PROMPT\"") |
| `claude` | 外部コマンド(Claude Code CLI) | ヘッドレスの原因調査本体 | 根拠: [起動コマンド] (行番号: 98-102 / 抜粋: "claude -p \"$PROMPT\"") |
| `jq` | 外部コマンド | `--output-format json`の`.result`抽出、および通知ペイロードのJSON組み立て | 根拠: [結果抽出/ペイロード] (行番号: 102, 116 / 抜粋: "jq -r '.result // .'", "PAYLOAD=$(jq -n --arg content") |
| `curl` | 外部コマンド | `WATCHDOG_NOTIFY_WEBHOOK_URL`への調査結果POST | 根拠: [通知] (行番号: 117-120 / 抜粋: "curl -fsS -X POST \"$WATCHDOG_NOTIFY_WEBHOOK_URL\"") |
| `CLAUDE_INVESTIGATE_PROJECT_DIR` | 環境変数 | リポジトリパスの上書き(既定`/home/masahiro/develop/my_python_project`) | 根拠: [変数定義] (行番号: 42 / 抜粋: "PROJECT_DIR=\"${CLAUDE_INVESTIGATE_PROJECT_DIR:-/home/masahiro/develop/my_python_project}\"") |
| `CLAUDE_INVESTIGATE_TIMEOUT_SEC` / `CLAUDE_INVESTIGATE_MAX_TURNS` | 環境変数 | タイムアウト秒(既定900)・`--max-turns`値(既定30)の上書き | 根拠: [変数定義] (行番号: 45-46 / 抜粋: "TIMEOUT_SEC=\"${CLAUDE_INVESTIGATE_TIMEOUT_SEC:-900}\"") |
| `CLAUDE_INVESTIGATE_DRY_RUN` | 環境変数 | `1`でドライラン(gh起票を許可ツールから外し、調査・提案のみ) | 根拠: [分岐] (行番号: 47, 72-79 / 抜粋: "if [ \"$DRY_RUN\" = \"1\" ]; then") |
| `WATCHDOG_NOTIFY_WEBHOOK_URL` | 環境変数(任意) | 調査結果の通知先Webhook | 根拠: [通知分岐] (行番号: 113 / 抜粋: "if [ -n \"${WATCHDOG_NOTIFY_WEBHOOK_URL:-}\" ]; then") |

### ブラックボックスとなる外部要素

| 名称 | 理由 | 根拠 |
| --- | --- | --- |
| `claude` CLIの実フラグ仕様 | `--permission-mode dontAsk`/`--allowedTools`/`--max-turns`/`--output-format json`はCLIバージョン依存で、本スクリプトからは検証できない(冒頭コメントで未検証と自認) | 根拠: [注意コメント] (行番号: 12-16 / 抜粋: "★重要(未検証フラグ)") |
| `gh issue create` / `gh pr create --draft` の認証・権限 | ghの認証状態は実機側セットアップに依存し本スクリプトからは不明 | 根拠: [前提コメント] (行番号: 36-38 / 抜粋: "gh CLI が Issue/PR作成の最小権限トークンで認証済み") |

## 4. 主要要素の定義（関数 / エンドポイント / コンポーネント）

### スクリプト本体（関数分割なし・直列実行）

* **役割**: (1) flockで多重起動を防止し、(2) 標準入力から異常サマリを読み、(3) ドライラン有無に応じて許可ツール(`ALLOWED_TOOLS`)と起票指示(`REPORTING_INSTRUCTION`)を組み立て、(4) `timeout`+`--max-turns`つきで`claude -p`をヘッドレス起動し、(5) 結果をログ出力・任意Webhookへ通知して、`claude`の終了コードで終了する。
* 根拠: (行番号: 40-124 / 抜粋: "set -euo pipefail", "exit \"$CLAUDE_EXIT\"")

* **引数/リクエスト**: コマンドライン引数なし。入力は標準入力の異常サマリのみ(空なら異常終了)。
* 根拠: [標準入力の読み取り] (行番号: 60-64 / 抜粋: "ANOMALY_SUMMARY=$(cat)\nif [ -z \"$ANOMALY_SUMMARY\" ]; then")

* **戻り値/レスポンス**: 終了コード。多重起動スキップ時は`0`、サマリ空は`1`、以降は`claude -p`(timeout込み)の終了コードを透過する。
* 根拠: (行番号: 55-57, 63, 124 / 抜粋: "exit 0", "exit 1", "exit \"$CLAUDE_EXIT\"")

* **副作用**: `logs/.claude_investigate.lock`の作成(flock用fd 9)、`claude -p`によるAPI呼び出しとリポジトリ読み取り(非ドライラン時は`gh`によるIssue/Draft PR起票を許可)、`WATCHDOG_NOTIFY_WEBHOOK_URL`へのHTTP POST(調査結果は先頭1500字に切り詰め)、標準出力へのログ(起動元のhealth_watch.py側で`logs/claude_investigate.log`へ追記される)。
* 根拠: [ロック/起動/通知] (行番号: 53, 98-102, 113-121 / 抜粋: "exec 9>\"$LOCK_FILE\"", "SNIPPET=$(printf '%s' \"$RESULT\" | head -c 1500)")

* **エラーハンドリング**: `set -euo pipefail`を基本としつつ、`claude -p`の呼び出しは`set +e`で囲んで終了コードを捕捉し、失敗時は`RESULT`にエラー説明を組み立てて通知に載せる。`curl`失敗は`|| echo ... >&2`で握りつぶし通知失敗として記録のみ。
* 根拠: (行番号: 96-108, 117-121 / 抜粋: "set +e", "CLAUDE_EXIT=$?", "|| echo \"[$(date)] 通知送信に失敗しました\" >&2")

### ガードレール（`ALLOWED_TOOLS` / プロンプト内指示）

* **役割**: 自動適用・自動デプロイ・`systemctl restart`を構造的に不可能にする。許可ツールは読み取り系(`Read,Grep,Glob`と`git log/diff/status`・`journalctl`・`df`・`free`・`tail`)+非ドライラン時のみ`gh issue create`/`gh pr create --draft`/`gh pr diff`。`--dangerously-skip-permissions`は使用しない。プロンプト側でも「提案のみ・サービス操作/push禁止」を明示する。
* 根拠: (行番号: 72-79, 92-94 / 抜粋: "ALLOWED_TOOLS=\"Read,Grep,Glob,Bash(git log*),...\"", "絶対に systemctl restart 等のサービス操作・コードの自動適用(push等)は行わないこと。")
* **引数/リクエスト・戻り値/レスポンス・副作用・エラーハンドリング**: 変数定義とヒアドキュメントのみで、それ自体の実行時副作用はない。

## 5. 処理フロー図

```mermaid
flowchart TD
    Start(["起動 (health_watch._fire_investigate_hook経由)"]) --> Lock{"flock -n 取得できたか?"}
    Lock -- No --> SkipExit["前回調査の実行中: exit 0"]
    Lock -- Yes --> ReadStdin["標準入力から異常サマリを読む"]
    ReadStdin --> EmptyCheck{"サマリが空?"}
    EmptyCheck -- Yes --> ErrExit["exit 1"]
    EmptyCheck -- No --> DryRun{"CLAUDE_INVESTIGATE_DRY_RUN=1 ?"}
    DryRun -- Yes --> ToolsDry["許可ツール: 読み取り系のみ<br>指示: 起票せず調査結果を出力"]
    DryRun -- No --> ToolsFull["許可ツール: 読み取り系 + gh issue create / gh pr create --draft<br>指示: 起票してURLを出力"]
    ToolsDry --> RunClaude["timeout + --max-turns つきで claude -p 起動"]
    ToolsFull --> RunClaude
    RunClaude --> Capture["jqで .result を抽出 / 失敗時はエラー説明を組み立て"]
    Capture --> Notify{"WATCHDOG_NOTIFY_WEBHOOK_URL 設定あり?"}
    Notify -- Yes --> Curl["curlで調査結果(先頭1500字)をPOST"]
    Notify -- No --> Done
    Curl --> Done(["claudeの終了コードでexit"])
```

## 6. 依存関係図

```mermaid
graph TD
    HealthWatch["monitors/health_watch.py<br>_fire_investigate_hook()"] -- "異常サマリ(標準入力)" --> Script["scripts/claude_investigate.sh"]
    Config["config.HEALTH_WATCH_INVESTIGATE_HOOK"] -. "起動パスを供給" .-> HealthWatch
    Script --> Flock["flock (logs/.claude_investigate.lock)"]
    Script --> Claude["claude -p (Claude Code CLI)"]
    Claude --> Repo["リポジトリのソース/ログ (読み取り)"]
    Claude -- "非ドライラン時のみ" --> Gh["gh issue create / gh pr create --draft"]
    Script --> Webhook["WATCHDOG_NOTIFY_WEBHOOK_URL (curl POST)"]
```

## 7. 次のステップ（リバースエンジニアリングの提案）

| 優先度 | ファイル名(推測可) | 理由 | 根拠 |
| --- | --- | --- | --- |
| 高 | `monitors/health_watch.py` | 本スクリプトの唯一の起動経路(`_fire_investigate_hook`)であり、発火条件(通知抑制との連動)を確認するため。 | 根拠: 冒頭コメント (行番号: 5-7 / 抜粋: "層1の monitors/health_watch.py が異常を検知したときに") |
| 中 | `docs/runbooks/raspi_claude_log_monitoring.md` | ガードレールの設計意図と実機での有効化手順を確認するため。 | 根拠: 冒頭コメント (行番号: 10 / 抜粋: "詳細設計・ガードレールは docs/runbooks/raspi_claude_log_monitoring.md を参照") |

## 8. 保守上の注意点

* **フラグ未検証のまま有効化しないこと**: `--permission-mode`/`--allowedTools`/`--max-turns`/`--output-format`は実機の`claude -p --help`で確認するまで想定値であり、確認前に`.env`の`HEALTH_WATCH_INVESTIGATE_HOOK`を設定するとガードレールが効かない可能性がある(冒頭の★コメント)。
* 導入初期は`CLAUDE_INVESTIGATE_DRY_RUN=1`(gh起票なし)での観察運用が推奨されている(ランブック参照)。
* 多重起動防止はflock(fd 9)であり、ロックはプロセス終了で自動解放される。前回調査が走行中の再発火はexit 0でスキップされる(health_watch側の6時間抑制と二重の防護)。
* 通知は`RESULT`を先頭1500字に切り詰めてからjqでJSONエンコードする(Discordのcontent上限2000字対策と、生文字列連結によるJSON破損防止)。
* `PROJECT_DIR`既定値はラズパイの実配置(`/home/masahiro/develop/my_python_project`)であり、他環境では`CLAUDE_INVESTIGATE_PROJECT_DIR`での上書きが必要(start_all.sh等と同じ非可搬パス規約)。

## 9. 不明事項一覧

| 項目 | 理由 | 必要なファイル |
| --- | --- | --- |
| `claude` CLIの実フラグ仕様・認証挙動 | CLIバージョン依存で、リポジトリ内には存在しないため。 | 実機の`claude -p --help`出力 |
| `WATCHDOG_NOTIFY_WEBHOOK_URL`の実値 | `.env`(gitignore対象)依存のため。 | 実機の`.env` |
| `gh`の認証権限の実態 | 実機セットアップ(最小権限トークン)に依存するため。 | 実機の`gh auth status` |

## 10. 自己検証結果

* [x] 推測・外部ファイルの仕様を一切含んでいない
* [x] 全関数・全クラス・全コンポーネントを列挙した（関数分割なしの直列スクリプトである旨を明記）
* [x] 全てのインポート要素を列挙した（外部コマンド・環境変数として）
* [x] すべての仕様説明に「根拠（行番号・抜粋）」を明記した
* [x] 根拠漏れが0件である
* [x] Mermaid構文にエラーの原因となる記号（エスケープ漏れ）がない
* [x] 不明事項を漏れなく列挙した

完了
