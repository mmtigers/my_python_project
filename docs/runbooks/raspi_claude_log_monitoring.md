# ラズパイ自動ログ監視・調査ランブック

ラズパイ（`home_system.service` 稼働機）のログを定期監視し、異常検知時に通知（将来的には調査・改修案提示まで）を行うための構成メモ。

**現状**: 層1（検知・通知）は実装済み（`MY_HOME_SYSTEM/monitors/health_watch.py`）。層2（Claudeによる自動調査）は**リポジトリ側の実装済み・実機セットアップ待ち**（Issue #339。`MY_HOME_SYSTEM/scripts/claude_investigate.sh` + `health_watch.py` のフック拡張。`.env` の `HEALTH_WATCH_INVESTIGATE_HOOK` が未設定のうちは何も起きない）。旧雛形 `scripts/claude_log_watchdog.sh` は廃止（一次チェック部分はhealth_watch.pyに置き換え済みで、`claude -p` 起動部分はclaude_investigate.shへ移設）。

## 目的

- ラズパイ側の障害（`home_system.service` のクラッシュ、ディスク/メモリ逼迫、NASマウント断など）を、人間が能動的にダッシュボードを見に行かなくても検知したい。
- ただし、自動適用（コード修正の自動デプロイ・`systemctl restart` の自動実行）は行わない。ラズパイは家庭用の本番システムであり、誤診断による停止のリスクを避けるため、自動化は「検知・通知（将来は調査・提案まで）」に留め、適用は人間判断とする。

## 全体構成（採用: 3層）

```
層1: 検知（ラズパイ上、毎時cron、トークン消費ゼロ） … 実装済み
  monitors/health_watch.py （run_task.sh経由、home_system.serviceから独立）
    - service active / journalctl err..emerg / logs/*.log のERROR
    - ディスク・メモリ閾値 / NASマウント
    - 異常あり → Discord errorチャンネルへ要約通知（同一異常の再通知は6時間抑制）
    - 異常なし → 通知せず終了

層2: 調査（異常時のみ、リポジトリ側実装済み・実機セットアップ待ち）
  health_watch.py が異常検知時(通知抑制の内側)に、
  config.HEALTH_WATCH_INVESTIGATE_HOOK のスクリプトを異常サマリつきで
  fire-and-forget起動する。実体は scripts/claude_investigate.sh:
  `claude -p` をヘッドレス起動し、原因調査 → GitHub Issue/Draft PR起票
  (ドライラン時は調査結果の通知のみ)。フック未設定のうちは従来どおり
  検知・通知のみ（現運用: 通知を受けて人間がClaude Codeセッションで調査）。

層3: 死活監視（ラズパイの外）
  Cloudflare Zero Trust の Tunnel Health アラート。
  ラズパイごと死ぬと層1自体が動けないため、既存のCloudflare Tunnel
  （cloudflared、常時稼働）の切断をCloudflare側からメール通知させる。
```

### なぜこの構成か（2026-09-02改訂）

初版（コミット `7c6d74c`）は「クラウドのClaude CodeからCloudflare Access経由で定期SSH」する設計だったが、以下の理由で本構成に変更した。

- Claude Code（Remote Control）がラズパイ実機上で動作しており、SSH到達性の整備（Cloudflare Access Application / Service Token）が不要になった。Tailscaleも導入済みで、緊急時の手動SSHは既に可能。
- 一次チェックは決定論的なスクリプトで十分であり、LLM（クラウドセッション）を毎時起動する必然性がない。トークンコストは異常時のみ（層2実装後）に限定できる。
- 初版がローカル完結案を却下した唯一の理由「ラズパイ本体が落ちたら自己診断できない」は、Cloudflare Tunnelの死活アラート（層3）だけで塞がる。

### 既存監視との棲み分け

`scheduler_boot.py` 配下の監視群（`server_watchdog.py`・`memory_monitor.py`・`nas_monitor.py`）は `home_system.service` と同じプロセスツリーで動くため、**サービスごと落ちると監視も一緒に停止する**。層1（cron駆動・サービス独立）はこの穴を塞ぐ位置づけであり、平常時は既存監視と重複検知しない（サービスがactiveなら層1のserviceチェックは沈黙する）。ログ走査のキーワード・除外パターンは週次の `log_analyzer.py` と共通（`LogAnalyzer` クラスを流用）。

## セットアップ

### 層1: cron登録（デプロイ後に1回）

`monitors/health_watch.py` がデプロイ済み（mergeして本番チェックアウトにpull済み）の状態で:

```
# ラズパイ一次ヘルスチェック (毎時10分)
10 * * * * /home/masahiro/develop/MY_HOME_SYSTEM/run_task.sh monitors/health_watch.py
```

毎時10分にしているのは、毎時0分に走る既存ジョブ（newface_monitor等)や時報系の負荷と重ねないため。

### 層3: Cloudflare Tunnelアラート（ダッシュボードで1回、無料）

1. [Cloudflare Zero Trustダッシュボード](https://one.dash.cloudflare.com/) → Notifications（アカウントの通知設定）
2. 「Add」→ アラートタイプ **Tunnel Health（Tunnel status changes / becomes unhealthy）** を選択
3. 対象トンネル（ラズパイで稼働中の既存Tunnel）とメール宛先を設定

これでラズパイの電源断・ネットワーク断・OSフリーズ時に、cloudflaredの切断をトリガーとしてCloudflareからメールが届く。

### 残存ギャップと運用でのカバー

- **層1のcron自体が壊れた場合**（venv破損等）: `run_task.sh` が `logs/health_watch.log` にERROR行を記録し、週次の `log_analyzer.py` レポートが拾う（最大1週間の検知遅延は許容）。
- **通知の送信失敗**: health_watchはexit 1で終了し、同様に週次レポートで拾われる。

## マーカーファイル規約

- `MY_HOME_SYSTEM/logs/.claude_watch_marker`: 前回チェック完了時刻のISO8601文字列のみ。ログ走査の重複検知を防ぐ。
- `MY_HOME_SYSTEM/logs/.claude_watch_notify_state`: 直近に通知した異常セットのフィンガープリントと通知時刻（JSON）。同一異常の継続中は6時間再通知を抑制する。
- いずれも `home_system.service` 本体のコードからは読み書きしない（監視の追加が本体アプリの挙動に影響しないようにするため）。

## 一次チェック基準

`monitors/health_watch.py` 実装（詳細は `docs/specifications/MY_HOME_SYSTEM/health_watch.md`）:

- `systemctl is-active home_system.service` が active でない
- `journalctl -u home_system.service --since <marker> -p err..emerg` に出力がある
- `logs/*.log` に前回マーカー以降で `ERROR`/`CRITICAL` 等を含む行がある（キーワード・除外は `log_analyzer.py` と共通。WARNINGは週次レポートに任せる）
- ルートディスク使用率 ≥ 90% / メモリ使用率 ≥ 90%（`services/analysis_service.py` と同じ取得方法）
- `NAS_MOUNT_POINT` がマウントされていない

誤検知（flakyな一時的エラー等）が続く場合は、`LogAnalyzer.IGNORE_PATTERNS` や閾値側を見直す。

## 層2（自動調査）のガードレール・有効化手順

リポジトリ側の実装（Issue #339）:

- `monitors/health_watch.py` の `_fire_investigate_hook()`: 異常検知かつ通知抑制(`_should_notify`)を通過したときのみ、`config.HEALTH_WATCH_INVESTIGATE_HOOK` のスクリプトを異常サマリ(標準入力)つきでfire-and-forget起動する。未設定なら完全no-op。フックの出力は `logs/claude_investigate.log` に追記される（`check_app_logs` の自己発火除外対象）。
- `scripts/claude_investigate.sh`: 調査専用スクリプト。flockによる多重起動防止、`timeout`(既定900秒)+`--max-turns`(既定30)、`--allowedTools` の機械的制限、`CLAUDE_INVESTIGATE_DRY_RUN=1` でのドライラン(gh起票なし)に対応。環境変数は `.env.example` の「ラズパイ監視 層2」セクション参照。

有効化手順（ラズパイ側。下の「準備」完了後）:

1. `claude -p --help` で `--permission-mode` / `--allowedTools` / `--max-turns` / `--output-format` の実フラグ名を確認し、`claude_investigate.sh` を実態に合わせて修正する（★スクリプト内のフラグは未検証の想定値）。
2. `.env` に `HEALTH_WATCH_INVESTIGATE_HOOK` と `CLAUDE_INVESTIGATE_DRY_RUN=1` を設定（まずドライラン）。
3. 異常サマリを手で流して単体検証: `echo "テスト異常" | MY_HOME_SYSTEM/scripts/claude_investigate.sh`
4. 1〜2週間ドライランで観察し、問題なければ `CLAUDE_INVESTIGATE_DRY_RUN` を外してgh起票を有効化する。

ガードレール:

- コード修正の自動適用・自動デプロイは行わない（GitHub IssueまたはDraft PRとして提案するのみ）。`claude -p` の `--allowedTools` を読み取り系 + `gh issue create`/`gh pr create --draft` のみに機械的に制限する。
- `systemctl restart` 等の破壊的操作は自動実行しない（`log_tab.py` の再起動ボタンが確認チェックボックス必須になっているのと同じ思想）。許可リストにこれらを含めないことで担保する。
- `--dangerously-skip-permissions`（全許可モード）は絶対に使わない。
- `--permission-mode` / `--allowedTools` 等のフラグ名・値はCLIのバージョンによって変わりうるため、実装前に必ず実機で `claude --help` / `claude -p --help` を確認してから設定する（ここを飛ばすと「ガードレールが効いていない」状態になりうる）。
- 多重起動防止は `fcntl.flock`（DDDのバッチと同じ方式）、暴走対策はタイムアウトと `--max-turns`。
- 通知は `notification_service.py` の既存Webhook設定を再利用し、新たな認証情報経路を増やさない。
- 詳細調査を毎回発火させる方向でのチューニングは行わない（コスト増につながるため）。層1の検知精度側を直す。

準備（ラズパイ側、未実施。上の有効化手順の前提）:

1. Claude Code CLIのインストール（`claude --version` で確認）。
2. ヘッドレス実行用の認証: ブラウザのある別マシンで `claude setup-token` を実行して長期トークンを発行し、ラズパイ側の環境変数 `CLAUDE_CODE_OAUTH_TOKEN` に設定（`.env` 等gitignore対象の場所。**リポジトリには絶対にコミットしない**）。
3. `gh` CLIをIssue/PR作成の最小権限トークンで認証。
4. コスト面: `claude -p` はステートレスで異常検知時のみ呼ばれるため平常時コストはゼロ。呼び出しコストは `--output-format json` のレスポンスから取得してログに記録することを検討する。

## 将来拡張（不採用だが記録として残す）

クラウドのClaude Codeから Cloudflare Access（Service Token + `cloudflared access ssh`）経由で定期SSHする初版設計は、ラズパイ上でClaude Codeが動かなくなった場合や、ラズパイダウン時にもクラウド側から調査を試みたい場合の拡張オプションとして有効。必要になったら初版（コミット `7c6d74c` のこのファイル）を参照。
